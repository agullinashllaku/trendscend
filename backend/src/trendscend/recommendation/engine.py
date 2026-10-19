"""Recommendation engine: rank strategies by OOS walk-forward performance.

Strategies are ranked by a composite score that rewards Sharpe ratio and
penalises maximum drawdown.  The hold_baseline is always included when present
so users can compare against a passive benchmark.

Score formula: oos_sharpe_mean / (1 + oos_max_drawdown_worst)

Label: "Based on past data; not a prediction or financial advice."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from trendscend.research.walk_forward import generate_wf_splits, run_walk_forward
from trendscend.schemas.market import OHLCV
from trendscend.strategies.base import Strategy

_DISCLAIMER = "Based on past data; not a prediction or financial advice."


@dataclass
class StrategyRanking:
    strategy_id: str
    oos_sharpe_mean: float
    oos_max_drawdown_worst: float
    oos_cagr_mean: float
    score: float
    num_splits: int
    disclaimer: str = field(default=_DISCLAIMER)


def rank_strategies(
    strategies: list[Strategy],
    candles: list[OHLCV],
    symbol: str,
    timeframe: str,
    initial_capital: Decimal,
    train_months: int = 12,
    test_months: int = 3,
) -> list[StrategyRanking]:
    """Run walk-forward backtests and return strategies ranked by composite score.

    Returns an empty list if the candle history is too short to produce any
    walk-forward splits, or if ``strategies`` is empty.

    Args:
        strategies:      Strategy instances to evaluate.
        candles:         Full OHLCV history, ascending time.
        symbol:          Trading pair, e.g. "BTC/USDT".
        timeframe:       Bar duration string, e.g. "1d".
        initial_capital: Starting capital for each OOS run.
        train_months:    Walk-forward training window in calendar months.
        test_months:     Walk-forward test window / step size in months.

    Returns:
        List of :class:`StrategyRanking` sorted by ``score`` descending.
    """
    if not strategies or not candles:
        return []

    start = candles[0].timestamp
    end = candles[-1].timestamp
    splits = generate_wf_splits(start, end, train_months=train_months, test_months=test_months)
    if not splits:
        return []

    rankings: list[StrategyRanking] = []
    for strategy in strategies:
        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=initial_capital,
            timeframe=timeframe,
            symbol=symbol,
        )
        if report.num_splits == 0:
            continue
        score = (
            report.oos_sharpe_mean / (1.0 + report.oos_max_drawdown_worst)
            if (1.0 + report.oos_max_drawdown_worst) > 0
            else 0.0
        )
        rankings.append(
            StrategyRanking(
                strategy_id=report.strategy_id,
                oos_sharpe_mean=report.oos_sharpe_mean,
                oos_max_drawdown_worst=report.oos_max_drawdown_worst,
                oos_cagr_mean=report.oos_cagr_mean,
                score=score,
                num_splits=report.num_splits,
            )
        )

    rankings.sort(key=lambda r: r.score, reverse=True)
    return rankings
