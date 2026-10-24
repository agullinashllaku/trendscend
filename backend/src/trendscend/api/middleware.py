"""Pure ASGI middleware (no BaseHTTPMiddleware).

Order, outermost first: SecurityHeaders -> RequestId -> AccessLogAndErrors.
The innermost layer converts unhandled exceptions into a generic 500 so the
outer layers still decorate it with security headers and the request id.
"""

from __future__ import annotations

import json
import logging
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from trendscend.api.logging import LOGGER_NAME

SECURITY_HEADERS: dict[bytes, bytes] = {
    b"strict-transport-security": b"max-age=63072000; includeSubDomains",
    b"content-security-policy": b"default-src 'none'; frame-ancestors 'none'",
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"no-referrer",
    b"cache-control": b"no-store",
}

_logger = logging.getLogger(LOGGER_NAME)


def _set_headers(message: Message, new: dict[bytes, bytes]) -> None:
    kept = [(k, v) for k, v in message.get("headers", []) if k.lower() not in new]
    message["headers"] = [*kept, *new.items()]


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                _set_headers(message, SECURITY_HEADERS)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _resolve_request_id(scope: Scope) -> str:
    for name, value in scope.get("headers", []):
        if name == b"x-request-id":
            try:
                return str(uuid.UUID(value.decode("ascii")))
            except (ValueError, UnicodeDecodeError):
                break
    return str(uuid.uuid4())


class RequestIdMiddleware:
    """Accept a valid UUID X-Request-ID, otherwise generate one; always echo it."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _resolve_request_id(scope)
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                _set_headers(message, {b"x-request-id": request_id.encode("ascii")})
            await send(message)

        await self.app(scope, receive, send_wrapper)


class AccessLogAndErrorMiddleware:
    """Log allowlisted fields per request; turn unhandled errors into a generic 500."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = scope.get("state", {}).get("request_id", "")
        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_wrapper(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                status = message["status"]
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Exception text/traceback deliberately not logged: it can embed SQL
            # or user data. Only the allowlisted fields are recorded.
            status = 500
            if response_started:
                self._log(scope, request_id, status, started)
                raise
            body = json.dumps({"detail": "internal error", "request_id": request_id}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
        self._log(scope, request_id, status, started)

    @staticmethod
    def _log(scope: Scope, request_id: str, status: int, started: float) -> None:
        _logger.info(
            "request",
            extra={
                "request_id": request_id,
                "method": scope.get("method", ""),
                "path": str(scope.get("path", ""))[:200],  # scope path has no query string
                "status": status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            },
        )
