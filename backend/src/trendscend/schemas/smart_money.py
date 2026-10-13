from datetime import datetime

from pydantic import BaseModel, Field


class SmartMoneyAssetState(BaseModel):
    symbol: str
    net_long_pct: float = Field(ge=0.0, le=1.0)
    net_short_pct: float = Field(ge=0.0, le=1.0)
    change_24h: float
    concentration: float = Field(ge=0.0, le=1.0)


class SmartMoneyState(BaseModel):
    per_asset: dict[str, SmartMoneyAssetState]
    vetted_wallet_count: int = Field(ge=0)
    computed_at: datetime
