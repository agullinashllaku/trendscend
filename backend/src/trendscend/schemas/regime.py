from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class RegimeType(StrEnum):
    TRENDING_UP = "trending_up"
    TRENDING_DOWN = "trending_down"
    RANGING = "ranging"
    HIGH_VOL_CHAOS = "high_vol_chaos"


class RegimeLabel(BaseModel):
    symbol: str
    timeframe: str
    regime: RegimeType
    confidence: float = Field(ge=0.0, le=1.0)
    adx: float | None = None
    vol_percentile: float | None = None
    ema_slope: float | None = None
    hurst_exponent: float | None = None
    labeled_at: datetime
