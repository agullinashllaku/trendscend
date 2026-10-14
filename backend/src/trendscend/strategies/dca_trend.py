"""DcaTrendStrategy — dollar-cost-averaging with EMA trend filter."""

from __future__ import annotations

from trendscend.indicators.trend import ema
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.signals import Signal
from trendscend.strategies.base import require_min_bars


class DcaTrendStrategy:
    """Incrementally builds a position (DCA) while price stays above EMA.

    Each call to ``generate_signal`` adds ``contribution_pct`` to the
    current exposure, up to a maximum of 1.0.  When ``pause_below_ema`` is
    ``True`` (default) and the close is below the EMA, the signal returns
    ``target_exposure=0.0`` — fully flat.

    Parameters:
        ema_period:       Period for the EMA trend filter.
        contribution_pct: Fraction of capital added per bar when active.
        pause_below_ema:  If True, return flat when close < EMA.

    Attributes:
        strategy_id: Fixed identifier ``"dca_trend"``.
    """

    strategy_id: str = "dca_trend"

    def __init__(
        self,
        ema_period: int = 200,
        contribution_pct: float = 0.10,
        pause_below_ema: bool = True,
    ) -> None:
        self.ema_period = ema_period
        self.contribution_pct = contribution_pct
        self.pause_below_ema = pause_below_ema
        # Per-symbol exposure tracking
        self._current_exposure: dict[str, float] = {}

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a DCA signal subject to EMA trend filter.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars.
            regime:  Ignored.

        Returns:
            ``Signal(target_exposure=0.0)`` if insufficient history or price
            is below the EMA (when ``pause_below_ema`` is True).
            Otherwise ``Signal(target_exposure=min(current + contribution_pct, 1.0))``.
        """
        # Guard: not enough bars for the EMA
        flat = require_min_bars(
            candles=candles,
            min_bars=self.ema_period,
            symbol=symbol,
            strategy_id=self.strategy_id,
        )
        if flat is not None:
            # Don't update internal state — reset to flat
            self._current_exposure[symbol] = 0.0
            return flat

        # Compute EMA and check trend filter
        ema_values = ema(candles, self.ema_period)
        latest_ema = ema_values[-1]
        latest_close = float(candles[-1].close)

        if self.pause_below_ema and latest_close < latest_ema:
            self._current_exposure[symbol] = 0.0
            return Signal(
                strategy_id=self.strategy_id,
                symbol=symbol,
                target_exposure=0.0,
                conviction=0.0,
                timeframe="4h",
                generated_at=candles[-1].timestamp,
            )

        # DCA: add contribution_pct to current exposure
        current = self._current_exposure.get(symbol, 0.0)
        new_exposure = min(current + self.contribution_pct, 1.0)
        self._current_exposure[symbol] = new_exposure

        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=new_exposure,
            conviction=1.0,
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
