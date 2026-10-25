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
        await c.execute(
            f'ALTER ROLE "{name}" CREATEROLE CREATEDB REPLICATION NOINHERIT CONNECTION LIMIT 0'
        )
        await c.execute(f'ALTER ROLE "{name}" SET session_replication_role = replica')
        await c.execute(f'ALTER ROLE "{name}" SET search_path = pg_temp')
        db = await c.fetchval("SELECT current_database()")
        await c.execute(f"ALTER ROLE \"{name}\" IN DATABASE {db} SET work_mem = '1MB'")
        await c.execute(f'GRANT "{other}" TO "{name}"')
        await c.execute(f"ALTER ROLE \"{name}\" PASSWORD 'wrong'")
        await bootstrap_api_role(c, name, SecretStr("pw-two"))
        row = await c.fetchrow(FLAGS, name)
        assert row is not None
        assert tuple(row)[:7] == (True, False, False, False, False, False, True)
        assert not row["rolconfig"]
        assert await c.fetchval("SELECT rolconnlimit FROM pg_roles WHERE rolname=$1", name) == -1
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
    for pw in ["p'a\"s\ns;--", "a\\ $1 %s %I\t;\r", "x" * 200]:
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
    assert ei.value.__cause__ is None and ei.value.__context__ is None
    assert ei.value.__suppress_context__
    _assert_no_secret_in_frames(ei.value, secret)
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
        assert ei2.value.__cause__ is None and ei2.value.__context__ is None
        _assert_no_secret_in_frames(ei2.value, secret)
    assert secret not in caplog.text
    assert "zz_b_other" not in await _roles(owner_pool)


def _assert_no_secret_in_frames(exc: BaseException, secret: str) -> None:
    tb = exc.__traceback__
    while tb is not None:
        if not tb.tb_frame.f_code.co_filename.endswith("trendscend/db/bootstrap.py"):
            tb = tb.tb_next
            continue
        for v in tb.tb_frame.f_locals.values():
            assert not (isinstance(v, str) and secret in v)
            assert not (isinstance(v, bytes) and secret.encode() in v)
        tb = tb.tb_next


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


# ---- fix round 1 -----------------------------------------------------------


class RecConn:
    """Delegating wrapper recording every SQL string and positional argument."""

    def __init__(self, conn: Any, fail_on: str | None = None) -> None:
        self._c = conn
        self.sql: list[str] = []
        self.args: list[Any] = []
        self.fail_on = fail_on

    def transaction(self) -> Any:
        return self._c.transaction()

    def _rec(self, sql: str, args: tuple[Any, ...]) -> None:
        self.sql.append(sql)
        self.args.extend(args)
        if self.fail_on and sql.startswith(self.fail_on):
            raise asyncpg.PostgresError("forced")

    async def execute(self, sql: str, *a: Any) -> Any:
        self._rec(sql, a)
        return await self._c.execute(sql, *a)

    async def fetch(self, sql: str, *a: Any) -> Any:
        self._rec(sql, a)
        return await self._c.fetch(sql, *a)

    async def fetchval(self, sql: str, *a: Any) -> Any:
        self._rec(sql, a)
        return await self._c.fetchval(sql, *a)

    async def fetchrow(self, sql: str, *a: Any) -> Any:
        self._rec(sql, a)
        return await self._c.fetchrow(sql, *a)

    def everything(self) -> list[str]:
        return [*self.sql, *(str(x) for x in self.args)]


PLAIN = "Pl4in'text;pw\\"


@LOOP
async def test_plaintext_never_sent_only_scram_verifier(owner_pool: asyncpg.Pool) -> None:
    name = "zz_b_scram"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        for _ in range(2):  # create path, then repair path
            rec = RecConn(c)
            await bootstrap_api_role(rec, name, SecretStr(PLAIN))  # type: ignore[arg-type]
            assert not any(PLAIN in x or "Pl4in" in x for x in rec.everything())
            verifiers = [a for a in rec.args if str(a).startswith("SCRAM-SHA-256$")]
            assert verifiers
            stored = await c.fetchval("SELECT rolpassword FROM pg_authid WHERE rolname=$1", name)
            assert stored == verifiers[0]
            ok = await asyncpg.connect(dsn_for(name, PLAIN))
            await ok.close()
            with pytest.raises(asyncpg.InvalidPasswordError):
                await asyncpg.connect(dsn_for(name, PLAIN + "x"))


