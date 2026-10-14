"""Tests for HoldBaselineStrategy — written TDD-first."""

from __future__ import annotations

import pytest

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import Strategy
from trendscend.strategies.hold_baseline import HoldBaselineStrategy


class TestHoldBaselineProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        """HoldBaselineStrategy must satisfy the Strategy Protocol."""
        s = HoldBaselineStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = HoldBaselineStrategy()
        assert s.strategy_id == "hold_baseline"


class TestHoldBaselineSignal:
    @pytest.fixture()
    def strategy(self) -> HoldBaselineStrategy:
        return HoldBaselineStrategy()

    @pytest.fixture()
    def bars(self) -> list:
        return load_btc_100_bars()

    def test_returns_signal_instance(self, strategy: HoldBaselineStrategy, bars: list) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert isinstance(result, Signal)

    def test_target_exposure_is_one(self, strategy: HoldBaselineStrategy, bars: list) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert result.target_exposure == 1.0

    def test_conviction_is_one(self, strategy: HoldBaselineStrategy, bars: list) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert result.conviction == 1.0

    def test_strategy_id_on_signal(self, strategy: HoldBaselineStrategy, bars: list) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert result.strategy_id == "hold_baseline"

    def test_symbol_on_signal(self, strategy: HoldBaselineStrategy, bars: list) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert result.symbol == "BTC/USDT"

    def test_generated_at_equals_last_candle_timestamp(
        self, strategy: HoldBaselineStrategy, bars: list
    ) -> None:
        result = strategy.generate_signal("BTC/USDT", bars)
        assert result.generated_at == bars[-1].timestamp


class TestHoldBaselineStateless:
    """Strategy must be stateless: same inputs → same output."""

    def test_first_call_returns_full_long(self) -> None:
        bars = load_btc_100_bars()
        s = HoldBaselineStrategy()
        result = s.generate_signal("BTC/USDT", bars)
        assert result.target_exposure == 1.0

    def test_subsequent_calls_still_return_full_long(self) -> None:
        bars = load_btc_100_bars()
        s = HoldBaselineStrategy()
        for _ in range(5):
            result = s.generate_signal("BTC/USDT", bars)
            assert result.target_exposure == 1.0

    def test_same_inputs_same_output(self) -> None:
        bars = load_btc_100_bars()
        s = HoldBaselineStrategy()
        r1 = s.generate_signal("BTC/USDT", bars)
        r2 = s.generate_signal("BTC/USDT", bars)
        # IDs will differ (UUID) but all content fields must match
        assert r1.target_exposure == r2.target_exposure
        assert r1.conviction == r2.conviction
        assert r1.generated_at == r2.generated_at

    def test_rising_bars_returns_full_long(self) -> None:
        bars = load_btc_100_bars()[:50]  # rising segment
        s = HoldBaselineStrategy()
        result = s.generate_signal("BTC/USDT", bars)
        assert result.target_exposure == 1.0

    def test_declining_bars_returns_full_long(self) -> None:
        bars = load_btc_100_bars()[75:]  # declining segment
        s = HoldBaselineStrategy()
        result = s.generate_signal("BTC/USDT", bars)
        assert result.target_exposure == 1.0

    def test_single_candle_works(self) -> None:
        bars = load_btc_100_bars()[:1]
        s = HoldBaselineStrategy()
        result = s.generate_signal("BTC/USDT", bars)
        assert result.target_exposure == 1.0
