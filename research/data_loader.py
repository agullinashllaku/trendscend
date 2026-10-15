"""Research data loader — OHLCV parquet cache with ccxt fallback.

Cache layout::

    research/data/{exchange_id}_{symbol_safe}_{timeframe}.parquet

where ``symbol_safe = symbol.replace("/", "_")``.

Prices are stored as **strings** in the parquet file to preserve Decimal
precision; they are read back with ``Decimal(str(val))``.

Usage::

    from research.data_loader import DataLoader
    import asyncio, ccxt.async_support as ccxt

    exchange = ccxt.binance()
    loader = DataLoader(exchange)
    candles = asyncio.run(
        loader.load("binance", "BTC/USDT", "1d", datetime(2022, 1, 1, tzinfo=UTC))
    )
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Root of the project-level research/ directory (same dir as this file).
_RESEARCH_ROOT = Path(__file__).parent
_DATA_DIR = _RESEARCH_ROOT / "data"


class DataLoader:
    """Load OHLCV candles from a local parquet cache, fetching via ccxt on miss.

    Args:
        exchange: A ccxt (async) exchange instance used when the parquet cache
                  is absent.  The same instance is passed to
                  :class:`~trendscend.market.fetcher.MarketDataFetcher`.
        data_dir: Override the default ``research/data/`` cache directory
                  (useful for testing).
    """

    def __init__(self, exchange: Any, data_dir: Path | None = None) -> None:
        self._exchange = exchange
        self._data_dir = data_dir if data_dir is not None else _DATA_DIR

    def load(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
    ) -> list[Any]:
        """Load OHLCV candles, using the parquet cache when available.

        This is a synchronous wrapper around :meth:`_load_async`.

        Args:
            exchange_id: Exchange identifier, e.g. ``"binance"``.
            symbol:      Trading pair, e.g. ``"BTC/USDT"``.
            timeframe:   Bar duration string, e.g. ``"1d"``.
            start_date:  UTC-aware start datetime for the fetch (ignored when
                         the full cache file already exists).

        Returns:
            List of :class:`~trendscend.schemas.market.OHLCV` instances sorted
            ascending by timestamp.
        """
        return asyncio.run(self._load_async(exchange_id, symbol, timeframe, start_date))

    async def _load_async(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
    ) -> list[Any]:
        """Async implementation of :meth:`load`."""
        # Import here so the project-level script doesn't require backend on PATH
        # unless needed.
        from trendscend.schemas.market import OHLCV  # noqa: PLC0415

        parquet_path = self._cache_path(exchange_id, symbol, timeframe)

        if parquet_path.exists():
            logger.info("Cache hit: %s", parquet_path)
            return _load_parquet(parquet_path)

        logger.info("Cache miss — fetching from %s …", exchange_id)
        from trendscend.market.fetcher import MarketDataFetcher  # noqa: PLC0415

        fetcher = MarketDataFetcher(self._exchange)
        candles = await fetcher.fetch_historical(
            exchange_id=exchange_id,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
        )

        if candles:
            _save_parquet(candles, parquet_path)
            logger.info("Saved %d candles to %s", len(candles), parquet_path)

        return candles

    def _cache_path(self, exchange_id: str, symbol: str, timeframe: str) -> Path:
        symbol_safe = symbol.replace("/", "_")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        return self._data_dir / f"{exchange_id}_{symbol_safe}_{timeframe}.parquet"


# ---------------------------------------------------------------------------
# Parquet helpers
# ---------------------------------------------------------------------------


def _save_parquet(candles: list[Any], path: Path) -> None:
    """Serialise *candles* to a parquet file.

    Prices and volume are stored as strings to preserve ``Decimal`` precision.
    Timestamps are stored as UTC-aware datetime64 (pandas default).
    """
    import pandas as pd  # noqa: PLC0415

    rows = [
        {
            "timestamp": c.timestamp,
            "open": str(c.open),
            "high": str(c.high),
            "low": str(c.low),
            "close": str(c.close),
            "volume": str(c.volume),
        }
        for c in candles
    ]
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.to_parquet(path, index=False)


def _load_parquet(path: Path) -> list[Any]:
    """Deserialise candles from a parquet file back to OHLCV instances."""
    import pandas as pd  # noqa: PLC0415

    from trendscend.schemas.market import OHLCV  # noqa: PLC0415

    df = pd.read_parquet(path)
    candles: list[OHLCV] = []

    for _, row in df.iterrows():
        ts = row["timestamp"]
        # Ensure timezone-aware UTC datetime
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        elif hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

        candles.append(
            OHLCV(
                timestamp=ts,
                open=Decimal(str(row["open"])),
                high=Decimal(str(row["high"])),
                low=Decimal(str(row["low"])),
                close=Decimal(str(row["close"])),
                volume=Decimal(str(row["volume"])),
            )
        )

    candles.sort(key=lambda c: c.timestamp)
    return candles
