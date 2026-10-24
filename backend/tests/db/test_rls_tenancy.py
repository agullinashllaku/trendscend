"""Cross-user isolation tests for users / invites / audit_log (TDD).

Every query runs inside a transaction under ``SET LOCAL ROLE trendscend_app``.
The test connection is a superuser/owner, which bypasses RLS; without the role
switch these tests would be a false green. Each test rolls back, so seed rows
(invites, audit entries) never persist.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import pytest

SKIP_IF_NO_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — skipping DB integration tests",
)

pytestmark = [
    SKIP_IF_NO_DB,
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

Users = tuple[uuid.UUID, uuid.UUID]


async def _set_user(conn: asyncpg.Connection, user: uuid.UUID | str | None) -> None:
    await conn.execute("SELECT set_config('app.current_user', $1, true)", str(user or ""))


async def _seed(conn: asyncpg.Connection, a: uuid.UUID, b: uuid.UUID) -> None:
    """As the owner role, give each user one invite and one audit row."""
    for uid in (a, b):
        await _set_user(conn, uid)
        await conn.execute(
            "INSERT INTO invites (id, user_id, token_hash, expires_at) "
            "VALUES (gen_random_uuid(), $1, $2, now() + interval '1 day')",
            uid,
            uuid.uuid4().bytes,
        )
        await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'seed')", uid)


@asynccontextmanager
async def app_tx(
    pool: asyncpg.Pool, a: uuid.UUID, b: uuid.UUID, as_user: uuid.UUID | str | None
) -> AsyncIterator[asyncpg.Connection]:
    """Seed, drop to trendscend_app, scope to ``as_user`` and always roll back."""
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _seed(conn, a, b)
            await conn.execute("SET LOCAL ROLE trendscend_app")
            await _set_user(conn, as_user)
            yield conn
        finally:
            await tx.rollback()


async def test_user_sees_only_own_row(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    async with app_tx(owner_pool, a, b, a) as conn:
        rows = await conn.fetch("SELECT id FROM users WHERE id = ANY($1)", [a, b])
        assert [r["id"] for r in rows] == [a]
        assert await conn.fetchval("SELECT current_user") == "trendscend_app"


async def test_user_cannot_update_other_user(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    async with app_tx(owner_pool, a, b, a) as conn:
        result = await conn.execute("UPDATE users SET email = 'x@test.invalid' WHERE id = $1", b)
        assert result == "UPDATE 0"
        own = await conn.execute("UPDATE users SET email = 'me@test.invalid' WHERE id = $1", a)
        assert own == "UPDATE 1"


async def test_app_role_cannot_escalate_role_or_status(
    owner_pool: asyncpg.Pool, two_users: Users
) -> None:
    a, b = two_users
    async with app_tx(owner_pool, a, b, a) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE users SET role = 'owner' WHERE id = $1", a)
    async with app_tx(owner_pool, a, b, a) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("UPDATE users SET status = 'active' WHERE id = $1", a)


async def test_app_role_cannot_insert_users(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    new = uuid.uuid4()
    async with app_tx(owner_pool, a, b, new) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO users (id, email, role, status) "
                "VALUES ($1, 'n@test.invalid', 'user', 'active')",
                new,
            )


async def test_invites_isolated_between_users(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    async with app_tx(owner_pool, a, b, a) as conn:
        rows = await conn.fetch("SELECT user_id FROM invites")
        assert {r["user_id"] for r in rows} == {a}
        assert len(rows) == 1
        # Cannot read, update, or insert on behalf of B.
        assert (
            await conn.execute("UPDATE invites SET redeemed_at = now() WHERE user_id = $1", b)
            == "UPDATE 0"
        )
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute(
                "INSERT INTO invites (id, user_id, token_hash, expires_at) "
                "VALUES (gen_random_uuid(), $1, $2, now())",
                b,
                uuid.uuid4().bytes,
            )


async def test_audit_log_isolated_between_users(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    async with app_tx(owner_pool, a, b, a) as conn:
        rows = await conn.fetch("SELECT user_id FROM audit_log")
        assert {r["user_id"] for r in rows} == {a}
        await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'mine')", a)
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'forged')", b)


@pytest.mark.parametrize("who", ["owner", "app"])
async def test_audit_log_rejects_update_and_delete_even_for_owner_role(
    owner_pool: asyncpg.Pool, two_users: Users, who: str
) -> None:
    a, b = two_users
    for stmt in (
        "UPDATE audit_log SET action = 'tampered'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        async with owner_pool.acquire() as conn:
            tx = conn.transaction()
            await tx.start()
            try:
                await _seed(conn, a, b)
                await _set_user(conn, a)  # row must be visible so row triggers fire
                if who == "app":
                    await conn.execute("SET LOCAL ROLE trendscend_app")
                # App role has no UPDATE/DELETE/TRUNCATE grants; owner path proves the trigger.
                expected = (
                    (asyncpg.InsufficientPrivilegeError, asyncpg.RestrictViolationError)
                    if who == "app"
                    else asyncpg.RestrictViolationError
                )
                with pytest.raises(expected):
                    await conn.execute(stmt)
            finally:
                await tx.rollback()


@pytest.mark.parametrize("table", ["users", "invites", "audit_log"])
@pytest.mark.parametrize("setting", ["unset", "empty"])
async def test_unset_or_empty_setting_returns_zero_rows(
    owner_pool: asyncpg.Pool, two_users: Users, table: str, setting: str
) -> None:
    a, b = two_users
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _seed(conn, a, b)
            # Seeded rows exist, so zero is not vacuous.
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") >= 1  # noqa: S608
            await conn.execute("SET LOCAL ROLE trendscend_app")
            if setting == "empty":
                await _set_user(conn, "")
            else:
                await conn.execute('RESET "app.current_user"')
            assert await conn.fetchval(f"SELECT count(*) FROM {table}") == 0  # noqa: S608
        finally:
            await tx.rollback()


async def test_all_user_owned_tables_have_forced_rls(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tables = await conn.fetch(
            """
            SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN information_schema.columns col
              ON col.table_schema = n.nspname AND col.table_name = c.relname
            WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p')
              AND col.column_name = 'user_id'
            """
        )
        names = {t["relname"] for t in tables}
        assert {"users", "invites", "audit_log"} <= names
        for t in tables:
            assert t["relrowsecurity"], f"{t['relname']}: RLS not enabled"
            assert t["relforcerowsecurity"], f"{t['relname']}: RLS not FORCED"
            n = await conn.fetchval(
                "SELECT count(*) FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = $1 AND qual LIKE '%app.current_user%'",
                t["relname"],
            )
            assert n >= 1, f"{t['relname']}: no policy referencing app.current_user"


async def test_app_role_is_not_superuser_or_bypassrls(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        role = await conn.fetchrow(
            "SELECT rolsuper, rolbypassrls, rolcanlogin FROM pg_roles "
            "WHERE rolname = 'trendscend_app'"
        )
        assert role is not None
        assert not role["rolsuper"]
        assert not role["rolbypassrls"]
        assert not role["rolcanlogin"]
        owners = await conn.fetch(
            "SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename IN ('users', 'invites', 'audit_log')"
        )
        assert len(owners) == 3
        assert all(o["tableowner"] != "trendscend_app" for o in owners)


async def test_migration_002_is_rerunnable(owner_pool: asyncpg.Pool) -> None:
    sql = (
        Path(__file__).parent.parent.parent / "src/trendscend/db/migrations/002_tenancy.sql"
    ).read_text()
    async with owner_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(sql)
            await conn.execute(sql)
