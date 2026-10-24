"""Role bootstrap tests (live DB). Roles are cluster-wide: always dropped in finally."""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import asyncpg
import pytest
from pydantic import SecretStr

from trendscend.db import bootstrap as bs
from trendscend.db.bootstrap import bootstrap_api_role, main

pytestmark = pytest.mark.integration
LOOP = pytest.mark.asyncio(loop_scope="session")

FLAGS = (
    "SELECT rolcanlogin, rolsuper, rolbypassrls, rolcreaterole, rolcreatedb, "
    "rolreplication, rolinherit, rolconfig FROM pg_roles WHERE rolname = $1"
)


def dsn_for(role: str, password: str) -> str:
    parts = urlsplit(os.environ["TEST_DATABASE_URL"])
    host = parts.hostname or "localhost"
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
    if parts.port:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


@asynccontextmanager
async def scratch_role(pool: asyncpg.Pool, *names: str) -> AsyncIterator[None]:
    try:
        yield
    finally:
        async with pool.acquire() as c:
            for n in names:
                await c.execute(f'DROP ROLE IF EXISTS "{n}"')  # test-only fixed names


async def memberships(conn: asyncpg.Connection, role: str) -> set[str]:
    rows = await conn.fetch(
        "SELECT r.rolname FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.roleid "
        "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = $1",
        role,
    )
    return {r["rolname"] for r in rows}


@LOOP
async def test_bootstrap_creates_safe_login_role(owner_pool: asyncpg.Pool) -> None:
    name = "zz_b_create"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await bootstrap_api_role(c, name, SecretStr("pw-one"))
        row = await c.fetchrow(FLAGS, name)
        assert row is not None
        assert tuple(row)[:7] == (True, False, False, False, False, False, True)
        assert not row["rolconfig"]
        assert await memberships(c, name) == {"trendscend_app"}
        # SCRAM-stored, not md5/plaintext.
        stored = await c.fetchval("SELECT rolpassword FROM pg_authid WHERE rolname = $1", name)
        if await c.fetchval("SHOW password_encryption") == "scram-sha-256":
            assert stored.startswith("SCRAM-SHA-256$")
        assert "pw-one" not in stored


@LOOP
async def test_bootstrap_is_idempotent_and_repairs_drift(owner_pool: asyncpg.Pool) -> None:
    name, other = "zz_b_drift", "zz_b_scratch"
    async with scratch_role(owner_pool, name, other), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{other}" NOLOGIN')
        await bootstrap_api_role(c, name, SecretStr("pw-one"))
        await bootstrap_api_role(c, name, SecretStr("pw-one"))  # idempotent
        await c.execute(f'ALTER ROLE "{name}" CREATEROLE CREATEDB REPLICATION NOINHERIT')
        await c.execute(f'ALTER ROLE "{name}" SET session_replication_role = replica')
        await c.execute(f'ALTER ROLE "{name}" SET search_path = pg_temp')
        await c.execute(f"ALTER ROLE \"{name}\" IN DATABASE trendscend_test SET work_mem = '1MB'")
        await c.execute(f'GRANT "{other}" TO "{name}"')
        await c.execute(f"ALTER ROLE \"{name}\" PASSWORD 'wrong'")
        await bootstrap_api_role(c, name, SecretStr("pw-two"))
        row = await c.fetchrow(FLAGS, name)
        assert row is not None
        assert tuple(row)[:7] == (True, False, False, False, False, False, True)
        assert not row["rolconfig"]
        assert await memberships(c, name) == {"trendscend_app"}
        assert (
            await c.fetchval(
                "SELECT count(*) FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole "
                "WHERE r.rolname = $1",
                name,
            )
            == 0
        )
        ok = await asyncpg.connect(dsn_for(name, "pw-two"))
        await ok.close()
        with pytest.raises(asyncpg.InvalidPasswordError):
            await asyncpg.connect(dsn_for(name, "pw-one"))


BAD_NAMES = [
    "",
    "Robert'); DROP TABLE users;--",
    "a b",
    "a" * 64,
    "pg_x",
    "pg_x\n",
    "x\n",
    "é",
    "Upper",
    "trendscend_app",
    "postgres",
]


@LOOP
@pytest.mark.parametrize("bad", BAD_NAMES)
async def test_rejects_bad_role_names(owner_pool: asyncpg.Pool, bad: str) -> None:
    async with owner_pool.acquire() as c:
        with pytest.raises(ValueError):
            await bootstrap_api_role(c, bad, SecretStr("pw"))


