"""Liveness and readiness probes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    try:
        async with request.app.state.pool.acquire(timeout=2) as conn:
            await conn.fetchval("SELECT 1", timeout=2)
        redis = getattr(request.app.state, "redis", None)
        if redis is not None:
            await redis.ping()
    except Exception:
        return JSONResponse({"status": "unavailable"}, status_code=503)
    return JSONResponse({"status": "ok"})
