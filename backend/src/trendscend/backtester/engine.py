"""Event-driven backtester engine.

Design invariants
-----------------
* Signal at iteration ``i`` sees only ``candles[:i]`` (all CLOSED bars).
* Fill executes at ``candles[i].open`` with slippage — the *next* bar's
  open after the signal bar.  This eliminates look-ahead bias.
* ALL financial arithmetic uses ``Decimal``.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol, runtime_checkable

from trendscend.backtester.fees import FeeModel
from trendscend.backtester.models import BacktestResult
from trendscend.backtester.portfolio import InsufficientFundsError, Portfolio
from trendscend.schemas.market import OHLCV
from trendscend.schemas.signals import Signal

_ORDER_SIDE = Literal["buy", "sell"]

# Annualisation factors (periods per year)
_PERIODS_PER_YEAR: dict[str, int] = {
    "1d": 252,
    "4h": 252 * 6,  # 1512
    "1h": 252 * 24,  # 6048
    "15m": 252 * 96,
    "5m": 252 * 288,
    "1m": 252 * 1440,
}


@runtime_checkable
class Strategy(Protocol):
    """Minimal interface a strategy must satisfy."""

    strategy_id: str

    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal:
        """Return a ``Signal`` based on *candles* (closed bars only)."""
        ...


class BacktestEngine:
    """Event-driven backtester that processes a candle series bar-by-bar.

    Usage::

        engine = BacktestEngine()
        result = engine.run(candles, strategy, initial_capital=Decimal("10000"))
    """

    def run(
        self,
        candles: list[OHLCV],
        strategy: Strategy,
        initial_capital: Decimal,
        exchange_id: str = "binance",
        timeframe: str = "1d",
        symbol: str = "BTC/USDT",
    ) -> BacktestResult:
        """Run the backtest and return a ``BacktestResult``.

        Args:
            candles:         Full OHLCV series in ascending time order.
            strategy:        Strategy object implementing :class:`Strategy`.
            initial_capital: Starting quote-currency balance.
            exchange_id:     Exchange for fee lookup (``"binance"`` or ``"bybit"``).
            timeframe:       Bar duration string, e.g. ``"4h"``.
            symbol:          Trading pair, e.g. ``"BTC/USDT"``.

        Returns:
            :class:`BacktestResult` with metrics and equity curve.
        """
        if len(candles) < 2:
            return self._empty_result(
                strategy, symbol, timeframe, exchange_id, initial_capital, candles
            )

        fee_model = FeeModel(exchange_id=exchange_id)
        portfolio = Portfolio(initial_cash=initial_capital)
        current_exposure: float = 0.0  # fraction of capital currently invested

        # Mark-to-market at bar 0 (starting state)
        portfolio.mark_to_market({symbol: candles[0].close}, candles[0].timestamp)

        # ── Main loop ─────────────────────────────────────────────────────────
        # For i in range(1, len(candles)):
        #   - history = candles[:i]  (closed bars the strategy can see)
        #   - signal derived from history
        #   - fill executes at candles[i].open (next bar's open)
        #   - mark-to-market at candles[i].close
        for i in range(1, len(candles)):
            history = candles[:i]  # closed bars only — no look-ahead
            current_bar = candles[i]  # bar we fill into

            signal: Signal = strategy.generate_signal(symbol, history)
            target_exposure: float = signal.target_exposure

            if target_exposure != current_exposure:
                filled = self._rebalance(
                    portfolio=portfolio,
                    fee_model=fee_model,
                    symbol=symbol,
                    current_bar=current_bar,
                    current_exposure=current_exposure,
                    target_exposure=target_exposure,
                    initial_capital=initial_capital,
                )
                if filled:
                    current_exposure = target_exposure

            portfolio.mark_to_market({symbol: current_bar.close}, current_bar.timestamp)

        # ── Compute metrics ───────────────────────────────────────────────────
        final_prices = {symbol: candles[-1].close}
        final_equity = portfolio.equity(final_prices)

        return self._build_result(
            strategy=strategy,
            symbol=symbol,
            timeframe=timeframe,
            exchange_id=exchange_id,
            initial_capital=initial_capital,
            final_equity=final_equity,
            portfolio=portfolio,
            candles=candles,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _rebalance(
        self,
        portfolio: Portfolio,
        fee_model: FeeModel,
        symbol: str,
        current_bar: OHLCV,
        current_exposure: float,
        target_exposure: float,
        initial_capital: Decimal,
    ) -> bool:
        """Adjust the portfolio from *current_exposure* to *target_exposure*.

        Fill price is ``current_bar.open`` with slippage applied.
        Uses taker fee.  For spot-only MVP we only go long (exposure >= 0).

        Returns:
            True if a fill was executed, False otherwise.
        """
        going_long = target_exposure > current_exposure

        # Slippage-adjusted fill price
        raw_open = current_bar.open
        order_side_str: _ORDER_SIDE = "buy" if going_long else "sell"
        fill_price = fee_model.apply_slippage(raw_open, order_side_str)

        if going_long:
            # Calculate how much capital to deploy
            # Use current portfolio equity snapshot for sizing
            eq_estimate = portfolio.cash + sum(
                portfolio.positions.get(symbol, Decimal("0")) * raw_open
                for _ in [None]  # single symbol
            )
            target_value = eq_estimate * Decimal(str(target_exposure))
            current_position_value = portfolio.positions.get(symbol, Decimal("0")) * fill_price
            delta_value = target_value - current_position_value
            if delta_value <= Decimal("0"):
                return False
            size = delta_value / fill_price
            if size <= Decimal("0"):
                return False
            fee = fee_model.compute_fee(size, fill_price, "taker")
            # Check we can afford it; trim if needed
            affordable = portfolio.cash - fee
            if affordable <= Decimal("0"):
                return False
            if size * fill_price > affordable:
                size = affordable / fill_price
                fee = fee_model.compute_fee(size, fill_price, "taker")
            try:
                portfolio.apply_fill(symbol, "buy", size, fill_price, fee, current_bar.timestamp)
                return True
            except InsufficientFundsError:
                return False  # can't buy — skip

        else:
            # Sell down to target exposure
            current_pos = portfolio.positions.get(symbol, Decimal("0"))
            if current_pos <= Decimal("0"):
                return False
            if target_exposure <= 0.0:
                # Close entire position
                size = current_pos
            else:
                # Partial sell — not needed for spot-only MVP but handle anyway
                eq_estimate = portfolio.cash + current_pos * fill_price
                target_value = eq_estimate * Decimal(str(target_exposure))
                current_value = current_pos * fill_price
                delta_value = current_value - target_value
                size = delta_value / fill_price
                size = min(size, current_pos)

            if size <= Decimal("0"):
                return False
            fee = fee_model.compute_fee(size, fill_price, "taker")
            portfolio.apply_fill(symbol, "sell", size, fill_price, fee, current_bar.timestamp)
            return True

    def _compute_metrics(
        self,
        portfolio: Portfolio,
        initial_capital: Decimal,
        candles: list[OHLCV],
        timeframe: str,
    ) -> tuple[float, float, float, float, float, float]:
        """Compute CAGR, Sharpe, Sortino, max_drawdown, win_rate, profit_factor.

        Returns:
            ``(cagr, sharpe, sortino, max_drawdown, win_rate, profit_factor)``
        """
        eq_history = portfolio.equity_history
        max_dd = portfolio.max_drawdown

        # ── CAGR ──────────────────────────────────────────────────────────────
        if len(eq_history) >= 2 and initial_capital > Decimal("0"):
            start_dt = eq_history[0][0]
            end_dt = eq_history[-1][0]
            total_days = max((end_dt - start_dt).total_seconds() / 86400.0, 1.0)
            final_eq = float(eq_history[-1][1])
            init_cap = float(initial_capital)
            if final_eq > 0 and init_cap > 0:
                cagr = (final_eq / init_cap) ** (365.0 / total_days) - 1.0
            else:
                cagr = 0.0
        else:
            cagr = 0.0

        # ── Return series ─────────────────────────────────────────────────────
        periods_per_year = _PERIODS_PER_YEAR.get(timeframe, 252)
        equities = [float(eq) for _, eq in eq_history]
        returns: list[float] = []
        for j in range(1, len(equities)):
            prev = equities[j - 1]
            if prev != 0.0:
                returns.append((equities[j] - prev) / prev)

        if len(returns) < 2:
            return cagr, 0.0, 0.0, max_dd, 0.0, 0.0

        n = len(returns)
        mean_r = sum(returns) / n
        variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
        std_r = math.sqrt(variance) if variance > 0 else 0.0

        # ── Sharpe ────────────────────────────────────────────────────────────
        # Risk-free rate = 0; annualise with sqrt(periods_per_year)
        sharpe = (mean_r / std_r * math.sqrt(periods_per_year)) if std_r > 0 else 0.0

        # ── Sortino ───────────────────────────────────────────────────────────
        # Note: downside is std(neg_returns) — variance centred on mean of negative
        # returns with Bessel correction (n-1), NOT the standard semi-deviation
        # sqrt(mean(r^2 for r < 0)). This formula matches some third-party tools
        # but differs from others; verify before comparing external results.
        neg_returns = [r for r in returns if r < 0]
        if len(neg_returns) >= 2:
            n_neg = len(neg_returns)
            mean_neg = sum(neg_returns) / n_neg
            downside_var = sum((r - mean_neg) ** 2 for r in neg_returns) / (n_neg - 1)
            downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
            sortino = (
                (mean_r / downside_std * math.sqrt(periods_per_year)) if downside_std > 0 else 0.0
            )
        elif neg_returns:
            # Single negative return — use it directly as downside std
            downside_std = abs(neg_returns[0])
            sortino = (
                (mean_r / downside_std * math.sqrt(periods_per_year)) if downside_std > 0 else 0.0
            )
        else:
            sortino = 0.0

        # ── Win rate & profit factor ───────────────────────────────────────────
        trade_log = portfolio.trade_log
        num_trades = len(trade_log)
        if num_trades > 0:
            wins = [t for t in trade_log if t.pnl > Decimal("0")]
            losses = [t for t in trade_log if t.pnl <= Decimal("0")]
            win_rate = len(wins) / num_trades
            gross_profit = float(sum(t.pnl for t in wins)) if wins else 0.0
            gross_loss = abs(float(sum(t.pnl for t in losses))) if losses else 0.0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        else:
            win_rate = 0.0
            profit_factor = 0.0

        return cagr, sharpe, sortino, max_dd, win_rate, profit_factor

    def _build_result(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str,
        exchange_id: str,
        initial_capital: Decimal,
        final_equity: Decimal,
        portfolio: Portfolio,
        candles: list[OHLCV],
    ) -> BacktestResult:
        cagr, sharpe, sortino, max_dd, win_rate, profit_factor = self._compute_metrics(
            portfolio, initial_capital, candles, timeframe
        )
        eq_history = portfolio.equity_history
        start_dt = eq_history[0][0] if eq_history else candles[0].timestamp
        end_dt = eq_history[-1][0] if eq_history else candles[-1].timestamp

        return BacktestResult(
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            exchange_id=exchange_id,
            start=start_dt,
            end=end_dt,
            initial_capital=initial_capital,
            final_equity=final_equity,
            cagr=cagr,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            num_trades=portfolio.fill_count,
            win_rate=win_rate,
            profit_factor=profit_factor,
            equity_curve=list(eq_history),
            trade_log=list(portfolio.trade_log),
        )

    def _empty_result(
        self,
        strategy: Strategy,
        symbol: str,
        timeframe: str,
        exchange_id: str,
        initial_capital: Decimal,
        candles: list[OHLCV],
    ) -> BacktestResult:
        now = candles[0].timestamp if candles else datetime.now()
        return BacktestResult(
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            exchange_id=exchange_id,
            start=now,
            end=now,
            initial_capital=initial_capital,
            final_equity=initial_capital,
            cagr=0.0,
            sharpe_ratio=0.0,
            sortino_ratio=0.0,
            max_drawdown=0.0,
            num_trades=0,
            win_rate=0.0,
            profit_factor=0.0,
            equity_curve=[],
            trade_log=[],
        )
