from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class NewsEventType(StrEnum):
    EXPLOIT_HACK = "exploit_hack"
    EXCHANGE_INSOLVENCY_OR_OUTAGE = "exchange_insolvency_or_outage"
    DELISTING = "delisting"
    LISTING = "listing"
    REGULATORY_NEGATIVE = "regulatory_negative"
    REGULATORY_POSITIVE = "regulatory_positive"
    ETF_FLOW_OR_APPROVAL = "etf_flow_or_approval"
    MACRO_RATES_CPI = "macro_rates_cpi"
    STABLECOIN_DEPEG = "stablecoin_depeg"
    PROTOCOL_UPGRADE = "protocol_upgrade"
    TOKEN_UNLOCK = "token_unlock"  # noqa: S105
    PARTNERSHIP_OR_ADOPTION = "partnership_or_adoption"
    RUMOUR_UNVERIFIED = "rumour_unverified"
    OTHER = "other"


class NewsDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class NewsEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source: str
    url: str
    published_at: datetime
    assets: list[str]
    event_type: NewsEventType
    severity: int = Field(ge=1, le=5)
    direction: NewsDirection
    half_life_hours: float = Field(gt=0)
    confidence: float = Field(ge=0.0, le=1.0)
    is_new_information: bool
    llm_model: str
    raw_hash: str
    classified_at: datetime


class AssetNewsState(BaseModel):
    symbol: str
    recent_events: list[NewsEvent]
    max_severity_24h: int = Field(ge=0, le=5)
    net_direction: NewsDirection
    has_severe_negative: bool


class NewsState(BaseModel):
    per_asset: dict[str, AssetNewsState]
    computed_at: datetime
