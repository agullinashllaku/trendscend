"""Tests for CandleRepository — written before implementation (TDD).

Unit tests (no DB) cover CandleRow dataclass construction and validation.
Integration tests require TEST_DATABASE_URL and are skipped when it is not set.
"""

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from trendscend.db.candle_repo import CandleRow

# ---------------------------------------------------------------------------
# Skip marker for integration tests
# ---------------------------------------------------------------------------

SKIP_IF_NO_DB = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="TEST_DATABASE_URL not set — skipping DB integration tests",
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

BASE_TIME = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)


def make_candle_row(
    *,
    time: datetime | None = None,
    exchange: str = "binance",
    symbol: str = "TEST/USDT",
    timeframe: str = "1h",
    open: str = "50000",
    high: str = "51000",
    low: str = "49000",
    close: str = "50500",
    volume: str = "100",
) -> CandleRow:
    return CandleRow(
        time=time or BASE_TIME,
        exchange=exchange,
        symbol=symbol,
        timeframe=timeframe,
        open=Decimal(open),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(volume),
    )


# ---------------------------------------------------------------------------
# Unit tests — no DB required
# ---------------------------------------------------------------------------


class TestCandleRowDataclass:
    """CandleRow is a plain dataclass (not Pydantic) for insertion performance."""

    def test_construction_with_all_fields(self) -> None:
        row = make_candle_row()
        assert row.exchange == "binance"
        assert row.symbol == "TEST/USDT"
        assert row.timeframe == "1h"
        assert isinstance(row.open, Decimal)
        assert isinstance(row.high, Decimal)
        assert isinstance(row.low, Decimal)
        assert isinstance(row.close, Decimal)
        assert isinstance(row.volume, Decimal)

    def test_time_field_is_datetime(self) -> None:
        row = make_candle_row()
        assert isinstance(row.time, datetime)
        assert row.time.tzinfo is not None  # must be timezone-aware

    def test_decimal_precision_preserved(self) -> None:
        row = make_candle_row(open="50000.12345678", high="51000.00000001")
        assert row.open == Decimal("50000.12345678")
        assert row.high == Decimal("51000.00000001")

    def test_fields_are_individually_accessible(self) -> None:
        row = make_candle_row(
            exchange="bybit",
            symbol="TEST/BTC",
            timeframe="4h",
            close="99.99",
            volume="0.001",
        )
        assert row.exchange == "bybit"
        assert row.symbol == "TEST/BTC"
        assert row.timeframe == "4h"
        assert row.close == Decimal("99.99")
        assert row.volume == Decimal("0.001")

    def test_candle_row_is_not_pydantic(self) -> None:
        """CandleRow must be a dataclass, not a Pydantic model, for perf."""
        from dataclasses import fields

        assert len(fields(CandleRow)) == 9

    def test_candle_row_equality(self) -> None:
        row1 = make_candle_row()
        row2 = make_candle_row()
        assert row1 == row2

    def test_candle_row_different_times_not_equal(self) -> None:
        row1 = make_candle_row(time=BASE_TIME)
        row2 = make_candle_row(time=BASE_TIME + timedelta(hours=1))
        assert row1 != row2


# ---------------------------------------------------------------------------
# Integration tests — require TEST_DATABASE_URL
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def db_conn():  # type: ignore[return]
    """Connect to test DB and apply migration, then clean up after tests."""
    import asyncpg

    url = os.environ["TEST_DATABASE_URL"]
    conn = await asyncpg.connect(url)
    migration_sql = (
        Path(__file__).parent.parent.parent / "src/trendscend/db/migrations/001_candles.sql"
    )
    await conn.execute(migration_sql.read_text())
    yield conn
    await conn.execute("DELETE FROM candles WHERE symbol LIKE 'TEST/%'")
    await conn.close()


