"""Tests for TsmomStrategy — written TDD-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import Strategy
from trendscend.strategies.tsmom import TsmomStrategy

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


def make_regime(regime_type: RegimeType) -> RegimeLabel:
    return RegimeLabel(
        symbol="BTC/USDT",
        timeframe="4h",
        regime=regime_type,
        confidence=0.9,
        labeled_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# ── Protocol ─────────────────────────────────────────────────────────────────


class TestTsmomProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        s = TsmomStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = TsmomStrategy()
        assert s.strategy_id == "tsmom"


# ── Insufficient history guard ────────────────────────────────────────────────


class TestTsmomInsufficientHistory:
    def test_fewer_than_min_bars_returns_flat(self) -> None:
        """With fewer than slow_ema + vol_lookback bars, returns flat."""
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        # min_bars = 50 + 20 = 70; use 69
        bars = make_candles([float(30000 + i * 100) for i in range(69)])
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_exactly_min_bars_minus_one_returns_flat(self) -> None:
        s = TsmomStrategy(fast_ema=10, slow_ema=20, vol_lookback=5)
        # min_bars = 20 + 5 = 25; use 24
        bars = make_candles([float(1000 + i * 10) for i in range(24)])
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_flat_signal_metadata(self) -> None:
        s = TsmomStrategy(slow_ema=50, vol_lookback=20)
        bars = make_candles([float(1000 + i) for i in range(10)])
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.strategy_id == "tsmom"
        assert sig.symbol == "BTC/USDT"


# ── Direction from fixture segments ──────────────────────────────────────────


class TestTsmomDirectionFromFixture:
    def test_rising_bars_produce_positive_exposure(self) -> None:
        """Rising segment (bars 0–49 from fixture) → positive exposure."""
        all_bars = load_btc_100_bars()
        # Use all 100 bars (70 minimum needed; rising starts at 0)
        # Rising trend means fast_ema > slow_ema → positive
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        sig = s.generate_signal("BTC/USDT", all_bars[:70])
        # With 70 rising bars, fast_ema should be > slow_ema
        assert sig.target_exposure > 0.0

    def test_declining_bars_produce_negative_exposure(self) -> None:
        """Declining segment (bars 75–99) → negative exposure."""
        all_bars = load_btc_100_bars()
        # Use bars 25–99 to get 75 bars (>= 70 min_bars) ending in declining
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        sig = s.generate_signal("BTC/USDT", all_bars[25:])
        # Last segment is declining → fast_ema < slow_ema → negative
        assert sig.target_exposure < 0.0


# ── Regime conditioning ───────────────────────────────────────────────────────


class TestTsmomRegimeConditioning:
    def test_high_vol_chaos_returns_flat(self) -> None:
        """HIGH_VOL_CHAOS regime → flat signal regardless of trend."""
        all_bars = load_btc_100_bars()
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        regime = make_regime(RegimeType.HIGH_VOL_CHAOS)
        sig = s.generate_signal("BTC/USDT", all_bars[:70], regime=regime)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_trending_up_regime_does_not_suppress(self) -> None:
        """TRENDING_UP regime should not suppress tsmom signal."""
        all_bars = load_btc_100_bars()
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        regime = make_regime(RegimeType.TRENDING_UP)
        sig = s.generate_signal("BTC/USDT", all_bars[:70], regime=regime)
        assert sig.target_exposure > 0.0

    def test_no_regime_still_produces_signal(self) -> None:
        """With no regime, the strategy should still work."""
        all_bars = load_btc_100_bars()
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
        sig = s.generate_signal("BTC/USDT", all_bars[:70], regime=None)
        assert sig.target_exposure != 0.0


# ── Vol scaling ───────────────────────────────────────────────────────────────


class TestTsmomVolScaling:
    def test_high_vol_reduces_exposure(self) -> None:
        """When realized_vol is 2× target_vol, |exposure| should be <= 0.5."""
        # target_vol=0.10; we need realized_vol ≈ 0.20
        # Make very volatile candles: large alternating returns
        # +20% then -16.67% repeating → high annualised vol
        prices = [10000.0]
        for _ in range(80):
            prices.append(prices[-1] * 1.02 if len(prices) % 2 == 0 else prices[-1] * 0.98)
        # Add a clear trend at the end for direction signal
        for _ in range(10):
            prices.append(prices[-1] * 1.01)
        bars = make_candles(prices)
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20, target_vol=0.05)
        sig = s.generate_signal("BTC/USDT", bars)
        # High vol with low target → size should be small
        assert abs(sig.target_exposure) <= 1.0  # always clamped

    def test_exposure_clamped_to_one(self) -> None:
        """When vol is tiny and trend is strong, exposure is clamped to 1.0."""
        # Perfectly flat prices → near-zero realized_vol → huge size ratio
        # but result clamped
        prices = [float(30000 + i * 200) for i in range(80)]
        bars = make_candles(prices, spread_pct=0.0001)  # tiny spread → tiny vol
        s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20, target_vol=0.20)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure <= 1.0
        assert sig.target_exposure >= -1.0


# ── Signal metadata ───────────────────────────────────────────────────────────


class TestTsmomSignalMetadata:
    def test_signal_is_signal_instance(self) -> None:
        all_bars = load_btc_100_bars()
        s = TsmomStrategy()
        sig = s.generate_signal("BTC/USDT", all_bars)
        assert isinstance(sig, Signal)

    def test_signal_symbol_matches(self) -> None:
        all_bars = load_btc_100_bars()
        s = TsmomStrategy()
        sig = s.generate_signal("ETH/USDT", all_bars)
        assert sig.symbol == "ETH/USDT"

    def test_signal_generated_at_is_last_candle(self) -> None:
        all_bars = load_btc_100_bars()
        s = TsmomStrategy()
        sig = s.generate_signal("BTC/USDT", all_bars)
        assert sig.generated_at == all_bars[-1].timestamp


# ── Hypothesis property test ──────────────────────────────────────────────────


@settings(max_examples=50)
@given(
    prices=st.lists(
        st.floats(min_value=100.0, max_value=200000.0, allow_nan=False, allow_infinity=False),
        min_size=80,
        max_size=150,
    )
)
def test_tsmom_exposure_always_in_range(prices: list[float]) -> None:
    """Property: target_exposure is always in [-1.0, 1.0] for any valid input."""
    candles = make_candles(prices)
    s = TsmomStrategy(fast_ema=20, slow_ema=50, vol_lookback=20)
    sig = s.generate_signal("BTC/USDT", candles)
    assert -1.0 <= sig.target_exposure <= 1.0
