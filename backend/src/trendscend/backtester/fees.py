"""Fee and slippage models for the backtester.

All financial values use Decimal to avoid floating-point drift.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

TAKER_FEE: dict[str, Decimal] = {
    "binance": Decimal("0.001"),
    "bybit": Decimal("0.001"),
}
MAKER_FEE: dict[str, Decimal] = {
    "binance": Decimal("0.0008"),
    "bybit": Decimal("0.0002"),
}
SLIPPAGE_PER_SIDE: Decimal = Decimal("0.0005")

_SIDE_LITERAL = Literal["taker", "maker"]
_ORDER_SIDE_LITERAL = Literal["buy", "sell"]


@dataclass
class FeeModel:
    """Per-exchange fee + slippage model.

    All inputs and outputs are ``Decimal``.
    """

    exchange_id: str

    def compute_fee(
        self,
        size_base: Decimal,
        price: Decimal,
        side: _SIDE_LITERAL,
    ) -> Decimal:
        """Return the fee for a fill.

        Args:
            size_base: Amount in base currency (e.g. BTC).
            price:     Fill price in quote currency (e.g. USDT per BTC).
            side:      ``"taker"`` or ``"maker"``.

        Returns:
            Fee in quote currency as a ``Decimal``.

        Raises:
            ValueError: If *side* is not ``"taker"`` or ``"maker"``.
        """
        notional = size_base * price
        if side == "taker":
            return notional * TAKER_FEE[self.exchange_id]
        if side == "maker":
            return notional * MAKER_FEE[self.exchange_id]
        raise ValueError(f"Unknown fee side {side!r}; expected 'taker' or 'maker'")

    def apply_slippage(self, price: Decimal, side: _ORDER_SIDE_LITERAL) -> Decimal:
        """Adjust *price* for market-impact slippage.

        Buys fill slightly higher; sells fill slightly lower.

        Args:
            price: Nominal fill price.
            side:  ``"buy"`` or ``"sell"``.

        Returns:
            Adjusted price as a ``Decimal``.
        """
        if side == "buy":
            return price * (Decimal("1") + SLIPPAGE_PER_SIDE)
        return price * (Decimal("1") - SLIPPAGE_PER_SIDE)