@LOOP
async def test_failure_path_statements_contain_only_verifier(owner_pool: asyncpg.Pool) -> None:
    low = "zz_b_low2"
    async with scratch_role(owner_pool, low), owner_pool.acquire() as c:
        await bootstrap_api_role(c, low, SecretStr("lowpw"))
        lc = await asyncpg.connect(dsn_for(low, "lowpw"))
        try:
            rec = RecConn(lc)
            with pytest.raises(RuntimeError):
                await bootstrap_api_role(rec, "zz_b_nope", SecretStr(PLAIN))  # type: ignore[arg-type]
        finally:
            await lc.close()
        assert not any("Pl4in" in x for x in rec.everything())
        assert any("SCRAM-SHA-256$" in x for x in rec.everything())


@LOOP
async def test_scram_verifier_matches_server_hashing(owner_pool: asyncpg.Pool) -> None:
    import base64

    name, pw = "zz_b_xcheck", "known answer pw"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{name}"')
        await c.execute("SET password_encryption = 'scram-sha-256'")
        await c.execute(f"ALTER ROLE \"{name}\" PASSWORD '{pw}'")
        stored = await c.fetchval("SELECT rolpassword FROM pg_authid WHERE rolname=$1", name)
        head, rest = stored.split("$", 1)
        assert head == "SCRAM-SHA-256"
        it_s, salt_s = rest.split("$")[0].split(":")
        mine = bs._scram_verifier(pw, salt=base64.b64decode(salt_s), iterations=int(it_s))
        assert mine == stored


def test_scram_verifier_defaults_random_salt() -> None:
    a, b = bs._scram_verifier("pw"), bs._scram_verifier("pw")
    assert a != b and a.startswith("SCRAM-SHA-256$4096:")


@LOOP
@pytest.mark.parametrize("pw", ["ﬁx", "é", "a\x00b", "naïve☃"])
async def test_non_ascii_or_nul_password_rejected_without_echo(
    owner_pool: asyncpg.Pool, pw: str
) -> None:
    async with owner_pool.acquire() as c:
        with pytest.raises(ValueError) as ei:
            await bootstrap_api_role(c, "zz_b_na", SecretStr(pw))
        assert pw not in str(ei.value) and pw not in repr(ei.value)
        assert await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname='zz_b_na'") is None


@LOOP
async def test_repair_removes_memberships_granted_by_other_grantors(
    owner_pool: asyncpg.Pool,
) -> None:
    name, granter, scratch = "zz_b_mem", "zz_b_granter", "zz_b_scr2"
    async with scratch_role(owner_pool, name, granter, scratch), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{scratch}" NOLOGIN')
        await c.execute(f'CREATE ROLE "{granter}" SUPERUSER NOLOGIN')
        await bootstrap_api_role(c, name, SecretStr("pw"))
        await c.execute(f'GRANT trendscend_app TO "{granter}" WITH ADMIN OPTION')
        await c.execute(f'GRANT "{scratch}" TO "{granter}" WITH ADMIN OPTION')
        await c.execute(
            f'GRANT trendscend_app TO "{name}" WITH ADMIN OPTION, INHERIT FALSE '
            f'GRANTED BY "{granter}"'
        )
        await c.execute(f'GRANT "{scratch}" TO "{name}" GRANTED BY "{granter}"')
        await bootstrap_api_role(c, name, SecretStr("pw"))
        rows = await c.fetch(
            "SELECT g.rolname, m.admin_option, m.inherit_option, m.set_option "
            "FROM pg_auth_members m JOIN pg_roles g ON g.oid = m.roleid "
            "JOIN pg_roles u ON u.oid = m.member WHERE u.rolname = $1",
            name,
        )
        assert [tuple(r) for r in rows] == [("trendscend_app", False, True, True)]


