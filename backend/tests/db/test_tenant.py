"""Tests for the fail-closed ``tenant_connection`` helper.

Policy chosen for user ids: parse with ``uuid.UUID`` and require that strings
are *already* the canonical lowercase hyphenated 36-char form (reject, never
repair). Whitespace, braces, URN prefix, uppercase and un-hyphenated hex are all
rejected so exactly one spelling of a tenant id can ever reach Postgres.

RLS assertions run under ``SET LOCAL ROLE trendscend_app`` because the test
role is a superuser with BYPASSRLS.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from trendscend.db.tenant import InvalidTenantError, normalise_user_id, tenant_connection

UID = uuid.UUID("550e8400-e29b-41d4-a716-446655440000")
CANON = "550e8400-e29b-41d4-a716-446655440000"

# ---------------------------------------------------------------- unit tests


def test_normalise_accepts_uuid_and_string() -> None:
    assert normalise_user_id(UID) == CANON
    assert normalise_user_id(CANON) == CANON


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "abc",
        "1; DROP TABLE users",
        "' OR '1'='1",
        None,
        CANON + "\n",
        " " + CANON,
        CANON + " ",
        "{" + CANON + "}",
        "urn:uuid:" + CANON,
        CANON.upper(),
        CANON.replace("-", ""),
        "550e8400-e29b-41d4-a716-44665544000g",
        12345,
    ],
)
def test_normalise_rejects_non_uuid(bad: object) -> None:
    with pytest.raises(InvalidTenantError):
        normalise_user_id(bad)  # type: ignore[arg-type]


def test_invalid_tenant_error_is_value_error() -> None:
    assert issubclass(InvalidTenantError, ValueError)


@pytest.mark.parametrize(
    "role", ["x; DROP ROLE y", "Trendscend_App", "1abc", "", 'a"b', "a b", "a\n", "trendscend_app;"]
)
async def test_role_param_rejects_injection_unit(role: str) -> None:
    # Validation happens before any connection is acquired, so pool=None is safe.
    with pytest.raises(InvalidTenantError):
        async with tenant_connection(None, UID, role=role):  # type: ignore[arg-type]
            pytest.fail("body must not run")


async def test_invalid_user_never_touches_pool() -> None:
    with pytest.raises(InvalidTenantError):
        async with tenant_connection(None, CANON + "\n"):  # type: ignore[arg-type]
            pytest.fail("body must not run")


# --------------------------------------------------------- integration tests


@pytest_asyncio.fixture(loop_scope="session")
async def small_pool(owner_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    """Pool of size 1 so 'after exit' checks reuse the very same connection."""
    pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1)
    try:
        yield pool
    finally:
        await pool.close()


@pytest_asyncio.fixture(loop_scope="session")
async def pool2(owner_pool: asyncpg.Pool) -> AsyncIterator[asyncpg.Pool]:
    pool = await asyncpg.create_pool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2)
    try:
        yield pool
    finally:
        await pool.close()


async def _setting(conn: asyncpg.Connection) -> str | None:
    value = await conn.fetchval("SELECT current_setting('app.current_user', true)")
    return value  # type: ignore[no-any-return]


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_context_applies_inside_transaction(small_pool: asyncpg.Pool) -> None:
    async with tenant_connection(small_pool, UID) as conn:
        assert conn.is_in_transaction()
        assert await _setting(conn) == CANON


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_string_id_applies(small_pool: asyncpg.Pool) -> None:
    async with tenant_connection(small_pool, CANON) as conn:
        assert await _setting(conn) == CANON


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_setting_not_visible_after_exit(small_pool: asyncpg.Pool) -> None:
    async with tenant_connection(small_pool, UID):
        pass
    async with small_pool.acquire() as conn:
        assert not await _setting(conn)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_setting_cleared_and_rolled_back_after_exception(
    small_pool: asyncpg.Pool, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, _b = two_users
    with pytest.raises(RuntimeError, match="boom"):
        async with tenant_connection(small_pool, a, role="trendscend_app") as conn:
            await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'x')", a)
            raise RuntimeError("boom")
    async with small_pool.acquire() as conn:
        assert not await _setting(conn)
    async with tenant_connection(small_pool, a) as conn:
        n = await conn.fetchval("SELECT count(*) FROM audit_log WHERE user_id = $1", a)
        assert n == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_commit_on_success(small_pool: asyncpg.Pool, owner_pool: asyncpg.Pool) -> None:
    uid = uuid.uuid4()
    async with tenant_connection(small_pool, uid) as conn:
        await conn.execute("SELECT 1")
        assert conn.is_in_transaction()
    async with small_pool.acquire() as conn:
        assert not conn.is_in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_forgotten_context_returns_no_rows(
    owner_pool: asyncpg.Pool, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    async with owner_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE trendscend_app")
        assert await conn.fetchval("SELECT count(*) FROM users") == 0


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_role_applies_rls_per_user(
    owner_pool: asyncpg.Pool, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    a, b = two_users
    async with tenant_connection(owner_pool, a, role="trendscend_app") as conn:
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM users")}
        assert ids == {a}
    async with tenant_connection(owner_pool, b, role="trendscend_app") as conn:
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM users")}
        assert ids == {b}


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_role_param_rejects_injection(owner_pool: asyncpg.Pool) -> None:
    with pytest.raises(InvalidTenantError):
        async with tenant_connection(owner_pool, UID, role="x; DROP ROLE y"):
            pytest.fail("body must not run")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_unknown_valid_role_fails_and_cleans_up(small_pool: asyncpg.Pool) -> None:
    with pytest.raises(asyncpg.PostgresError):
        async with tenant_connection(small_pool, UID, role="no_such_role_xyz"):
            pytest.fail("body must not run")
    async with small_pool.acquire() as conn:
        assert not await _setting(conn)
        assert not conn.is_in_transaction()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_nested_invalid_inner_does_not_leak_outer_user(pool2: asyncpg.Pool) -> None:
    other = uuid.uuid4()
    async with tenant_connection(pool2, UID) as outer:
        with pytest.raises(InvalidTenantError):
            async with tenant_connection(pool2, "not-a-uuid") as inner:
                pytest.fail(f"inner body must not run {inner}")
        # a valid inner call gets its own connection and its own user
        async with tenant_connection(pool2, other) as inner2:
            assert inner2 is not outer
            assert await _setting(inner2) == str(other)
        assert await _setting(outer) == CANON
    async with pool2.acquire() as c1, pool2.acquire() as c2:
        assert not await _setting(c1)
        assert not await _setting(c2)
