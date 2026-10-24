"""Tests for the reusable isolation helper (tests/isolation_helpers.py)."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
import pytest_asyncio

from tests.isolation_helpers import assert_table_isolated
from trendscend.db.tenant import tenant_connection

pytestmark = [
    pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL not set"),
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
]

Users = tuple[uuid.UUID, uuid.UUID]


async def _noop(_: uuid.UUID) -> None:
    return None


async def test_helper_passes_for_users_table(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    await assert_table_isolated(owner_pool, "users", _noop, a, b)


async def test_helper_rejects_unknown_table_name(
    owner_pool: asyncpg.Pool, two_users: Users
) -> None:
    a, b = two_users
    for bad in ("nope", 'users"; DROP TABLE users; --', "candles", "pg_class"):
        with pytest.raises(ValueError):
            await assert_table_isolated(owner_pool, bad, _noop, a, b)


@pytest_asyncio.fixture(loop_scope="session")
async def no_rls_table(owner_pool: asyncpg.Pool) -> AsyncIterator[str]:
    name = f"tmp_norls_{uuid.uuid4().hex[:12]}"
    async with owner_pool.acquire() as conn:
        await conn.execute(f'CREATE TABLE "{name}" (id serial PRIMARY KEY, user_id uuid NOT NULL)')
        await conn.execute(f'GRANT SELECT ON "{name}" TO trendscend_app')
        try:
            yield name
        finally:
            await conn.execute(f'DROP TABLE IF EXISTS "{name}"')


async def test_helper_fails_for_table_without_rls(
    owner_pool: asyncpg.Pool, two_users: Users, no_rls_table: str
) -> None:
    a, b = two_users

    async def make_row(uid: uuid.UUID) -> None:
        async with owner_pool.acquire() as conn:
            await conn.execute(f'INSERT INTO "{no_rls_table}" (user_id) VALUES ($1)', uid)  # noqa: S608

    with pytest.raises(AssertionError, match="row-level security"):
        await assert_table_isolated(owner_pool, no_rls_table, make_row, a, b)


async def _set_owner_role(pool: asyncpg.Pool, uid: uuid.UUID) -> None:
    async with tenant_connection(pool, uid) as conn:
        await conn.execute("UPDATE users SET role = 'owner' WHERE id = $1", uid)


async def test_helper_reusable_for_invites(owner_pool: asyncpg.Pool, two_users: Users) -> None:
    a, b = two_users
    for uid in (a, b):
        await _set_owner_role(owner_pool, uid)

    async def make_row(uid: uuid.UUID) -> None:
        async with tenant_connection(owner_pool, uid) as conn:
            await conn.execute(
                "INSERT INTO invites (id, user_id, token_hash, expires_at) "
                "VALUES (gen_random_uuid(), $1, $2, now() + interval '1 day')",
                uid,
                uuid.uuid4().bytes,
            )

    # Attacker is the *other* user of the pair: victim b -> attacker a, and back.
    async def insert_as_other(victim: uuid.UUID) -> None:
        attacker = a if victim == b else b
        async with tenant_connection(owner_pool, attacker, role="trendscend_app") as conn:
            await conn.execute(
                "INSERT INTO invites (id, user_id, token_hash, expires_at) "
                "VALUES (gen_random_uuid(), $1, $2, now() + interval '1 day')",
                victim,
                uuid.uuid4().bytes,
            )

    await assert_table_isolated(
        owner_pool, "invites", make_row, a, b, insert_as_other=insert_as_other
    )


@pytest_asyncio.fixture(loop_scope="session")
async def audit_cleanup(owner_pool: asyncpg.Pool, two_users: Users) -> AsyncIterator[None]:
    yield
    # audit_log is append-only by trigger; lift it only inside one transaction.
    async with owner_pool.acquire() as conn, conn.transaction():
        await conn.execute("ALTER TABLE audit_log DISABLE TRIGGER audit_log_immutable")
        for uid in two_users:
            await conn.execute("SELECT set_config('app.current_user', $1, true)", str(uid))
            await conn.execute("DELETE FROM audit_log WHERE user_id = $1", uid)
        await conn.execute("ALTER TABLE audit_log ENABLE TRIGGER audit_log_immutable")


async def test_helper_reusable_for_audit_log(
    owner_pool: asyncpg.Pool, two_users: Users, audit_cleanup: None
) -> None:
    a, b = two_users

    async def make_row(uid: uuid.UUID) -> None:
        async with tenant_connection(owner_pool, uid) as conn:
            await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'iso')", uid)

    async def insert_as_other(victim: uuid.UUID) -> None:
        attacker = a if victim == b else b
        async with tenant_connection(owner_pool, attacker, role="trendscend_app") as conn:
            await conn.execute("INSERT INTO audit_log (user_id, action) VALUES ($1, 'x')", victim)

    await assert_table_isolated(
        owner_pool, "audit_log", make_row, a, b, insert_as_other=insert_as_other
    )
