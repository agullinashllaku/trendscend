"""FastAPI application factory.

Testing seam: ``create_app(settings, pool=stub)`` installs ``pool`` on
``app.state.pool`` immediately and makes the lifespan a no-op (the caller owns
the pool). Without ``pool`` the lifespan creates the asyncpg pool, runs
``assert_safe_db_role`` and closes the pool on shutdown.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
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


def create_app(
    settings: Settings | None = None, *, pool: Any | None = None, redis: Any | None = None
) -> FastAPI:
    cfg = settings if settings is not None else load_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        new_redis = None
        if redis is None:
            new_redis = aioredis.from_url(
                cfg.redis_url.get_secret_value(),
                socket_connect_timeout=REDIS_SOCKET_TIMEOUT_S,
                socket_timeout=REDIS_SOCKET_TIMEOUT_S,
            )
            app.state.redis = new_redis
        try:
            if pool is not None:
                yield
                return
            async with _db_lifespan(app):
                yield
        finally:
            if new_redis is not None:
                await new_redis.aclose()

    @asynccontextmanager
    async def _db_lifespan(app: FastAPI) -> AsyncIterator[None]:
        new_pool = await asyncpg.create_pool(
            cfg.database_url.get_secret_value(), min_size=1, max_size=10
        )
        try:
            # allow_unsafe_db_role is already rejected outside dev by Settings;
            # the environment check here is defence in depth.
            if not (cfg.allow_unsafe_db_role and cfg.environment == "dev"):
                async with new_pool.acquire() as conn:
                    await assert_safe_db_role(conn)
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
