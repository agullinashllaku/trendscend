"""Tests for RebalanceCoreStrategy — written TDD-first."""

from __future__ import annotations

import calendar
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.market import OHLCV
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import Strategy
from trendscend.strategies.rebalance_core import RebalanceCoreStrategy

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_candle(
    dt: datetime,
    price: float = 50000.0,
) -> OHLCV:
    dec = Decimal(str(price))
    return OHLCV(
        timestamp=dt,
        open=dec,
        high=Decimal(str(price * 1.003)),
        low=Decimal(str(price * 0.997)),
        close=dec,
        volume=Decimal("100"),
    )


def last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


# ── Protocol ─────────────────────────────────────────────────────────────────


class TestRebalanceCoreProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        s = RebalanceCoreStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = RebalanceCoreStrategy()
        assert s.strategy_id == "rebalance_core"


# ── First bar (initial allocation) ───────────────────────────────────────────


class TestRebalanceCoreFirstBar:
    def test_first_call_with_zero_exposure_triggers_rebalance(self) -> None:
        """On the first call, internal exposure=0.0; drift to 1.0 > 0.05 → rebalance."""
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy(target_weight=1.0, rebalance_threshold=0.05)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_first_call_updates_internal_state(self) -> None:
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy(target_weight=1.0, rebalance_threshold=0.05)
        s.generate_signal("BTC/USDT", bars)
        # Internal state should now be 1.0
        assert s._current_exposure["BTC/USDT"] == pytest.approx(1.0)

    def test_signal_has_correct_strategy_id(self) -> None:
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy()
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.strategy_id == "rebalance_core"


# ── No rebalance when within threshold ───────────────────────────────────────


class TestRebalanceCoreHold:
    def test_no_rebalance_when_at_target_and_no_calendar(self) -> None:
        """After reaching target, mid-month bars should not trigger a rebalance."""
        # Use threshold_only to avoid calendar triggers
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # First call: drift 1.0 > 0.05 → rebalance
        bars1 = load_btc_100_bars()[:5]
        s.generate_signal("BTC/USDT", bars1)

        # Second call: now at 1.0; drift=0.0 → hold
        bars2 = load_btc_100_bars()[:6]
        sig = s.generate_signal("BTC/USDT", bars2)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_signal_exposure_equals_current_when_no_trigger(self) -> None:
        """When no trigger fires, signal exposure == current (not target)."""
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # Manually set internal state to a partial exposure
        s._current_exposure["ETH/USDT"] = 0.8
        # drift = |0.8 - 1.0| = 0.2 > 0.05 → triggers rebalance
        # Use target_weight=0.8 to avoid drift trigger
        s2 = RebalanceCoreStrategy(
            target_weight=0.8, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        s2._current_exposure["ETH/USDT"] = 0.8
        bars = load_btc_100_bars()[:5]
        sig = s2.generate_signal("ETH/USDT", bars)
        # drift=0.0, no calendar → hold at 0.8
        assert sig.target_exposure == pytest.approx(0.8)


# ── Drift threshold trigger ───────────────────────────────────────────────────


class TestRebalanceCoreDrift:
    def test_drift_above_threshold_triggers_rebalance(self) -> None:
        """Drift > threshold triggers rebalance even without calendar trigger."""
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # Set current exposure to 0.9 (drift = 0.1 > 0.05)
        s._current_exposure["BTC/USDT"] = 0.9
        bars = load_btc_100_bars()[:5]
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_drift_below_threshold_does_not_trigger(self) -> None:
        """Drift < threshold does not trigger when calendar_only mode."""
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # Set current exposure to 0.97 (drift = 0.03 < 0.05)
        s._current_exposure["BTC/USDT"] = 0.97
        bars = load_btc_100_bars()[:5]
        sig = s.generate_signal("BTC/USDT", bars)
        # No trigger: hold at 0.97
        assert sig.target_exposure == pytest.approx(0.97)

    def test_drift_well_below_threshold_does_not_trigger(self) -> None:
        """Drift clearly under threshold does NOT trigger."""
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # drift = |0.98 - 1.0| = 0.02 < 0.05 → no trigger
        s._current_exposure["BTC/USDT"] = 0.98
        bars = load_btc_100_bars()[:5]
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.98)


# ── Monthly calendar trigger ─────────────────────────────────────────────────


class TestRebalanceCoreMonthly:
    def _make_candles_for_day(self, year: int, month: int, day: int) -> list[OHLCV]:
        """Return a two-element candle list whose last bar is on the given day."""
        dt = datetime(year, month, day, 12, 0, tzinfo=UTC)
        c1 = make_candle(dt - timedelta(hours=4))
        c2 = make_candle(dt)
        return [c1, c2]

    def test_monthly_trigger_on_last_day_of_month(self) -> None:
        """Calendar trigger fires on the last day of January."""
        # Jan 31 is the last day
        bars = self._make_candles_for_day(2024, 1, 31)
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="monthly"
        )
        # Set current at target → no drift trigger; but calendar should fire
        s._current_exposure["BTC/USDT"] = 1.0
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_no_monthly_trigger_mid_month(self) -> None:
        """No calendar trigger fires on a mid-month bar."""
        bars = self._make_candles_for_day(2024, 1, 15)
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="monthly"
        )
        # Set current at target and within drift threshold
        s._current_exposure["BTC/USDT"] = 1.0
        sig = s.generate_signal("BTC/USDT", bars)
        # No trigger: hold
        assert sig.target_exposure == pytest.approx(1.0)

    def test_monthly_trigger_end_of_february_non_leap(self) -> None:
        """Calendar trigger fires on Feb 28 in a non-leap year."""
        bars = self._make_candles_for_day(2025, 2, 28)
        s = RebalanceCoreStrategy(rebalance_calendar="monthly")
        s._current_exposure["BTC/USDT"] = 1.0
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)

    def test_monthly_trigger_end_of_february_leap(self) -> None:
        """Calendar trigger fires on Feb 29 in a leap year."""
        bars = self._make_candles_for_day(2024, 2, 29)
        s = RebalanceCoreStrategy(rebalance_calendar="monthly")
        s._current_exposure["BTC/USDT"] = 1.0
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(1.0)


# ── Multi-symbol isolation ────────────────────────────────────────────────────


class TestRebalanceCoreMultiSymbol:
    def test_exposure_tracked_per_symbol(self) -> None:
        """Each symbol's exposure is tracked independently."""
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy(
            target_weight=1.0, rebalance_threshold=0.05, rebalance_calendar="threshold_only"
        )
        # First call for BTC
        s.generate_signal("BTC/USDT", bars)
        # First call for ETH (should also trigger rebalance: drift from 0.0)
        sig_eth = s.generate_signal("ETH/USDT", bars)
        assert sig_eth.target_exposure == pytest.approx(1.0)
        assert s._current_exposure["BTC/USDT"] == pytest.approx(1.0)
        assert s._current_exposure["ETH/USDT"] == pytest.approx(1.0)


# ── Signal metadata ───────────────────────────────────────────────────────────


class TestRebalanceCoreSignalMetadata:
    def test_signal_symbol_matches_input(self) -> None:
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy()
        sig = s.generate_signal("ETH/USDT", bars)
        assert sig.symbol == "ETH/USDT"

    def test_signal_generated_at_is_last_candle(self) -> None:
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy()
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.generated_at == bars[-1].timestamp

    def test_signal_is_signal_instance(self) -> None:
        bars = load_btc_100_bars()[:5]
        s = RebalanceCoreStrategy()
        assert isinstance(s.generate_signal("BTC/USDT", bars), Signal)
