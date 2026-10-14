"""A1 Market Data Fetcher — ccxt-based OHLCV ingestion.

Wraps a ccxt exchange instance for testability (exchange is injected, never
constructed here).  All prices are converted to Decimal; all timestamps are
UTC-aware datetime objects.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from trendscend.schemas.market import OHLCV

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TIMEFRAME_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "1w": 604800,
}

SUPPORTED_EXCHANGES: set[str] = {"binance", "bybit"}

DEFAULT_ASSETS: list[str] = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "DOT/USDT",
]

DEFAULT_TIMEFRAMES: list[str] = ["1h", "4h", "1d"]

_CANDLE_LIMIT = 1000  # max candles per ccxt request


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _raw_to_ohlcv(raw: list[Any]) -> OHLCV:
    """Convert a single ccxt raw candle list to an OHLCV instance.

    ccxt format: [timestamp_ms, open, high, low, close, volume]
    All values may be strings or numbers.
    """
    ts_ms, o, h, lo, c, v = raw
    timestamp = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=UTC)
    return OHLCV(
        timestamp=timestamp,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal(str(v)),
    )


def _validate_exchange(exchange_id: str) -> None:
    """Raise ValueError if the exchange is not supported."""
    if exchange_id not in SUPPORTED_EXCHANGES:
        raise ValueError(
            f"Unsupported exchange: '{exchange_id}'. " f"Supported: {sorted(SUPPORTED_EXCHANGES)}"
        )


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class MarketDataFetcher:
    """Fetches OHLCV data from a ccxt exchange instance.

    The exchange object is injected to allow deterministic unit testing without
    real network calls.

    Args:
        exchange: A ccxt (or ccxt.pro) exchange instance with ``fetch_ohlcv``
                  as an async method.
    """

    def __init__(self, exchange: Any) -> None:
        self._exchange = exchange

    async def fetch_ohlcv(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[OHLCV]:
        """Fetch a single page of OHLCV candles (up to 1000).

        Args:
            exchange_id: Exchange identifier — must be in SUPPORTED_EXCHANGES.
            symbol: Trading pair, e.g. "BTC/USDT".
            timeframe: Candle interval, e.g. "1h".
            since: Optional inclusive lower bound (UTC-aware datetime).
            until: Optional exclusive upper bound (UTC-aware datetime).  Candles
                   with timestamp >= until are discarded from the response.

        Returns:
            List of OHLCV instances sorted ascending by timestamp.

        Raises:
            ValueError: If exchange_id is not in SUPPORTED_EXCHANGES.
        """
        _validate_exchange(exchange_id)

        since_ms: int | None = None
        if since is not None:
            since_ms = int(since.timestamp() * 1000)

        raw: list[list[Any]] = await self._exchange.fetch_ohlcv(
            symbol, timeframe, since=since_ms, limit=_CANDLE_LIMIT
        )

        candles = [_raw_to_ohlcv(r) for r in raw]
        candles.sort(key=lambda c: c.timestamp)

        if until is not None:
            candles = [c for c in candles if c.timestamp < until]

        return candles

    async def fetch_historical(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
        until: datetime | None = None,
    ) -> list[OHLCV]:
        """Fetch all historical OHLCV candles by paging through the exchange API.

        Pages by repeatedly calling ``fetch_ohlcv`` with the timestamp of the
        last received candle as the new ``since``.  Stops when the exchange
        returns no new candles (i.e. the returned batch contains no candles
        with timestamps strictly after the current frontier).

        Rate limiting: sleeps ``exchange.rateLimit / 1000`` seconds between
        pages as required by ccxt.

        Args:
            exchange_id: Exchange identifier — must be in SUPPORTED_EXCHANGES.
            symbol: Trading pair.
            timeframe: Candle interval.
            start_date: UTC-aware datetime from which to start fetching.
            until: Optional UTC-aware datetime upper bound (exclusive).

        Returns:
            Deduplicated list of OHLCV instances sorted ascending by timestamp.

        Raises:
            ValueError: If exchange_id is not in SUPPORTED_EXCHANGES.
        """
        _validate_exchange(exchange_id)

        all_candles: dict[datetime, OHLCV] = {}
        frontier: datetime = start_date

        while True:
            page = await self.fetch_ohlcv(
                exchange_id, symbol, timeframe, since=frontier, until=until
            )

            if not page:
                break

            # On the very first iteration, include everything from the page.
            if not all_candles:
                for c in page:
                    all_candles[c.timestamp] = c
            else:
                new_strictly_after = [c for c in page if c.timestamp not in all_candles]
                if not new_strictly_after:
                    # No progress — all candles already seen, stop paging.
                    break
                for c in new_strictly_after:
                    all_candles[c.timestamp] = c

            # Advance frontier to the latest timestamp seen.
            latest = max(all_candles.keys())
            if latest <= frontier:
                break
            frontier = latest

            # Stop if we have reached or passed the upper bound.
            if until is not None and frontier >= until:
                break

            rate_limit_s = getattr(self._exchange, "rateLimit", 0) / 1000.0
            if rate_limit_s > 0:
                await asyncio.sleep(rate_limit_s)

        result = sorted(all_candles.values(), key=lambda c: c.timestamp)
        if until is not None:
            result = [c for c in result if c.timestamp < until]
        return result
