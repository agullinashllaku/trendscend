"""Per-bot state consumed by the Risk Manager every decision cycle."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field

from trendscend.schemas.risk import RiskStyle


class BotRiskState(BaseModel):
    """Snapshot of a bot's financial and operational state.

    Passed to :class:`~trendscend.risk.manager.RiskManager` together with
    a :class:`~trendscend.schemas.signals.TradeProposal` to produce a
    :class:`~trendscend.schemas.risk.RiskDecision`.

    Attributes:
        bot_id:             Unique bot identifier.
        style:              Risk style (conservative / balanced / aggressive).
        allocation:         Total budget the bot may use, in quote currency.
        current_equity:     Current portfolio value (cash + positions mark-to-market).
        equity_peak:        All-time high-water mark of current_equity.
        daily_start_equity: Equity recorded at 00:00 UTC of the current trading day.
        open_positions:     symbol → fraction of allocation currently deployed
                            (0.0 = flat, 1.0 = fully allocated to that symbol).
        trades_today:       Number of fills executed since 00:00 UTC today.
        is_paused:          True if the daily-loss limit was hit; blocks new entries.
        is_killed:          True if the drawdown kill switch was triggered.
        last_updated:       UTC timestamp of the most recent state update.
    """

    bot_id: UUID
    style: RiskStyle
    allocation: Decimal = Field(gt=Decimal("0"))
    current_equity: Decimal = Field(ge=Decimal("0"))
    equity_peak: Decimal = Field(ge=Decimal("0"))
    daily_start_equity: Decimal = Field(ge=Decimal("0"))
    open_positions: dict[str, float] = Field(default_factory=dict)
    trades_today: int = Field(ge=0, default=0)
    is_paused: bool = False
    is_killed: bool = False
    last_updated: datetime