@LOOP
async def test_rejects_owner_own_name_and_empty_password(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as c:
        me = await c.fetchval("SELECT current_user")
        with pytest.raises(ValueError):
            await bootstrap_api_role(c, me, SecretStr("pw"))
        with pytest.raises(ValueError):
            await bootstrap_api_role(c, "zz_b_nopw", SecretStr(""))
        assert await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'zz_b_nopw'") is None


@LOOP
async def test_max_length_name_accepted(owner_pool: asyncpg.Pool) -> None:
    name = "z" * 63
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await bootstrap_api_role(c, name, SecretStr("pw"))


@LOOP
async def test_password_with_quotes_and_newlines_works(owner_pool: asyncpg.Pool) -> None:
    name = "zz_b_pwd"
    for pw in ["p'a\"s\ns;--", "ünï©ode ☃ \\ $1 %s %I", "x" * 200]:
        async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
            await bootstrap_api_role(c, name, SecretStr(pw))
            conn = await asyncpg.connect(dsn_for(name, pw))
            try:
                assert await conn.fetchval("SELECT current_user") == name
            finally:
                await conn.close()


class BoomConn:
    """Fake connection whose every call fails with the password in the message."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc

    def transaction(self) -> Any:
        return self

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *a: object) -> None:
        return None

    async def fetchval(self, *a: Any, **k: Any) -> Any:
        if a and "current_user" in a[0]:
            return "owner"
        raise self.exc

    fetch = execute = fetchrow = fetchval


@LOOP
async def test_password_not_in_exceptions_or_repr(
    owner_pool: asyncpg.Pool, caplog: pytest.LogCaptureFixture
) -> None:
    secret = "S3CR3T-p'w"  # noqa: S105
    caplog.set_level(logging.DEBUG)
    # Fake failure carrying the secret in its text.
    with pytest.raises(RuntimeError) as ei:
        await bootstrap_api_role(
            BoomConn(asyncpg.PostgresError(f"bad {secret}")),  # type: ignore[arg-type]
            "zz_b_boom",
            SecretStr(secret),
        )
    assert str(ei.value) == "bootstrap failed"
    assert ei.value.__cause__ is None and ei.value.__suppress_context__
    assert secret not in repr(ei.value)
    # Real failure: an unprivileged role cannot create roles.
    low = "zz_b_low"
    async with scratch_role(owner_pool, low), owner_pool.acquire() as c:
        await bootstrap_api_role(c, low, SecretStr("lowpw"))
        lc = await asyncpg.connect(dsn_for(low, "lowpw"))
        try:
            with pytest.raises(RuntimeError) as ei2:
                await bootstrap_api_role(lc, "zz_b_other", SecretStr(secret))
        finally:
            await lc.close()
        assert str(ei2.value) == "bootstrap failed"
        assert ei2.value.__cause__ is None
    assert secret not in caplog.text
    assert "zz_b_other" not in await _roles(owner_pool)


async def _roles(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as c:
        return ",".join(r["rolname"] for r in await c.fetch("SELECT rolname FROM pg_roles"))


def test_main_missing_env_names_variable_only(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env = {
        "OWNER_DATABASE_URL": "postgresql://o:OWNERPW@h/db",
        "API_DB_ROLE": "zz_b_main",
        "API_DB_PASSWORD": "APIPW",
    }
    for missing in env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        monkeypatch.delenv(missing)
        assert main([]) == 2
        out = capsys.readouterr()
        text = out.out + out.err
        assert missing in text
        for v in env.values():
            assert v not in text
    assert main(["--bogus"]) == 2


def test_main_failure_is_generic(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("OWNER_DATABASE_URL", "postgresql://o:OWNERPW@127.0.0.1:1/db")
    monkeypatch.setenv("API_DB_ROLE", "zz_b_main")
    monkeypatch.setenv("API_DB_PASSWORD", "APIPW")
    assert main([]) == 1
    out = capsys.readouterr()
    assert "OWNERPW" not in out.out + out.err and "APIPW" not in out.out + out.err
    assert out.out == ""
    monkeypatch.setenv("API_DB_ROLE", "Bad Name")
    assert main([]) == 2
    assert "API_DB_ROLE" in capsys.readouterr().err


def test_main_success_path(
    owner_pool: asyncpg.Pool,  # noqa: ARG001 - ensures DB exists
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import asyncio

    monkeypatch.setenv("OWNER_DATABASE_URL", os.environ["TEST_DATABASE_URL"])
    monkeypatch.setenv("API_DB_ROLE", "zz_b_main")
    monkeypatch.setenv("API_DB_PASSWORD", "main-pw'1")
    try:
        assert main([]) == 0
        out = capsys.readouterr()
        assert out.out.strip() == "ok" and out.err == ""

        async def check() -> str:
            c = await asyncpg.connect(dsn_for("zz_b_main", "main-pw'1"))
            try:
                return str(await c.fetchval("SELECT current_user"))
            finally:
                await c.close()

        assert asyncio.run(check()) == "zz_b_main"
    finally:

        async def drop() -> None:
            c = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
            try:
                await c.execute('DROP ROLE IF EXISTS "zz_b_main"')
            finally:
                await c.close()

        asyncio.run(drop())


def test_module_exposes_entry() -> None:
    assert callable(bs.main)
