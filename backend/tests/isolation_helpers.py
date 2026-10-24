"""Reusable cross-user isolation check for user-owned tables (PLAN.md section 11).

Call :func:`assert_table_isolated` from the test for every new user-owned table
(AGENTS.md: "add a cross-user access test for every new table/endpoint").

The table name is never interpolated as free text: it must appear in the
catalog list of public tables that have a ``user_id`` column, and is then
quoted as an identifier.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import asyncpg

from trendscend.db.tenant import tenant_connection

APP_ROLE = "trendscend_app"


async def _user_owned_tables(pool: asyncpg.Pool) -> set[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT c.table_name FROM information_schema.columns c "
            "JOIN information_schema.tables t "
            "  ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
            "WHERE c.table_schema = 'public' AND c.column_name = 'user_id' "
            "  AND t.table_type = 'BASE TABLE'"
        )
    return {r["table_name"] for r in rows}


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


async def _assert_rls_enabled(pool: asyncpg.Pool, table: str) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'public' AND c.relname = $1",
            table,
        )
    assert row is not None, f"{table}: not found in pg_class"
    assert row["relrowsecurity"], f"{table}: row-level security is not enabled"
    assert row["relforcerowsecurity"], f"{table}: row-level security is not FORCEd"


_COMMANDS = ("SELECT", "INSERT", "UPDATE", "DELETE")
_TENANT_SETTING = "app.current_user"


async def _granted_commands(pool: asyncpg.Pool, table: str) -> dict[str, bool]:
    """Which commands the app role can perform on ``table`` (table or column grants)."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT has_table_privilege($1, t.r, 'SELECT') AS sel, "
            "       has_any_column_privilege($1, t.r, 'INSERT') AS ins, "
            "       has_any_column_privilege($1, t.r, 'UPDATE') AS upd, "
            "       has_table_privilege($1, t.r, 'DELETE') AS del "
            "FROM (SELECT ('public.' || quote_ident($2))::regclass AS r) t",
            APP_ROLE,
            table,
        )
    assert row is not None
    return {"SELECT": row["sel"], "INSERT": row["ins"], "UPDATE": row["upd"], "DELETE": row["del"]}


async def _assert_policies_cover_commands(
    pool: asyncpg.Pool, table: str, granted: dict[str, bool]
) -> None:
    """Every command the app role can run must be governed by tenant-scoped policies.

    All applicable permissive policies must reference ``app.current_user``: one
    stray ``USING (true)`` policy is OR-ed in by Postgres and defeats the rest.
    A command with no applicable policy is denied by default, which also
    isolates, but is reported so a missing policy is not mistaken for coverage.
    """
    async with pool.acquire() as conn:
        policies = await conn.fetch(
            "SELECT policyname, cmd, qual, with_check, roles FROM pg_policies "
            "WHERE schemaname = 'public' AND tablename = $1 AND permissive = 'PERMISSIVE'",
            table,
        )
    for cmd in _COMMANDS:
        if not granted[cmd]:
            continue  # not granted: reported as skipped, nothing to cover
        applicable = [
            p
            for p in policies
            if p["cmd"] in (cmd, "ALL") and {"public", APP_ROLE} & set(p["roles"])
        ]
        assert applicable, f"{table}: app role can {cmd} but no policy applies to {cmd}"
        for p in applicable:
            # WITH CHECK defaults to USING when omitted.
            exprs = {
                "SELECT": [p["qual"]],
                "DELETE": [p["qual"]],
                "INSERT": [p["with_check"] or p["qual"]],
                "UPDATE": [p["qual"], p["with_check"] or p["qual"]],
            }[cmd]
            for expr in exprs:
                assert expr is not None and _TENANT_SETTING in expr, (
                    f"{table}: {cmd} policy {p['policyname']!r} does not reference "
                    f"{_TENANT_SETTING}"
                )


async def _validated_mutable_column(
    pool: asyncpg.Pool, table: str, column: str | None
) -> str | None:
    if column is None:
        return None
    async with pool.acquire() as conn:
        cols = {
            r["column_name"]
            for r in await conn.fetch(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = $1",
                table,
            )
        }
        if column not in cols:
            raise ValueError(f"{table}: unknown column {column!r}")
        can = await conn.fetchval(
            "SELECT has_column_privilege("
            "$1, ('public.' || quote_ident($2))::regclass, $3, 'UPDATE')",
            APP_ROLE,
            table,
            column,
        )
    if not can:
        raise ValueError(
            f"{table}: {APP_ROLE} has no UPDATE privilege on {column!r}; "
            "pass a granted column or mutable_column=None"
        )
    return _quote_ident(column)


async def _owner_count(pool: asyncpg.Pool, qtable: str, user: uuid.UUID) -> int:
    """Ground truth: rows carrying ``user``'s id, read as the owner role."""
    async with tenant_connection(pool, user) as conn:
        return int(
            await conn.fetchval(f"SELECT count(*) FROM {qtable} WHERE user_id = $1", user)  # noqa: S608
        )


