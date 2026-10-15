"""Research utilities: walk-forward framework and metrics."""

from trendscend.research.metrics import (
    cagr,
    max_drawdown,
    profit_factor,
    sharpe_ratio,
    sortino_ratio,
    win_rate,
)
from trendscend.research.walk_forward import (
    WalkForwardReport,
    WFSplit,
    generate_wf_splits,
    run_walk_forward,
)

__all__ = [
    "WFSplit",
    "WalkForwardReport",
    "cagr",
    "generate_wf_splits",
    "max_drawdown",
    "profit_factor",
    "run_walk_forward",
    "sharpe_ratio",
    "sortino_ratio",
    "win_rate",
]
