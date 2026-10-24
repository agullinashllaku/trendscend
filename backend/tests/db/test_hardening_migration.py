"""Migration 003: ambient PUBLIC privileges removed (review findings M1, M2).

Role-based checks run inside a transaction that is always rolled back, so the
temporary ``zz_h_member`` role (roles are cluster-wide) never persists.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import pytest

from trendscend.db.migrate import MIGRATIONS_DIR

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("TEST_DATABASE_URL"),
        reason="TEST_DATABASE_URL not set — skipping DB integration tests",
    ),
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

ADMIN_FUNCTIONS = ["add_job", "alter_job", "delete_job", "run_job", "get_telemetry_report"]
MIGRATION = MIGRATIONS_DIR / "003_hardening.sql"


@asynccontextmanager
async def member_tx(
    pool: asyncpg.Pool, *, switch: bool = True
) -> AsyncIterator[asyncpg.Connection]:
    """Rolled-back tx with a LOGIN-less member of trendscend_app (optionally switched to)."""
    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE zz_h_member NOLOGIN")
            await conn.execute("GRANT trendscend_app TO zz_h_member")
            if switch:
                await conn.execute("SET LOCAL ROLE zz_h_member")
            yield conn
        finally:
            await tx.rollback()


async def test_temp_table_creation_denied(owner_pool: asyncpg.Pool) -> None:
    async with member_tx(owner_pool) as conn:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await conn.execute("CREATE TEMP TABLE audit_log(x int)")


@pytest.mark.parametrize("name", ADMIN_FUNCTIONS)
async def test_timescale_admin_functions_not_executable(
    owner_pool: asyncpg.Pool, name: str
) -> None:
    async with member_tx(owner_pool, switch=False) as conn:
        rows = await conn.fetch(
            """
            SELECT p.oid::regprocedure::text AS sig,
                   has_function_privilege('zz_h_member', p.oid, 'EXECUTE') AS can_exec
            FROM pg_proc p
            JOIN pg_depend d ON d.classid = 'pg_proc'::regclass AND d.objid = p.oid
                            AND d.deptype = 'e'
            JOIN pg_extension e ON e.oid = d.refobjid AND e.extname = 'timescaledb'
            WHERE p.proname = $1
            """,
            name,
        )
        assert rows, f"no timescaledb overload of {name} found (vacuous test)"
        executable = [r["sig"] for r in rows if r["can_exec"]]
        assert executable == []


async def test_telemetry_off() -> None:
    # ALTER DATABASE ... SET only affects new sessions, so open a fresh connection.
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    try:
        assert await conn.fetchval("SELECT current_setting('timescaledb.telemetry_level')") == "off"
    finally:
        await conn.close()


async def test_migration_is_idempotent(owner_pool: asyncpg.Pool) -> None:
    sql = MIGRATION.read_text()
    async with owner_pool.acquire() as conn:
        await conn.execute(sql)
        await conn.execute(sql)


async def test_owner_still_works(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("CREATE TEMP TABLE zz_owner_tmp(x int) ON COMMIT DROP")
