"""HoldBaselineStrategy — always fully long, stateless."""

from __future__ import annotations

from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.signals import Signal


class HoldBaselineStrategy:
    """Unconditionally holds a full long position.

    This is the simplest possible strategy: buy once and never sell.
    It serves as a buy-and-hold benchmark in backtests.

    Attributes:
        strategy_id: Fixed identifier ``"hold_baseline"``.
    """

    strategy_id: str = "hold_baseline"

    def generate_signal(
        self,
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None = None,
    ) -> Signal:
        """Return ``target_exposure=1.0`` regardless of market conditions.

        Args:
            symbol:  Trading pair.
            candles: Closed OHLCV bars (at least one required).
            regime:  Ignored.

        Returns:
            Signal with ``target_exposure=1.0`` and ``conviction=1.0``.
        """
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=1.0,
            conviction=1.0,
            timeframe="4h",
            generated_at=candles[-1].timestamp,
        )
