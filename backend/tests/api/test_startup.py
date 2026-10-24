from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from tests.api.test_app import make_settings
from trendscend.api import app as app_module
from trendscend.api.app import create_app
from trendscend.api.startup import assert_safe_db_role


class FakeConn:
    def __init__(self, row: dict[str, bool] | None) -> None:
        self.row = row

    async def fetchrow(self, *_a: Any) -> dict[str, bool] | None:
        return self.row


def row(s: bool = False, b: bool = False, o: bool = False, m: bool = False) -> dict[str, bool]:
    return {
        "rolsuper": s,
        "rolbypassrls": b,
        "controls_rls_tables": o,
        "can_assume_privileged": m,
    }


async def test_safe_role_passes() -> None:
    await assert_safe_db_role(FakeConn(row()))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "r,word",
    [
        (row(s=True), "superuser"),
        (row(b=True), "BYPASSRLS"),
        (row(o=True), "owns or inherits ownership of RLS tables"),
        (row(m=True), "member of a superuser/BYPASSRLS role"),
        (None, "not found"),
    ],
)
async def test_unsafe_role_raises(r: dict[str, bool] | None, word: str) -> None:
    with pytest.raises(RuntimeError, match=word):
        await assert_safe_db_role(FakeConn(r))  # type: ignore[arg-type]


class FakePool:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.closed = False

    def acquire(self) -> Any:
        conn = self.conn

        class Ctx:
            async def __aenter__(self) -> FakeConn:
                return conn

            async def __aexit__(self, *_a: Any) -> None:
                return None

        return Ctx()

    async def close(self) -> None:
        self.closed = True


def patch_pool(monkeypatch: pytest.MonkeyPatch, pool: FakePool) -> None:
    async def fake_create_pool(dsn: str, **_k: Any) -> FakePool:
        return pool

    monkeypatch.setattr(app_module.asyncpg, "create_pool", fake_create_pool)


async def test_lifespan_fails_startup_on_unsafe_role_and_closes_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool(FakeConn(row(s=True)))
    patch_pool(monkeypatch, pool)
    app = create_app(make_settings("prod"))
    with pytest.raises(RuntimeError) as ei:
        async with app.router.lifespan_context(app):
            pytest.fail("startup must not succeed")
    assert "pw" not in str(ei.value).replace("superuser", "")  # no DSN password
    assert pool.closed


async def test_lifespan_ok_with_safe_role(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(FakeConn(row()))
    patch_pool(monkeypatch, pool)
    app = create_app(make_settings("prod"))
    async with app.router.lifespan_context(app):
        assert app.state.pool is pool
    assert pool.closed


async def test_flag_skips_check_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = FakePool(FakeConn(row(s=True)))
    patch_pool(monkeypatch, pool)
    app = create_app(make_settings("dev", allow_unsafe_db_role=True))
    async with app.router.lifespan_context(app):
        pass


async def test_flag_ignored_outside_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    # Settings rejects the combination; bypass validation to prove the
    # lifespan itself also ignores the flag outside dev.
    pool = FakePool(FakeConn(row(s=True)))
    patch_pool(monkeypatch, pool)
    settings = make_settings("staging").model_copy(update={"allow_unsafe_db_role": True})
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="superuser"):
        async with app.router.lifespan_context(app):
            pass


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_superuser_login_role_rejected(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        with pytest.raises(RuntimeError, match="superuser"):
            await assert_safe_db_role(conn)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_trendscend_app_passes(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE trendscend_app")
        await assert_safe_db_role(conn)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_table_owner_rejected(owner_pool: asyncpg.Pool) -> None:
    # A non-super, non-bypass role that owns a tenancy table must be rejected.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE tmp_owner_chk NOLOGIN")
            await conn.execute("GRANT tmp_owner_chk TO CURRENT_USER")
            await conn.execute("ALTER TABLE audit_log OWNER TO tmp_owner_chk")
            await conn.execute("SET LOCAL ROLE tmp_owner_chk")
            with pytest.raises(RuntimeError, match="RLS tables"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_readyz(owner_pool: asyncpg.Pool) -> None:
    import httpx

    app = create_app(make_settings("dev"), pool=owner_pool)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.get("/readyz")).status_code == 200


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_inherited_ownership_rejected(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE tmp_own NOLOGIN")
            await conn.execute("CREATE ROLE tmp_member NOLOGIN INHERIT IN ROLE tmp_own")
            await conn.execute("GRANT tmp_member TO CURRENT_USER")
            await conn.execute("CREATE TABLE tmp_rls_scratch (id int)")
            await conn.execute("ALTER TABLE tmp_rls_scratch ENABLE ROW LEVEL SECURITY")
            await conn.execute("ALTER TABLE tmp_rls_scratch OWNER TO tmp_own")
            await conn.execute("SET LOCAL ROLE tmp_member")
            with pytest.raises(RuntimeError, match="RLS tables"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_member_of_bypassrls_role_rejected(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE tmp_bypass NOLOGIN BYPASSRLS")
            await conn.execute("CREATE ROLE tmp_esc NOLOGIN IN ROLE tmp_bypass")
            await conn.execute("GRANT tmp_esc TO CURRENT_USER")
            await conn.execute("SET LOCAL ROLE tmp_esc")
            with pytest.raises(RuntimeError, match="member of a superuser/BYPASSRLS role"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()
