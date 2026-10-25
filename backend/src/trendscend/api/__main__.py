"""Committed, reviewed uvicorn launch configuration (``python -m trendscend.api``).

Why these options are fixed rather than left to the uvicorn CLI defaults:

* ``proxy_headers=False``: uvicorn's ProxyHeadersMiddleware would rewrite
  ``scope["client"]`` from X-Forwarded-For *before* our rate limiter runs,
  bypassing the app's own TRUSTED_PROXIES logic (``client_ip``). The app is the
  single place that decides which proxies to trust. ``forwarded_allow_ips`` is
  also emptied so no path trusts any peer even if the flag were flipped.
* ``access_log=False`` + ``log_config=None``: uvicorn's access log prints the
  full request line including the query string, bypassing the allowlist logger.
  ``configure_logging()`` owns logging; uvicorn installs no formatters.
* ``server_header=False``: do not advertise the server software.
* ``host`` defaults to loopback; only ``API_HOST`` (IP or ``localhost``) changes it.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Mapping

import uvicorn

from trendscend.api.settings import Settings, load_settings

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def _host(env: Mapping[str, str]) -> str:
    if "API_HOST" not in env:
        return DEFAULT_HOST
    raw = env["API_HOST"]
    if raw == "localhost":
        return raw
    try:
        ipaddress.ip_address(raw)
    except ValueError:
        raise RuntimeError("Invalid API_HOST: must be an IP address or 'localhost'") from None
    return raw


def _port(env: Mapping[str, str]) -> int:
    if "API_PORT" not in env:
        return DEFAULT_PORT
    raw = env["API_PORT"]
    if not (raw.isascii() and raw.isdigit() and 1 <= int(raw) <= 65535):
        raise RuntimeError("Invalid API_PORT: must be an integer between 1 and 65535")
    return int(raw)


def uvicorn_options(settings: Settings, env: Mapping[str, str] | None = None) -> dict[str, object]:
    """Keyword arguments for ``uvicorn.run``. Errors name the variable, never its value."""
    source = os.environ if env is None else env
    return {
        "host": _host(source),
        "port": _port(source),
        "factory": True,
        "proxy_headers": False,
        "forwarded_allow_ips": "",
        "access_log": False,
        "server_header": False,
        "date_header": True,
        "log_config": None,
        "lifespan": "on",
    }


def main() -> None:
    uvicorn.run("trendscend.api.app:create_app", **uvicorn_options(load_settings()))  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
