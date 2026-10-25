from __future__ import annotations

from typing import Any

import asyncpg
import pytest

from tests.api.fakes import FakeRedis
from tests.api.test_app import make_settings
from trendscend.api import app as app_module
from trendscend.api.app import create_app
from trendscend.api.startup import assert_safe_db_role


class FakeConn:
    """Fake connection; ``rows`` is what the role query returns (one per role)."""

    def __init__(self, rows: list[dict[str, bool]] | dict[str, bool] | None) -> None:
        if rows is None:
            self.rows: list[dict[str, bool]] = []
        elif isinstance(rows, dict):
            self.rows = [rows]
        else:
            self.rows = rows

    async def fetch(self, *_a: Any) -> list[dict[str, bool]]:
        return self.rows


def row(
    s: bool = False,
    b: bool = False,
    o: bool = False,
    m: bool = False,
    sc: bool = False,
    d: bool = False,
    repl: bool = False,
    cr: bool = False,
    sf: bool = False,
    fn: bool = False,
    tp: bool = False,
    ro: bool = False,
    dc: bool = False,
    dt: bool = False,
) -> dict[str, bool]:
    return {
        "rolsuper": s,
        "rolbypassrls": b,
        "controls_rls_tables": o,
        "can_assume_privileged": m,
        "controls_rls_schema": sc,
        "controls_database": d,
        "rolreplication": repl,
        "rolcreaterole": cr,
        "can_use_server_files": sf,
        "controls_rls_functions": fn,
        "has_dangerous_table_privs": tp,
        "replication_role_not_origin": ro,
        "holds_db_create": dc,
        "holds_db_temp": dt,
    }


async def test_safe_role_passes() -> None:
    await assert_safe_db_role(FakeConn(row()))  # type: ignore[arg-type]


async def test_problems_merge_across_session_and_current_role() -> None:
    # session_user is clean, current_user (after SET ROLE) is the unsafe one.
    conn = FakeConn([row(), row(b=True)])
    with pytest.raises(RuntimeError, match="BYPASSRLS"):
        await assert_safe_db_role(conn)  # type: ignore[arg-type]
    # Reverse: session_user unsafe, current_user clean.
    conn = FakeConn([row(s=True), row()])
    with pytest.raises(RuntimeError, match="superuser"):
        await assert_safe_db_role(conn)  # type: ignore[arg-type]
    # Both rows contribute distinct problems to one message.
    conn = FakeConn([row(s=True), row(sc=True)])
    with pytest.raises(RuntimeError) as ei:
        await assert_safe_db_role(conn)  # type: ignore[arg-type]
    assert "superuser" in str(ei.value) and "schema" in str(ei.value)


@pytest.mark.parametrize(
    "r,word",
    [
        (row(s=True), "superuser"),
        (row(b=True), "BYPASSRLS"),
        (row(o=True), "owns or inherits ownership of RLS tables"),
        (row(m=True), "member of a superuser/BYPASSRLS role"),
        (row(sc=True), "owns or can assume ownership of a schema holding RLS tables"),
        (row(d=True), "owns or can assume ownership of the database"),
        (row(repl=True), "REPLICATION"),
        (row(cr=True), "CREATEROLE"),
        (row(sf=True), "member of a server-file/program built-in role"),
        (row(fn=True), "owns or can assume ownership of a trigger function on RLS tables"),
        (row(tp=True), "holds TRUNCATE/TRIGGER/REFERENCES on RLS tables"),
        (row(ro=True), "session_replication_role is not 'origin'"),
        (row(dc=True), "holds CREATE on the database"),
        (row(dt=True), "holds TEMPORARY on the database"),
        (None, "not found"),
    ],
)
async def test_unsafe_role_raises(r: dict[str, bool] | None, word: str) -> None:
    with pytest.raises(RuntimeError, match=word):
        await assert_safe_db_role(FakeConn(r))  # type: ignore[arg-type]


