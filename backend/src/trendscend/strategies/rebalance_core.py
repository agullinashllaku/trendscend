"""RebalanceCoreStrategy — periodic rebalancing with drift guard."""

from __future__ import annotations

import calendar

from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.signals import Signal


def _is_last_bar_of_month(candles: list[OHLCV]) -> bool:
    """Return True if the last candle's date is the last day of its month."""
    last_dt = candles[-1].timestamp
    last_day = calendar.monthrange(last_dt.year, last_dt.month)[1]
    return last_dt.day == last_day


def _is_last_bar_of_week(candles: list[OHLCV]) -> bool:
    """Return True if the last candle falls on a Friday (weekday==4)."""
    return candles[-1].timestamp.weekday() == 4  # Friday


class RebalanceCoreStrategy:
    """Rebalances a single asset back to a target weight on calendar triggers
    or when the current exposure drifts beyond a threshold.

    Parameters:
        target_weight:        Desired exposure fraction [0.0, 1.0].
        rebalance_threshold:  Drift (|current - target|) that forces a rebalance.
        rebalance_calendar:   ``"monthly"``, ``"weekly"``, or ``"threshold_only"``.

    Attributes:
        strategy_id: Fixed identifier ``"rebalance_core"``.
    """

    strategy_id: str = "rebalance_core"

    def __init__(
        self,
        target_weight: float = 1.0,
        rebalance_threshold: float = 0.05,
        rebalance_calendar: str = "monthly",
    ) -> None:
        self.target_weight = target_weight
        self.rebalance_threshold = rebalance_threshold
        self.rebalance_calendar = rebalance_calendar
        # Per-symbol internal exposure tracking
        self._current_exposure: dict[str, float] = {}

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a rebalance signal if drift or calendar condition triggers.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars (at least one required).
            regime:  Ignored.

        Returns:
            Signal with ``target_exposure=target_weight`` when rebalance
            is triggered, or ``target_exposure=_current_exposure`` otherwise.
        """
        current = self._current_exposure.get(symbol, 0.0)
        drift = abs(current - self.target_weight)

        # Evaluate triggers
        drift_trigger = drift > self.rebalance_threshold

        calendar_trigger = False
        if self.rebalance_calendar == "monthly":
            calendar_trigger = _is_last_bar_of_month(candles)
        elif self.rebalance_calendar == "weekly":
            calendar_trigger = _is_last_bar_of_week(candles)
        # "threshold_only" → calendar_trigger stays False

        if drift_trigger or calendar_trigger:
            target = self.target_weight
        else:
            target = current

        # Update internal state
        self._current_exposure[symbol] = target

        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=target,
            conviction=1.0,
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
