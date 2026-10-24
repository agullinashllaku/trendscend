from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from trendscend.api.app import create_app
from trendscend.api.settings import Settings, load_settings

EXPECTED_HEADERS = {
    "strict-transport-security": "max-age=63072000; includeSubDomains",
    "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
    "cache-control": "no-store",
}


def make_settings(env: str = "dev", **kw: Any) -> Settings:
    return Settings(
        database_url="postgresql://u:pw@h/db",
        redis_url="redis://h",
        environment=env,  # type: ignore[arg-type]
        **kw,
    )


class FakeConn:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def fetchval(self, *_a: Any, **_k: Any) -> int:
        if self.fail:
            raise ConnectionError("db down: password=pw")
        return 1


class FakeAcquire:
    def __init__(self, fail: bool) -> None:
        self.fail = fail

    async def __aenter__(self) -> FakeConn:
        return FakeConn(self.fail)

    async def __aexit__(self, *_a: Any) -> None:
        return None


class FakePool:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    def acquire(self, **_k: Any) -> FakeAcquire:
        return FakeAcquire(self.fail)


def build(env: str = "dev", pool: Any = None) -> Any:
    app = create_app(make_settings(env), pool=pool or FakePool())

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret SQL: SELECT * FROM users")

    return app


def client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_healthz_ok() -> None:
    async with client(build()) as c:
        r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_security_headers_present_on_every_response() -> None:
    async with client(build()) as c:
        responses = [await c.get("/healthz"), await c.get("/nope"), await c.get("/boom")]
    assert [r.status_code for r in responses] == [200, 404, 500]
    for r in responses:
        for k, v in EXPECTED_HEADERS.items():
            assert r.headers[k] == v


async def test_request_id_echoed_and_regenerated_when_invalid() -> None:
    rid = "123e4567-e89b-12d3-a456-426614174000"
    async with client(build()) as c:
        ok = await c.get("/healthz", headers={"X-Request-ID": rid})
        bad = await c.get("/healthz", headers={"X-Request-ID": "x\r\nSet-Cookie: a=b"})
        bad2 = await c.get("/healthz", headers={"X-Request-ID": "not-a-uuid"})
    assert ok.headers["x-request-id"] == rid
    for r in (bad, bad2):
        assert r.headers["x-request-id"] != "not-a-uuid"
        assert "set-cookie" not in r.headers
        assert len(r.headers["x-request-id"]) == 36


async def test_unhandled_exception_returns_generic_500() -> None:
    async with client(build()) as c:
        r = await c.get("/boom", headers={"X-Request-ID": "123e4567-e89b-12d3-a456-426614174000"})
    assert r.status_code == 500
    assert r.json() == {
        "detail": "internal error",
        "request_id": "123e4567-e89b-12d3-a456-426614174000",
    }
    for bad in ("secret", "SELECT", "Traceback"):
        assert bad not in r.text


async def test_error_logs_do_not_contain_authorization_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    async with client(build()) as c:
        await c.get(
            "/boom?token=qsecret",
            headers={"Authorization": "Bearer sekrit", "Cookie": "sid=cookiesecret"},
        )
    ours = [r for r in caplog.records if r.name.startswith("trendscend")]
    assert ours  # the test client's own httpx logs are out of scope
    blob = " ".join(
        r.getMessage() + json.dumps(r.__dict__, default=str) + (r.exc_text or "") for r in ours
    )
    for s in ("sekrit", "cookiesecret", "qsecret", "SELECT"):
        assert s not in blob


async def test_access_log_only_allowlisted_fields() -> None:
    from trendscend.api.logging import AllowlistJsonFormatter

    rec = logging.LogRecord("x", logging.INFO, "f", 1, "request", None, None)
    rec.request_id = "r"
    rec.authorization = "Bearer sekrit"  # type: ignore[attr-defined]
    out = json.loads(AllowlistJsonFormatter().format(rec))
    assert set(out) == {"level", "event", "request_id"}


@pytest.mark.parametrize("env,expected", [("dev", 200), ("staging", 200), ("prod", 404)])
async def test_docs_disabled_in_prod(env: str, expected: int) -> None:
    async with client(build(env)) as c:
        assert (await c.get("/openapi.json")).status_code == expected
        assert (await c.get("/docs")).status_code == expected


async def test_readyz_ok_and_unavailable() -> None:
    async with client(build()) as c:
        assert (await c.get("/readyz")).json() == {"status": "ok"}
    async with client(build(pool=FakePool(fail=True))) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert "password" not in r.text


async def test_readyz_checks_redis_when_set() -> None:
    class BadRedis:
        async def ping(self) -> None:
            raise ConnectionError("x")

    class GoodRedis:
        async def ping(self) -> bool:
            return True

    app = build()
    app.state.redis = GoodRedis()
    async with client(app) as c:
        assert (await c.get("/readyz")).status_code == 200
        app.state.redis = BadRedis()
        assert (await c.get("/readyz")).status_code == 503


BASE = {
    "DATABASE_URL": "postgresql://u:topsecretpw@h/db",
    "REDIS_URL": "redis://h",
    "ENVIRONMENT": "dev",
}


@pytest.mark.parametrize("missing", ["DATABASE_URL", "REDIS_URL", "ENVIRONMENT"])
def test_load_settings_missing_var_names_variable_not_value(missing: str) -> None:
    env = {k: v for k, v in BASE.items() if k != missing}
    with pytest.raises(RuntimeError) as ei:
        load_settings(env)
    assert missing in str(ei.value)
    assert "topsecretpw" not in str(ei.value)


def test_load_settings_invalid_environment_does_not_echo_value() -> None:
    with pytest.raises(RuntimeError) as ei:
        load_settings({**BASE, "ENVIRONMENT": "leakyvalue"})
    assert "environment" in str(ei.value) and "leakyvalue" not in str(ei.value)


def test_load_settings_parses_values() -> None:
    s = load_settings(
        {**BASE, "TRUSTED_PROXIES": "10.0.0.1, 10.0.0.2", "RATE_LIMIT_PER_MINUTE": "5"}
    )
    assert s.trusted_proxies == {"10.0.0.1", "10.0.0.2"}
    assert s.rate_limit_per_minute == 5 and s.allow_unsafe_db_role is False


def test_allow_unsafe_db_role_dev_only() -> None:
    assert load_settings({**BASE, "ALLOW_UNSAFE_DB_ROLE": "1"}).allow_unsafe_db_role is True
    for env in ("staging", "prod"):
        with pytest.raises(RuntimeError, match="ALLOW_UNSAFE_DB_ROLE"):
            load_settings({**BASE, "ENVIRONMENT": env, "ALLOW_UNSAFE_DB_ROLE": "1"})
    with pytest.raises(ValueError):
        make_settings("prod", allow_unsafe_db_role=True)