@LOOP
async def test_membership_repair_is_atomic(owner_pool: asyncpg.Pool) -> None:
    name, scratch = "zz_b_atom", "zz_b_scr3"
    async with scratch_role(owner_pool, name, scratch), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{scratch}" NOLOGIN')
        await bootstrap_api_role(c, name, SecretStr("pw"))
        await c.execute(f'GRANT "{scratch}" TO "{name}"')
        before = await memberships(c, name)
        assert before == {"trendscend_app", scratch}
        with pytest.raises(RuntimeError):
            await bootstrap_api_role(
                RecConn(c, fail_on="GRANT"),  # type: ignore[arg-type]
                name,
                SecretStr("pw"),
            )
        assert await memberships(c, name) == before


@LOOP
async def test_concurrent_bootstraps_serialise(owner_pool: asyncpg.Pool) -> None:
    import asyncio

    name = "zz_b_conc"
    async with scratch_role(owner_pool, name):
        a, b = await owner_pool.acquire(), await owner_pool.acquire()
        try:
            res = await asyncio.gather(
                bootstrap_api_role(a, name, SecretStr("pw1")),
                bootstrap_api_role(b, name, SecretStr("pw1")),
                return_exceptions=True,
            )
        finally:
            await owner_pool.release(a)
            await owner_pool.release(b)
        assert res == [None, None]
        async with owner_pool.acquire() as c:
            assert await memberships(c, name) == {"trendscend_app"}


@LOOP
async def test_refuses_to_adopt_superuser_role(owner_pool: asyncpg.Pool) -> None:
    name = "zz_b_su"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{name}" LOGIN SUPERUSER')
        with pytest.raises(ValueError, match="refusing to adopt"):
            await bootstrap_api_role(c, name, SecretStr("pw"))
        assert await c.fetchval("SELECT rolsuper FROM pg_roles WHERE rolname=$1", name) is True


@LOOP
async def test_refuses_to_adopt_role_owning_objects(owner_pool: asyncpg.Pool) -> None:
    name = "zz_b_owns"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{name}" LOGIN CREATEROLE')
        await c.execute("CREATE TABLE public.zz_b_owned (x int)")
        try:
            await c.execute(f'ALTER TABLE public.zz_b_owned OWNER TO "{name}"')
            with pytest.raises(ValueError, match="refusing to adopt"):
                await bootstrap_api_role(c, name, SecretStr("pw"))
            assert await c.fetchval("SELECT rolcreaterole FROM pg_roles WHERE rolname=$1", name)
        finally:
            await c.execute("DROP TABLE IF EXISTS public.zz_b_owned")


@LOOP
async def test_refuses_to_adopt_role_owning_database(owner_pool: asyncpg.Pool) -> None:
    name, db = "zz_b_dbown", "zz_b_db"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        await c.execute(f'CREATE ROLE "{name}" LOGIN CREATEROLE')
        try:
            await c.execute(f'CREATE DATABASE "{db}" TEMPLATE template0 OWNER "{name}"')
            with pytest.raises(ValueError, match="refusing to adopt"):
                await bootstrap_api_role(c, name, SecretStr("pw"))
            assert await c.fetchval("SELECT rolcreaterole FROM pg_roles WHERE rolname=$1", name)
        finally:
            await c.execute(f'DROP DATABASE IF EXISTS "{db}"')


class NoMembershipView(RecConn):
    async def fetch(self, sql: str, *a: Any) -> Any:
        if "set_option" in sql:
            return []  # simulate the verification seeing a wrong final state
        return await super().fetch(sql, *a)


@LOOP
async def test_final_membership_verification_failure_rolls_back(
    owner_pool: asyncpg.Pool,
) -> None:
    name = "zz_b_verify"
    async with scratch_role(owner_pool, name), owner_pool.acquire() as c:
        with pytest.raises(RuntimeError, match="bootstrap failed"):
            await bootstrap_api_role(NoMembershipView(c), name, SecretStr("pw"))  # type: ignore[arg-type]
        assert await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname=$1", name) is None
