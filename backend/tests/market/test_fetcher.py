"""Tests for MarketDataFetcher — all network calls are mocked."""

from __future__ import annotations

import json
import pathlib
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from trendscend.market.fetcher import MarketDataFetcher

# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

FIXTURE_PATH = pathlib.Path(__file__).parent / "fixtures" / "binance_btcusdt_1h_sample.json"


def load_fixture() -> list[list]:
    """Load the 50-candle ccxt-format fixture."""
    return json.loads(FIXTURE_PATH.read_text())


def _make_exchange(raw_candles: list[list] | None = None) -> MagicMock:
    """Build a minimal mock ccxt exchange object."""
    exchange = MagicMock()
    exchange.id = "binance"
    exchange.rateLimit = 100  # ms
    exchange.fetch_ohlcv = AsyncMock(return_value=raw_candles or load_fixture())
    return exchange


# ---------------------------------------------------------------------------
# fetch_ohlcv — basic contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ohlcv_returns_list_of_ohlcv() -> None:
    """fetch_ohlcv must return a list of OHLCV instances."""
    from trendscend.schemas.market import OHLCV

    exchange = _make_exchange()
    fetcher = MarketDataFetcher(exchange=exchange)

    candles = await fetcher.fetch_ohlcv("binance", "BTC/USDT", "1h")

    assert isinstance(candles, list)
    assert len(candles) == 50
    for c in candles:
        assert isinstance(c, OHLCV)


@pytest.mark.asyncio
async def test_fetch_ohlcv_prices_are_decimal() -> None:
    """All price/volume fields must be Decimal, not float."""
    exchange = _make_exchange()
    fetcher = MarketDataFetcher(exchange=exchange)

    candles = await fetcher.fetch_ohlcv("binance", "BTC/USDT", "1h")

    for c in candles:
        assert isinstance(c.open, Decimal), f"open is {type(c.open)}"
        assert isinstance(c.high, Decimal), f"high is {type(c.high)}"
        assert isinstance(c.low, Decimal), f"low is {type(c.low)}"
        assert isinstance(c.close, Decimal), f"close is {type(c.close)}"
        assert isinstance(c.volume, Decimal), f"volume is {type(c.volume)}"


@pytest.mark.asyncio
async def test_fetch_ohlcv_timestamps_are_utc_aware() -> None:
    """All timestamps must be timezone-aware UTC datetime objects."""
    exchange = _make_exchange()
    fetcher = MarketDataFetcher(exchange=exchange)

    candles = await fetcher.fetch_ohlcv("binance", "BTC/USDT", "1h")

    for c in candles:
        assert isinstance(c.timestamp, datetime)
        assert c.timestamp.tzinfo is not None
        assert c.timestamp.tzinfo == UTC or c.timestamp.utcoffset().total_seconds() == 0


@pytest.mark.asyncio
async def test_fetch_ohlcv_sorted_ascending() -> None:
    """Candles must be sorted ascending by timestamp."""
    # Shuffle the fixture slightly by reversing it — mock returns reversed order.
    raw = list(reversed(load_fixture()))
    exchange = _make_exchange(raw_candles=raw)
    fetcher = MarketDataFetcher(exchange=exchange)

    candles = await fetcher.fetch_ohlcv("binance", "BTC/USDT", "1h")

    timestamps = [c.timestamp for c in candles]
    assert timestamps == sorted(timestamps)


@pytest.mark.asyncio
async def test_fetch_ohlcv_first_candle_correct_values() -> None:
    """Spot-check the first candle's values match the fixture."""
    exchange = _make_exchange()
    fetcher = MarketDataFetcher(exchange=exchange)

    candles = await fetcher.fetch_ohlcv("binance", "BTC/USDT", "1h")

    first = candles[0]
    # Fixture row 0: [1704067200000, "42000.0", "42061.72", "41965.35", "42058.56", "189.2843"]
    assert first.timestamp == datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    assert first.open == Decimal("42000.0")
    assert first.high == Decimal("42061.72")
    assert first.low == Decimal("41965.35")
    assert first.close == Decimal("42058.56")
    assert first.volume == Decimal("189.2843")


# ---------------------------------------------------------------------------
# fetch_ohlcv — unsupported exchange
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_ohlcv_raises_for_unsupported_exchange() -> None:
    """Unsupported exchange IDs must raise ValueError."""
    exchange = _make_exchange()
    exchange.id = "kraken"
    fetcher = MarketDataFetcher(exchange=exchange)

    with pytest.raises(ValueError, match="kraken"):
        await fetcher.fetch_ohlcv("kraken", "BTC/USDT", "1h")


# ---------------------------------------------------------------------------
# fetch_historical — pagination
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fetch_historical_pages_until_no_new_candles() -> None:
    """fetch_historical must call fetch_ohlcv in a loop and stop when exhausted."""
    raw = load_fixture()  # 50 candles; last ts = 2024-01-03 01:00 UTC

    # First call returns 50 candles, second call returns empty (signals end).
    exchange = _make_exchange()
    exchange.fetch_ohlcv = AsyncMock(side_effect=[raw, []])
    fetcher = MarketDataFetcher(exchange=exchange)

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    # Use a far-future until so all 50 fixture candles are included.
    until = datetime(2024, 1, 5, 0, 0, 0, tzinfo=UTC)

    candles = await fetcher.fetch_historical("binance", "BTC/USDT", "1h", start, until)

    assert len(candles) == 50
    # exchange.fetch_ohlcv should have been called at least twice
    assert exchange.fetch_ohlcv.call_count >= 2


@pytest.mark.asyncio
async def test_fetch_historical_deduplicates_overlapping_pages() -> None:
    """Overlapping results from consecutive pages must not produce duplicates."""
    raw = load_fixture()  # 50 candles; last ts = 1704243600000

    # Second page starts with the same last candle from first page (overlap) + 0 new
    # Returning empty [] on second call means no overlap duplication issue to test;
    # but we simulate partial overlap: second page has 1 candle already in first page.
    overlap_raw = [raw[-1]]  # just the last candle of the first page
    exchange = _make_exchange()
    exchange.fetch_ohlcv = AsyncMock(side_effect=[raw, overlap_raw, []])
    fetcher = MarketDataFetcher(exchange=exchange)

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    until = datetime(2024, 1, 5, 0, 0, 0, tzinfo=UTC)

    candles = await fetcher.fetch_historical("binance", "BTC/USDT", "1h", start, until)

    # Should have exactly 50 unique candles despite the overlap
    timestamps = [c.timestamp for c in candles]
    assert len(timestamps) == len(set(timestamps)), "Duplicate timestamps found"
    assert len(candles) == 50


@pytest.mark.asyncio
async def test_fetch_historical_raises_for_unsupported_exchange() -> None:
    """Unsupported exchange must raise ValueError in fetch_historical too."""
    exchange = _make_exchange()
    exchange.id = "ftx"
    fetcher = MarketDataFetcher(exchange=exchange)

    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    with pytest.raises(ValueError, match="ftx"):
        await fetcher.fetch_historical("ftx", "BTC/USDT", "1h", start)
