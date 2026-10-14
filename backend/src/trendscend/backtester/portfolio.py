"""Portfolio state tracker for the backtester.

All financial values use ``Decimal``.  The portfolio is intentionally
pure-Python with no external I/O so it can be unit-tested without any
mocking.
"""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from trendscend.backtester.models import OrderSide, TradeRecord


class InsufficientFundsError(Exception):
    """Raised when a BUY would make the portfolio's cash balance negative."""


_SIDE = Literal["buy", "sell"]


class Portfolio:
    """Tracks cash, positions, equity history and drawdown.

    Attributes:
        cash:           Current quote-currency balance.
        positions:      ``{symbol: base_currency_size}`` mapping.
        equity_history: Ordered list of ``(timestamp, equity)`` snapshots.
        peak_equity:    Running maximum equity seen so far.
        trade_log:      List of completed ``TradeRecord`` objects.
    """

    def __init__(self, initial_cash: Decimal) -> None:
        self.cash: Decimal = initial_cash
        self.positions: dict[str, Decimal] = {}
        self.equity_history: list[tuple[datetime, Decimal]] = []
        self.peak_equity: Decimal = initial_cash
        self.trade_log: list[TradeRecord] = []
        self._max_drawdown: float = 0.0
        self.fill_count: int = 0  # total number of executed fills (buy + sell)

        # Track open "long" entries for PnL book-keeping
        # symbol -> list of (entry_price, size, fee, timestamp)
        self._open_entries: dict[str, list[tuple[Decimal, Decimal, Decimal, datetime]]] = {}

    # ── Public interface ──────────────────────────────────────────────────────

    def apply_fill(
        self,
        symbol: str,
        side: _SIDE,
        size: Decimal,
        fill_price: Decimal,
        fee: Decimal,
        timestamp: datetime,
    ) -> None:
        """Apply a filled order to the portfolio state.

        Args:
            symbol:     Trading pair, e.g. ``"BTC/USDT"``.
            side:       ``"buy"`` or ``"sell"``.
            size:       Amount in base currency.
            fill_price: Execution price in quote currency.
            fee:        Fee in quote currency.
            timestamp:  Fill timestamp.

        Raises:
            InsufficientFundsError: If a BUY would result in negative cash.
        """
        notional = fill_price * size

        if side == "buy":
            cost = notional + fee
            if self.cash - cost < Decimal("0"):
                raise InsufficientFundsError(f"Insufficient funds: need {cost}, have {self.cash}")
            self.cash -= cost
            self.positions[symbol] = self.positions.get(symbol, Decimal("0")) + size
            self.fill_count += 1
            # Track entry for PnL
            if symbol not in self._open_entries:
                self._open_entries[symbol] = []
            self._open_entries[symbol].append((fill_price, size, fee, timestamp))

        elif side == "sell":
            self.cash += notional - fee
            self.fill_count += 1
            current = self.positions.get(symbol, Decimal("0"))
            new_pos = current - size
            self.positions[symbol] = new_pos if new_pos > Decimal("0") else Decimal("0")

            # Match against oldest entries (FIFO) to record closed trades
            remaining_to_close = size
            while remaining_to_close > Decimal("0") and self._open_entries.get(symbol):
                entry_price, entry_size, entry_fee, entry_time = self._open_entries[symbol][0]
                if entry_size <= remaining_to_close:
                    # Entire entry consumed
                    closed_size = entry_size
                    self._open_entries[symbol].pop(0)
                else:
                    # Partial close
                    closed_size = remaining_to_close
                    self._open_entries[symbol][0] = (
                        entry_price,
                        entry_size - closed_size,
                        entry_fee * (entry_size - closed_size) / entry_size,
                        entry_time,
                    )
                remaining_to_close -= closed_size

                # Proportional exit fee
                exit_fee_portion = fee * closed_size / size
                pnl = (
                    (fill_price - entry_price) * closed_size
                    - entry_fee
                    * (closed_size / entry_size if entry_size > Decimal("0") else Decimal("0"))
                    - exit_fee_portion
                )
                pnl_pct = float(pnl / (entry_price * closed_size)) if entry_price > 0 else 0.0

                record = TradeRecord(
                    symbol=symbol,
                    side=OrderSide.BUY,  # we record entry side
                    entry_price=entry_price,
                    exit_price=fill_price,
                    size=closed_size,
                    entry_fee=(
                        entry_fee * closed_size / entry_size if entry_size > 0 else Decimal("0")
                    ),
                    exit_fee=exit_fee_portion,
                    entry_time=entry_time,
                    exit_time=timestamp,
                    pnl=pnl,
                    pnl_pct=pnl_pct,
                )
                self.trade_log.append(record)

    def mark_to_market(
        self,
        prices: dict[str, Decimal],
        timestamp: datetime,
    ) -> None:
        """Compute current equity, record it in history, and update drawdown.

        Args:
            prices:    ``{symbol: mark_price}`` mapping.
            timestamp: Bar timestamp for the equity curve.
        """
        eq = self.equity(prices)
        self.equity_history.append((timestamp, eq))
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity > Decimal("0"):
            dd = float((self.peak_equity - eq) / self.peak_equity)
            if dd > self._max_drawdown:
                self._max_drawdown = dd

    def equity(self, prices: dict[str, Decimal]) -> Decimal:
        """Return current portfolio equity.

        ``equity = cash + sum(position_size * mark_price for each position)``

        Args:
            prices: ``{symbol: mark_price}`` mapping.

        Returns:
            Total portfolio value in quote currency.
        """
        position_value = sum(
            size * prices[symbol]
            for symbol, size in self.positions.items()
            if symbol in prices and size > Decimal("0")
        )
        return self.cash + position_value

    @property
    def current_drawdown(self) -> float:
        """Instantaneous drawdown from peak equity (no mark price — use last history)."""
        if not self.equity_history or self.peak_equity <= Decimal("0"):
            return 0.0
        last_equity = self.equity_history[-1][1]
        dd = float((self.peak_equity - last_equity) / self.peak_equity)
        return max(0.0, dd)

    @property
    def max_drawdown(self) -> float:
        """Maximum drawdown seen since inception (fraction, 0..1)."""
        return self._max_drawdown
