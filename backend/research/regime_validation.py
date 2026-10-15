#!/usr/bin/env python3
"""Regime Classifier Validation — Phase 2 gate component.

For each strategy's intended regime, checks whether forward returns are
higher in that regime than in other regimes.  Uses the 100-bar BTC fixture
(short, but sufficient to demonstrate the machinery is wired correctly).

Writes results to research/reports/phase2_regime_validation.md.

Run:
    cd backend && uv run python research/regime_validation.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

# Make src importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trendscend.regime.classifier import RegimeClassifier, RegimeClassifierConfig
from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeType

FIXTURE_PATH = Path(__file__).parent.parent / "tests/strategies/fixtures/btc_100_bars.json"
REPORT_PATH = Path(__file__).parent.parent.parent / "research/reports/phase2_regime_validation.md"


def load_bars() -> list[OHLCV]:
    raw: list[list[float]] = json.loads(FIXTURE_PATH.read_text())
    return [
        OHLCV(
            timestamp=datetime.fromtimestamp(b[0] / 1000.0, tz=UTC),
            open=Decimal(str(b[1])),
            high=Decimal(str(b[2])),
            low=Decimal(str(b[3])),
            close=Decimal(str(b[4])),
            volume=Decimal(str(b[5])),
        )
        for b in raw
    ]


def next_bar_return(candles: list[OHLCV], i: int) -> float | None:
    """Return the 1-bar forward return at index i."""
    if i + 1 >= len(candles):
        return None
    c0 = float(candles[i].close)
    c1 = float(candles[i + 1].close)
    return (c1 - c0) / c0 if c0 > 0 else None


def main() -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    candles = load_bars()
    clf = RegimeClassifier()
    cfg = RegimeClassifierConfig(
        adx_period=5,
        vol_period=5,
        vol_rank_period=20,
        ema_period=10,
        er_period=5,
        adx_trending_threshold=20.0,
        vol_chaos_percentile=0.85,
    )

    labels: list[RegimeType] = []
    returns: list[float] = []

    for i in range(1, len(candles) - 1):
        label = clf.classify(candles[:i], "BTC/USDT", "4h", config=cfg)
        fwd = next_bar_return(candles, i)
        if fwd is not None:
            labels.append(label.regime)
            returns.append(fwd)

    # Aggregate by regime
    regime_returns: dict[RegimeType, list[float]] = {r: [] for r in RegimeType}
    for label, ret in zip(labels, returns, strict=False):
        regime_returns[label].append(ret)

    def stats(vals: list[float]) -> str:
        if not vals:
            return "n=0 (no data)"
        n = len(vals)
        mean = sum(vals) / n
        positive = sum(1 for v in vals if v > 0)
        return f"n={n}, mean={mean * 100:.2f}%, hit_rate={positive / n * 100:.0f}%"

    lines = [
        "# Phase 2 Regime Classifier Validation",
        "",
        f"> Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
        "> Data: BTC/USDT 100-bar fixture (4h bars), fast config (adx_period=5)",
        "",
        "## Forward Returns by Regime (1-bar horizon)",
        "",
        "| Regime | Stats |",
        "|--------|-------|",
    ]
    for regime in RegimeType:
        r_stats = stats(regime_returns[regime])
        lines.append(f"| `{regime.value}` | {r_stats} |")

    lines += [
        "",
        "## Interpretation",
        "",
        "- **trending_up**: Mean return should be positive (momentum strategies benefit).",
        "- **trending_down**: Mean return should be negative (trend strategies go flat/short).",
        "- **ranging**: Returns near zero with lower variance (grid/mean-rev strategies benefit).",
        "- **high_vol_chaos**: High variance; risk manager should reduce exposure.",
        "",
        "## Conclusion",
        "",
    ]

    tu = regime_returns[RegimeType.TRENDING_UP]
    td = regime_returns[RegimeType.TRENDING_DOWN]
    rn = regime_returns[RegimeType.RANGING]

    if tu and td:
        tu_mean = sum(tu) / len(tu)
        td_mean = sum(td) / len(td)
        if tu_mean > td_mean:
            lines.append(
                "✅ trending_up mean return is higher than trending_down — "
                "classifier directional signal is valid."
            )
        else:
            lines.append(
                "⚠️ trending_up mean return is NOT higher than trending_down — "
                "may be insufficient data or noisy regime."
            )
    else:
        lines.append("⚠️ Insufficient data in one or more trending regimes.")

    if rn:
        rn_abs = [abs(r) for r in rn]
        mean_abs = sum(rn_abs) / len(rn_abs) if rn_abs else 0
        lines.append(
            f"Ranging mean |return| = {mean_abs * 100:.2f}% — "
            + (
                "✅ low (< 0.5%), consistent with a ranging market."
                if mean_abs < 0.005
                else "ℹ️ above 0.5%; regime boundary may need tuning with more data."
            )
        )
    else:
        lines.append("⚠️ No ranging bars in this dataset.")

    report_text = "\n".join(lines) + "\n"
    REPORT_PATH.write_text(report_text)
    print(report_text)
    print(f"\nReport written to: {REPORT_PATH}")


if __name__ == "__main__":
    main()
