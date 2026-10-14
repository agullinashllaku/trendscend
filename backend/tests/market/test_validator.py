"""Tests for CandleValidator — gap detection and outlier detection."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trendscend.market.validator import CandleValidator, GapReport, OutlierReport
from trendscend.schemas.market import OHLCV

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_candle(
    ts: datetime,
    close: float = 42000.0,
    volume: float = 200.0,
    open_: float | None = None,
    high: float | None = None,
    low: float | None = None,
) -> OHLCV:
    c = close
    o = open_ if open_ is not None else c
    h = high if high is not None else c
    lo = low if low is not None else c
    return OHLCV(
        timestamp=ts,
        open=Decimal(str(o)),
        high=Decimal(str(h)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=Decimal(str(volume)),
    )


def _make_hourly_series(n: int, start_ts: datetime, close: float = 42000.0) -> list[OHLCV]:
    """Build n consecutive 1h candles with stable prices."""
    return [_make_candle(start_ts + timedelta(hours=i), close=close) for i in range(n)]


# ---------------------------------------------------------------------------
# check_gaps
# ---------------------------------------------------------------------------


def test_check_gaps_detects_missing_bar() -> None:
    """check_gaps must flag a gap when one 1h bar is missing."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(10, start)

    # Remove candle at index 5 to create a 2-hour gap
    del candles[5]

    validator = CandleValidator()
    gaps = validator.check_gaps(candles, "BTC/USDT", "1h")

    assert len(gaps) == 1
    gap = gaps[0]
    assert isinstance(gap, GapReport)
    assert gap.symbol == "BTC/USDT"
    assert gap.timeframe == "1h"
    assert gap.missing_bars == 1


def test_check_gaps_returns_empty_for_gapless_series() -> None:
    """No gaps must be reported for a perfect consecutive 1h series."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(50, start)

    validator = CandleValidator()
    gaps = validator.check_gaps(candles, "BTC/USDT", "1h")

    assert gaps == []


def test_check_gaps_detects_multiple_gaps() -> None:
    """Multiple non-consecutive gaps must each produce a GapReport."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(20, start)

    # Remove two separate bars to create two gaps
    del candles[15]
    del candles[5]

    validator = CandleValidator()
    gaps = validator.check_gaps(candles, "BTC/USDT", "1h")

    assert len(gaps) == 2


def test_check_gaps_gap_start_end_timestamps() -> None:
    """GapReport timestamps must correctly bracket the gap."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(10, start)

    # Remove index 5: gap between candle[4] (04:00) and candle[5] (06:00)
    del candles[5]

    validator = CandleValidator()
    gaps = validator.check_gaps(candles, "BTC/USDT", "1h")

    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.gap_start == start + timedelta(hours=4)
    assert gap.gap_end == start + timedelta(hours=6)


def test_check_gaps_returns_empty_list_for_empty_series() -> None:
    """Empty candle list must not raise and must return empty list."""
    validator = CandleValidator()
    assert validator.check_gaps([], "BTC/USDT", "1h") == []


def test_check_gaps_single_candle_no_gaps() -> None:
    """Single candle must not produce a gap."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = [_make_candle(start)]
    validator = CandleValidator()
    assert validator.check_gaps(candles, "BTC/USDT", "1h") == []


# ---------------------------------------------------------------------------
# check_outliers
# ---------------------------------------------------------------------------


def test_check_outliers_flags_price_spike_1h() -> None:
    """A >20% close-to-close change in a 1h series must be flagged as price_spike."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start, close=42000.0)

    # Replace candle at index 3 with a 25% spike
    candles[3] = _make_candle(start + timedelta(hours=3), close=42000.0 * 1.25)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    spike_reports = [o for o in outliers if o.reason == "price_spike"]
    assert len(spike_reports) >= 1


def test_check_outliers_no_spike_below_threshold_1h() -> None:
    """A 10% change in a 1h series (below 20% threshold) must not be flagged."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start, close=42000.0)

    # 10% change — below the 20% threshold for 1h
    candles[3] = _make_candle(start + timedelta(hours=3), close=42000.0 * 1.10)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    spike_reports = [o for o in outliers if o.reason == "price_spike"]
    assert len(spike_reports) == 0


def test_check_outliers_flags_negative_close() -> None:
    """A candle with close <= 0 must be flagged as negative_price."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start)
    candles[2] = _make_candle(start + timedelta(hours=2), close=-1.0)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    neg_reports = [o for o in outliers if o.reason == "negative_price"]
    assert len(neg_reports) == 1
    assert neg_reports[0].timestamp == start + timedelta(hours=2)


def test_check_outliers_flags_zero_close() -> None:
    """A candle with close == 0 must be flagged as negative_price."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start)
    candles[1] = _make_candle(start + timedelta(hours=1), close=0.0)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    neg_reports = [o for o in outliers if o.reason == "negative_price"]
    assert len(neg_reports) == 1


