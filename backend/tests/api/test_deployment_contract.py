"""End-to-end deployment contract: bootstrapped role + real lifespan + startup assertion."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import asyncpg
import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport
from pydantic import SecretStr

from tests.db.test_bootstrap import dsn_for
from trendscend.api.app import create_app
from trendscend.api.settings import Settings
from trendscend.db.bootstrap import bootstrap_api_role

pytestmark = pytest.mark.integration
LOOP = pytest.mark.asyncio(loop_scope="session")
ROLE, PW = "zz_b_deploy", "dep'loy\"pw;1"


def settings_for(dsn: str) -> Settings:
    return Settings(
        database_url=SecretStr(dsn),
        redis_url=SecretStr(os.environ.get("TEST_REDIS_URL", "redis://localhost:6379/1")),
        environment="staging",
    )


@asynccontextmanager
async def running(dsn: str) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings_for(dsn))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://t"
        ) as client:
            yield client


@pytest_asyncio.fixture(loop_scope="session")
async def api_role(owner_pool: asyncpg.Pool) -> AsyncIterator[str]:
    async with owner_pool.acquire() as c:
        await bootstrap_api_role(c, ROLE, SecretStr(PW))
    try:
        yield dsn_for(ROLE, PW)
    finally:
        async with owner_pool.acquire() as c:
            await c.execute(f'DROP ROLE IF EXISTS "{ROLE}"')


@LOOP
async def test_app_boots_with_bootstrapped_login_role(api_role: str) -> None:
    async with running(api_role) as client:
        assert (await client.get("/readyz")).status_code == 200
    c = await asyncpg.connect(api_role)
    try:
        assert await c.fetchval("SELECT count(*) FROM users") == 0
    finally:
        await c.close()


@LOOP
async def test_app_refuses_owner_dsn(owner_pool: asyncpg.Pool) -> None:
    with pytest.raises(RuntimeError):
        async with running(os.environ["TEST_DATABASE_URL"]):
            pytest.fail("startup must refuse the superuser DSN")


@LOOP
async def test_bootstrapped_role_cannot_create_or_schedule(api_role: str) -> None:
    c = await asyncpg.connect(api_role)
    try:
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("CREATE TEMP TABLE t (x int)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.execute("CREATE TABLE public.zz_x (x int)")
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            await c.fetch("SELECT add_job('pg_catalog.now'::regproc, '1 hour')")
    finally:
        await c.close()


@LOOP
async def test_drift_is_caught_at_startup_and_repaired(
    api_role: str, owner_pool: asyncpg.Pool
) -> None:
    async with owner_pool.acquire() as c:
        await c.execute(f'ALTER ROLE "{ROLE}" CREATEROLE')
    with pytest.raises(RuntimeError):
        async with running(api_role):
            pytest.fail("startup must refuse a drifted role")
    async with owner_pool.acquire() as c:
        await bootstrap_api_role(c, ROLE, SecretStr(PW))
    async with running(api_role) as client:
        assert (await client.get("/readyz")).status_code == 200
