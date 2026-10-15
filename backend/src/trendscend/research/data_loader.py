"""Research DataLoader — synchronous and async entry points for OHLCV ingestion.

Wraps :class:`~trendscend.market.fetcher.MarketDataFetcher` with a simple
interface suited for research scripts and notebooks.

Usage examples::

    # Synchronous (scripts, CLI tools — no running event loop):
    loader = DataLoader(exchange)
    candles = loader.load("binance", "BTC/USDT", "1d", start_date)

    # Asynchronous (FastAPI routes, Jupyter notebooks, async tests):
    loader = DataLoader(exchange)
    candles = await loader.load_async("binance", "BTC/USDT", "1d", start_date)

.. warning::
    :meth:`load` uses ``asyncio.run`` internally and **cannot** be called from
    a running event loop (e.g. inside a FastAPI route, Jupyter notebook, or any
    ``async def`` function).  Use ``await loader.load_async(...)`` instead.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

from trendscend.market.fetcher import MarketDataFetcher
from trendscend.schemas.market import OHLCV


class DataLoader:
    """High-level OHLCV loader for research scripts and notebooks.

    Wraps :class:`~trendscend.market.fetcher.MarketDataFetcher` and exposes
    both a synchronous :meth:`load` and an asynchronous :meth:`load_async`
    entry point so callers can choose the appropriate interface for their
    execution context.

    Args:
        exchange: A ccxt (or ccxt.pro) exchange instance injected for
                  testability — the same object passed to
                  :class:`~trendscend.market.fetcher.MarketDataFetcher`.
    """

    def __init__(self, exchange: Any) -> None:
        self._fetcher = MarketDataFetcher(exchange)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
    ) -> list[OHLCV]:
        """Fetch all historical OHLCV candles synchronously.

        Suitable for pure synchronous scripts and CLI tools.

        .. warning::
            This method uses ``asyncio.run`` internally and **cannot** be
            called from a running event loop.  Use
            ``await loader.load_async(...)`` instead when inside an
            ``async def`` function, FastAPI route, or Jupyter notebook.

        Args:
            exchange_id: Exchange identifier, e.g. ``"binance"`` or
                         ``"bybit"``.
            symbol:      Trading pair, e.g. ``"BTC/USDT"``.
            timeframe:   Candle interval, e.g. ``"1d"`` or ``"1h"``.
            start_date:  UTC-aware :class:`~datetime.datetime` from which
                         to start fetching.

        Returns:
            List of :class:`~trendscend.schemas.market.OHLCV` instances
            sorted ascending by timestamp.

        Raises:
            ValueError: If ``exchange_id`` is not supported.
            RuntimeError: If called from within a running event loop.
        """
        return asyncio.run(self._load_async(exchange_id, symbol, timeframe, start_date))

    async def load_async(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
    ) -> list[OHLCV]:
        """Fetch all historical OHLCV candles asynchronously.

        Use this method inside ``async def`` functions, FastAPI routes, or
        Jupyter notebooks where an event loop is already running.

        Args:
            exchange_id: Exchange identifier, e.g. ``"binance"`` or
                         ``"bybit"``.
            symbol:      Trading pair, e.g. ``"BTC/USDT"``.
            timeframe:   Candle interval, e.g. ``"1d"`` or ``"1h"``.
            start_date:  UTC-aware :class:`~datetime.datetime` from which
                         to start fetching.

        Returns:
            List of :class:`~trendscend.schemas.market.OHLCV` instances
            sorted ascending by timestamp.

        Raises:
            ValueError: If ``exchange_id`` is not supported.
        """
        return await self._load_async(exchange_id, symbol, timeframe, start_date)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _load_async(
        self,
        exchange_id: str,
        symbol: str,
        timeframe: str,
        start_date: datetime,
    ) -> list[OHLCV]:
        """Internal coroutine that delegates to the fetcher."""
        return await self._fetcher.fetch_historical(
            exchange_id=exchange_id,
            symbol=symbol,
            timeframe=timeframe,
            start_date=start_date,
        )
