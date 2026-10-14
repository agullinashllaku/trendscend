"""Candle data quality validator.

Provides:
  - CandleValidator.check_gaps    — detects missing bars in a time series
  - CandleValidator.check_outliers — detects price/volume anomalies
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta

from trendscend.market.fetcher import TIMEFRAME_SECONDS
from trendscend.schemas.market import OHLCV

# ---------------------------------------------------------------------------
# Report dataclasses
# ---------------------------------------------------------------------------


@dataclass
class GapReport:
    """Represents a gap (missing bars) between two consecutive candles.

    Attributes:
        symbol: Trading pair, e.g. "BTC/USDT".
        timeframe: Candle interval, e.g. "1h".
        gap_start: Timestamp of the last candle *before* the gap.
        gap_end: Timestamp of the first candle *after* the gap.
        missing_bars: Number of expected candles absent in [gap_start, gap_end].
    """

    symbol: str
    timeframe: str
    gap_start: datetime
    gap_end: datetime
    missing_bars: int


@dataclass
class OutlierReport:
    """Represents a single data quality anomaly in a candle series.

    Attributes:
        symbol: Trading pair.
        timeframe: Candle interval.
        timestamp: Timestamp of the anomalous candle.
        reason: One of "price_spike", "negative_price", "zero_volume",
                "volume_spike".
        value: The numeric value that triggered the flag.
    """

    symbol: str
    timeframe: str
    timestamp: datetime
    reason: str
    value: float


# ---------------------------------------------------------------------------
# Spike thresholds per timeframe
# ---------------------------------------------------------------------------

_PRICE_SPIKE_THRESHOLD: dict[str, float] = {
    "1h": 0.20,
    "4h": 0.30,
    "1d": 0.50,
}

_DEFAULT_PRICE_SPIKE_THRESHOLD = 0.20
_VOLUME_SPIKE_MULTIPLIER = 10
_VOLUME_LOOKBACK = 50


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


class CandleValidator:
    """Stateless validator for OHLCV candle series.

    All methods are pure functions of their inputs — no DB access, no state.
    """

    def check_gaps(
        self,
        candles: list[OHLCV],
        symbol: str,
        timeframe: str,
    ) -> list[GapReport]:
        """Detect missing bars in a candle series.

        A gap is identified when the time difference between two consecutive
        candles exceeds ``TIMEFRAME_SECONDS[timeframe] * 1.5``.

        Args:
            candles: Sorted (ascending) list of OHLCV candles.
            symbol: Trading pair label used in the report.
            timeframe: Candle interval key, e.g. "1h".

        Returns:
            List of GapReport instances, one per detected gap.
        """
        if len(candles) < 2:
            return []

        interval_s = TIMEFRAME_SECONDS.get(timeframe, 3600)
        threshold = timedelta(seconds=interval_s * 1.5)
        interval = timedelta(seconds=interval_s)

        reports: list[GapReport] = []
        for prev, curr in zip(candles, candles[1:], strict=False):
            diff = curr.timestamp - prev.timestamp
            if diff > threshold:
                # Number of bars that *should* exist between prev and curr
                # (exclusive on both ends).
                missing = round(diff / interval) - 1
                reports.append(
                    GapReport(
                        symbol=symbol,
                        timeframe=timeframe,
                        gap_start=prev.timestamp,
                        gap_end=curr.timestamp,
                        missing_bars=missing,
                    )
                )

        return reports

    def check_outliers(
        self,
        candles: list[OHLCV],
        symbol: str,
        timeframe: str,
    ) -> list[OutlierReport]:
        """Detect price and volume anomalies in a candle series.

        Rules applied (in order, each candle checked independently):
        1. ``close <= 0``              → ``negative_price``
        2. ``volume == 0``             → ``zero_volume``
        3. ``|close/prev_close - 1|``
           > 0.20 (1h) / 0.30 (4h) / 0.50 (1d)  → ``price_spike``
        4. ``volume > 10 * median(last 50 volumes)`` → ``volume_spike``

        Args:
            candles: List of OHLCV candles (any order, but typically ascending).
            symbol: Trading pair label used in the report.
            timeframe: Candle interval key; governs price-spike threshold.

        Returns:
            List of OutlierReport instances.
        """
        reports: list[OutlierReport] = []
        spike_threshold = _PRICE_SPIKE_THRESHOLD.get(timeframe, _DEFAULT_PRICE_SPIKE_THRESHOLD)

        for i, candle in enumerate(candles):
            close_f = float(candle.close)
            vol_f = float(candle.volume)

            # Rule 1: negative or zero price
            if candle.close <= 0:
                reports.append(
                    OutlierReport(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                        reason="negative_price",
                        value=close_f,
                    )
                )

            # Rule 2: zero volume
            if candle.volume == 0:
                reports.append(
                    OutlierReport(
                        symbol=symbol,
                        timeframe=timeframe,
                        timestamp=candle.timestamp,
                        reason="zero_volume",
                        value=vol_f,
                    )
                )

            # Rule 3: price spike (requires a previous candle with positive close)
            if i > 0:
                prev_close = float(candles[i - 1].close)
                if prev_close > 0 and candle.close > 0:
                    change = abs(close_f / prev_close - 1)
                    if change > spike_threshold:
                        reports.append(
                            OutlierReport(
                                symbol=symbol,
                                timeframe=timeframe,
                                timestamp=candle.timestamp,
                                reason="price_spike",
                                value=change,
                            )
                        )

            # Rule 4: volume spike (uses last 50 volumes, median)
            lookback_start = max(0, i - _VOLUME_LOOKBACK)
            lookback_vols = [
                float(candles[j].volume)
                for j in range(lookback_start, i)
                if float(candles[j].volume) > 0
            ]
            if lookback_vols and vol_f > 0:
                med = statistics.median(lookback_vols)
                if med > 0 and vol_f > _VOLUME_SPIKE_MULTIPLIER * med:
                    reports.append(
                        OutlierReport(
                            symbol=symbol,
                            timeframe=timeframe,
                            timestamp=candle.timestamp,
                            reason="volume_spike",
                            value=vol_f,
                        )
                    )

        return reports
