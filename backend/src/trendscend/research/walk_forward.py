"""Walk-forward analysis framework.

The walk-forward approach simulates realistic out-of-sample (OOS) evaluation
by dividing the full history into consecutive train/test windows.  For Phase 1
strategies the train window is unused for parameter fitting (parameters are
fixed), but the infrastructure is in place for Phase 2+ optimisation.

Critical invariant (enforced by ``generate_wf_splits``):
    ``split.test_start > split.train_end`` STRICTLY for every split.
    This guarantees no data leakage between the train and test windows.
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from trendscend.backtester.engine import BacktestEngine, Strategy
from trendscend.backtester.models import BacktestResult
from trendscend.research.metrics import cagr as compute_cagr
from trendscend.research.metrics import max_drawdown as compute_max_drawdown
from trendscend.research.metrics import sharpe_ratio as compute_sharpe
from trendscend.schemas.market import OHLCV


@dataclass
class WFSplit:
    """A single train/test window for walk-forward analysis.

    Attributes:
        train_start: Inclusive start of the training period.
        train_end:   Exclusive end of the training period.
        test_start:  Inclusive start of the test (out-of-sample) period.
                     **Strictly greater than** ``train_end``.
        test_end:    Exclusive end of the test period.
    """

    train_start: datetime
    train_end: datetime  # exclusive
    test_start: datetime  # STRICT: test_start > train_end always
    test_end: datetime  # exclusive


@dataclass
class WalkForwardReport:
    """Aggregated out-of-sample statistics across all walk-forward splits.

    Attributes:
        strategy_id:            Identifier of the tested strategy.
        symbol:                 Trading pair, e.g. ``"BTC/USDT"``.
        timeframe:              Bar duration string, e.g. ``"1d"``.
        num_splits:             Number of OOS windows evaluated.
        oos_sharpe_mean:        Mean Sharpe ratio across OOS windows.
        oos_sharpe_std:         Std-dev of Sharpe ratios across OOS windows.
        oos_max_drawdown_mean:  Mean max drawdown across OOS windows.
        oos_max_drawdown_worst: Worst (largest) max drawdown seen in any window.
        oos_cagr_mean:          Mean CAGR across OOS windows.
        per_split_results:      Individual :class:`BacktestResult` per split.
    """

    strategy_id: str
    symbol: str
    timeframe: str
    num_splits: int
    oos_sharpe_mean: float
    oos_sharpe_std: float
    oos_max_drawdown_mean: float
    oos_max_drawdown_worst: float
    oos_cagr_mean: float
    per_split_results: list[BacktestResult]


def generate_wf_splits(
    start: datetime,
    end: datetime,
    train_months: int = 12,
    test_months: int = 3,
) -> list[WFSplit]:
    """Generate walk-forward train/test splits stepping forward by *test_months*.

    Each split has a training window of *train_months* immediately followed by
    a test window of *test_months*.  The series steps forward by *test_months*
    each iteration.  Generation stops when ``test_end > end``.

    **Critical invariant:** ``split.test_start > split.train_end`` strictly
    for every returned split.

    Args:
        start:        First date of the available data range (inclusive).
        end:          Last date of the available data range (exclusive).
        train_months: Length of each training window in calendar months.
        test_months:  Length of each test window in calendar months (also the
                      step size).

    Returns:
        Ordered list of :class:`WFSplit` objects; may be empty if the range is
        too short to produce even one split.
    """
    splits: list[WFSplit] = []
    cursor = start

    while True:
        train_start = cursor
        train_end = _add_months(train_start, train_months)

        # test_start is the day *after* train_end — strictly greater.
        # We advance by one day to guarantee the strict inequality even when
        # month arithmetic lands on a boundary.  For candle filtering this has
        # no practical effect because candles are compared with ``>=`` / ``<``.
        test_start = _add_months(train_start, train_months)
        # Guarantee strict inequality: test_start must be > train_end.
        # Since train_end == test_start at this point (both computed from
        # add_months), we advance test_start by one microsecond.
        if test_start <= train_end:
            test_start = train_end + timedelta(microseconds=1)

        test_end = _add_months(test_start, test_months)

        if test_end > end:
            break

        splits.append(
            WFSplit(
                train_start=train_start,
                train_end=train_end,
                test_start=test_start,
                test_end=test_end,
            )
        )

        # Advance cursor by test_months for the next window.
        cursor = _add_months(cursor, test_months)

    return splits


def run_walk_forward(
    strategy: Strategy,
    candles: list[OHLCV],
    splits: list[WFSplit],
    initial_capital: Decimal,
    exchange_id: str = "binance",
    timeframe: str = "1d",
    symbol: str = "BTC/USDT",
) -> WalkForwardReport:
    """Run the backtester on each OOS window and aggregate the results.

    For each split the test candles (``[split.test_start, split.test_end)``) are
    extracted from the full ``candles`` list and passed to
    :class:`~trendscend.backtester.engine.BacktestEngine`.  The full candle
    history is **not** passed to the engine per split; only the test slice is
    used for the OOS evaluation.  (Phase 1 strategies use fixed parameters so
    the train window is not needed for optimisation.)

    Args:
        strategy:        Strategy to evaluate.
        candles:         Full OHLCV candle series, ascending by time.
        splits:          Walk-forward splits generated by :func:`generate_wf_splits`.
        initial_capital: Starting capital for each OOS run.
        exchange_id:     Exchange identifier for fee lookup.
        timeframe:       Bar duration string used in result metadata.
        symbol:          Trading pair string used in result metadata.

    Returns:
        :class:`WalkForwardReport` with per-split results and aggregate stats.
    """
    engine = BacktestEngine()
    per_split_results: list[BacktestResult] = []

    for split in splits:
        # Extract OOS candles: [test_start, test_end)
        oos_candles = [c for c in candles if split.test_start <= c.timestamp < split.test_end]

        result = engine.run(
            candles=oos_candles,
            strategy=strategy,
            initial_capital=initial_capital,
            exchange_id=exchange_id,
            timeframe=timeframe,
            symbol=symbol,
        )
        per_split_results.append(result)

    # Aggregate OOS metrics from equity curves so we use the same pure
    # functions as the rest of the research pipeline.
    sharpes = [compute_sharpe(r.equity_curve) for r in per_split_results]
    drawdowns = [compute_max_drawdown(r.equity_curve) for r in per_split_results]
    cagrs = [compute_cagr(r.equity_curve) for r in per_split_results]

    num_splits = len(per_split_results)
    if num_splits == 0:
        return WalkForwardReport(
            strategy_id=strategy.strategy_id,
            symbol=symbol,
            timeframe=timeframe,
            num_splits=0,
            oos_sharpe_mean=0.0,
            oos_sharpe_std=0.0,
            oos_max_drawdown_mean=0.0,
            oos_max_drawdown_worst=0.0,
            oos_cagr_mean=0.0,
            per_split_results=[],
        )

    oos_sharpe_mean = sum(sharpes) / num_splits
    oos_sharpe_std = statistics.stdev(sharpes) if num_splits > 1 else 0.0
    oos_max_drawdown_mean = sum(drawdowns) / num_splits
    oos_max_drawdown_worst = max(drawdowns)
    oos_cagr_mean = sum(cagrs) / num_splits

    strategy_id = per_split_results[0].strategy_id

    return WalkForwardReport(
        strategy_id=strategy_id,
        symbol=symbol,
        timeframe=timeframe,
        num_splits=num_splits,
        oos_sharpe_mean=oos_sharpe_mean,
        oos_sharpe_std=oos_sharpe_std,
        oos_max_drawdown_mean=oos_max_drawdown_mean,
        oos_max_drawdown_worst=oos_max_drawdown_worst,
        oos_cagr_mean=oos_cagr_mean,
        per_split_results=per_split_results,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _add_months(dt: datetime, months: int) -> datetime:
    """Return *dt* advanced by *months* calendar months.

    Day clamping: if the resulting month has fewer days than the original day
    (e.g. Jan 31 + 1 month → Feb 28/29), the day is clamped to the last day
    of the target month.
    """
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)
