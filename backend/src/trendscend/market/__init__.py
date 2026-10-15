"""Market data package: OHLCV fetching and validation."""

from trendscend.market.fetcher import (
    DEFAULT_ASSETS,
    DEFAULT_TIMEFRAMES,
    SUPPORTED_EXCHANGES,
    TIMEFRAME_SECONDS,
    MarketDataFetcher,
)
from trendscend.market.validator import (
    CandleValidator,
    GapReport,
    OutlierReport,
)

__all__ = [
    "DEFAULT_ASSETS",
    "DEFAULT_TIMEFRAMES",
    "SUPPORTED_EXCHANGES",
    "TIMEFRAME_SECONDS",
    "MarketDataFetcher",
    "CandleValidator",
    "GapReport",
    "OutlierReport",
]
