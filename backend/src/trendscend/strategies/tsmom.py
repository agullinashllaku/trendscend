"""TsmomStrategy — time-series momentum with volatility scaling."""

from __future__ import annotations

import math

from trendscend.indicators.momentum import realized_vol, tsmom_signal
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import require_min_bars


class TsmomStrategy:
    """Time-series momentum strategy with volatility-scaled position sizing.

    Uses an EMA crossover to determine trend direction and scales position
    size by the ratio of target annualised volatility to realised volatility.

    Parameters:
        fast_ema:     Period for the fast EMA (default 20).
        slow_ema:     Period for the slow EMA (default 50).
        vol_lookback: Look-back window for realised volatility (default 20).
        target_vol:   Target annualised volatility fraction (default 0.20 = 20%).

    Attributes:
        strategy_id: Fixed identifier ``"tsmom"``.
    """

    strategy_id: str = "tsmom"

    def __init__(
        self,
        fast_ema: int = 20,
        slow_ema: int = 50,
        vol_lookback: int = 20,
        target_vol: float = 0.20,
    ) -> None:
        self.fast_ema = fast_ema
        self.slow_ema = slow_ema
        self.vol_lookback = vol_lookback
        self.target_vol = target_vol

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a momentum signal scaled by realised volatility.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars in ascending time order.
            regime:  Optional regime context.  If ``HIGH_VOL_CHAOS``, returns flat.

        Returns:
            ``Signal(target_exposure=0.0)`` if insufficient history or regime
            is ``HIGH_VOL_CHAOS``.  Otherwise a volatility-scaled signal with
            ``target_exposure`` in ``[-1.0, 1.0]``.
        """
        min_bars = self.slow_ema + self.vol_lookback

        flat = require_min_bars(
            candles=candles,
            min_bars=min_bars,
            symbol=symbol,
            strategy_id=self.strategy_id,
        )
        if flat is not None:
            return flat

        # Regime gate: HIGH_VOL_CHAOS suppresses the strategy entirely
        if regime is not None and regime.regime == RegimeType.HIGH_VOL_CHAOS:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # Compute EMA crossover signal
        crossover = tsmom_signal(candles, self.fast_ema, self.slow_ema)
        if not crossover:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        latest_signal = crossover[-1]
        direction = math.copysign(1.0, latest_signal) if latest_signal != 0.0 else 0.0

        # Compute realised volatility for position sizing
        vol_values = realized_vol(candles, self.vol_lookback)
        if not vol_values:
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        latest_vol = max(vol_values[-1], 0.01)
        size = self.target_vol / latest_vol
        # Clamp size to [0.0, 1.0] before applying direction
        size = max(0.0, min(1.0, size))

        target_exposure = direction * size
        # Final clamp to [-1.0, 1.0] (enforces the Signal field constraint)
        target_exposure = max(-1.0, min(1.0, target_exposure))

        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=target_exposure,
            conviction=abs(target_exposure),
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
