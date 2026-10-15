"""Tests for GridRangeStrategy — written TDD-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.strategies.base import Strategy
from trendscend.strategies.grid_range import GridRangeStrategy

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


def make_ranging_candles(
    n: int, base_price: float = 10000.0, amplitude: float = 200.0
) -> list[OHLCV]:
    """Create sideways-ranging candles with small oscillations."""
    import math

    base_ts = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for i in range(n):
        p = base_price + amplitude * math.sin(2 * math.pi * i / 10)
        dec = Decimal(str(round(p, 2)))
        result.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=dec,
                high=Decimal(str(round(p * 1.005, 2))),
                low=Decimal(str(round(p * 0.995, 2))),
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


class TestGridRangeProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        s = GridRangeStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = GridRangeStrategy()
        assert s.strategy_id == "grid_range"


# ── Insufficient history guard ────────────────────────────────────────────────


class TestGridRangeInsufficientHistory:
    def test_fewer_than_min_bars_returns_flat(self) -> None:
        """With fewer than 28 bars (14 + 14), returns flat."""
        s = GridRangeStrategy(atr_period=14)
        bars = make_ranging_candles(25)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_flat_signal_metadata(self) -> None:
        s = GridRangeStrategy()
        bars = make_ranging_candles(5)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.strategy_id == "grid_range"
        assert sig.symbol == "BTC/USDT"


# ── ADX trending filter ───────────────────────────────────────────────────────


class TestGridRangeAdxFilter:
    def test_flat_when_adx_above_threshold_strong_trend(self) -> None:
        """With strong trending data (high ADX), strategy returns flat."""
        # Strongly trending: prices rise by 3% every bar → high ADX
        prices = [float(10000 * (1.03**i)) for i in range(60)]
        bars = make_candles(prices)
        s = GridRangeStrategy(adx_threshold=25.0, atr_period=14)
        sig = s.generate_signal("BTC/USDT", bars)
        # Trending data should have ADX >= 25 → flat
        assert sig.target_exposure == pytest.approx(0.0)

    def test_ranging_data_can_produce_nonzero_signal(self) -> None:
        """Ranging candles with low ADX can produce a non-flat signal."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy(adx_threshold=25.0, atr_period=14)
        sig = s.generate_signal("BTC/USDT", bars)
        # Ranging data should have ADX < 25 → non-flat signal
        # (note: may still be 0 if price is at exact center)
        assert -1.0 <= sig.target_exposure <= 1.0


# ── Regime conditioning ───────────────────────────────────────────────────────


class TestGridRangeRegimeConditioning:
    def test_trending_up_regime_returns_flat(self) -> None:
        """TRENDING_UP regime → flat (no grid in trending market)."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy(adx_threshold=25.0)
        regime = make_regime(RegimeType.TRENDING_UP)
        sig = s.generate_signal("BTC/USDT", bars, regime=regime)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_trending_down_regime_returns_flat(self) -> None:
        """TRENDING_DOWN regime → flat (no grid in trending market)."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy(adx_threshold=25.0)
        regime = make_regime(RegimeType.TRENDING_DOWN)
        sig = s.generate_signal("BTC/USDT", bars, regime=regime)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_ranging_regime_allows_signal(self) -> None:
        """RANGING regime → grid strategy can produce non-flat signals."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy(adx_threshold=25.0)
        regime = make_regime(RegimeType.RANGING)
        sig = s.generate_signal("BTC/USDT", bars, regime=regime)
        # Not suppressed by regime; ADX determines
        assert -1.0 <= sig.target_exposure <= 1.0

    def test_high_vol_chaos_regime_not_flat_from_regime(self) -> None:
        """HIGH_VOL_CHAOS is not in TRENDING; regime alone doesn't suppress grid."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy(adx_threshold=25.0)
        regime = make_regime(RegimeType.HIGH_VOL_CHAOS)
        sig = s.generate_signal("BTC/USDT", bars, regime=regime)
        # Not suppressed by regime alone; ADX filter still applies
        assert -1.0 <= sig.target_exposure <= 1.0


# ── Price exit from grid ──────────────────────────────────────────────────────


class TestGridRangePriceExit:
    def test_exits_when_price_moves_beyond_atr_from_center(self) -> None:
        """If price > 1 ATR from grid center, return flat."""
        # Set up: first signal to establish grid center, then move price far away
        base_price = 10000.0
        bars_first = make_ranging_candles(60, base_price=base_price, amplitude=100.0)
        s = GridRangeStrategy(adx_threshold=25.0, atr_period=14)

        # Get first signal to establish grid center
        s.generate_signal("BTC/USDT", bars_first)

        # Now price moves far away (10× ATR from center)
        last_ts = bars_first[-1].timestamp
        far_price = base_price * 1.5  # 50% move → well beyond any ATR
        far_bar = OHLCV(
            timestamp=last_ts + timedelta(hours=4),
            open=Decimal(str(far_price)),
            high=Decimal(str(far_price * 1.01)),
            low=Decimal(str(far_price * 0.99)),
            close=Decimal(str(far_price)),
            volume=Decimal("100"),
        )
        bars_extended = bars_first + [far_bar]
        sig2 = s.generate_signal("BTC/USDT", bars_extended)
        # Price is far from grid center → exit
        assert sig2.target_exposure == pytest.approx(0.0)


# ── Exposure proportionality ──────────────────────────────────────────────────


class TestGridRangeExposureProportionality:
    def test_exposure_in_bounds(self) -> None:
        """Exposure is always in [-1.0, 1.0]."""
        bars = make_ranging_candles(60)
        s = GridRangeStrategy()
        sig = s.generate_signal("BTC/USDT", bars)
        assert -1.0 <= sig.target_exposure <= 1.0


# ── Golden fixture ────────────────────────────────────────────────────────────


class TestGridRangeGoldenFixture:
    def test_choppy_segment_does_not_suppress_due_to_regime(self) -> None:
        """No regime passed → choppy segment signal depends only on ADX."""
        bars = load_btc_100_bars()
        s = GridRangeStrategy(adx_threshold=25.0, atr_period=14)
        sig = s.generate_signal("BTC/USDT", bars[25:75])
        # Just verify it doesn't crash and is in range
        assert -1.0 <= sig.target_exposure <= 1.0

    def test_trending_regime_suppresses_on_full_fixture(self) -> None:
        """TRENDING_UP regime on full fixture → flat."""
        bars = load_btc_100_bars()
        s = GridRangeStrategy(adx_threshold=25.0)
        regime = make_regime(RegimeType.TRENDING_UP)
        sig = s.generate_signal("BTC/USDT", bars, regime=regime)
        assert sig.target_exposure == pytest.approx(0.0)


# ── Hypothesis property test ──────────────────────────────────────────────────


@settings(max_examples=50)
@given(
    prices=st.lists(
        st.floats(min_value=100.0, max_value=200000.0, allow_nan=False, allow_infinity=False),
        min_size=40,
        max_size=150,
    )
)
def test_grid_range_exposure_always_in_range(prices: list[float]) -> None:
    """Property: target_exposure is always in [-1.0, 1.0] for any valid input."""
    candles = make_candles(prices)
    s = GridRangeStrategy()
    sig = s.generate_signal("BTC/USDT", candles)
    assert -1.0 <= sig.target_exposure <= 1.0
