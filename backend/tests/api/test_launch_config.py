from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
import pytest

from tests.api.fakes import FakeRedis
from tests.api.test_app import FakePool as OkPool
from tests.api.test_app import make_settings
from trendscend.api import __main__ as launch
from trendscend.api.app import create_app


def test_uvicorn_options_disable_proxy_headers_and_access_log() -> None:
    opts = launch.uvicorn_options(make_settings(), {})
    assert opts == {
        "host": "127.0.0.1",
        "port": 8000,
        "factory": True,
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "access_log": False,
        "server_header": False,
        "date_header": True,
        "log_config": None,
        "lifespan": "on",
    }


def test_host_defaults_to_loopback() -> None:
    assert launch.uvicorn_options(make_settings(), {})["host"] == "127.0.0.1"


ALL_IFACES = "0.0.0.0"  # noqa: S104


@pytest.mark.parametrize("host", [ALL_IFACES, "::1", "::", "10.1.2.3", "localhost"])
def test_api_host_accepts_ip_or_localhost(host: str) -> None:
    assert launch.uvicorn_options(make_settings(), {"API_HOST": host})["host"] == host


@pytest.mark.parametrize("host", ["example.com", "", "1.2.3.4; rm", "999.1.1.1", "a b"])
def test_api_host_must_be_ip_or_localhost(host: str) -> None:
    with pytest.raises(RuntimeError, match="API_HOST"):
        launch.uvicorn_options(make_settings(), {"API_HOST": host})


@pytest.mark.parametrize("port", ["0", "65536", "-1", "abc", "", "80.5"])
def test_api_port_range(port: str) -> None:
    with pytest.raises(RuntimeError, match="API_PORT"):
        launch.uvicorn_options(make_settings(), {"API_PORT": port})


@pytest.mark.parametrize("port", ["1", "8080", "65535"])
def test_api_port_valid(port: str) -> None:
    assert launch.uvicorn_options(make_settings(), {"API_PORT": port})["port"] == int(port)


def test_options_error_names_variable_only() -> None:
    with pytest.raises(RuntimeError) as ei:
        launch.uvicorn_options(make_settings(), {"API_HOST": "evil-VALUE.example"})
    assert "API_HOST" in str(ei.value) and "evil-VALUE" not in str(ei.value)
    with pytest.raises(RuntimeError) as ei:
        launch.uvicorn_options(make_settings(), {"API_PORT": "secretPORTvalue"})
    assert "API_PORT" in str(ei.value) and "secretPORTvalue" not in str(ei.value)


def test_main_calls_uvicorn_run_with_options(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(launch.uvicorn, "run", lambda *a, **k: calls.append((a, k)))
    settings = make_settings()
    monkeypatch.setattr(launch, "load_settings", lambda: settings)
    monkeypatch.delenv("API_HOST", raising=False)
    monkeypatch.delenv("API_PORT", raising=False)
    launch.main()
    assert calls == [
        (("trendscend.api.app:create_app",), launch.uvicorn_options(settings, {})),
    ]


async def test_real_uvicorn_ignores_xff_and_logs_no_query(
    capfd: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    import uvicorn

    redis = FakeRedis()
    app = create_app(make_settings(), pool=OkPool(), redis=redis, trust_injected_pool=True)
    opts = launch.uvicorn_options(make_settings(), {})
    opts.update(port=0, factory=False)
    server = uvicorn.Server(uvicorn.Config(app, **opts))  # type: ignore[arg-type]
    caplog.set_level(logging.DEBUG)
    # The test's own HTTP client logs the URL it requests; only server-side output matters.
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.WARNING, logger="httpcore")
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(100):
            if server.started or task.done():
                break
            await asyncio.sleep(0.05)
        if task.done() or not server.started:
            pytest.skip("cannot bind a local socket in this sandbox")
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            r = await c.get("/readyz?token=SECRETQ", headers={"X-Forwarded-For": "203.0.113.9"})
            assert r.status_code == 200
            assert "server" not in r.headers
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
    keys = " ".join(redis.counts)
    assert "127.0.0.1" in keys  # limiter keyed on the TCP peer (/readyz is rate-limited)
    assert "203.0.113.9" not in keys
    out = capfd.readouterr()
    assert "SECRETQ" not in out.out + out.err + caplog.text
