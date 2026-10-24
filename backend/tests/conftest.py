"""Shared pytest fixtures for DB-backed (integration) tests.

All fixtures here skip when ``TEST_DATABASE_URL`` is unset. The pool connects
as the *owner* (a privileged role): tests that exercise row-level security must
drop to ``trendscend_app`` with ``SET LOCAL ROLE`` inside a transaction,
otherwise the owner/superuser bypasses RLS and the test proves nothing.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from trendscend.db.migrate import apply_migrations


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def owner_pool() -> AsyncIterator[asyncpg.Pool]:
    """Session-scoped owner pool with all migrations applied once."""
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set — skipping DB integration tests")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            # 001_candles needs TimescaleDB; extensions are created via the owner.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            await apply_migrations(conn)
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def two_users(owner_pool: asyncpg.Pool) -> AsyncIterator[tuple[uuid.UUID, uuid.UUID]]:
    """Insert users A and B as the owner connection; remove them afterwards."""
    a, b = uuid.uuid4(), uuid.uuid4()
    async with owner_pool.acquire() as conn:
        # FORCE RLS applies to the owner too, so scope each insert to its row.
        for uid, email in ((a, f"a-{a}@test.invalid"), (b, f"b-{b}@test.invalid")):
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.current_user', $1, true)", str(uid))
                await conn.execute(
                    "INSERT INTO users (id, email, role, status) VALUES ($1, $2, 'user', 'active')",
                    uid,
                    email,
                )
        try:
            yield a, b
        finally:
            for uid in (a, b):
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.current_user', $1, true)", str(uid))
                    await conn.execute("DELETE FROM invites WHERE user_id = $1", uid)
                    await conn.execute("DELETE FROM users WHERE id = $1", uid)
