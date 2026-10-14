"""Tests for DcaTrendStrategy — written TDD-first."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from tests.strategies.conftest import load_btc_100_bars
from trendscend.schemas.market import OHLCV
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import Strategy
from trendscend.strategies.dca_trend import DcaTrendStrategy

# ── Helpers ───────────────────────────────────────────────────────────────────


def make_rising_candles(n: int, start_price: float = 30000.0, step: float = 100.0) -> list[OHLCV]:
    """Return n candles with strictly rising close prices."""
    base_ts = datetime(2020, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(n):
        price = start_price + step * i
        dec = Decimal(str(price))
        candles.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=dec,
                high=Decimal(str(price * 1.003)),
                low=Decimal(str(price * 0.997)),
                close=dec,
                volume=Decimal("100"),
            )
        )
    return candles


def make_flat_candles(n: int, price: float = 50000.0) -> list[OHLCV]:
    """Return n candles with constant close prices."""
    base_ts = datetime(2020, 1, 1, tzinfo=UTC)
    candles = []
    for i in range(n):
        dec = Decimal(str(price))
        candles.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=dec,
                high=Decimal(str(price * 1.003)),
                low=Decimal(str(price * 0.997)),
                close=dec,
                volume=Decimal("100"),
            )
        )
    return candles


# ── Protocol ─────────────────────────────────────────────────────────────────


class TestDcaTrendProtocol:
    def test_isinstance_strategy_protocol(self) -> None:
        s = DcaTrendStrategy()
        assert isinstance(s, Strategy)

    def test_strategy_id_is_correct(self) -> None:
        s = DcaTrendStrategy()
        assert s.strategy_id == "dca_trend"


# ── Insufficient history guard ────────────────────────────────────────────────


class TestDcaTrendInsufficientHistory:
    def test_fewer_than_ema_period_returns_flat(self) -> None:
        """With fewer than ema_period bars, target_exposure must be 0.0."""
        s = DcaTrendStrategy(ema_period=200)
        bars = make_rising_candles(50)  # only 50 < 200
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_exactly_ema_period_minus_one_returns_flat(self) -> None:
        s = DcaTrendStrategy(ema_period=10)
        bars = make_rising_candles(9)  # 9 < 10
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)

    def test_exactly_ema_period_allows_signal(self) -> None:
        """Exactly ema_period bars is enough to compute EMA."""
        s = DcaTrendStrategy(ema_period=10, pause_below_ema=False)
        # Rising candles so EMA will be below close
        bars = make_rising_candles(10)
        sig = s.generate_signal("BTC/USDT", bars)
        # Should not be flat (unless price < EMA, but pause_below_ema=False)
        assert sig.target_exposure == pytest.approx(0.10)

    def test_flat_signal_has_correct_metadata(self) -> None:
        s = DcaTrendStrategy(ema_period=200)
        bars = make_rising_candles(50)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.strategy_id == "dca_trend"
        assert sig.symbol == "BTC/USDT"
        assert sig.target_exposure == pytest.approx(0.0)

    def test_single_bar_returns_flat(self) -> None:
        s = DcaTrendStrategy(ema_period=200)
        bars = make_rising_candles(1)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.0)


# ── Pause below EMA ──────────────────────────────────────────────────────────


class TestDcaTrendPauseBelowEma:
    def test_pauses_when_close_below_ema(self) -> None:
        """When close < EMA, target_exposure must be 0.0 with pause_below_ema=True."""
        # Create candles where later bars decline sharply so close < EMA
        # Use ema_period=10 to keep it small; build a series that drops at the end
        rising_part = make_rising_candles(40, start_price=50000.0, step=100.0)
        # Add 10 bars that drop below the EMA
        base_ts = rising_part[-1].timestamp + timedelta(hours=4)
        falling_bars = []
        for i in range(10):
            low_price = 30000.0  # well below EMA of ~53900
            dec = Decimal(str(low_price))
            falling_bars.append(
                OHLCV(
                    timestamp=base_ts + timedelta(hours=4 * i),
                    open=dec,
                    high=Decimal(str(low_price * 1.003)),
                    low=Decimal(str(low_price * 0.997)),
                    close=dec,
                    volume=Decimal("100"),
                )
            )
        candles = rising_part + falling_bars  # 50 bars, last close = 30000

        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=True)
        sig = s.generate_signal("BTC/USDT", candles)
        # close = 30000 << EMA ≈ 50000+; should return 0.0
        assert sig.target_exposure == pytest.approx(0.0)

    def test_active_when_close_above_ema(self) -> None:
        """When close > EMA on a rising series, strategy should contribute."""
        # Rising candles: close always increases, EMA will lag below
        bars = make_rising_candles(50, start_price=30000.0, step=200.0)
        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=True)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure > 0.0

    def test_pause_below_ema_false_ignores_ema_filter(self) -> None:
        """With pause_below_ema=False, signal is positive even when close < EMA."""
        # Create a big drop
        high_bars = make_rising_candles(40, start_price=50000.0, step=100.0)
        base_ts = high_bars[-1].timestamp + timedelta(hours=4)
        low_price = 10000.0
        low_bars = []
        for i in range(10):
            dec = Decimal(str(low_price))
            low_bars.append(
                OHLCV(
                    timestamp=base_ts + timedelta(hours=4 * i),
                    open=dec,
                    high=Decimal(str(low_price * 1.003)),
                    low=Decimal(str(low_price * 0.997)),
                    close=dec,
                    volume=Decimal("100"),
                )
            )
        candles = high_bars + low_bars

        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=False)
        sig = s.generate_signal("BTC/USDT", candles)
        assert sig.target_exposure == pytest.approx(0.10)


# ── Contribution accumulation ─────────────────────────────────────────────────


class TestDcaTrendAccumulation:
    def test_first_contribution_equals_contribution_pct(self) -> None:
        """First active signal adds contribution_pct from 0.0."""
        bars = make_rising_candles(20, step=200.0)  # rising, close > EMA
        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=False)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.10)

    def test_second_contribution_accumulates(self) -> None:
        """Second active signal adds another contribution_pct."""
        bars_v1 = make_rising_candles(20, step=200.0)
        bars_v2 = make_rising_candles(21, step=200.0)
        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=False)
        s.generate_signal("BTC/USDT", bars_v1)
        sig2 = s.generate_signal("BTC/USDT", bars_v2)
        assert sig2.target_exposure == pytest.approx(0.20)

    def test_exposure_caps_at_one(self) -> None:
        """Exposure must not exceed 1.0 even after many contributions."""
        s = DcaTrendStrategy(ema_period=5, contribution_pct=0.30, pause_below_ema=False)
        exposure = 0.0
        for n in range(5, 20):
            bars = make_rising_candles(n, step=200.0)
            sig = s.generate_signal("BTC/USDT", bars)
            assert sig.target_exposure <= 1.0
            exposure = sig.target_exposure
        assert exposure == pytest.approx(1.0)

    def test_custom_contribution_pct(self) -> None:
        bars = make_rising_candles(15, step=200.0)
        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.25, pause_below_ema=False)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.target_exposure == pytest.approx(0.25)

    def test_exposure_resets_to_zero_when_below_ema(self) -> None:
        """After being active, if close drops below EMA, exposure resets to 0."""
        s = DcaTrendStrategy(ema_period=10, contribution_pct=0.10, pause_below_ema=True)

        # Build up some exposure
        rising = make_rising_candles(20, step=200.0)
        s.generate_signal("BTC/USDT", rising)
        assert s._current_exposure.get("BTC/USDT", 0.0) == pytest.approx(0.10)

        # Now drop below EMA
        rising_part = make_rising_candles(40, start_price=50000.0, step=100.0)
        base_ts = rising_part[-1].timestamp + timedelta(hours=4)
        low_bars = []
        for i in range(10):
            dec = Decimal("5000")
            low_bars.append(
                OHLCV(
                    timestamp=base_ts + timedelta(hours=4 * i),
                    open=dec,
                    high=dec,
                    low=dec,
                    close=dec,
                    volume=Decimal("100"),
                )
            )
        candles = rising_part + low_bars
        sig = s.generate_signal("BTC/USDT", candles)
        assert sig.target_exposure == pytest.approx(0.0)
        assert s._current_exposure["BTC/USDT"] == pytest.approx(0.0)


# ── Signal metadata ───────────────────────────────────────────────────────────


class TestDcaTrendSignalMetadata:
    def test_signal_is_signal_instance(self) -> None:
        s = DcaTrendStrategy(ema_period=5, pause_below_ema=False)
        bars = make_rising_candles(10, step=200.0)
        assert isinstance(s.generate_signal("BTC/USDT", bars), Signal)

    def test_signal_symbol_matches_input(self) -> None:
        s = DcaTrendStrategy(ema_period=5, pause_below_ema=False)
        bars = make_rising_candles(10, step=200.0)
        sig = s.generate_signal("ETH/USDT", bars)
        assert sig.symbol == "ETH/USDT"

    def test_signal_generated_at_is_last_candle(self) -> None:
        s = DcaTrendStrategy(ema_period=5, pause_below_ema=False)
        bars = make_rising_candles(10, step=200.0)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.generated_at == bars[-1].timestamp

    def test_conviction_is_zero_when_flat(self) -> None:
        s = DcaTrendStrategy(ema_period=200)
        bars = make_rising_candles(50)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.conviction == pytest.approx(0.0)

    def test_conviction_is_one_when_active(self) -> None:
        s = DcaTrendStrategy(ema_period=5, contribution_pct=0.10, pause_below_ema=False)
        bars = make_rising_candles(10, step=200.0)
        sig = s.generate_signal("BTC/USDT", bars)
        assert sig.conviction == pytest.approx(1.0)


# ── Golden fixture integration test ──────────────────────────────────────────


class TestDcaTrendGoldenFixture:
    def test_100_bar_fixture_declining_phase_below_ema(self) -> None:
        """On the 100-bar fixture with ema_period=20, the declining segment
        should produce flat signals since EMA lags the decline."""
        bars = load_btc_100_bars()
        # Use ema_period=20 so we have enough bars from the full fixture
        s = DcaTrendStrategy(ema_period=20, contribution_pct=0.10, pause_below_ema=True)
        sig = s.generate_signal("BTC/USDT", bars)
        # Last price is 35000; EMA of 100 bars ending at ~35000 will still be high
        # (EMA lags the decline) so close < EMA → flat
        assert sig.target_exposure == pytest.approx(0.0)
