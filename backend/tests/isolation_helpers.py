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
            ("UPDATE", f"UPDATE {qtable} SET user_id = DEFAULT WHERE user_id = $1"),  # noqa: S608
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
) -> None:
    """Prove users A and B cannot see or modify each other's rows in ``table``.

    ``make_row(user_id)`` creates (as the owner role) a row for that user ; cleaning
    up those rows is the caller's job. It may be a no-op when rows already exist
    (e.g. ``users``). Both users must end up with at least one row.

    ``insert_as_other(victim_id)``, if given, must itself open
    ``tenant_connection(pool, <attacker>, role="trendscend_app")`` and try to
    INSERT a row carrying ``victim_id`` as ``user_id``; the helper requires that
    to raise an RLS or privilege error (and, as the attacker, calls it for both
    directions by passing each victim id).

    Raises ``ValueError`` for an unknown table and ``AssertionError`` when
    isolation does not hold (including RLS not enabled and forced).
    """
    if table not in await _user_owned_tables(owner_pool):
        raise ValueError(f"{table!r} is not a public table with a user_id column")
    qtable = _quote_ident(table)
    await _assert_rls_enabled(owner_pool, table)

    await make_row(user_a)
    await make_row(user_b)
    rows_a = await _owner_count(owner_pool, qtable, user_a)
    rows_b = await _owner_count(owner_pool, qtable, user_b)
    assert rows_a > 0 and rows_b > 0, f"{table}: make_row left no rows to test with"

    await _assert_one_direction(owner_pool, qtable, table, user_a, user_b, rows_a)
    await _assert_one_direction(owner_pool, qtable, table, user_b, user_a, rows_b)

    if insert_as_other is not None:
        for victim in (user_b, user_a):
            try:
                await insert_as_other(victim)
            except asyncpg.InsufficientPrivilegeError:
                continue  # RLS WITH CHECK violations and missing grants both use 42501
            raise AssertionError(f"{table}: cross-user INSERT for {victim} was allowed")
