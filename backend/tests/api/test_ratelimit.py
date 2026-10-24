from __future__ import annotations

import os
import re
import uuid
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from tests.api.fakes import Clock, FakeRedis
from tests.api.test_app import EXPECTED_HEADERS, FakePool, make_settings
from trendscend.api.app import create_app
from trendscend.api.ratelimit import RateLimiter, client_ip
from trendscend.api.settings import Settings, load_settings


def scope(peer: str | None, xff: list[str] | None = None) -> dict[str, Any]:
    s: dict[str, Any] = {
        "type": "http",
        "client": (peer, 1234) if peer is not None else None,
        "headers": [(b"x-forwarded-for", v.encode("utf-8")) for v in (xff or [])],
    }
    return s


TRUSTED = frozenset({"10.0.0.0/8", "192.168.1.1"})


def build(limit: int = 3, redis: FakeRedis | None = None, **kw: Any) -> tuple[Any, FakeRedis]:
    r = redis or FakeRedis()
    app = create_app(make_settings(rate_limit_per_minute=limit, **kw), pool=FakePool(), redis=r)
    return app, r


def client(app: Any, peer: str = "203.0.113.5") -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=ASGITransport(app=app, client=(peer, 5000)), base_url="http://t"
    )


# ---- limiter ----
async def test_allows_up_to_limit_then_429() -> None:
    app, _ = build(limit=3)
    async with client(app) as c:
        codes = [(await c.get("/readyz")).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_429_body_generic_and_retry_after_positive_int_and_headers() -> None:
    app, _ = build(limit=1)
    async with client(app) as c:
        await c.get("/readyz")
        r = await c.get("/readyz")
    assert r.status_code == 429
    assert r.json() == {"detail": "rate limit exceeded"}
    assert re.fullmatch(r"[1-9][0-9]*", r.headers["retry-after"])
    for k, v in EXPECTED_HEADERS.items():
        assert r.headers[k] == v
    uuid.UUID(r.headers["x-request-id"])


async def test_window_resets_after_expiry() -> None:
    clock = Clock(1_000_000_020.0)
    r = FakeRedis(clock)
    lim = RateLimiter(r, limit=1, clock=clock)
    assert (await lim.hit("1.2.3.4")).allowed
    res = await lim.hit("1.2.3.4")
    assert not res.allowed and 1 <= res.retry_after <= 60
    clock.now += 61
    assert (await lim.hit("1.2.3.4")).allowed


async def test_key_format_has_only_ip_and_minute() -> None:
    clock = Clock(1_000_000_020.0)
    r = FakeRedis(clock)
    await RateLimiter(r, limit=5, clock=clock).hit("1.2.3.4")
    assert list(r.counts) == [f"rl:1.2.3.4:{int(1_000_000_020 // 60)}"]


async def test_expire_always_set() -> None:
    r = FakeRedis()
    lim = RateLimiter(r, limit=100)
    for ip in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        await lim.hit(ip)
        await lim.hit(ip)
    assert set(r.counts) == set(r.expires_at) and len(r.counts) == 3


async def test_script_is_single_atomic_eval_with_incr_and_expire() -> None:
    from trendscend.api import ratelimit

    assert "INCR" in ratelimit._SCRIPT and "EXPIRE" in ratelimit._SCRIPT


# ---- spoofing ----
def test_xff_ignored_when_peer_not_trusted() -> None:
    assert client_ip(scope("203.0.113.5", ["9.9.9.9"]), TRUSTED) == "203.0.113.5"


async def test_spoofed_xff_cannot_mint_buckets() -> None:
    app, r = build(limit=2)
    async with client(app) as c:
        codes = [
            (await c.get("/readyz", headers={"X-Forwarded-For": f"9.9.9.{i}"})).status_code
            for i in range(3)
        ]
    assert codes == [200, 200, 429]
    assert len(r.counts) == 1


def test_xff_used_when_peer_trusted() -> None:
    assert client_ip(scope("10.1.2.3", ["198.51.100.7"]), TRUSTED) == "198.51.100.7"
    assert client_ip(scope("192.168.1.1", ["198.51.100.7"]), TRUSTED) == "198.51.100.7"


def test_rightmost_untrusted_entry_used() -> None:
    # client-supplied "6.6.6.6" is left of the real client the proxy appended.
    xff = ["6.6.6.6, 198.51.100.7, 10.9.9.9"]
    assert client_ip(scope("10.1.2.3", xff), TRUSTED) == "198.51.100.7"
    # multiple header lines are joined in order
    assert (
        client_ip(scope("10.1.2.3", ["6.6.6.6", "198.51.100.7, 10.0.0.2"]), TRUSTED)
        == "198.51.100.7"
    )


def test_all_trusted_chain_falls_back_to_peer() -> None:
    assert client_ip(scope("10.1.2.3", ["10.0.0.4, 10.0.0.5"]), TRUSTED) == "10.1.2.3"


@pytest.mark.parametrize("bad", ["not-an-ip", "ünïcode", "1.2.3.4.5", "", "999.1.1.1", "evil\x00"])
def test_malformed_xff_ignored_falls_back_to_peer(bad: str) -> None:
    assert client_ip(scope("10.1.2.3", [bad]), TRUSTED) == "10.1.2.3"
    # a malformed right-most entry never selects an older (spoofable) entry
    assert client_ip(scope("10.1.2.3", [f"6.6.6.6, {bad}"]), TRUSTED) == "10.1.2.3"


def test_missing_client_scope_uses_unknown_bucket() -> None:
    assert client_ip(scope(None), TRUSTED) == "unknown"
    assert client_ip(scope(None, ["1.2.3.4"]), TRUSTED) == "unknown"


def test_ipv6_peer_and_xff() -> None:
    t = frozenset({"fd00::/8"})
    assert client_ip(scope("fd00::1", ["2001:db8::5"]), t) == "2001:db8::5"
    assert client_ip(scope("2001:db8::9"), t) == "2001:db8::9"


# ---- fail closed ----
async def test_redis_failure_returns_503_except_healthz() -> None:
    app, r = build()
    r.fail = True
    async with client(app) as c:
        h = await c.get("/healthz")
        x = await c.get("/readyz")
    assert h.status_code == 200 and h.json() == {"status": "ok"}
    assert x.status_code == 503 and x.json() == {"detail": "service unavailable"}
    assert "redis" not in x.text and "pw" not in x.text
    for k, v in EXPECTED_HEADERS.items():
        assert x.headers[k] == v
    uuid.UUID(x.headers["x-request-id"])


async def test_503_when_no_redis_client_configured() -> None:
    app = create_app(make_settings(), pool=FakePool())
    async with client(app) as c:
        assert (await c.get("/readyz")).status_code == 503
        assert (await c.get("/healthz")).status_code == 200


async def test_healthz_not_counted_against_limit() -> None:
    app, r = build(limit=1)
    async with client(app) as c:
        for _ in range(5):
            assert (await c.get("/healthz")).status_code == 200
    assert r.counts == {}


# ---- readyz / lifespan ----
async def test_readyz_503_when_redis_ping_fails_or_slow() -> None:
    app, r = build(limit=100)
    async with client(app) as c:
        assert (await c.get("/readyz")).status_code == 200
        r.ping_delay = 5
        resp = await c.get("/readyz")
    assert resp.status_code == 503 and resp.json() == {"status": "unavailable"}


async def test_lifespan_creates_and_closes_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    import trendscend.api.app as appmod

    created: list[FakeRedis] = []
    seen_urls: list[str] = []

    def fake_from_url(url: str) -> FakeRedis:
        seen_urls.append(url)
        created.append(FakeRedis())
        return created[0]

    monkeypatch.setattr(appmod.aioredis, "from_url", fake_from_url)
    app = create_app(make_settings(), pool=FakePool())
    async with app.router.lifespan_context(app):
        assert app.state.redis is created[0]
    assert seen_urls == ["redis://:TOPSECRETRD@h"] and created[0].closed


# ---- settings ----
BASE = {"DATABASE_URL": "postgresql://u:pw@h/db", "REDIS_URL": "redis://h", "ENVIRONMENT": "dev"}


@pytest.mark.parametrize("v", ["0", "-5"])
def test_rate_limit_must_be_positive(v: str) -> None:
    with pytest.raises(RuntimeError, match="rate_limit_per_minute"):
        load_settings({**BASE, "RATE_LIMIT_PER_MINUTE": v})
    with pytest.raises(ValueError):
        Settings(database_url="x", redis_url="y", environment="dev", rate_limit_per_minute=0)


def test_trusted_proxies_validated() -> None:
    s = load_settings({**BASE, "TRUSTED_PROXIES": "10.0.0.1, 172.16.0.0/12 ,fd00::/8"})
    assert s.trusted_proxies == {"10.0.0.1", "172.16.0.0/12", "fd00::/8"}
    with pytest.raises(RuntimeError) as ei:
        load_settings({**BASE, "TRUSTED_PROXIES": "10.0.0.1,not-an-ip-SECRETVAL"})
    assert "trusted_proxies" in str(ei.value).lower() and "SECRETVAL" not in str(ei.value)


# ---- integration ----
@pytest.mark.integration
async def test_hit_against_real_redis() -> None:
    url = os.environ.get("TEST_REDIS_URL")
    if not url:
        pytest.skip("TEST_REDIS_URL not set")
    import redis.asyncio as aioredis

    r = aioredis.from_url(url)
    ip = f"it-{uuid.uuid4().hex}"
    try:
        lim = RateLimiter(r, limit=2)
        assert (await lim.hit(ip)).allowed
        assert (await lim.hit(ip)).allowed
        third = await lim.hit(ip)
        assert not third.allowed and 1 <= third.retry_after <= 60
        keys = [k async for k in r.scan_iter(match=f"rl:{ip}:*")]
        assert keys
        for k in keys:
            assert 0 < await r.ttl(k) <= 60
    finally:
        async for k in r.scan_iter(match=f"rl:{ip}:*"):
            await r.delete(k)
        await r.aclose()
