"""BreakoutStrategy — Donchian channel breakout with ATR-based stops."""

from __future__ import annotations

from trendscend.indicators.trend import atr, donchian_high, donchian_low
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import require_min_bars


class BreakoutStrategy:
    """Donchian channel breakout strategy with volatility-scaled stop levels.

    Enters long when the current close breaks above the N-bar high (from the
    previous bars, excluding the current one).  Exits flat when price breaks
    below the N-bar low.  Spot-only: no short positions.

    The stop loss is expressed as a percentage of current close, derived from
    ATR with a configurable multiplier.

    Parameters:
        channel_period:      Number of bars for the Donchian channel (default 20).
        atr_period:          Period for ATR calculation (default 14).
        stop_atr_multiplier: ATR multiplier for stop distance (default 2.0).

    Attributes:
        strategy_id: Fixed identifier ``"breakout"``.
    """

    strategy_id: str = "breakout"

    def __init__(
        self,
        channel_period: int = 20,
        atr_period: int = 14,
        stop_atr_multiplier: float = 2.0,
    ) -> None:
        self.channel_period = channel_period
        self.atr_period = atr_period
        self.stop_atr_multiplier = stop_atr_multiplier
        # Per-symbol current exposure tracking (for HOLD behaviour)
        self._current_exposure: dict[str, float] = {}

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a breakout signal based on Donchian channel and ATR stop.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars in ascending time order.
            regime:  Unused (breakout strategy ignores regime).

        Returns:
            BUY (``target_exposure=1.0``) on upward breakout,
            SELL/flat (``target_exposure=0.0``) on downward breakout (spot-only),
            HOLD (returns current exposure) when neither breakout occurs.
        """
        min_bars = max(self.channel_period, self.atr_period) + 1

        flat = require_min_bars(
            candles=candles,
            min_bars=min_bars,
            symbol=symbol,
            strategy_id=self.strategy_id,
        )
        if flat is not None:
            self._current_exposure[symbol] = 0.0
            return flat

        current_close = float(candles[-1].close)

        # Compute ATR over the full candle list
        atr_values = atr(candles, self.atr_period)
        latest_atr = atr_values[-1] if atr_values else 0.0

        # Donchian channel computed on the *previous* bars (excluding current bar)
        # so we don't look ahead into the current candle
        prev_candles = candles[:-1]
        high_channel = donchian_high(prev_candles, self.channel_period)
        low_channel = donchian_low(prev_candles, self.channel_period)

        if not high_channel or not low_channel:
            # Not enough previous bars for channel — hold
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=self._current_exposure.get(symbol, 0.0),
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        channel_high = high_channel[-1]
        channel_low = low_channel[-1]

        # ATR-based stop distance as fraction of close
        stop_pct = (
            self.stop_atr_multiplier * latest_atr / current_close if current_close > 0 else 0.0
        )

        # BUY: close breaks above the channel high of previous N bars
        if current_close > channel_high:
            self._current_exposure[symbol] = 1.0
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=1.0,
                conviction=1.0,
                suggested_stop_pct=stop_pct,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # SELL: close breaks below the channel low → go flat (spot-only, no shorting)
        if current_close < channel_low:
            self._current_exposure[symbol] = 0.0
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                suggested_stop_pct=stop_pct,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # HOLD: no new breakout — return current exposure
        current = self._current_exposure.get(symbol, 0.0)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=current,
            conviction=0.0,
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
