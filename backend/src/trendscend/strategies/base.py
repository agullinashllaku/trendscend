"""Strategy Protocol and shared helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.signals import Signal


@runtime_checkable
class Strategy(Protocol):
    """Structural interface that all strategies must satisfy.

    Using ``@runtime_checkable`` allows ``isinstance(obj, Strategy)`` checks
    without requiring a concrete base class.  Strategies are plain classes —
    not subclasses of anything — which keeps them composable and testable in
    isolation.
    """

    strategy_id: str

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],  # closed candles only, ascending time
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return a trading signal based on closed candles and optional regime.

        Args:
            symbol:  Trading pair, e.g. ``"BTC/USDT"``.
            candles: Closed OHLCV bars in ascending time order (no look-ahead).
            regime:  Optional market-regime context; strategies may ignore it.

        Returns:
            A :class:`~trendscend.schemas.signals.Signal` with
            ``target_exposure`` in [-1.0, 1.0].
        """
        ...


def require_min_bars(
    candles: list[OHLCV],
    min_bars: int,
    symbol: str,
    strategy_id: str,
    timeframe: str = "4h",
) -> Signal | None:
    """Return a flat ``Signal`` if there is insufficient history, else ``None``.

    Strategies call this at the top of ``generate_signal``; if the return value
    is not ``None`` they should return it immediately.

    Args:
        candles:     Candle history passed to ``generate_signal``.
        min_bars:    Minimum number of bars required before the strategy can act.
        symbol:      Trading pair (forwarded to Signal).
        strategy_id: Strategy identifier (forwarded to Signal).
        timeframe:   Bar duration string (forwarded to Signal).

    Returns:
        A flat ``Signal(target_exposure=0.0)`` when ``len(candles) < min_bars``,
        otherwise ``None``.
    """
    if len(candles) < min_bars:
        ts = candles[-1].timestamp if candles else datetime.now(UTC)
        return Signal(
            strategy_id=strategy_id,
            symbol=symbol,
            target_exposure=0.0,
            conviction=0.0,
            timeframe=timeframe,
            generated_at=ts,
        )
    return None
