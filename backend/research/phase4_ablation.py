"""Phase 4 ablation: strategies-only vs Coordinator (code-side weighting).

Runs two paper trading simulations over identical 3-month OOS windows drawn
from 18 months of synthetic trending data, and compares Sharpe, drawdown and
CAGR between:

  Mode A — strategies-only:   single TsmomStrategy via PaperEngine.step
  Mode B — with Coordinator:  [TsmomStrategy, DcaTrendStrategy] via
                               PaperEngine.step_multi + Coordinator(llm_enabled=False)

Usage:
    cd backend && uv run python research/phase4_ablation.py
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trendscend.coordinator.coordinator import Coordinator
from trendscend.paper.engine import PaperEngine
from trendscend.research.metrics import cagr, max_drawdown, sharpe_ratio
from trendscend.research.walk_forward import generate_wf_splits
from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.market import OHLCV
from trendscend.schemas.risk import RiskStyle
from trendscend.strategies.dca_trend import DcaTrendStrategy
from trendscend.strategies.hold_baseline import HoldBaselineStrategy
from trendscend.strategies.tsmom import TsmomStrategy

# ── Synthetic data generation ────────────────────────────────────────────────


def _make_candles(n_days: int, seed_price: float = 30_000.0, trend: float = 5.0) -> list[OHLCV]:
    """Create synthetic daily OHLCV candles with a gentle uptrend."""
    base = datetime(2022, 1, 1, tzinfo=UTC)
    candles = []
    price = seed_price
    for i in range(n_days):
        p = Decimal(str(round(price, 2)))
        candles.append(
            OHLCV(
                timestamp=base + timedelta(days=i),
                open=p,
                high=p * Decimal("1.01"),
                low=p * Decimal("0.99"),
                close=p,
                volume=Decimal("1000"),
            )
        )
        price += trend
    return candles


# ── Paper run helpers ─────────────────────────────────────────────────────────


def _init_state(bot_id=None) -> BotRiskState:  # type: ignore[type-arg]
    now = datetime.now(UTC)
    allocation = Decimal("10000")
    _id = bot_id or uuid4()
    return BotRiskState(
        bot_id=_id,
        style=RiskStyle.BALANCED,
        allocation=allocation,
        current_equity=allocation,
        equity_peak=allocation,
        daily_start_equity=allocation,
        open_positions={},
        trades_today=0,
        is_paused=False,
        is_killed=False,
        last_updated=now,
    )


def run_strategies_only(candles: list[OHLCV]) -> dict:  # type: ignore[type-arg]
    """Mode A: single TsmomStrategy, no Coordinator."""
    bot_id = uuid4()
    engine = PaperEngine(bot_id=bot_id)
    strategy = TsmomStrategy(fast_ema=10, slow_ema=30)
    state = _init_state(bot_id)

    for i in range(1, len(candles)):
        history = candles[:i]
        fill_price = candles[i].open if i < len(candles) else candles[-1].close
        _, state = engine.step(
            symbol="BTC/USDT",
            history=history,
            fill_price=fill_price,
            fill_timestamp=candles[i].timestamp,
            strategy=strategy,
            bot_state=state,
            timeframe="1d",
        )

    equity_curve = engine.portfolio.equity_history
    return {
        "sharpe": sharpe_ratio(equity_curve, periods_per_year=365),
        "max_drawdown": max_drawdown(equity_curve),
        "cagr": cagr(equity_curve),
        "trade_count": sum(1 for r in engine.decision_log if r.order_ids),
    }


def run_with_coordinator(candles: list[OHLCV]) -> dict:  # type: ignore[type-arg]
    """Mode B: TsmomStrategy + DcaTrendStrategy via Coordinator (code-side only)."""
    bot_id = uuid4()
    engine = PaperEngine(bot_id=bot_id)
    strategies = [TsmomStrategy(fast_ema=10, slow_ema=30), DcaTrendStrategy()]
    coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
    state = _init_state(bot_id)

    for i in range(1, len(candles)):
        history = candles[:i]
        fill_price = candles[i].open if i < len(candles) else candles[-1].close
        _, state = engine.step_multi(
            symbol="BTC/USDT",
            history=history,
            fill_price=fill_price,
            fill_timestamp=candles[i].timestamp,
            strategies=strategies,
            coordinator=coordinator,
            bot_state=state,
            timeframe="1d",
        )

    equity_curve = engine.portfolio.equity_history
    return {
        "sharpe": sharpe_ratio(equity_curve, periods_per_year=365),
        "max_drawdown": max_drawdown(equity_curve),
        "cagr": cagr(equity_curve),
        "trade_count": sum(1 for r in engine.decision_log if r.order_ids),
    }


def run_buy_and_hold(candles: list[OHLCV]) -> dict:  # type: ignore[type-arg]
    """Buy-and-hold baseline using HoldBaselineStrategy via PaperEngine.step."""
    bot_id = uuid4()
    engine = PaperEngine(bot_id=bot_id)
    strategy = HoldBaselineStrategy()
    state = _init_state(bot_id)

    for i in range(1, len(candles)):
        history = candles[:i]
        fill_price = candles[i].open if i < len(candles) else candles[-1].close
        _, state = engine.step(
            symbol="BTC/USDT",
            history=history,
            fill_price=fill_price,
            fill_timestamp=candles[i].timestamp,
            strategy=strategy,
            bot_state=state,
            timeframe="1d",
        )

    equity_curve = engine.portfolio.equity_history
    return {
        "sharpe": sharpe_ratio(equity_curve, periods_per_year=365),
        "max_drawdown": max_drawdown(equity_curve),
        "cagr": cagr(equity_curve),
        "trade_count": sum(1 for r in engine.decision_log if r.order_ids),
    }


def evaluate_gate(
    coordinator_sharpe: float,
    bah_sharpe: float,
    coordinator_max_dd: float,
    bah_max_dd: float,
) -> tuple[bool, str]:
    """Evaluate the PLAN.md Phase 4 gate criterion.

    Gate: Coordinator Sharpe >= buy-and-hold Sharpe
    OR Coordinator max drawdown <= buy-and-hold max drawdown * 0.9.

    Returns (passed, reason_string).
    """
    sharpe_ok = coordinator_sharpe >= bah_sharpe
    dd_ok = coordinator_max_dd <= bah_max_dd * 0.9

    if sharpe_ok and dd_ok:
        reason = (
            f"PASS — Coordinator Sharpe ({coordinator_sharpe:.3f}) >= "
            f"buy-and-hold ({bah_sharpe:.3f}) AND "
            f"max drawdown ({coordinator_max_dd:.1%}) <= "
            f"buy-and-hold * 0.9 ({bah_max_dd * 0.9:.1%})"
        )
    elif sharpe_ok:
        reason = (
            f"PASS — Coordinator Sharpe ({coordinator_sharpe:.3f}) >= "
            f"buy-and-hold ({bah_sharpe:.3f})"
        )
    elif dd_ok:
        reason = (
            f"PASS — Coordinator max drawdown ({coordinator_max_dd:.1%}) <= "
            f"buy-and-hold * 0.9 ({bah_max_dd * 0.9:.1%})"
        )
    else:
        reason = (
            f"FAIL — Coordinator Sharpe ({coordinator_sharpe:.3f}) < "
            f"buy-and-hold ({bah_sharpe:.3f}) AND "
            f"max drawdown ({coordinator_max_dd:.1%}) > "
            f"buy-and-hold * 0.9 ({bah_max_dd * 0.9:.1%})"
        )

    return (sharpe_ok or dd_ok), reason


def run_ablation(
    n_days: int = 550,
    seed_price: float = 30_000.0,
    trend: float = 8.0,
    train_months: int = 12,
    test_months: int = 3,
) -> dict:  # type: ignore[type-arg]
    """Run the full ablation and return all results as a dictionary.

    This function is extracted from main() so it can be called from tests.

    Returns a dict with keys: splits, results_a, results_b, results_bah,
    summary (dict with mean metrics), gate_passed, gate_reason.
    """
    all_candles = _make_candles(n_days, seed_price=seed_price, trend=trend)
    start = all_candles[0].timestamp
    end = all_candles[-1].timestamp
    splits = generate_wf_splits(start, end, train_months=train_months, test_months=test_months)

    results_a: list[dict] = []  # type: ignore[type-arg]
    results_b: list[dict] = []  # type: ignore[type-arg]
    results_bah: list[dict] = []  # type: ignore[type-arg]

    for split in splits:
        oos = [c for c in all_candles if split.test_start <= c.timestamp < split.test_end]
        if len(oos) < 30:
            continue
        ra = run_strategies_only(oos)
        rb = run_with_coordinator(oos)
        rc = run_buy_and_hold(oos)
        results_a.append(ra)
        results_b.append(rb)
        results_bah.append(rc)

    def avg(vals: list, key: str) -> float:  # type: ignore[type-arg]
        if not vals:
            return 0.0
        return statistics.mean(v[key] for v in vals)

    summary = {
        "a_sharpe": avg(results_a, "sharpe"),
        "b_sharpe": avg(results_b, "sharpe"),
        "bah_sharpe": avg(results_bah, "sharpe"),
        "a_max_drawdown": avg(results_a, "max_drawdown"),
        "b_max_drawdown": avg(results_b, "max_drawdown"),
        "bah_max_drawdown": avg(results_bah, "max_drawdown"),
        "a_cagr": avg(results_a, "cagr"),
        "b_cagr": avg(results_b, "cagr"),
        "bah_cagr": avg(results_bah, "cagr"),
        "a_trade_count": avg(results_a, "trade_count"),
        "b_trade_count": avg(results_b, "trade_count"),
    }

    gate_passed, gate_reason = evaluate_gate(
        coordinator_sharpe=summary["b_sharpe"],
        bah_sharpe=summary["bah_sharpe"],
        coordinator_max_dd=summary["b_max_drawdown"],
        bah_max_dd=summary["bah_max_drawdown"],
    )

    return {
        "splits": splits,
        "results_a": results_a,
        "results_b": results_b,
        "results_bah": results_bah,
        "summary": summary,
        "gate_passed": gate_passed,
        "gate_reason": gate_reason,
    }


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    print("Phase 4 Ablation: strategies-only vs Coordinator (code-side)\n")

    result = run_ablation()
    splits = result["splits"]
    results_a = result["results_a"]
    results_b = result["results_b"]
    results_bah = result["results_bah"]
    summary = result["summary"]

    print(f"Walk-forward: {len(splits)} OOS windows of 3 months each\n")

    for i, (ra, rb, rc) in enumerate(zip(results_a, results_b, results_bah, strict=False)):
        print(
            f"  Window {i + 1}: "
            f"A sharpe={ra['sharpe']:.2f} dd={ra['max_drawdown']:.1%} | "
            f"B sharpe={rb['sharpe']:.2f} dd={rb['max_drawdown']:.1%} | "
            f"B&H sharpe={rc['sharpe']:.2f} dd={rc['max_drawdown']:.1%}"
        )

    if not results_a:
        print("No OOS windows — increase candle count.")
        return

    print("\n── Summary ──────────────────────────────────────────────────────────")
    print(
        f"{'Metric':<22} {'Strategies-only (A)':>20} {'Coordinator (B)':>18} {'Buy-and-Hold':>14}"
    )
    print(
        f"{'Mean Sharpe':<22} {summary['a_sharpe']:>20.3f} "
        f"{summary['b_sharpe']:>18.3f} {summary['bah_sharpe']:>14.3f}"
    )
    print(
        f"{'Mean Max Drawdown':<22} {summary['a_max_drawdown']:>19.1%} "
        f"{summary['b_max_drawdown']:>17.1%} {summary['bah_max_drawdown']:>13.1%}"
    )
    print(
        f"{'Mean CAGR':<22} {summary['a_cagr']:>19.1%} "
        f"{summary['b_cagr']:>17.1%} {summary['bah_cagr']:>13.1%}"
    )
    print(
        f"{'Mean Trades':<22} {summary['a_trade_count']:>20.1f} "
        f"{summary['b_trade_count']:>18.1f} {'N/A':>14}"
    )
    print()
    print(f"Gate: {result['gate_reason']}")
    print()
    print("Gate note: LLM-in-the-loop ablation requires live paper trading data")
    print("accumulated over ≥30 days. This report covers code-side weighting only.")


if __name__ == "__main__":
    main()
