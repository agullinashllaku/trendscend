from datetime import datetime
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from .market import MarketSnapshot
from .news import NewsState
from .regime import RegimeLabel
from .risk import RiskDecision
from .signals import Signal, TradeProposal
from .smart_money import SmartMoneyState


class DecisionRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    cycle_triggered_by: str
    triggered_at: datetime
    market_snapshots: list[MarketSnapshot]
    regime_labels: list[RegimeLabel]
    news_state: NewsState | None = None
    smart_money_state: SmartMoneyState | None = None
    signals: list[Signal] = Field(default_factory=list)
    proposal: TradeProposal | None = None
    risk_decision: RiskDecision | None = None
    order_ids: list[str] = Field(default_factory=list)
    completed_at: datetime | None = None
    error: str | None = None