async def test_new_problems_merge_across_roles_and_carry_no_role_names() -> None:
    conn = FakeConn([row(repl=True, sf=True), row(cr=True, fn=True, tp=True)])
    with pytest.raises(RuntimeError) as ei:
        await assert_safe_db_role(conn)  # type: ignore[arg-type]
    msg = str(ei.value)
    for word in ("REPLICATION", "CREATEROLE", "server-file/program", "trigger function"):
        assert word in msg
    assert "TRUNCATE" in msg
    assert "zz_" not in msg and "pg_read_server_files" not in msg


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

    monkeypatch.setattr(app_module, "create_pool", fake_create_pool)


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
async def test_live_login_role_granted_trendscend_app_passes(owner_pool: asyncpg.Pool) -> None:
    # The realistic deployment: a LOGIN role that is a member of trendscend_app
    # and owns nothing. SET SESSION AUTHORIZATION makes session_user it too.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "CREATE ROLE zz_api_login LOGIN NOSUPERUSER NOBYPASSRLS IN ROLE trendscend_app"
            )
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_api_login")
            await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_superuser_session_with_downgraded_role_rejected(
    owner_pool: asyncpg.Pool,
) -> None:
    # RESET ROLE would regain superuser, so SET ROLE downgrade is not safe.
    async with owner_pool.acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL ROLE trendscend_app")
        with pytest.raises(RuntimeError, match="superuser"):
            await assert_safe_db_role(conn)


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_noinherit_member_of_table_owner_rejected(owner_pool: asyncpg.Pool) -> None:
    # NOINHERIT does not stop SET ROLE <owner>, after which RLS can be disabled.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE zz_own NOLOGIN")
            await conn.execute("CREATE ROLE zz_noinh LOGIN NOINHERIT IN ROLE zz_own")
            await conn.execute("ALTER TABLE audit_log OWNER TO zz_own")
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_noinh")
            with pytest.raises(RuntimeError, match="RLS tables"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_schema_owner_rejected(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE zz_schema_own LOGIN IN ROLE trendscend_app")
            await conn.execute("ALTER SCHEMA public OWNER TO zz_schema_own")
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_schema_own")
            with pytest.raises(RuntimeError, match="schema holding RLS tables"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_database_owner_rejected(owner_pool: asyncpg.Pool) -> None:
    # Database ownership is changed inside the transaction and rolled back.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute("CREATE ROLE zz_db_own LOGIN IN ROLE trendscend_app")
            dbname = await conn.fetchval("SELECT current_database()")
            await conn.execute(f'ALTER DATABASE "{dbname}" OWNER TO zz_db_own')  # noqa: S608
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_db_own")
            with pytest.raises(RuntimeError, match="the database"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


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

    app = create_app(make_settings("dev"), pool=owner_pool, redis=FakeRedis())
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


_SAFE_CLAUSE = "IN ROLE trendscend_app"


async def _reject_live(
    owner_pool: asyncpg.Pool, setup: list[str], role: str, match: str, *, login: str | None = None
) -> None:
    """Run ``setup`` in a rolled-back tx, enter ``role`` as session_user, expect rejection."""
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            for stmt in setup:
                await conn.execute(stmt)
            await conn.execute(f"SET LOCAL SESSION AUTHORIZATION {login or role}")
            with pytest.raises(RuntimeError, match=match) as ei:
                await assert_safe_db_role(conn)
            assert "zz_s_" not in str(ei.value)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "builtin",
    ["pg_execute_server_program", "pg_read_server_files", "pg_write_server_files"],
)
async def test_live_server_file_builtin_member_rejected(
    owner_pool: asyncpg.Pool, builtin: str
) -> None:
    await _reject_live(
        owner_pool,
        [f"CREATE ROLE zz_s_sf LOGIN NOSUPERUSER NOBYPASSRLS {_SAFE_CLAUSE}, {builtin}"],
        "zz_s_sf",
        "server-file/program built-in role",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_replication_role_rejected(owner_pool: asyncpg.Pool) -> None:
    await _reject_live(
        owner_pool,
        [f"CREATE ROLE zz_s_repl LOGIN REPLICATION {_SAFE_CLAUSE}"],
        "zz_s_repl",
        "REPLICATION",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_createrole_role_rejected(owner_pool: asyncpg.Pool) -> None:
    await _reject_live(
        owner_pool,
        [f"CREATE ROLE zz_s_cr LOGIN CREATEROLE {_SAFE_CLAUSE}"],
        "zz_s_cr",
        "CREATEROLE",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "priv,table",
    [("TRUNCATE", "audit_log"), ("TRIGGER", "users"), ("REFERENCES", "invites")],
)
async def test_live_dangerous_table_privilege_rejected(
    owner_pool: asyncpg.Pool, priv: str, table: str
) -> None:
    await _reject_live(
        owner_pool,
        [
            f"CREATE ROLE zz_s_priv LOGIN {_SAFE_CLAUSE}",
            f"GRANT {priv} ON {table} TO zz_s_priv",
        ],
        "zz_s_priv",
        "TRUNCATE/TRIGGER/REFERENCES on RLS tables",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_trigger_function_owner_member_rejected(owner_pool: asyncpg.Pool) -> None:
    await _reject_live(
        owner_pool,
        [
            "CREATE ROLE zz_s_own NOLOGIN",
            f"CREATE ROLE zz_s_mem LOGIN {_SAFE_CLAUSE}, zz_s_own",
            "CREATE TABLE zz_s_scratch (id int)",
            "ALTER TABLE zz_s_scratch ENABLE ROW LEVEL SECURITY",
            "CREATE FUNCTION zz_s_trg() RETURNS trigger LANGUAGE plpgsql "
            "AS 'BEGIN RETURN NEW; END'",
            "ALTER FUNCTION zz_s_trg() OWNER TO zz_s_own",
            "CREATE TRIGGER zz_s_t BEFORE INSERT ON zz_s_scratch "
            "FOR EACH ROW EXECUTE FUNCTION zz_s_trg()",
        ],
        "zz_s_mem",
        "trigger function on RLS tables",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_realistic_login_role_still_passes(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(
                "CREATE ROLE zz_s_login LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEROLE NOREPLICATION"
            )
            await conn.execute("GRANT trendscend_app TO zz_s_login")
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_s_login")
            await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_column_level_references_rejected(owner_pool: asyncpg.Pool) -> None:
    await _reject_live(
        owner_pool,
        [
            f"CREATE ROLE zz_s_priv LOGIN {_SAFE_CLAUSE}",
            "GRANT REFERENCES (id) ON users TO zz_s_priv",
        ],
        "zz_s_priv",
        "TRUNCATE/TRIGGER/REFERENCES on RLS tables",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_noinherit_member_of_column_references_holder_rejected(
    owner_pool: asyncpg.Pool,
) -> None:
    await _reject_live(
        owner_pool,
        [
            "CREATE ROLE zz_s_holder NOLOGIN",
            "GRANT REFERENCES (id) ON users TO zz_s_holder",
            f"CREATE ROLE zz_s_noinh LOGIN NOINHERIT {_SAFE_CLAUSE}, zz_s_holder",
        ],
        "zz_s_noinh",
        "TRUNCATE/TRIGGER/REFERENCES on RLS tables",
    )


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_column_select_on_non_rls_table_passes(owner_pool: asyncpg.Pool) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f"CREATE ROLE zz_s_col LOGIN {_SAFE_CLAUSE}")
            await conn.execute("CREATE TABLE zz_s_plain (id int)")
            await conn.execute("GRANT SELECT (id) ON zz_s_plain TO zz_s_col")
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_s_col")
            await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_replica_session_replication_role_rejected(owner_pool: asyncpg.Pool) -> None:
    # Only a superuser can set it; the check is on the effective session value,
    # which persists across SET LOCAL SESSION AUTHORIZATION to a safe login role.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await conn.execute(f"CREATE ROLE zz_s_rep LOGIN {_SAFE_CLAUSE}")
            await conn.execute("SET LOCAL session_replication_role = replica")
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_s_rep")
            with pytest.raises(RuntimeError, match="session_replication_role is not 'origin'"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


# --- injected pools are role-checked by default (L6) -------------------------


async def test_injected_pool_is_role_checked_by_default() -> None:
    pool = FakePool(FakeConn(row(s=True)))
    app = create_app(make_settings("prod"), pool=pool, redis=FakeRedis())
    with pytest.raises(RuntimeError, match="Unsafe database role"):
        async with app.router.lifespan_context(app):
            pytest.fail("startup must not succeed")
    assert not pool.closed  # the caller owns an injected pool


async def test_injected_safe_pool_passes_check() -> None:
    pool = FakePool(FakeConn(row()))
    app = create_app(make_settings("prod"), pool=pool, redis=FakeRedis())
    async with app.router.lifespan_context(app):
        assert app.state.pool is pool
    assert not pool.closed


async def test_trust_injected_pool_skips_check() -> None:
    pool = FakePool(FakeConn(row(s=True)))
    app = create_app(make_settings("prod"), pool=pool, redis=FakeRedis(), trust_injected_pool=True)
    async with app.router.lifespan_context(app):
        assert app.state.pool is pool


async def test_injected_pool_check_honours_dev_flag_only_in_dev() -> None:
    dev = create_app(
        make_settings("dev", allow_unsafe_db_role=True),
        pool=FakePool(FakeConn(row(s=True))),
        redis=FakeRedis(),
    )
    async with dev.router.lifespan_context(dev):
        pass
    settings = make_settings("staging").model_copy(update={"allow_unsafe_db_role": True})
    other = create_app(settings, pool=FakePool(FakeConn(row(s=True))), redis=FakeRedis())
    with pytest.raises(RuntimeError, match="superuser"):
        async with other.router.lifespan_context(other):
            pass


async def test_created_pool_still_role_checked_and_closed_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = FakePool(FakeConn(row(s=True)))
    patch_pool(monkeypatch, pool)
    app = create_app(make_settings("prod"), redis=FakeRedis())
    with pytest.raises(RuntimeError, match="Unsafe database role"):
        async with app.router.lifespan_context(app):
            pass
    assert pool.closed


async def _grant_tx(conn: asyncpg.Connection, role_sql: str, grant: str | None) -> None:
    await conn.execute(role_sql)
    if grant:
        dbname = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'GRANT {grant} ON DATABASE "{dbname}" TO zz_dbpriv')  # noqa: S608
    await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_dbpriv")


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
@pytest.mark.parametrize(
    "grant,word",
    [
        ("CREATE", "holds CREATE on the database"),
        ("TEMPORARY", "holds TEMPORARY on the database"),
        (None, None),
        ("CONNECT", None),
    ],
)
async def test_live_database_create_temp_privileges(
    owner_pool: asyncpg.Pool, grant: str | None, word: str | None
) -> None:
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            await _grant_tx(conn, "CREATE ROLE zz_dbpriv LOGIN IN ROLE trendscend_app", grant)
            if word is None:
                await assert_safe_db_role(conn)
            else:
                with pytest.raises(RuntimeError, match=word):
                    await assert_safe_db_role(conn)
        finally:
            await tx.rollback()


@pytest.mark.integration
@pytest.mark.asyncio(loop_scope="session")
async def test_live_noinherit_member_of_create_holder_rejected(owner_pool: asyncpg.Pool) -> None:
    # has_database_privilege ignores a NOINHERIT member's SET ROLE reach; the
    # query is membership-based so it is still refused.
    async with owner_pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        try:
            dbname = await conn.fetchval("SELECT current_database()")
            await conn.execute("CREATE ROLE zz_holder NOLOGIN")
            await conn.execute(f'GRANT CREATE ON DATABASE "{dbname}" TO zz_holder')  # noqa: S608
            await conn.execute(
                "CREATE ROLE zz_dbpriv LOGIN NOINHERIT IN ROLE trendscend_app, zz_holder"
            )
            await conn.execute("SET LOCAL SESSION AUTHORIZATION zz_dbpriv")
            with pytest.raises(RuntimeError, match="holds CREATE on the database"):
                await assert_safe_db_role(conn)
        finally:
            await tx.rollback()
