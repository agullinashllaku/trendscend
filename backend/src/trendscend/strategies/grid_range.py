"""GridRangeStrategy — mean-reversion grid trading for ranging markets."""

from __future__ import annotations

from trendscend.indicators.regime import adx as compute_adx
from trendscend.indicators.trend import atr as compute_atr
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import require_min_bars

_TRENDING_REGIMES = frozenset({RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN})


class GridRangeStrategy:
    """Mean-reversion grid strategy for ranging (low-ADX) markets.

    Places a virtual grid around the current price when the market is ranging
    (ADX < ``adx_threshold``).  Exposure is proportional to the current
    price's position in the grid: near the bottom → higher exposure (buy
    dips), near the top → lower exposure (sell rips).

    The strategy maintains a per-symbol grid centre.  If price moves more than
    1 ATR away from the centre the position is closed entirely.

    Parameters:
        grid_levels:      Number of grid levels above/below the centre (default 10).
        grid_spacing_pct: Fractional spacing between grid levels (default 0.01 = 1%).
        adx_threshold:    ADX threshold above which the strategy goes flat (default 25.0).
        atr_period:       ATR period used for the exit check (default 14).

    Attributes:
        strategy_id: Fixed identifier ``"grid_range"``.
    """

    strategy_id: str = "grid_range"

    def __init__(
        self,
        grid_levels: int = 10,
        grid_spacing_pct: float = 0.01,
        adx_threshold: float = 25.0,
        atr_period: int = 14,
    ) -> None:
        self.grid_levels = grid_levels
        self.grid_spacing_pct = grid_spacing_pct
        self.adx_threshold = adx_threshold
        self.atr_period = atr_period
        # Per-symbol grid centre (set once when first valid signal fires)
        self._grid_center: dict[str, float] = {}

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a grid-range signal based on ADX filter and price position.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars in ascending time order.
            regime:  Optional regime context.  ``TRENDING_UP`` or
                     ``TRENDING_DOWN`` returns flat immediately.

        Returns:
            ``Signal(target_exposure=0.0)`` if: insufficient history, ADX is
            above threshold (trending), regime is trending, or price moved
            beyond 1 ATR from the grid centre.
            Otherwise a position proportional to the price's place in the grid.
        """
        # ADX needs 2*period bars; add atr_period for the ATR exit check
        min_bars = 2 * 14 + self.atr_period  # 28 + atr_period

        flat = require_min_bars(
            candles=candles,
            min_bars=min_bars,
            symbol=symbol,
            strategy_id=self.strategy_id,
        )
        if flat is not None:
            self._grid_center.pop(symbol, None)
            return flat

        # Regime gate: no grid in trending markets
        if regime is not None and regime.regime in _TRENDING_REGIMES:
            self._grid_center.pop(symbol, None)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # ADX filter: go flat when market is trending
        adx_values = compute_adx(candles, period=14)
        if not adx_values or adx_values[-1] >= self.adx_threshold:
            self._grid_center.pop(symbol, None)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        current_close = float(candles[-1].close)

        # ATR for the exit check
        atr_values = compute_atr(candles, self.atr_period)
        latest_atr = atr_values[-1] if atr_values else 0.0

        # Establish or keep grid center
        grid_center = self._grid_center.get(symbol)
        if grid_center is None:
            grid_center = current_close
            self._grid_center[symbol] = grid_center

        # Exit if price has moved too far from the grid centre
        if latest_atr > 0 and abs(current_close - grid_center) > latest_atr:
            self._grid_center.pop(symbol, None)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # Compute target exposure from price position in grid
        # Grid range: center ± half_range
        half_range = self.grid_levels * self.grid_spacing_pct / 2.0 * grid_center
        if half_range <= 0:
            self._grid_center.pop(symbol, None)
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        grid_bottom = grid_center - half_range
        grid_top = grid_center + half_range

        # Normalised position in grid: 0 = bottom, 1 = top
        norm_pos = (current_close - grid_bottom) / (grid_top - grid_bottom)
        # Clamp to [0, 1]
        norm_pos = max(0.0, min(1.0, norm_pos))

        # Near bottom → high exposure (buy dips); near top → zero exposure (sold out)
        # Map: 0 → 1.0 exposure, 1 → 0.0 exposure
        target_exposure = 1.0 - norm_pos
        # Clamp to [0.0, 1.0] (spot-only)
        target_exposure = max(0.0, min(1.0, target_exposure))

        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=target_exposure,
            conviction=1.0 - adx_values[-1] / 100.0,
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
