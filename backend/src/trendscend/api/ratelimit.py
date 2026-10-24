"""Per-IP fixed-window rate limiting backed by Redis. Fails closed.

Keys are ``rl:{ip}:{minute_epoch}`` only: no user ids, tokens or request
content ever enter Redis keys. INCR and EXPIRE run inside one Lua script so a
crash between the two cannot leave a key without a TTL.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

from trendscend.api.logging import LOGGER_NAME

WINDOW_SECONDS = 60
UNKNOWN_CLIENT = "unknown"
REDIS_SOCKET_TIMEOUT_S = 1
LIMITER_TIMEOUT_S = 2
_BYPASS_PATHS = frozenset({"/healthz"})
_MAX_XFF_BYTES = 2048

# Sets the TTL whenever the key has none (covers creation and any stray
# immortal key), then returns {count, ttl}.
_SCRIPT = """
local c = redis.call('INCR', KEYS[1])
local t = redis.call('TTL', KEYS[1])
if t < 0 then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
  t = tonumber(ARGV[1])
end
return {c, t}
"""

_logger = logging.getLogger(LOGGER_NAME)

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


@lru_cache(maxsize=32)
def _networks(entries: frozenset[str]) -> tuple[IPNetwork, ...]:
    return tuple(ipaddress.ip_network(e, strict=False) for e in entries)


def _is_trusted(ip: IPAddress, nets: tuple[IPNetwork, ...]) -> bool:
    return any(ip.version == n.version and ip in n for n in nets)


def _parse_ip(raw: str) -> IPAddress | None:
    if "%" in raw:  # IPv6 zone id: never a legitimate client address
        return None
    try:
        ip = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


def client_ip(scope: Scope, trusted_proxies: frozenset[str]) -> str:
    """Peer address, or the right-most untrusted X-Forwarded-For entry.

    XFF is only consulted when the immediate peer is a trusted proxy. Any
    malformed entry encountered on the walk falls back to the peer address.
    """
    client = scope.get("client")
    if not client or not client[0]:
        return UNKNOWN_CLIENT
    peer_raw = str(client[0])
    peer = _parse_ip(peer_raw)
    if peer is None:
        return peer_raw[:64]
    peer_s = str(peer)
    nets = _networks(trusted_proxies)
    if not _is_trusted(peer, nets):
        return peer_s
    parts: list[str] = []
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            if len(value) > _MAX_XFF_BYTES:
                return peer_s
            try:
                parts.append(value.decode("ascii"))
            except UnicodeDecodeError:
                return peer_s
    entries = [e for chunk in parts for e in chunk.split(",")]
    for raw in reversed(entries):
        ip = _parse_ip(raw)
        if ip is None:
            return peer_s
        if not _is_trusted(ip, nets):
            return str(ip)
    return peer_s


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after: int


class RateLimiter:
    def __init__(self, redis: Any, limit: int, clock: Callable[[], float] = time.time) -> None:
        self._redis = redis
        self._limit = limit
        self._clock = clock

    async def hit(self, key: str) -> RateLimitResult:
        """Count one request for ``key`` (an IP); raises on Redis errors."""
        minute = int(self._clock() // WINDOW_SECONDS)
        count, ttl = await self._redis.eval(_SCRIPT, 1, f"rl:{key}:{minute}", WINDOW_SECONDS)
        retry_after = max(1, min(int(ttl), WINDOW_SECONDS))
        return RateLimitResult(allowed=int(count) <= self._limit, retry_after=retry_after)


async def _json(send: Send, status: int, detail: str, extra: list[tuple[bytes, bytes]]) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class RateLimitMiddleware:
    """Per-IP limiter. Install inside RequestId/SecurityHeaders so 429/503 get both."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in _BYPASS_PATHS:
            await self.app(scope, receive, send)
            return
        try:
            state = scope["app"].state
            settings = state.settings
            ip = client_ip(scope, settings.trusted_proxies)
            limiter = RateLimiter(state.redis, settings.rate_limit_per_minute)
            result = await asyncio.wait_for(limiter.hit(ip), timeout=LIMITER_TIMEOUT_S)
        except Exception:
            # Fail closed. Exception text may embed the Redis DSN: not logged.
            _logger.error("rate limiter unavailable")
            await _json(send, 503, "service unavailable", [])
            return
        if not result.allowed:
            await _json(
                send,
                429,
                "rate limit exceeded",
                [(b"retry-after", str(result.retry_after).encode())],
            )
            return
        await self.app(scope, receive, send)
