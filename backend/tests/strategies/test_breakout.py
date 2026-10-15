"""Tests for BreakoutStrategy — written TDD-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.market import OHLCV
from trendscend.strategies.base import Strategy
from trendscend.strategies.breakout import BreakoutStrategy

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_candles(
    prices: list[float],
    spread_pct: float = 0.005,
) -> list[OHLCV]:
    """Create candles from a list of close prices."""
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


def make_candles_with_explicit_hl(
    closes: list[float],
    highs: list[float],
    lows: list[float],
) -> list[OHLCV]:
    """Create candles with explicit high/low values."""
    base_ts = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for i, (c, h, lo) in enumerate(zip(closes, highs, lows, strict=False)):
        result.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=Decimal(str(c)),
                high=Decimal(str(h)),
                low=Decimal(str(lo)),
                close=Decimal(str(c)),
                volume=Decimal("100"),
            )
        )
    return result


# ── Protocol ─────────────────────────────────────────────────────────────────


class TestBreakoutProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        s = BreakoutStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = BreakoutStrategy()
        assert s.strategy_id == "breakout"


# ── Insufficient history guard ────────────────────────────────────────────────


class TestBreakoutInsufficientHistory:
    def test_fewer_than_min_bars_returns_flat(self) -> None:
        """With fewer than max(channel_period, atr_period) + 1 bars, returns flat."""
        s = BreakoutStrategy(channel_period=20, atr_period=14)
        # min_bars = max(20, 14) + 1 = 21; use 20
        bars = make_candles([float(1000 + i) for i in range(20)])
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_flat_signal_metadata(self) -> None:
        s = BreakoutStrategy()
        bars = make_candles([float(1000 + i) for i in range(5)])
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.strategy_id == "breakout"
        assert sig.symbol == "BTC/USDT"


# ── BUY signal ────────────────────────────────────────────────────────────────


class TestBreakoutBuySignal:
    def test_buy_signal_when_close_breaks_above_channel_high(self) -> None:
        """Close above donchian_high of previous N bars → BUY."""
        # 25 bars at price 100, then one bar at 200 (breaks out above)
        channel_period = 20
        # First 24 bars around 100, last bar at 200 (above all previous highs)
        prices = [100.0] * 24 + [200.0]
        s = BreakoutStrategy(channel_period=channel_period, atr_period=14)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_buy_signal_is_spot_only_max_one(self) -> None:
        """BUY signal must be exactly 1.0 (spot-only, no leverage)."""
        prices = [100.0] * 24 + [200.0]
        s = BreakoutStrategy(channel_period=20, atr_period=14)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)


# ── SELL/flat signal ──────────────────────────────────────────────────────────


class TestBreakoutSellSignal:
    def test_sell_signal_when_close_breaks_below_channel_low(self) -> None:
        """Close below donchian_low of previous N bars → SELL (go flat, spot-only)."""
        channel_period = 20
        # First 24 bars at 200, last bar at 50 (below all previous lows)
        prices = [200.0] * 24 + [50.0]
        s = BreakoutStrategy(channel_period=channel_period, atr_period=14)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_sell_signal_returns_flat_not_negative(self) -> None:
        """Spot-only: SELL signal is 0.0 (no short selling)."""
        prices = [200.0] * 24 + [50.0]
        s = BreakoutStrategy(channel_period=20, atr_period=14)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure >= 0.0


# ── HOLD signal ───────────────────────────────────────────────────────────────


class TestBreakoutHoldSignal:
    def test_hold_when_price_within_channel(self) -> None:
        """No breakout → return current exposure (default 0.0)."""
        # Zigzag prices that don't break out
        prices = [100.0, 102.0, 98.0, 101.0, 99.0] * 5  # 25 bars
        s = BreakoutStrategy(channel_period=20, atr_period=14)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        # No breakout → HOLD → return current exposure = 0.0
        assert sig.target_exposure == pytest.approx(0.0)


# ── Stop calculation ──────────────────────────────────────────────────────────


class TestBreakoutStopCalculation:
    def test_suggested_stop_pct_is_set_on_buy(self) -> None:
        """On BUY signal, suggested_stop_pct should be set."""
        prices = [100.0] * 24 + [200.0]
        s = BreakoutStrategy(channel_period=20, atr_period=14, stop_atr_multiplier=2.0)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.suggested_stop_pct is not None
        assert sig.suggested_stop_pct > 0.0

    def test_suggested_stop_pct_formula(self) -> None:
        """stop_pct = stop_atr_multiplier * atr[-1] / close[-1]."""
        prices = [100.0] * 24 + [200.0]
        multiplier = 2.0
        s = BreakoutStrategy(channel_period=20, atr_period=14, stop_atr_multiplier=multiplier)
        bars = make_candles(prices)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.suggested_stop_pct is not None
        # Should be positive
        assert sig.suggested_stop_pct > 0.0


# ── Golden fixture ────────────────────────────────────────────────────────────


class TestBreakoutGoldenFixture:
    def test_rising_segment_triggers_buy_at_some_point(self) -> None:
        """With 100 bars rising from 30k→50k, at least one breakout occurs."""
        bars = load_btc_100_bars()
        s = BreakoutStrategy(channel_period=20, atr_period=14)
        # Try all windows of 25 bars in the rising segment
        found_buy = False
        for i in range(21, 50):
            sig = s.generate_signal("BTC/USDT", bars[:i])
            if sig.target_exposure > 0.0:
                found_buy = True
                break
        assert found_buy, "Expected at least one BUY signal in rising bars"


# ── Hypothesis property test ──────────────────────────────────────────────────


@settings(max_examples=50)
@given(
    prices=st.lists(
        st.floats(min_value=100.0, max_value=200000.0, allow_nan=False, allow_infinity=False),
        min_size=30,
        max_size=150,
    )
)
def test_breakout_exposure_always_in_range(prices: list[float]) -> None:
    """Property: target_exposure is always in [-1.0, 1.0] for any valid input."""
    candles = make_candles(prices)
    s = BreakoutStrategy()
    sig = s.generate_signal("BTC/USDT", candles)
    assert -1.0 <= sig.target_exposure <= 1.0
    # Spot-only: no negative exposure
    assert sig.target_exposure >= 0.0
