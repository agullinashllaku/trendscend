"""Scoring schemas for shadow-mode signal evaluation."""

from __future__ import annotations

from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field

from trendscend.schemas.news import NewsDirection


class NewsEventScore(BaseModel):
    """Realised price return after a NewsEvent, measured at 1h/4h/24h horizons."""

    event_id: UUID
    symbol: str
    direction: NewsDirection
    severity: int = Field(ge=1, le=5)
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    scored_at: AwareDatetime


class SmartMoneyScore(BaseModel):
    """Realised return after a smart-money positioning shift."""

    snapshot_at: AwareDatetime
    symbol: str
    net_long_pct_before: float = Field(ge=0.0, le=1.0)
    net_long_pct_after: float = Field(ge=0.0, le=1.0)
    return_1h: float | None = None
    return_4h: float | None = None
    return_24h: float | None = None
    scored_at: AwareDatetime
