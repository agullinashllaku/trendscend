"""Tests for pure metric functions in trendscend.research.metrics."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trendscend.backtester.models import TradeRecord
from trendscend.research.metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    win_rate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _equity_curve(
    values: list[float],
    start: datetime | None = None,
    step_days: int = 1,
) -> list[tuple[datetime, Decimal]]:
    """Build an equity curve list from a plain list of floats."""
    base = start or datetime(2022, 1, 1, tzinfo=UTC)
    return [(base + timedelta(days=i * step_days), Decimal(str(v))) for i, v in enumerate(values)]


def _trade(pnl: float) -> TradeRecord:
    t = TradeRecord()
    t.pnl = Decimal(str(pnl))
    return t


# ---------------------------------------------------------------------------
# sharpe_ratio
# ---------------------------------------------------------------------------


class TestSharpeRatio:
    def test_zero_volatility_returns_zero(self) -> None:
        """Constant equity curve → Sharpe == 0.0, not NaN or exception."""
        curve = _equity_curve([1000.0] * 50)
        result = sharpe_ratio(curve)
        assert result == 0.0
        assert not math.isnan(result)

    def test_empty_curve_returns_zero(self) -> None:
        assert sharpe_ratio([]) == 0.0

    def test_single_point_returns_zero(self) -> None:
        assert sharpe_ratio(_equity_curve([1000.0])) == 0.0

    def test_positive_trend_gives_positive_sharpe(self) -> None:
        """Steadily rising equity should give positive Sharpe."""
        values = [1000.0 + i * 5 for i in range(100)]
        # Add tiny noise to avoid zero variance
        noisy = [v + (0.01 if i % 2 == 0 else -0.01) for i, v in enumerate(values)]
        curve = _equity_curve(noisy)
        result = sharpe_ratio(curve)
        assert result > 0.0

    def test_annualisation_factor(self) -> None:
        """Passing periods_per_year=1 should produce a different result than 252."""
        values = [1000.0 + i * 2 + (1.0 if i % 3 == 0 else -0.5) for i in range(60)]
        curve = _equity_curve(values)
        sharpe_252 = sharpe_ratio(curve, periods_per_year=252)
        sharpe_1 = sharpe_ratio(curve, periods_per_year=1)
        assert abs(sharpe_252) > abs(sharpe_1)


# ---------------------------------------------------------------------------
# sortino_ratio
# ---------------------------------------------------------------------------


class TestSortinoRatio:
    def test_no_negative_returns_gives_zero(self) -> None:
        """If there are no negative returns, Sortino returns 0.0."""
        # Monotonically rising curve — no negative periods
        curve = _equity_curve([float(i + 1000) for i in range(50)])
        result = sortino_ratio(curve)
        assert result == 0.0

    def test_empty_curve_returns_zero(self) -> None:
        assert sortino_ratio([]) == 0.0

    def test_mixed_returns_gives_finite_value(self) -> None:
        """Mixed positive/negative returns should give a finite, non-NaN result."""
        values = [1000.0, 1050.0, 980.0, 1100.0, 950.0, 1200.0, 1150.0, 1300.0]
        curve = _equity_curve(values)
        result = sortino_ratio(curve)
        assert math.isfinite(result)


# ---------------------------------------------------------------------------
# max_drawdown
# ---------------------------------------------------------------------------


class TestMaxDrawdown:
    def test_monotonically_rising_is_zero(self) -> None:
        """No drawdown on a curve that only goes up."""
        curve = _equity_curve([1000.0 + i * 10 for i in range(100)])
        assert max_drawdown(curve) == 0.0

    def test_thirty_percent_drop_then_recover(self) -> None:
        """Peak 1000 → trough 700 → recover to 1000: drawdown = 0.30."""
        values = [1000.0, 900.0, 800.0, 700.0, 800.0, 900.0, 1000.0]
        curve = _equity_curve(values)
        result = max_drawdown(curve)
        assert abs(result - 0.30) < 1e-6, f"Expected 0.30, got {result}"

    def test_empty_curve_returns_zero(self) -> None:
        assert max_drawdown([]) == 0.0

    def test_single_point_returns_zero(self) -> None:
        assert max_drawdown(_equity_curve([1000.0])) == 0.0

    def test_partial_recovery(self) -> None:
        """Peak 1000 → trough 600 (40% dd); no recovery — result == 0.40."""
        values = [1000.0, 950.0, 850.0, 750.0, 700.0, 650.0, 600.0]
        curve = _equity_curve(values)
        result = max_drawdown(curve)
        assert abs(result - 0.40) < 1e-6

    def test_multiple_drawdowns_returns_worst(self) -> None:
        """Returns the worst (deepest) drawdown when there are multiple peaks."""
        # First dd: 1000 → 800 = 20%; second dd: 1100 → 880 = 20%;
        # third dd: 1200 → 840 = 30% (worst)
        values = [
            1000.0,
            800.0,  # dd1 start
            1100.0,  # new peak
            880.0,  # dd2
            1200.0,  # new peak
            840.0,  # dd3 (30%)
        ]
        curve = _equity_curve(values)
        result = max_drawdown(curve)
        assert abs(result - 0.30) < 1e-6


# ---------------------------------------------------------------------------
# cagr
# ---------------------------------------------------------------------------


class TestCagr:
    def test_double_equity_over_two_years(self) -> None:
        """2× equity over 2 years: CAGR = 2^(1/2) - 1 ≈ 0.41421."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=730)  # ~2 years
        curve = [(start, Decimal("1000")), (end, Decimal("2000"))]
        result = cagr(curve)
        expected = 2.0**0.5 - 1.0  # ≈ 0.41421
        assert abs(result - expected) < 1e-4, f"Expected ~0.414, got {result}"

    def test_flat_equity_gives_zero_cagr(self) -> None:
        """No growth → CAGR == 0.0."""
        start = datetime(2021, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=365)
        curve = [(start, Decimal("1000")), (end, Decimal("1000"))]
        result = cagr(curve)
        assert abs(result) < 1e-9

    def test_empty_curve_returns_zero(self) -> None:
        assert cagr([]) == 0.0

    def test_single_point_returns_zero(self) -> None:
        start = datetime(2021, 1, 1, tzinfo=UTC)
        assert cagr([(start, Decimal("1000"))]) == 0.0

    def test_one_year_doubling_gives_100_percent(self) -> None:
        """1× → 2× over one year: CAGR = 1.0 (100%)."""
        start = datetime(2021, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=365)
        curve = [(start, Decimal("1000")), (end, Decimal("2000"))]
        result = cagr(curve)
        assert abs(result - 1.0) < 1e-3

    def test_declining_equity_gives_negative_cagr(self) -> None:
        """Equity dropping to half over 2 years → negative CAGR."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = start + timedelta(days=730)
        curve = [(start, Decimal("1000")), (end, Decimal("500"))]
        result = cagr(curve)
        assert result < 0.0


# ---------------------------------------------------------------------------
# win_rate
# ---------------------------------------------------------------------------


class TestWinRate:
    def test_all_wins(self) -> None:
        trades = [_trade(100.0), _trade(200.0), _trade(50.0)]
        assert win_rate(trades) == 1.0

    def test_all_losses(self) -> None:
        trades = [_trade(-100.0), _trade(-50.0)]
        assert win_rate(trades) == 0.0

    def test_half_wins(self) -> None:
        trades = [_trade(100.0), _trade(-100.0)]
        assert abs(win_rate(trades) - 0.5) < 1e-9

    def test_empty_log_returns_zero(self) -> None:
        assert win_rate([]) == 0.0

    def test_break_even_trade_counts_as_loss(self) -> None:
        """PnL == 0 is not a win (pnl > 0 required)."""
        trades = [_trade(0.0)]
        assert win_rate(trades) == 0.0


# ---------------------------------------------------------------------------
# profit_factor
# ---------------------------------------------------------------------------


class TestProfitFactor:
    def test_equal_profits_and_losses(self) -> None:
        trades = [_trade(100.0), _trade(-100.0)]
        assert abs(profit_factor(trades) - 1.0) < 1e-9

    def test_no_losses_returns_inf(self) -> None:
        trades = [_trade(100.0), _trade(200.0)]
        result = profit_factor(trades)
        assert result == float("inf")

    def test_no_wins_returns_zero(self) -> None:
        trades = [_trade(-100.0), _trade(-50.0)]
        assert profit_factor(trades) == 0.0

    def test_empty_log_returns_zero(self) -> None:
        assert profit_factor([]) == 0.0

    def test_two_to_one_ratio(self) -> None:
        """$200 gross profit, $100 gross loss → profit factor == 2.0."""
        trades = [_trade(200.0), _trade(-100.0)]
        assert abs(profit_factor(trades) - 2.0) < 1e-9
