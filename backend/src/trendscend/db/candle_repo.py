"""CandleRepository — async asyncpg-based repository for OHLCV candle data.

CandleRow is a plain dataclass (not Pydantic) for fast bulk insertion.
All financial values are Decimal to avoid floating-point rounding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import asyncpg

from trendscend.schemas.market import OHLCV


@dataclass
class CandleRow:
    """Lightweight dataclass for inserting candle data into the DB.

    Not Pydantic: avoids validation overhead during high-frequency bulk inserts.
    Financial fields use Decimal for correctness.
    """

    time: datetime
    exchange: str
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


async def insert_candles(
    conn: asyncpg.Connection,
    rows: list[CandleRow],
) -> int:
    """Insert candle rows using ON CONFLICT DO NOTHING for idempotency.

    Uses a single SQL statement with ``unnest`` arrays for atomic batch
    insertion. Returns the exact number of rows newly written (0 when all
    are duplicates).

    Args:
        conn: An open asyncpg connection.
        rows: List of CandleRow instances to insert.

    Returns:
        The number of newly inserted rows.
    """
    if not rows:
        return 0

    times = [r.time for r in rows]
    exchanges = [r.exchange for r in rows]
    symbols = [r.symbol for r in rows]
    timeframes = [r.timeframe for r in rows]
    opens = [r.open for r in rows]
    highs = [r.high for r in rows]
    lows = [r.low for r in rows]
    closes = [r.close for r in rows]
    volumes = [r.volume for r in rows]

    result = await conn.fetchval(
        """
        WITH inserted AS (
            INSERT INTO candles
                (time, exchange, symbol, timeframe, open, high, low, close, volume)
            SELECT
                unnest($1::timestamptz[]),
                unnest($2::text[]),
                unnest($3::text[]),
                unnest($4::text[]),
                unnest($5::numeric[]),
                unnest($6::numeric[]),
                unnest($7::numeric[]),
                unnest($8::numeric[]),
                unnest($9::numeric[])
            ON CONFLICT DO NOTHING
            RETURNING 1
        )
        SELECT count(*) FROM inserted
        """,
        times,
        exchanges,
        symbols,
        timeframes,
        opens,
        highs,
        lows,
        closes,
        volumes,
    )
    return int(result)


async def fetch_candles(
    conn: asyncpg.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> list[OHLCV]:
    """Fetch candles for a given exchange/symbol/timeframe within a time range.

    Args:
        conn: An open asyncpg connection.
        exchange: Exchange identifier (e.g. "binance").
        symbol: Trading pair (e.g. "BTC/USDT").
        timeframe: Candle interval (e.g. "1h", "4h").
        start: Inclusive lower bound (UTC datetime).
        end: Exclusive upper bound (UTC datetime).

    Returns:
        List of OHLCV instances sorted ascending by timestamp.
    """
    rows = await conn.fetch(
        """
        SELECT time, open, high, low, close, volume
        FROM candles
        WHERE exchange = $1
          AND symbol = $2
          AND timeframe = $3
          AND time >= $4
          AND time < $5
        ORDER BY time ASC
        """,
        exchange,
        symbol,
        timeframe,
        start,
        end,
    )
    return [
        OHLCV(
            timestamp=row["time"],
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
        )
        for row in rows
    ]


async def get_latest_candle_time(
    conn: asyncpg.Connection,
    exchange: str,
    symbol: str,
    timeframe: str,
) -> datetime | None:
    """Return the most recent candle timestamp for the given series, or None.

    Args:
        conn: An open asyncpg connection.
        exchange: Exchange identifier.
        symbol: Trading pair.
        timeframe: Candle interval.

    Returns:
        The latest ``time`` value in the candles table for this series, or
        ``None`` if no candles exist yet.
    """
    result: datetime | None = await conn.fetchval(
        """
        SELECT max(time)
        FROM candles
        WHERE exchange = $1
          AND symbol = $2
          AND timeframe = $3
        """,
        exchange,
        symbol,
        timeframe,
    )
    return result
