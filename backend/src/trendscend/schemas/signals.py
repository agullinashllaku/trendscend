from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TradeAction(StrEnum):
    BUY = "buy"
    SELL = "sell"
    CLOSE = "close"
    HOLD = "hold"


class Signal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    strategy_id: str
    symbol: str
    target_exposure: float = Field(ge=-1.0, le=1.0)
    conviction: float = Field(ge=0.0, le=1.0)
    suggested_stop_pct: float | None = None
    timeframe: str
    generated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class TradeProposal(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    symbol: str
    action: TradeAction
    size_pct: float = Field(ge=0.0, le=1.0)
    entry_price_hint: float | None = None
    stop_pct: float | None = None
    rationale: str
    llm_adjustment_factor: float = Field(ge=0.0, le=1.25, default=1.0)
    llm_concerns: str | None = None
    contributing_signals: list[Signal]
    proposed_at: datetime
