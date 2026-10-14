"""Backtester data models."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from uuid import UUID, uuid4


class OrderSide(StrEnum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class TradeRecord:
    id: UUID = field(default_factory=uuid4)
    symbol: str = ""
    side: OrderSide = OrderSide.BUY
    entry_price: Decimal = field(default_factory=lambda: Decimal("0"))
    exit_price: Decimal | None = None
    size: Decimal = field(default_factory=lambda: Decimal("0"))
    entry_fee: Decimal = field(default_factory=lambda: Decimal("0"))
    exit_fee: Decimal = field(default_factory=lambda: Decimal("0"))
    entry_time: datetime = field(default_factory=datetime.now)
    exit_time: datetime | None = None
    pnl: Decimal = field(default_factory=lambda: Decimal("0"))
    pnl_pct: float = 0.0


@dataclass
class BacktestResult:
    strategy_id: str
    symbol: str
    timeframe: str
    exchange_id: str
    start: datetime
    end: datetime
    initial_capital: Decimal
    final_equity: Decimal
    cagr: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float  # fraction, e.g. 0.15 = 15%
    num_trades: int
    win_rate: float  # fraction
    profit_factor: float
    equity_curve: list[tuple[datetime, Decimal]]
    trade_log: list[TradeRecord]
