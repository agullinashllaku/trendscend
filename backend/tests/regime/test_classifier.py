"""Tests for the A2 Regime Classifier."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trendscend.regime.classifier import RegimeClassifier, RegimeClassifierConfig
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeType


def make_candles(
    prices: list[float],
    spread_pct: float = 0.005,
) -> list[OHLCV]:
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


def make_trending_up(n: int = 200) -> list[OHLCV]:
    """Monotonically rising series: good ADX, positive EMA slope."""
    prices = [100.0 + i * 0.5 for i in range(n)]
    return make_candles(prices, spread_pct=0.002)


def make_ranging(n: int = 200) -> list[OHLCV]:
    """True zigzag: low ADX, near-zero efficiency ratio (ER = net/path ≈ 0/n)."""
    prices: list[float] = []
    p = 100.0
    for i in range(n):
        p += 1.0 if i % 2 == 0 else -1.0
        prices.append(p)
    return make_candles(prices, spread_pct=0.001)


def make_trending_down(n: int = 200) -> list[OHLCV]:
    """Declining series with periodic noise so realized-vol is stable across windows.

    A perfectly constant slope produces monotonically increasing window stds
    (log returns grow in magnitude as price falls), pushing vol_percentile to
    97.5 % and triggering high_vol_chaos.  Adding a small oscillation keeps
    every 4-bar sub-window at similar std, so vol_pct stays near 0.5.
    """
    prices = [200.0 - i * 0.5 + (0.15 if i % 4 < 2 else -0.15) for i in range(n)]
    return make_candles(prices, spread_pct=0.002)


cfg_fast = RegimeClassifierConfig(
    adx_period=5,
    vol_period=5,
    vol_rank_period=20,
    ema_period=10,
    er_period=5,
    adx_trending_threshold=20.0,
    vol_chaos_percentile=0.85,
)


class TestRegimeClassifierInsufficientData:
    def test_returns_ranging_low_confidence_when_not_enough_bars(self) -> None:
        candles = make_candles([100.0] * 10)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h")
        assert label.regime == RegimeType.RANGING
        assert label.confidence == 0.0
        assert label.symbol == "BTC/USDT"
        assert label.timeframe == "4h"

    def test_returns_correct_symbol_and_timeframe(self) -> None:
        candles = make_candles([100.0] * 5)
        clf = RegimeClassifier()
        label = clf.classify(candles, "ETH/USDT", "1d")
        assert label.symbol == "ETH/USDT"
        assert label.timeframe == "1d"


class TestRegimeClassifierTrendingUp:
    def test_monotonic_rise_classified_as_trending_up(self) -> None:
        candles = make_trending_up(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.regime == RegimeType.TRENDING_UP

    def test_trending_up_has_positive_ema_slope(self) -> None:
        candles = make_trending_up(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.ema_slope is not None
        assert label.ema_slope > 0.0

    def test_trending_up_confidence_above_zero(self) -> None:
        candles = make_trending_up(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.confidence > 0.0


class TestRegimeClassifierTrendingDown:
    def test_monotonic_fall_classified_as_trending_down(self) -> None:
        candles = make_trending_down(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.regime == RegimeType.TRENDING_DOWN

    def test_trending_down_has_negative_ema_slope(self) -> None:
        candles = make_trending_down(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.ema_slope is not None
        assert label.ema_slope < 0.0


class TestRegimeClassifierRanging:
    def test_oscillating_series_classified_as_ranging(self) -> None:
        candles = make_ranging(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.regime == RegimeType.RANGING

    def test_ranging_has_low_efficiency_ratio(self) -> None:
        candles = make_ranging(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        # hurst_exponent field stores efficiency ratio
        assert label.hurst_exponent is not None
        assert label.hurst_exponent < 0.5


class TestRegimeClassifierOutputSchema:
    def test_confidence_bounded_0_to_1(self) -> None:
        for factory in [make_trending_up, make_ranging, make_trending_down]:
            candles = factory(200)
            clf = RegimeClassifier()
            label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
            assert 0.0 <= label.confidence <= 1.0, (
                f"confidence {label.confidence} out of [0,1] for {factory.__name__}"
            )

    def test_adx_is_populated(self) -> None:
        candles = make_trending_up(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        assert label.adx is not None
        assert label.adx >= 0.0

    def test_labeled_at_is_recent(self) -> None:
        candles = make_trending_up(200)
        clf = RegimeClassifier()
        label = clf.classify(candles, "BTC/USDT", "4h", config=cfg_fast)
        # labeled_at should be the timestamp of the last candle
        assert label.labeled_at == candles[-1].timestamp
