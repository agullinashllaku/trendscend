"""Phase 1 Gate Report — Walk-forward strategy comparison vs buy-and-hold.

Run from the worktree root:
    /path/to/uv run --project backend python research/run_phase1_report.py

Or from the backend directory:
    cd backend && /path/to/uv run python ../research/run_phase1_report.py

Self-bootstraps to the backend venv if ccxt is not importable.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Bootstrap: if ccxt is missing, re-exec with the backend venv Python.
# This allows running "uv run python research/run_phase1_report.py" from the
# worktree root even when the root has no pyproject.toml.
# ---------------------------------------------------------------------------
import sys
import os

_WORKTREE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BACKEND_VENV_PYTHON = os.path.join(_WORKTREE_ROOT, "backend", ".venv", "bin", "python")

try:
    import ccxt  # noqa: F401
except ModuleNotFoundError:
    if os.path.exists(_BACKEND_VENV_PYTHON) and sys.executable != _BACKEND_VENV_PYTHON:
        print(f"[bootstrap] ccxt not found; re-execing with {_BACKEND_VENV_PYTHON}", flush=True)
        os.execv(_BACKEND_VENV_PYTHON, [_BACKEND_VENV_PYTHON] + sys.argv)
    else:
        raise

# ---------------------------------------------------------------------------
# Normal imports (ccxt is available from here on)
# ---------------------------------------------------------------------------
import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Ensure trendscend package is importable
_BACKEND_SRC = os.path.join(_WORKTREE_ROOT, "backend", "src")
_RESEARCH_ROOT_PATH = os.path.dirname(os.path.abspath(__file__))
if _BACKEND_SRC not in sys.path:
    sys.path.insert(0, _BACKEND_SRC)
if _RESEARCH_ROOT_PATH not in sys.path:
    sys.path.insert(0, _RESEARCH_ROOT_PATH)

import ccxt.async_support as ccxt_async

from data_loader import DataLoader
from trendscend.research.walk_forward import WalkForwardReport, generate_wf_splits, run_walk_forward
from trendscend.strategies.breakout import BreakoutStrategy
from trendscend.strategies.dca_trend import DcaTrendStrategy
from trendscend.strategies.grid_range import GridRangeStrategy
from trendscend.strategies.hold_baseline import HoldBaselineStrategy
from trendscend.strategies.rebalance_core import RebalanceCoreStrategy
from trendscend.strategies.tsmom import TsmomStrategy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phase1_report")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ASSETS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "DOT/USDT",
]

TIMEFRAME = "4h"
EXCHANGE_ID = "binance"
INITIAL_CAPITAL = Decimal("10000")

WF_START = datetime(2021, 1, 1, tzinfo=timezone.utc)
WF_END = datetime(2025, 1, 1, tzinfo=timezone.utc)
TRAIN_MONTHS = 12
TEST_MONTHS = 3

FEE_NOTE = (
    "Round-trip fee model: Binance taker 0.1% + 0.05% slippage per side = 0.15% per side"
)

# Gate thresholds
GATE_DD_IMPROVEMENT_PP = 5.0   # ≥5 percentage-point lower drawdown beats baseline
GATE_MIN_ASSET_FRACTION = 0.5  # strategy must beat baseline on majority of assets

_RESEARCH_ROOT = Path(__file__).parent
_REPORTS_DIR = _RESEARCH_ROOT / "reports"
_DATA_DIR = _RESEARCH_ROOT / "data"


# ---------------------------------------------------------------------------
# Strategy factory — fresh instances per asset to reset internal state
# ---------------------------------------------------------------------------


def _make_strategies() -> list:
    return [
        HoldBaselineStrategy(),
        RebalanceCoreStrategy(),
        DcaTrendStrategy(),
        TsmomStrategy(),
        BreakoutStrategy(),
        GridRangeStrategy(),
    ]


# ---------------------------------------------------------------------------
# Main async runner
# ---------------------------------------------------------------------------


async def main() -> None:
    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    _DATA_DIR.mkdir(parents=True, exist_ok=True)

    exchange = ccxt_async.binance({"enableRateLimit": True})

    try:
        splits = generate_wf_splits(WF_START, WF_END, TRAIN_MONTHS, TEST_MONTHS)
        logger.info("Walk-forward: %d splits, train=%dm test=%dm", len(splits), TRAIN_MONTHS, TEST_MONTHS)

        loader = DataLoader(exchange, data_dir=_DATA_DIR)

        # results[strategy_id][symbol] = WalkForwardReport
        results: dict[str, dict[str, WalkForwardReport | None]] = {}

        strategy_ids = [s.strategy_id for s in _make_strategies()]
        for sid in strategy_ids:
            results[sid] = {}

        for symbol in ASSETS:
            logger.info("Loading candles: %s %s %s", EXCHANGE_ID, symbol, TIMEFRAME)
            try:
                candles = await loader._load_async(EXCHANGE_ID, symbol, TIMEFRAME, WF_START)
                logger.info("  Loaded %d candles for %s", len(candles), symbol)
            except Exception:
                logger.error("  FAILED to load candles for %s:\n%s", symbol, traceback.format_exc())
                for sid in strategy_ids:
                    results[sid][symbol] = None
                continue

            # Run each strategy on this asset
            for strategy in _make_strategies():
                sid = strategy.strategy_id
                logger.info("  Running %s / %s ...", sid, symbol)
                try:
                    report = run_walk_forward(
                        strategy=strategy,
                        candles=candles,
                        splits=splits,
                        initial_capital=INITIAL_CAPITAL,
                        exchange_id=EXCHANGE_ID,
                        timeframe=TIMEFRAME,
                        symbol=symbol,
                    )
                    results[sid][symbol] = report
                    logger.info(
                        "    %s %s: sharpe=%.3f dd=%.1f%% cagr=%.1f%%",
                        sid, symbol,
                        report.oos_sharpe_mean,
                        report.oos_max_drawdown_mean * 100,
                        report.oos_cagr_mean * 100,
                    )
                except Exception:
                    logger.error(
                        "  FAILED %s / %s:\n%s", sid, symbol, traceback.format_exc()
                    )
                    results[sid][symbol] = None

    finally:
        await exchange.close()

    # -----------------------------------------------------------------------
    # Aggregate per-strategy metrics across all assets
    # -----------------------------------------------------------------------
    agg: dict[str, dict] = {}
    for sid in strategy_ids:
        sharpes = []
        drawdowns = []
        cagrs = []
        asset_results = []
        for symbol in ASSETS:
            r = results[sid].get(symbol)
            if r is not None and r.num_splits > 0:
                sharpes.append(r.oos_sharpe_mean)
                drawdowns.append(r.oos_max_drawdown_mean)
                cagrs.append(r.oos_cagr_mean)
                asset_results.append({"symbol": symbol, "sharpe": r.oos_sharpe_mean,
                                      "max_dd": r.oos_max_drawdown_mean, "cagr": r.oos_cagr_mean,
                                      "num_splits": r.num_splits})
            else:
                asset_results.append({"symbol": symbol, "sharpe": None,
                                      "max_dd": None, "cagr": None, "num_splits": 0})

        n = len(sharpes)
        agg[sid] = {
            "strategy_id": sid,
            "n_assets": n,
            "oos_sharpe_mean": sum(sharpes) / n if n else 0.0,
            "oos_max_dd_mean": sum(drawdowns) / n if n else 0.0,
            "oos_cagr_mean": sum(cagrs) / n if n else 0.0,
            "per_asset": asset_results,
        }

    # -----------------------------------------------------------------------
    # Gate assessment
    # -----------------------------------------------------------------------
    baseline_sharpe = agg["hold_baseline"]["oos_sharpe_mean"]
    baseline_dd = agg["hold_baseline"]["oos_max_dd_mean"]

    gate_results: list[dict] = []
    gate_passed = False
    gate_winner: str | None = None
    gate_reason: str | None = None

    for sid in strategy_ids:
        if sid == "hold_baseline":
            continue
        entry = agg[sid]
        # Count per-asset beats
        sharpe_beats = 0
        dd_beats = 0
        valid_assets = 0
        for ar in entry["per_asset"]:
            bline = next(
                (x for x in agg["hold_baseline"]["per_asset"] if x["symbol"] == ar["symbol"]),
                None
            )
            if ar["sharpe"] is None or bline is None or bline["sharpe"] is None:
                continue
            valid_assets += 1
            if ar["sharpe"] > bline["sharpe"]:
                sharpe_beats += 1
            # DD: lower (less negative / smaller magnitude) is better
            if bline["max_dd"] - ar["max_dd"] >= GATE_DD_IMPROVEMENT_PP / 100:
                dd_beats += 1

        majority = valid_assets * GATE_MIN_ASSET_FRACTION
        sharpe_passes = (sharpe_beats >= majority) and valid_assets > 0
        dd_passes = (dd_beats >= majority) and valid_assets > 0

        passes = sharpe_passes or dd_passes
        reason_parts = []
        if sharpe_passes:
            reason_parts.append(
                f"higher OOS Sharpe on {sharpe_beats}/{valid_assets} assets"
            )
        if dd_passes:
            reason_parts.append(
                f"≥5pp lower max drawdown on {dd_beats}/{valid_assets} assets"
            )

        gate_results.append({
            "strategy_id": sid,
            "passes": passes,
            "sharpe_beats": sharpe_beats,
            "dd_beats": dd_beats,
            "valid_assets": valid_assets,
            "reason": "; ".join(reason_parts) if reason_parts else "does not beat baseline",
        })

        if passes and not gate_passed:
            gate_passed = True
            gate_winner = sid
            gate_reason = "; ".join(reason_parts)

    # -----------------------------------------------------------------------
    # Write JSON
    # -----------------------------------------------------------------------
    output_json = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "config": {
            "assets": ASSETS,
            "timeframe": TIMEFRAME,
            "exchange": EXCHANGE_ID,
            "wf_start": WF_START.isoformat(),
            "wf_end": WF_END.isoformat(),
            "train_months": TRAIN_MONTHS,
            "test_months": TEST_MONTHS,
            "initial_capital": str(INITIAL_CAPITAL),
            "num_splits": len(splits),
            "fee_note": FEE_NOTE,
        },
        "strategies": list(agg.values()),
        "gate": {
            "passed": gate_passed,
            "winner": gate_winner,
            "reason": gate_reason,
            "per_strategy": gate_results,
        },
    }

    json_path = _REPORTS_DIR / "phase1_strategy_comparison.json"
    json_path.write_text(json.dumps(output_json, indent=2))
    logger.info("Wrote %s", json_path)

    # -----------------------------------------------------------------------
    # Write Markdown report
    # -----------------------------------------------------------------------
    md_lines = [
        "# Phase 1 Strategy Comparison — Walk-Forward OOS Report",
        "",
        f"**Generated:** {output_json['generated_at']}",
        f"**Period:** {WF_START.date()} → {WF_END.date()}  |  "
        f"Train {TRAIN_MONTHS}m / Test {TEST_MONTHS}m  |  "
        f"{len(splits)} OOS windows",
        f"**Assets ({len(ASSETS)}):** {', '.join(ASSETS)}",
        f"**Fee model:** {FEE_NOTE}",
        "",
        "## Aggregated OOS Metrics (mean across assets and windows)",
        "",
        "| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | vs Hold (Sharpe) | vs Hold (DD) |",
        "|----------|-----------|-----------|---------|-----------------|-------------|",
    ]

    for sid in strategy_ids:
        entry = agg[sid]
        sharpe = entry["oos_sharpe_mean"]
        dd = entry["oos_max_dd_mean"] * 100
        cagr = entry["oos_cagr_mean"] * 100
        if sid == "hold_baseline":
            vs_sharpe = "—"
            vs_dd = "—"
        else:
            d_sharpe = sharpe - baseline_sharpe
            d_dd = (baseline_dd - entry["oos_max_dd_mean"]) * 100
            vs_sharpe = f"{d_sharpe:+.3f}"
            vs_dd = f"{d_dd:+.1f}pp"
        md_lines.append(
            f"| {sid} | {sharpe:.3f} | {dd:.1f}% | {cagr:.1f}% | {vs_sharpe} | {vs_dd} |"
        )

    md_lines += [
        "",
        "## Per-Asset Detail",
        "",
    ]

    for symbol in ASSETS:
        md_lines.append(f"### {symbol}")
        md_lines.append("")
        md_lines.append("| Strategy | OOS Sharpe | OOS Max DD | OOS CAGR | Splits |")
        md_lines.append("|----------|-----------|-----------|---------|--------|")
        for sid in strategy_ids:
            ar = next(
                (x for x in agg[sid]["per_asset"] if x["symbol"] == symbol), None
            )
            if ar and ar["sharpe"] is not None:
                md_lines.append(
                    f"| {sid} | {ar['sharpe']:.3f} | {ar['max_dd']*100:.1f}% "
                    f"| {ar['cagr']*100:.1f}% | {ar['num_splits']} |"
                )
            else:
                md_lines.append(f"| {sid} | N/A | N/A | N/A | 0 |")
        md_lines.append("")

    # Gate assessment section
    md_lines += [
        "## Gate Assessment",
        "",
        f"**Gate condition:** at least one non-baseline strategy must have higher mean OOS Sharpe  ",
        f"OR ≥{GATE_DD_IMPROVEMENT_PP:.0f}pp lower max drawdown vs `hold_baseline`, "
        f"across ≥{int(GATE_MIN_ASSET_FRACTION * 100)}% of assets.",
        "",
    ]

    if gate_passed:
        md_lines += [
            f"**Gate: PASSED** — `{gate_winner}` {gate_reason}",
            "",
        ]
    else:
        md_lines += [
            "**Gate: FAILED** — No strategy cleared the bar. ",
            "Owner review required before proceeding to Phase 2.",
            "",
        ]

    md_lines.append("| Strategy | Sharpe beats | DD beats | Valid assets | Passes |")
    md_lines.append("|----------|-------------|---------|-------------|--------|")
    for gr in gate_results:
        tick = "✓" if gr["passes"] else "✗"
        md_lines.append(
            f"| {gr['strategy_id']} | {gr['sharpe_beats']}/{gr['valid_assets']} "
            f"| {gr['dd_beats']}/{gr['valid_assets']} | {gr['valid_assets']} | {tick} |"
        )

    md_lines += [
        "",
        "---",
        "",
        f"*{FEE_NOTE}*",
    ]

    md_path = _REPORTS_DIR / "phase1_strategy_comparison.md"
    md_path.write_text("\n".join(md_lines) + "\n")
    logger.info("Wrote %s", md_path)

    # Print summary to stdout
    print("\n" + "=" * 70)
    print("PHASE 1 GATE REPORT SUMMARY")
    print("=" * 70)
    print(f"{'Strategy':<20} {'Sharpe':>8} {'Max DD':>8} {'CAGR':>8}")
    print("-" * 70)
    for sid in strategy_ids:
        entry = agg[sid]
        print(
            f"{sid:<20} {entry['oos_sharpe_mean']:>8.3f} "
            f"{entry['oos_max_dd_mean']*100:>7.1f}% "
            f"{entry['oos_cagr_mean']*100:>7.1f}%"
        )
    print("-" * 70)
    if gate_passed:
        print(f"GATE: PASSED — {gate_winner}: {gate_reason}")
    else:
        print("GATE: FAILED — No strategy beat buy-and-hold. Report to owner.")
    print("=" * 70)
    print(f"\nFull report: {md_path}")
    print(f"JSON data:   {json_path}")


if __name__ == "__main__":
    asyncio.run(main())
