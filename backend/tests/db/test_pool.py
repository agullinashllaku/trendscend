"""Pool reset hook: DISCARD ALL on release (review finding M1).

asyncpg's default reset does not drop temp objects and leaves session-level
SET ROLE / SET SESSION AUTHORIZATION in place, so state planted by one request
could leak to the next one on the same pooled connection. All tests use
``max_size=1`` against the (super)user DSN so the same backend is re-acquired.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

from trendscend.db import pool as pool_mod
from trendscend.db.pool import create_pool, reset_connection

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(loop_scope="session")
async def pool1(owner_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    # owner_pool guarantees migrations (trendscend_app role) exist.
    url = os.environ["TEST_DATABASE_URL"]
    pool = await create_pool(url, min_size=1, max_size=1)
    try:
        yield pool
    finally:
        await pool.close()


async def _pid(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as c:
        return int(await c.fetchval("SELECT pg_backend_pid()"))


@pytest.mark.asyncio(loop_scope="session")
async def test_temp_table_does_not_survive_release(pool1: asyncpg.Pool) -> None:
    async with pool1.acquire() as c:
        pid = await c.fetchval("SELECT pg_backend_pid()")
        await c.execute("CREATE TEMP TABLE t (x int)")
        assert await c.fetchval("SELECT to_regclass('pg_temp.t')") is not None
    async with pool1.acquire() as c:
        assert await c.fetchval("SELECT pg_backend_pid()") == pid
        assert await c.fetchval("SELECT to_regclass('pg_temp.t')") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_shadow_audit_log_does_not_survive(pool1: asyncpg.Pool) -> None:
    async with pool1.acquire() as c:
        await c.execute("CREATE TEMP TABLE audit_log (x int)")
    async with pool1.acquire() as c:
        assert await c.fetchval("SELECT to_regclass('pg_temp.audit_log')") is None


@pytest.mark.asyncio(loop_scope="session")
async def test_session_role_does_not_survive_release(pool1: asyncpg.Pool) -> None:
    async with pool1.acquire() as c:
        login = await c.fetchval("SELECT session_user")
        await c.execute("SET ROLE trendscend_app")
        assert await c.fetchval("SELECT current_user") == "trendscend_app"
    async with pool1.acquire() as c:
        assert await c.fetchval("SELECT current_user") == await c.fetchval("SELECT session_user")
        assert await c.fetchval("SELECT current_user") == login


@pytest.mark.asyncio(loop_scope="session")
async def test_session_authorization_and_guc_reset(pool1: asyncpg.Pool) -> None:
    async with pool1.acquire() as c:
        login = await c.fetchval("SELECT session_user")
        await c.execute("SET SESSION AUTHORIZATION trendscend_app")
        await c.execute("SELECT set_config('app.current_user', 'victim', false)")
        assert await c.fetchval("SELECT session_user") == "trendscend_app"
    async with pool1.acquire() as c:
        assert await c.fetchval("SELECT session_user") == login
        assert await c.fetchval("SELECT current_user") == login
        assert not await c.fetchval("SELECT current_setting('app.current_user', true)")


@pytest.mark.asyncio(loop_scope="session")
async def test_advisory_locks_released(pool1: asyncpg.Pool, owner_pool: asyncpg.Pool) -> None:
    async with pool1.acquire() as c:
        await c.execute("SELECT pg_advisory_lock(424242)")
    async with owner_pool.acquire() as other:
        assert await other.fetchval("SELECT pg_try_advisory_lock(424242)") is True
        await other.execute("SELECT pg_advisory_unlock(424242)")


@pytest.mark.asyncio(loop_scope="session")
async def test_cached_prepared_statement_works_after_release(pool1: asyncpg.Pool) -> None:
    for _ in range(3):
        async with pool1.acquire() as c:
            # Repeated identical query populates asyncpg's client statement cache;
            # DISCARD ALL deallocates it server-side.
            assert await c.fetchval("SELECT $1::int + 1", 1) == 2
            stmt = await c.prepare("SELECT $1::int * 2")
            assert await stmt.fetchval(4) == 8


@pytest.mark.asyncio(loop_scope="session")
async def test_release_mid_transaction_is_clean(pool1: asyncpg.Pool) -> None:
    c = await pool1.acquire()
    pid = await c.fetchval("SELECT pg_backend_pid()")
    tx = c.transaction()
    await tx.start()
    await c.execute("CREATE TEMP TABLE midtx (x int)")
    await c.execute("SET LOCAL ROLE trendscend_app")
    await pool1.release(c)  # asyncpg rolls back, then our reset runs
    async with pool1.acquire() as c2:
        assert await c2.fetchval("SELECT pg_backend_pid()") == pid
        assert await c2.fetchval("SELECT to_regclass('pg_temp.midtx')") is None
        assert not c2.is_in_transaction()
        assert await c2.fetchval("SELECT current_user") == await c2.fetchval("SELECT session_user")


@pytest.mark.asyncio(loop_scope="session")
async def test_release_in_failed_transaction_is_clean(pool1: asyncpg.Pool) -> None:
    c = await pool1.acquire()
    pid = await c.fetchval("SELECT pg_backend_pid()")
    await c.execute("BEGIN")
    with pytest.raises(asyncpg.PostgresError):
        await c.execute("SELECT 1/0")  # transaction now aborted
    await pool1.release(c)
    async with pool1.acquire() as c2:
        assert await c2.fetchval("SELECT pg_backend_pid()") == pid
        assert await c2.fetchval("SELECT 1") == 1


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_failure_discards_connection(
    owner_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = os.environ["TEST_DATABASE_URL"]
    calls = {"n": 0}

    async def bad_reset(conn: asyncpg.Connection) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("reset boom")
        await reset_connection(conn)

    pool = await asyncpg.create_pool(url, min_size=1, max_size=1, reset=bad_reset)
    try:
        c = await pool.acquire()
        pid = await c.fetchval("SELECT pg_backend_pid()")
        raw = c._con  # underlying connection; the proxy is unusable once released
        with pytest.raises(RuntimeError):
            await pool.release(c)
        assert raw is not None and raw.is_closed()
        assert calls["n"] == 1
        async with pool.acquire() as c2:
            assert await c2.fetchval("SELECT pg_backend_pid()") != pid
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_create_pool_passes_reset_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_create_pool(dsn: str, **kw: Any) -> str:
        captured.update(dsn=dsn, **kw)
        return "pool"

    monkeypatch.setattr("trendscend.db.pool.asyncpg.create_pool", fake_create_pool)
    assert await create_pool("dsn://x", min_size=2, max_size=3) == "pool"  # type: ignore[comparison-overlap]
    assert captured == {
        "dsn": "dsn://x",
        "min_size": 2,
        "max_size": 3,
        "reset": reset_connection,
        "statement_cache_size": 0,
    }


@pytest.mark.asyncio(loop_scope="session")
async def test_create_pool_disables_statement_cache(owner_pool: asyncpg.Pool) -> None:
    pool = await create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1)
    try:
        async with pool.acquire() as c:
            assert c._con is not None and c._con._stmt_cache.get_max_size() == 0
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_concurrent_use_two_connections_no_stale_statements(owner_pool: asyncpg.Pool) -> None:
    pool = await create_pool(os.environ["TEST_DATABASE_URL"], min_size=2, max_size=2)
    start = time.monotonic()

    async def worker(w: int) -> None:
        for i in range(50):
            async with pool.acquire() as c:
                assert not await c.fetchval("SELECT to_regclass('pg_temp.leak')")
                assert await c.fetchval("SELECT $1::int + $2::int", w, i) == w + i
                await c.execute("CREATE TEMP TABLE leak (x int)")
                await c.execute("SET ROLE trendscend_app")
                await asyncio.sleep(0)

    try:
        await asyncio.gather(*(worker(w) for w in range(4)))
        async with pool.acquire() as c:
            assert await c.fetchval("SELECT current_user") == await c.fetchval(
                "SELECT session_user"
            )
    finally:
        await pool.close()
    assert time.monotonic() - start < 3


@pytest.mark.asyncio(loop_scope="session")
async def test_hung_discard_all_is_bounded(
    owner_pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pool_mod, "RESET_TIMEOUT_S", 0.2)
    real_execute = asyncpg.Connection.execute

    async def slow_execute(self: asyncpg.Connection, query: str, *a: Any, **kw: Any) -> str:
        if query == "DISCARD ALL":
            query = "SELECT pg_sleep(30)"
        return await real_execute(self, query, *a, **kw)

    pool = await create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1)
    try:
        c = await pool.acquire()
        pid = await c.fetchval("SELECT pg_backend_pid()")
        raw = c._con
        monkeypatch.setattr(asyncpg.Connection, "execute", slow_execute)
        t0 = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            await pool.release(c)
        assert time.monotonic() - t0 < 3
        monkeypatch.setattr(asyncpg.Connection, "execute", real_execute)
        assert raw is not None and raw.is_closed()
        async with pool.acquire() as c2:
            assert await c2.fetchval("SELECT pg_backend_pid()") != pid
    finally:
        await pool.close()


@pytest.mark.asyncio(loop_scope="session")
async def test_reset_connection_executes_discard_all_with_timeout() -> None:
    seen: list[tuple[str, float | None]] = []

    class C:
        async def execute(self, q: str, timeout: float | None = None) -> None:
            seen.append((q, timeout))

    await reset_connection(C())  # type: ignore[arg-type]
    assert seen == [("DISCARD ALL", 5.0)]
