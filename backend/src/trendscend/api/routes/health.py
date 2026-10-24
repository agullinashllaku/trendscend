"""Liveness and readiness probes."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from trendscend.api.isolation import public_route

router = APIRouter()


@router.get("/healthz")
@public_route("liveness probe")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


async def _check_db(request: Request) -> None:
    async with request.app.state.pool.acquire(timeout=2) as conn:
        await conn.fetchval("SELECT 1", timeout=2)


@router.get("/readyz")
@public_route("readiness probe, no user data")
async def readyz(request: Request) -> JSONResponse:
    try:
        await asyncio.wait_for(_check_db(request), 2)
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            await asyncio.wait_for(redis.ping(), 2)
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