def test_check_outliers_flags_zero_volume() -> None:
    """A candle with volume == 0 must be flagged as zero_volume."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start)
    candles[2] = _make_candle(start + timedelta(hours=2), volume=0.0)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    vol_reports = [o for o in outliers if o.reason == "zero_volume"]
    assert len(vol_reports) == 1
    assert vol_reports[0].timestamp == start + timedelta(hours=2)


def test_check_outliers_flags_volume_spike() -> None:
    """A volume > 10x median of the last 50 candles must be flagged as volume_spike."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    # 50 candles with volume=200, then one candle with volume=2001 (>10x)
    candles = _make_hourly_series(50, start, close=42000.0)
    spike_ts = start + timedelta(hours=50)
    spike_candle = _make_candle(spike_ts, close=42000.0, volume=200.0 * 11)  # 11x median
    candles.append(spike_candle)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    vol_spike = [o for o in outliers if o.reason == "volume_spike"]
    assert len(vol_spike) == 1
    assert vol_spike[0].timestamp == spike_ts


def test_check_outliers_returns_empty_for_clean_data() -> None:
    """Clean data with no anomalies must return an empty list."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(50, start)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    assert outliers == []


def test_check_outliers_outlier_report_fields() -> None:
    """OutlierReport must have the correct fields set."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(5, start)
    candles[2] = _make_candle(start + timedelta(hours=2), close=-5.0)

    validator = CandleValidator()
    outliers = validator.check_outliers(candles, "BTC/USDT", "1h")

    assert len(outliers) >= 1
    report = outliers[0]
    assert isinstance(report, OutlierReport)
    assert report.symbol == "BTC/USDT"
    assert report.timeframe == "1h"
    assert isinstance(report.timestamp, datetime)
    assert isinstance(report.reason, str)
    assert isinstance(report.value, float)


def test_check_outliers_price_spike_4h_threshold() -> None:
    """A >30% change in a 4h series must be flagged; 25% must not."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    interval = timedelta(hours=4)
    candles = [_make_candle(start + interval * i, close=42000.0) for i in range(5)]

    # 25% change — below 30% threshold for 4h
    candles[3] = _make_candle(start + interval * 3, close=42000.0 * 1.25)

    validator = CandleValidator()
    outliers_25 = validator.check_outliers(candles, "BTC/USDT", "4h")
    assert not any(o.reason == "price_spike" for o in outliers_25)

    # 35% change — above 30% threshold for 4h
    candles[3] = _make_candle(start + interval * 3, close=42000.0 * 1.35)
    outliers_35 = validator.check_outliers(candles, "BTC/USDT", "4h")
    assert any(o.reason == "price_spike" for o in outliers_35)


def test_check_outliers_price_spike_1d_threshold() -> None:
    """A >50% change in a 1d series must be flagged; 40% must not."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    interval = timedelta(days=1)
    candles = [_make_candle(start + interval * i, close=42000.0) for i in range(5)]

    # 40% change — below 50% threshold for 1d
    candles[3] = _make_candle(start + interval * 3, close=42000.0 * 1.40)

    validator = CandleValidator()
    outliers_40 = validator.check_outliers(candles, "BTC/USDT", "1d")
    assert not any(o.reason == "price_spike" for o in outliers_40)

    # 55% change — above 50% threshold for 1d
    candles[3] = _make_candle(start + interval * 3, close=42000.0 * 1.55)
    outliers_55 = validator.check_outliers(candles, "BTC/USDT", "1d")
    assert any(o.reason == "price_spike" for o in outliers_55)


# ---------------------------------------------------------------------------
# Unknown timeframe handling
# ---------------------------------------------------------------------------


def test_check_gaps_raises_on_unknown_timeframe() -> None:
    """check_gaps must raise ValueError for an unknown timeframe string."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(10, start)

    validator = CandleValidator()
    with pytest.raises(ValueError, match="Unknown timeframe: '3h'"):
        validator.check_gaps(candles, "BTC/USDT", "3h")


def test_check_outliers_raises_on_unknown_timeframe() -> None:
    """check_outliers must raise ValueError for an unknown timeframe string."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC)
    candles = _make_hourly_series(10, start)

    validator = CandleValidator()
    with pytest.raises(ValueError, match="Unknown timeframe: '2h'"):
        validator.check_outliers(candles, "BTC/USDT", "2h")
