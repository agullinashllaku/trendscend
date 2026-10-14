"""Pure metric functions for strategy evaluation.

All functions are stateless; they take an equity curve or trade log and
return a single scalar.  The equity curve type is
``list[tuple[datetime, Decimal]]`` — the same format produced by
:class:`~trendscend.backtester.portfolio.Portfolio`.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import Decimal

from trendscend.backtester.models import TradeRecord


def sharpe_ratio(
    equity_curve: list[tuple[datetime, Decimal]],
    periods_per_year: int = 252,
) -> float:
    """Annualised Sharpe ratio (risk-free rate = 0).

    Returns 0.0 when the equity curve has fewer than 2 points or
    when volatility is zero (constant equity).

    Args:
        equity_curve:    Ascending-time sequence of ``(timestamp, equity)`` pairs.
        periods_per_year: Number of return periods in one year (e.g. 252 for
                          daily, 8760 for hourly).

    Returns:
        Annualised Sharpe ratio as a plain ``float``.
    """
    returns = _period_returns(equity_curve)
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n
    variance = sum((r - mean_r) ** 2 for r in returns) / (n - 1)
    std_r = math.sqrt(variance) if variance > 0 else 0.0

    if std_r == 0.0:
        return 0.0

    return mean_r / std_r * math.sqrt(periods_per_year)


def sortino_ratio(
    equity_curve: list[tuple[datetime, Decimal]],
    periods_per_year: int = 252,
) -> float:
    """Annualised Sortino ratio (risk-free rate = 0).

    Uses the standard deviation of negative returns (centred on the mean
    of negative returns, with Bessel correction) as the downside deviation,
    matching the formula used in the engine.

    Returns 0.0 when there are fewer than 2 return periods or no downside
    deviation.

    Args:
        equity_curve:    Ascending-time sequence of ``(timestamp, equity)`` pairs.
        periods_per_year: Number of return periods in one year.

    Returns:
        Annualised Sortino ratio as a plain ``float``.
    """
    returns = _period_returns(equity_curve)
    if len(returns) < 2:
        return 0.0

    n = len(returns)
    mean_r = sum(returns) / n

    neg_returns = [r for r in returns if r < 0]
    if len(neg_returns) >= 2:
        n_neg = len(neg_returns)
        mean_neg = sum(neg_returns) / n_neg
        downside_var = sum((r - mean_neg) ** 2 for r in neg_returns) / (n_neg - 1)
        downside_std = math.sqrt(downside_var) if downside_var > 0 else 0.0
    elif neg_returns:
        downside_std = abs(neg_returns[0])
    else:
        return 0.0

    if downside_std == 0.0:
        return 0.0

    return mean_r / downside_std * math.sqrt(periods_per_year)


def max_drawdown(equity_curve: list[tuple[datetime, Decimal]]) -> float:
    """Maximum peak-to-trough drawdown as a positive fraction (0.0–1.0).

    Returns 0.0 for curves with fewer than 2 data points or for a
    monotonically non-decreasing curve.

    Args:
        equity_curve: Ascending-time sequence of ``(timestamp, equity)`` pairs.

    Returns:
        Maximum drawdown as a fraction, e.g. ``0.30`` for a 30% drawdown.
    """
    if len(equity_curve) < 2:
        return 0.0

    peak = float(equity_curve[0][1])
    max_dd = 0.0

    for _, eq in equity_curve[1:]:
        value = float(eq)
        if value > peak:
            peak = value
        elif peak > 0.0:
            dd = (peak - value) / peak
            if dd > max_dd:
                max_dd = dd

    return max_dd


def cagr(equity_curve: list[tuple[datetime, Decimal]]) -> float:
    """Compound Annual Growth Rate.

    Uses the time span between the first and last equity curve points.
    Returns 0.0 when the curve has fewer than 2 points or the time span
    is zero.

    Args:
        equity_curve: Ascending-time sequence of ``(timestamp, equity)`` pairs.

    Returns:
        CAGR as a fraction (e.g. ``0.25`` = 25% per year).
    """
    if len(equity_curve) < 2:
        return 0.0

    start_dt, start_eq = equity_curve[0]
    end_dt, end_eq = equity_curve[-1]

    total_days = (end_dt - start_dt).total_seconds() / 86400.0
    if total_days <= 0.0:
        return 0.0

    start_val = float(start_eq)
    end_val = float(end_eq)
    if start_val <= 0.0 or end_val <= 0.0:
        return 0.0

    return float((end_val / start_val) ** (365.0 / total_days)) - 1.0


def win_rate(trade_log: list[TradeRecord]) -> float:
    """Fraction of trades with positive PnL.

    Args:
        trade_log: List of completed :class:`~trendscend.backtester.models.TradeRecord`.

    Returns:
        Win rate in [0.0, 1.0]; returns 0.0 for an empty log.
    """
    if not trade_log:
        return 0.0
    wins = sum(1 for t in trade_log if t.pnl > Decimal("0"))
    return wins / len(trade_log)


def profit_factor(trade_log: list[TradeRecord]) -> float:
    """Ratio of gross profit to gross loss.

    Args:
        trade_log: List of completed :class:`~trendscend.backtester.models.TradeRecord`.

    Returns:
        Profit factor; returns ``0.0`` for an empty log and ``float('inf')``
        when there are no losing trades.
    """
    if not trade_log:
        return 0.0

    gross_profit = float(sum(t.pnl for t in trade_log if t.pnl > Decimal("0")))
    gross_loss = abs(float(sum(t.pnl for t in trade_log if t.pnl <= Decimal("0"))))

    if gross_loss == 0.0:
        return float("inf")
    return gross_profit / gross_loss


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _period_returns(equity_curve: list[tuple[datetime, Decimal]]) -> list[float]:
    """Convert an equity curve to a list of per-period simple returns."""
    equities = [float(eq) for _, eq in equity_curve]
    returns: list[float] = []
    for i in range(1, len(equities)):
        prev = equities[i - 1]
        if prev != 0.0:
            returns.append((equities[i] - prev) / prev)
    return returns