@SKIP_IF_NO_DB
@pytest.mark.integration
class TestCandleRepositoryIntegration:
    async def test_insert_candles_returns_count(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import insert_candles

        rows = [make_candle_row(time=BASE_TIME + timedelta(hours=i)) for i in range(5)]
        count = await insert_candles(db_conn, rows)
        assert count == 5

    async def test_insert_candles_idempotent(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        """Re-inserting the same (exchange, symbol, timeframe, time) must produce
        no duplicates — ON CONFLICT DO NOTHING."""
        from trendscend.db.candle_repo import insert_candles

        rows = [make_candle_row(time=BASE_TIME + timedelta(hours=100))]
        first = await insert_candles(db_conn, rows)
        second = await insert_candles(db_conn, rows)
        assert first == 1
        assert second == 0  # duplicate silently ignored

    async def test_fetch_candles_ascending_order(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import fetch_candles, insert_candles

        # Insert in reverse order to verify fetch returns ascending
        rows = [
            make_candle_row(time=BASE_TIME + timedelta(hours=i), symbol="TEST/ORDER")
            for i in range(3, 0, -1)
        ]
        await insert_candles(db_conn, rows)
        results = await fetch_candles(
            db_conn,
            exchange="binance",
            symbol="TEST/ORDER",
            timeframe="1h",
            start=BASE_TIME,
            end=BASE_TIME + timedelta(hours=10),
        )
        assert len(results) == 3
        for i in range(len(results) - 1):
            assert results[i].timestamp < results[i + 1].timestamp

    async def test_fetch_candles_filters_by_time_range(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import fetch_candles, insert_candles

        symbol = "TEST/RANGE"
        rows = [
            make_candle_row(time=BASE_TIME + timedelta(hours=i), symbol=symbol) for i in range(10)
        ]
        await insert_candles(db_conn, rows)
        results = await fetch_candles(
            db_conn,
            exchange="binance",
            symbol=symbol,
            timeframe="1h",
            start=BASE_TIME + timedelta(hours=3),
            end=BASE_TIME + timedelta(hours=7),
        )
        # Should return candles at hours 3, 4, 5, 6 (start inclusive, end exclusive)
        assert all(r.timestamp >= BASE_TIME + timedelta(hours=3) for r in results)
        assert all(r.timestamp < BASE_TIME + timedelta(hours=7) for r in results)

    async def test_fetch_candles_filters_by_exchange_symbol_timeframe(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import fetch_candles, insert_candles

        # Insert rows for two different exchanges
        rows_binance = [
            make_candle_row(
                time=BASE_TIME + timedelta(hours=i),
                exchange="binance",
                symbol="TEST/FILTER",
            )
            for i in range(3)
        ]
        rows_bybit = [
            make_candle_row(
                time=BASE_TIME + timedelta(hours=i),
                exchange="bybit",
                symbol="TEST/FILTER",
            )
            for i in range(3)
        ]
        await insert_candles(db_conn, rows_binance + rows_bybit)
        results = await fetch_candles(
            db_conn,
            exchange="binance",
            symbol="TEST/FILTER",
            timeframe="1h",
            start=BASE_TIME - timedelta(hours=1),
            end=BASE_TIME + timedelta(hours=10),
        )
        assert all(r.timestamp is not None for r in results)
        # Verify all returned are for binance only (fetch_candles filters)
        assert len(results) == 3

    async def test_get_latest_candle_time_returns_none_for_unknown(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import get_latest_candle_time

        result = await get_latest_candle_time(db_conn, "binance", "UNKNOWN/SYMBOL", "1h")
        assert result is None

    async def test_get_latest_candle_time_returns_max_timestamp(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import get_latest_candle_time, insert_candles

        symbol = "TEST/LATEST"
        rows = [
            make_candle_row(time=BASE_TIME + timedelta(hours=i), symbol=symbol) for i in range(5)
        ]
        await insert_candles(db_conn, rows)
        result = await get_latest_candle_time(db_conn, "binance", symbol, "1h")
        assert result is not None
        assert result == BASE_TIME + timedelta(hours=4)

    async def test_batch_insert_1000_rows(self, db_conn) -> None:  # type: ignore[no-untyped-def]
        from trendscend.db.candle_repo import insert_candles

        symbol = "TEST/BATCH"
        rows = [
            make_candle_row(
                time=BASE_TIME + timedelta(hours=i),
                symbol=symbol,
                open=str(50000 + i),
                close=str(50000 + i),
            )
            for i in range(1000)
        ]
        count = await insert_candles(db_conn, rows)
        assert count == 1000
