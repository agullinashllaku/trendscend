from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel


class OHLCV(BaseModel):
    timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


class MarketSnapshot(BaseModel):
    exchange: str
    symbol: str
    timeframe: str
    candles: list[OHLCV]
    last_trade_price: Decimal
    bid: Decimal | None = None
    ask: Decimal | None = None
    funding_rate: Decimal | None = None
    open_interest: Decimal | None = None
    fetched_at: datetime
    is_stale: bool = False
