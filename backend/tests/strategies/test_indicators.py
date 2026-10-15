"""Tests for trendscend.indicators.trend — written TDD-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.strategies.conftest import load_btc_100_bars
from trendscend.indicators.trend import atr, donchian_high, donchian_low, ema
from trendscend.schemas.market import OHLCV

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_candles(
    prices: list[float],
    spread_pct: float = 0.005,
) -> list[OHLCV]:
    """Create candles from a list of close prices (open == close for simplicity)."""
    base_ts = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for i, p in enumerate(prices):
        dec = Decimal(str(p))
        result.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=dec,
                high=Decimal(str(p * (1 + spread_pct))),
                low=Decimal(str(p * (1 - spread_pct))),
                close=dec,
                volume=Decimal("100"),
            )
        )
    return result


# ── EMA tests ─────────────────────────────────────────────────────────────────


class TestEma:
    def test_returns_empty_when_fewer_candles_than_period(self) -> None:
        candles = make_candles([1.0, 2.0, 3.0])
        assert ema(candles, 5) == []

    def test_returns_empty_when_candles_equal_period_minus_one(self) -> None:
        candles = make_candles([1.0, 2.0, 3.0, 4.0])
        assert ema(candles, 5) == []

    def test_returns_single_value_when_candles_equal_period(self) -> None:
        prices = [10.0, 11.0, 12.0, 13.0, 14.0]
        candles = make_candles(prices)
        result = ema(candles, 5)
        assert len(result) == 1
        # With exactly period bars, EMA == SMA
        expected = sum(prices) / 5
        assert abs(result[0] - expected) < 1e-9

    def test_output_length_matches_expected(self) -> None:
        candles = make_candles(list(range(1, 21)))  # 20 bars
        result = ema(candles, 5)
        # Length = len(candles) - period + 1 = 20 - 5 + 1 = 16
        assert len(result) == 16

    def test_ema_values_smoothed_upward_on_rising_series(self) -> None:
        prices = list(range(1, 21))  # 1..20
        candles = make_candles(prices)
        result = ema(candles, 5)
        # EMA of rising series should be strictly increasing
        for i in range(1, len(result)):
            assert result[i] > result[i - 1]

    def test_ema_on_fixture_100_bars(self) -> None:
        bars = load_btc_100_bars()
        result = ema(bars, 20)
        assert len(result) == 100 - 20 + 1
        # All values should be in the price range 30000–52000
        assert all(28000 <= v <= 54000 for v in result)

    def test_ema_period_1_equals_close_prices(self) -> None:
        prices = [10.0, 20.0, 30.0]
        candles = make_candles(prices)
        result = ema(candles, 1)
        assert len(result) == 3
        assert abs(result[0] - 10.0) < 1e-9
        assert abs(result[1] - 20.0) < 1e-9
        assert abs(result[2] - 30.0) < 1e-9


# ── ATR tests ─────────────────────────────────────────────────────────────────


class TestAtr:
    def test_returns_empty_when_not_enough_candles(self) -> None:
        candles = make_candles([1.0, 2.0, 3.0])
        # Need period + 1 = 5 + 1 = 6 bars for period=5
        assert atr(candles, 5) == []

    def test_returns_empty_when_candles_equal_period(self) -> None:
        candles = make_candles([1.0] * 5)
        assert atr(candles, 5) == []

    def test_returns_single_value_when_candles_equal_period_plus_one(self) -> None:
        candles = make_candles([10.0] * 6)
        result = atr(candles, 5)
        assert len(result) == 1

    def test_atr_output_length(self) -> None:
        candles = make_candles(list(range(1, 21)))  # 20 bars
        result = atr(candles, 5)
        # true_ranges has len-1 = 19 values; ATR seed uses first 5; then 14 more
        # Length = 19 - 5 + 1 = 15
        assert len(result) == 15

    def test_atr_positive_values(self) -> None:
        candles = make_candles([100.0, 102.0, 98.0, 105.0, 99.0, 103.0, 101.0])
        result = atr(candles, 5)
        assert all(v > 0 for v in result)

    def test_atr_flat_series_equals_spread(self) -> None:
        """For a flat price series with constant spread_pct,
        ATR should ≈ price * 2 * spread_pct (high - low = 2 * spread_pct * price)."""
        price = 100.0
        spread_pct = 0.01
        candles = make_candles([price] * 10, spread_pct=spread_pct)
        result = atr(candles, 5)
        expected_tr = price * 2 * spread_pct
        for v in result:
            assert abs(v - expected_tr) < 1e-6


# ── Donchian tests ────────────────────────────────────────────────────────────


class TestDonchianHigh:
    def test_returns_empty_when_fewer_candles_than_period(self) -> None:
        candles = make_candles([1.0, 2.0, 3.0])
        assert donchian_high(candles, 5) == []

    def test_returns_single_value_when_candles_equal_period(self) -> None:
        candles = make_candles([10.0, 20.0, 15.0, 25.0, 5.0])
        result = donchian_high(candles, 5)
        assert len(result) == 1
        # highest high = 25 * 1.005 (spread 0.5%)
        assert result[0] == pytest.approx(25.0 * 1.005, rel=1e-6)

    def test_output_length(self) -> None:
        candles = make_candles(list(range(1, 11)))  # 10 bars
        result = donchian_high(candles, 3)
        assert len(result) == 10 - 3 + 1

    def test_rolling_max(self) -> None:
        # prices: 1,2,3,4,5 — with spread the highs are price * 1.005
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        candles = make_candles(prices, spread_pct=0.0)
        result = donchian_high(candles, 3)
        # Window 0: bars[0..2] highs = 1,2,3 → max = 3
        assert result[0] == pytest.approx(3.0)
        # Window 1: bars[1..3] highs = 2,3,4 → max = 4
        assert result[1] == pytest.approx(4.0)
        # Window 2: bars[2..4] highs = 3,4,5 → max = 5
        assert result[2] == pytest.approx(5.0)


class TestDonchianLow:
    def test_returns_empty_when_fewer_candles_than_period(self) -> None:
        candles = make_candles([1.0, 2.0, 3.0])
        assert donchian_low(candles, 5) == []

    def test_returns_single_value_when_candles_equal_period(self) -> None:
        candles = make_candles([10.0, 20.0, 15.0, 25.0, 5.0])
        result = donchian_low(candles, 5)
        assert len(result) == 1
        assert result[0] == pytest.approx(5.0 * 0.995, rel=1e-6)

    def test_output_length(self) -> None:
        candles = make_candles(list(range(1, 11)))  # 10 bars
        result = donchian_low(candles, 3)
        assert len(result) == 10 - 3 + 1

    def test_rolling_min(self) -> None:
        prices = [5.0, 4.0, 3.0, 2.0, 1.0]
        candles = make_candles(prices, spread_pct=0.0)
        result = donchian_low(candles, 3)
        assert result[0] == pytest.approx(3.0)
        assert result[1] == pytest.approx(2.0)
        assert result[2] == pytest.approx(1.0)