async def _assert_one_direction(
    pool: asyncpg.Pool,
    qtable: str,
    table: str,
    me: uuid.UUID,
    other: uuid.UUID,
    my_rows: int,
    update_sql: str,
) -> None:
    async with tenant_connection(pool, me, role=APP_ROLE) as conn:
        total = await conn.fetchval(f"SELECT count(*) FROM {qtable}")  # noqa: S608
        assert total == my_rows, f"{table}: {me} sees {total} rows, expected {my_rows}"
        leaked = await conn.fetchval(
            f"SELECT count(*) FROM {qtable} WHERE user_id = $1",  # noqa: S608
            other,
        )
        assert leaked == 0, f"{table}: {me} can read {leaked} rows of {other}"

        # Each mutation runs in its own savepoint so a privilege error on one
        # does not abort (and so mask) the next. A missing privilege means the
        # app role cannot touch the table that way at all: isolation holds.
        for verb, sql in (
            ("UPDATE", update_sql),
            ("DELETE", f"DELETE FROM {qtable} WHERE user_id = $1"),  # noqa: S608
        ):
            try:
                async with conn.transaction():
                    status = await conn.execute(sql, other)
            except asyncpg.InsufficientPrivilegeError:
                continue
            except asyncpg.NotNullViolationError:
                # SET user_id = DEFAULT only errors when it matched a row.
                raise AssertionError(f"{table}: {me} {verb} touched rows of {other}") from None
            affected = int(status.rsplit(" ", 1)[-1])
            assert affected == 0, f"{table}: {me} {verb} touched {affected} rows of {other}"


async def assert_table_isolated(
    owner_pool: asyncpg.Pool,
    table: str,
    make_row: Callable[[uuid.UUID], Awaitable[None]],
    user_a: uuid.UUID,
    user_b: uuid.UUID,
    *,
    insert_as_other: Callable[[uuid.UUID], Awaitable[None]] | None = None,
    mutable_column: str | None = None,
) -> None:
    """Prove users A and B cannot see or modify each other's rows in ``table``.

    What is proven, and what is not:

    * RLS enabled and FORCEd (pg_class), and, via pg_policies, that for every
      command (SELECT/INSERT/UPDATE/DELETE) the app role holds a privilege for,
      each applicable permissive policy references ``app.current_user``. This
      catalog check is what proves RLS covers each command; commands the app
      role cannot perform at all are skipped as "not granted".
    * Behaviour as A then as B: visible rows equal that user's own rows, none of
      the other user's rows are readable, and UPDATE/DELETE aimed at the other
      user's rows affect 0 rows.
    * A privilege error (42501) on UPDATE/DELETE/INSERT only proves the grant is
      missing. It does NOT prove RLS holds, and the behavioural probe is then
      vacuous for that command. By default the UPDATE probe sets ``user_id`` and
      so hits the privilege check on column-grant-only tables; pass
      ``mutable_column`` (a column the app role may UPDATE, e.g. ``redeemed_at``
      on invites) so the probe reaches the RLS row filter. A column without
      UPDATE privilege raises ``ValueError``.
    * ``insert_as_other(victim_id)`` must be supplied for every table on which
      the app role has INSERT: if INSERT is granted and it is None the helper
      raises ``AssertionError`` naming the table. It must itself open
      ``tenant_connection(pool, <attacker>, role="trendscend_app")`` and INSERT
      a row carrying ``victim_id`` as ``user_id``; the helper requires an RLS
      or privilege error and calls it for both victims. Which of the two it was
      is covered by the catalog check above.

    ``make_row(user_id)`` creates (as the owner role) a row for that user;
    cleaning up those rows is the caller's job. It may be a no-op when rows
    already exist (e.g. ``users``). Both users must end up with at least one row.

    Raises ``ValueError`` for an unknown table or unusable ``mutable_column``
    and ``AssertionError`` when isolation does not hold.
    """
    if table not in await _user_owned_tables(owner_pool):
        raise ValueError(f"{table!r} is not a public table with a user_id column")
    qtable = _quote_ident(table)
    await _assert_rls_enabled(owner_pool, table)
    granted = await _granted_commands(owner_pool, table)
    await _assert_policies_cover_commands(owner_pool, table, granted)
    column = await _validated_mutable_column(owner_pool, table, mutable_column)
    if granted["INSERT"] and insert_as_other is None:
        raise AssertionError(
            f"{table}: {APP_ROLE} has INSERT but insert_as_other was not supplied; "
            "cross-user INSERT would go untested"
        )
    update_sql = (
        f"UPDATE {qtable} SET {column} = {column} WHERE user_id = $1"  # noqa: S608
        if column is not None
        else f"UPDATE {qtable} SET user_id = DEFAULT WHERE user_id = $1"  # noqa: S608
    )

    await make_row(user_a)
    await make_row(user_b)
    rows_a = await _owner_count(owner_pool, qtable, user_a)
    rows_b = await _owner_count(owner_pool, qtable, user_b)
    assert rows_a > 0 and rows_b > 0, f"{table}: make_row left no rows to test with"

    await _assert_one_direction(owner_pool, qtable, table, user_a, user_b, rows_a, update_sql)
    await _assert_one_direction(owner_pool, qtable, table, user_b, user_a, rows_b, update_sql)

    if insert_as_other is not None:
        for victim in (user_b, user_a):
            try:
                await insert_as_other(victim)
            except asyncpg.InsufficientPrivilegeError:
                continue  # RLS WITH CHECK violations and missing grants both use 42501
            raise AssertionError(f"{table}: cross-user INSERT for {victim} was allowed")
