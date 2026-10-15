"""A2 Regime Classifier.

Classifies a market as trending_up, trending_down, ranging, or high_vol_chaos
using ADX, realised-vol percentile, EMA slope, and Kaufman's Efficiency Ratio.

The ``hurst_exponent`` field of ``RegimeLabel`` stores the Efficiency Ratio
(both measure market efficiency; ER is simpler to compute without rolling
regression and produces comparable signal quality at short periods).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from trendscend.indicators.momentum import realized_vol
from trendscend.indicators.regime import adx, efficiency_ratio, ema_slope
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType

# Minimum bars required for a meaningful classification with default config.
MIN_BARS_DEFAULT: int = 120


@dataclass
class RegimeClassifierConfig:
    """Tuneable thresholds for the regime classifier.

    Attributes:
        adx_period:              ADX smoothing period (default 14).
        vol_period:              Realized-vol look-back window (default 20).
        vol_rank_period:         How many historical vol values to rank against (default 100).
        ema_period:              EMA period for slope calculation (default 50).
        er_period:               Efficiency Ratio look-back period (default 20).
        adx_trending_threshold:  ADX above this → trending market (default 25.0).
        vol_chaos_percentile:    Vol percentile above this → high_vol_chaos (default 0.85).
    """

    adx_period: int = 14
    vol_period: int = 20
    vol_rank_period: int = 100
    ema_period: int = 50
    er_period: int = 20
    adx_trending_threshold: float = 25.0
    vol_chaos_percentile: float = 0.85
    _min_bars: int = field(init=False, repr=False, default=0)

    def __post_init__(self) -> None:
        # bars needed for vol percentile: vol_rank_period + vol_period
        # bars needed for ADX: 2 * adx_period + 1
        # bars needed for EMA slope: ema_period + 1
        # bars needed for ER: er_period + 1
        self._min_bars = max(
            self.vol_rank_period + self.vol_period,
            2 * self.adx_period + 1,
            self.ema_period + 1,
            self.er_period + 1,
        )

    @property
    def min_bars(self) -> int:
        return self._min_bars


class RegimeClassifier:
    """Classifies market regime from a candle history.

    Stateless — each call to ``classify`` is independent.
    """

    def classify(
        self,
        candles: list[OHLCV],
        symbol: str,
        timeframe: str,
        config: RegimeClassifierConfig | None = None,
    ) -> RegimeLabel:
        """Classify the most recent bar's market regime.

        Args:
            candles:   OHLCV history in ascending time order. Must include at
                       least ``config.min_bars`` bars for a meaningful label;
                       returns RANGING with confidence=0.0 otherwise.
            symbol:    Trading pair, e.g. "BTC/USDT".
            timeframe: Bar duration string, e.g. "4h".
            config:    Classifier configuration; uses default values if None.

        Returns:
            A ``RegimeLabel`` with regime, confidence (0–1), and raw indicator
            values for transparency.
        """
        if config is None:
            config = RegimeClassifierConfig()

        now = candles[-1].timestamp if candles else datetime.now(UTC)

        if len(candles) < config.min_bars:
            return RegimeLabel(
                symbol=symbol,
                timeframe=timeframe,
                regime=RegimeType.RANGING,
                confidence=0.0,
                labeled_at=now,
            )

        # ── Compute indicators ───────────────────────────────────────────────
        adx_vals = adx(candles, config.adx_period)
        er_vals = efficiency_ratio(candles, config.er_period)
        slope_vals = ema_slope(candles, config.ema_period, slope_period=1)
        vol_vals = realized_vol(candles, config.vol_period)

        current_adx = adx_vals[-1] if adx_vals else 0.0
        current_er = er_vals[-1] if er_vals else 0.0
        current_slope = slope_vals[-1] if slope_vals else 0.0

        # Rolling vol percentile — midrank formula avoids false 100th-percentile
        # when all recent vols are equal (flat trend with constant log-returns).
        vol_pct = 0.5
        if len(vol_vals) >= config.vol_rank_period:
            window = vol_vals[-config.vol_rank_period :]
            current_vol = window[-1]
            vol_pct = (sum(1 for v in window if v < current_vol) + 0.5) / len(window)

        # ── Classification ───────────────────────────────────────────────────
        if vol_pct > config.vol_chaos_percentile:
            # Extreme volatility dominates any trend signal
            above = vol_pct - config.vol_chaos_percentile
            span = 1.0 - config.vol_chaos_percentile
            confidence = min(1.0, above / span) if span > 0.0 else 1.0
            return RegimeLabel(
                symbol=symbol,
                timeframe=timeframe,
                regime=RegimeType.HIGH_VOL_CHAOS,
                confidence=round(confidence, 4),
                adx=round(current_adx, 4),
                vol_percentile=round(vol_pct, 4),
                ema_slope=round(current_slope, 6),
                hurst_exponent=round(current_er, 4),
                labeled_at=now,
            )

        if current_adx >= config.adx_trending_threshold:
            regime = RegimeType.TRENDING_UP if current_slope >= 0.0 else RegimeType.TRENDING_DOWN
            # Confidence: blend ADX signal strength and efficiency ratio
            adx_conf = min(
                1.0,
                (current_adx - config.adx_trending_threshold) / config.adx_trending_threshold,
            )
            confidence = min(1.0, (adx_conf + current_er) / 2.0)
            return RegimeLabel(
                symbol=symbol,
                timeframe=timeframe,
                regime=regime,
                confidence=round(confidence, 4),
                adx=round(current_adx, 4),
                vol_percentile=round(vol_pct, 4),
                ema_slope=round(current_slope, 6),
                hurst_exponent=round(current_er, 4),
                labeled_at=now,
            )

        # Ranging: low ADX and low efficiency
        ranging_conf = min(
            1.0,
            (1.0 - current_adx / config.adx_trending_threshold) * (1.0 - current_er),
        )
        return RegimeLabel(
            symbol=symbol,
            timeframe=timeframe,
            regime=RegimeType.RANGING,
            confidence=round(max(0.0, ranging_conf), 4),
            adx=round(current_adx, 4),
            vol_percentile=round(vol_pct, 4),
            ema_slope=round(current_slope, 6),
            hurst_exponent=round(current_er, 4),
            labeled_at=now,
        )
