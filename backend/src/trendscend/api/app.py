"""FastAPI application factory.

An injected ``pool`` is installed on ``app.state.pool`` immediately and the
caller owns (and closes) it, but the lifespan still runs ``assert_safe_db_role``
on a connection from it unless ``trust_injected_pool=True`` (test-only, for
fakes). Without ``pool`` the lifespan creates the asyncpg pool, runs
``assert_safe_db_role`` and closes the pool on shutdown. In both cases the
dev-only ``allow_unsafe_db_role`` flag is the only way to skip the check.

Unparseable or unreachable ``DATABASE_URL``/``REDIS_URL`` raise a fixed
RuntimeError with no chained exception: driver messages can contain a fragment
of the password and uvicorn prints startup errors to stderr unfiltered.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import redis.asyncio as aioredis
from fastapi import FastAPI

from trendscend.api.logging import configure_logging
from trendscend.api.middleware import (
    AccessLogAndErrorMiddleware,
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
)
from trendscend.api.ratelimit import REDIS_SOCKET_TIMEOUT_S, RateLimitMiddleware
from trendscend.api.routes import health
from trendscend.api.settings import Settings, load_settings
from trendscend.api.startup import assert_safe_db_role
from trendscend.db.pool import create_pool


def create_app(
    settings: Settings | None = None,
    *,
    pool: Any | None = None,
    redis: Any | None = None,
    trust_injected_pool: bool = False,
) -> FastAPI:
    """Build the app. ``trust_injected_pool=True`` is TEST-ONLY: it skips the DB-role
    check for an injected ``pool`` (fakes, deliberately unsafe pools). Never use it
    in a worker or script that reuses this factory with a real pool."""
    cfg = settings if settings is not None else load_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        new_redis = None
        if redis is None:
            redis_failed = False
            try:
                new_redis = aioredis.from_url(
                    cfg.redis_url.get_secret_value(),
                    socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
                    socket_timeout=REDIS_SOCKET_TIMEOUT_S,
                )
            except Exception:
                # The parser's message embeds a fragment of the URL (password).
                redis_failed = True
            # Raised outside the except block: no __context__/__cause__ retained.
            if redis_failed:
                raise RuntimeError("Invalid REDIS_URL (value not shown)") from None
            app.state.redis = new_redis
        try:
            if pool is not None:
                if not trust_injected_pool:
                    await _check_role(pool)
                yield
                return
            async with _db_lifespan(app):
                yield
        finally:
            if new_redis is not None:
                await new_redis.aclose()

    async def _check_role(p: Any) -> None:
        # allow_unsafe_db_role is already rejected outside dev by Settings;
        # the environment check here is defence in depth.
        if not (cfg.allow_unsafe_db_role and cfg.environment == "dev"):
            async with p.acquire() as conn:
                await assert_safe_db_role(conn)

    @asynccontextmanager
    async def _db_lifespan(app: FastAPI) -> AsyncIterator[None]:
        new_pool = None
        try:
            new_pool = await create_pool(
                cfg.database_url.get_secret_value(), min_size=1, max_size=10
            )
        except Exception:
            # DSN parse/connect errors may embed URL fragments; never propagate them.
            new_pool = None
        # Raised outside the except block: no __context__/__cause__ retained.
        if new_pool is None:
            raise RuntimeError(
                "Database connection failed; check DATABASE_URL (value not shown)"
            ) from None
        try:
            await _check_role(new_pool)
            app.state.pool = new_pool
            yield
        finally:
            await new_pool.close()

    prod = cfg.environment == "prod"
    app = FastAPI(
        title="trendscend",
        lifespan=lifespan,
        docs_url=None if prod else "/docs",
        redoc_url=None if prod else "/redoc",
        openapi_url=None if prod else "/openapi.json",
    )
    app.state.settings = cfg
    if pool is not None:
        app.state.pool = pool
    if redis is not None:
        app.state.redis = redis
    # add_middleware prepends: last added is outermost. The rate limiter is
    # innermost so its 429/503 responses still get request id + security headers.
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(AccessLogAndErrorMiddleware)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(health.router)
    return app
