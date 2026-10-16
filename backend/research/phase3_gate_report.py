#!/usr/bin/env python3
"""Phase 3 gate report: shadow signal predictive value.

Usage:
    uv run python research/phase3_gate_report.py            # real data (needs API keys)
    uv run python research/phase3_gate_report.py --fixture  # synthetic fixture data

Outputs a markdown report to stdout and writes it to
research/reports/phase3_shadow_signal_report.md (relative to repo root).
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

# Ensure src/ is on the path when run directly.
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from trendscend.schemas.market import OHLCV
from trendscend.schemas.news import NewsDirection, NewsEvent, NewsEventType
from trendscend.schemas.scoring import NewsEventScore, SmartMoneyScore
from trendscend.schemas.smart_money import SmartMoneyAssetState, SmartMoneyState
from trendscend.scoring.news_scorer import NewsScorer
from trendscend.scoring.smart_money_scorer import SmartMoneyScorer


def _make_fixture_data() -> tuple[list[NewsEvent], list[OHLCV], list[SmartMoneyState]]:
    """Synthetic fixture: 10 news events, 72h of candles, 3 SM snapshots."""
    base_ts = datetime(2024, 6, 1, 0, 0, tzinfo=UTC)
    # Candles: hourly, price trends up from 60000 to 62000 over 72h
    candles = [
        OHLCV(
            timestamp=base_ts + timedelta(hours=i),
            open=Decimal(str(60000 + i * 27)),
            high=Decimal(str(60000 + i * 27 + 50)),
            low=Decimal(str(60000 + i * 27 - 50)),
            close=Decimal(str(60000 + i * 27)),
            volume=Decimal("10"),
        )
        for i in range(72)
    ]

    events: list[NewsEvent] = []
    directions = [NewsDirection.BULLISH, NewsDirection.BEARISH, NewsDirection.NEUTRAL]
    for i in range(10):
        ts = base_ts + timedelta(hours=i * 3)
        events.append(
            NewsEvent(
                id=uuid4(),
                source="fixture",
                url=f"https://example.com/{i}",
                published_at=ts,
                assets=["BTC"],
                event_type=NewsEventType.OTHER,
                severity=(i % 5) + 1,
                direction=directions[i % 3],
                half_life_hours=4.0,
                confidence=0.7,
                is_new_information=True,
                llm_model="claude-haiku-4-5-20251001",
                raw_hash=f"hash{i}",
                classified_at=ts,
            )
        )

    sm_states = [
        SmartMoneyState(
            per_asset={
                "BTC/USDT": SmartMoneyAssetState(
                    symbol="BTC/USDT",
                    net_long_pct=0.5 + i * 0.1,
                    net_short_pct=0.5 - i * 0.1,
                    change_24h=0.1 * i,
                    concentration=0.1,
                )
            },
            vetted_wallet_count=10,
            computed_at=base_ts + timedelta(hours=i * 12),
        )
        for i in range(3)
    ]

    return events, candles, sm_states


def _directional_accuracy(scores: list[NewsEventScore]) -> dict[str, float]:
    """Fraction of events where direction matches sign of return."""
    results: dict[str, float] = {}
    for horizon in ("1h", "4h", "24h"):
        attr = f"return_{horizon}"
        valid = [(s.direction, getattr(s, attr)) for s in scores if getattr(s, attr) is not None]
        if not valid:
            results[horizon] = float("nan")
            continue
        correct = sum(
            1
            for direction, ret in valid
            if (direction == NewsDirection.BULLISH and ret > 0)
            or (direction == NewsDirection.BEARISH and ret < 0)
            or (direction == NewsDirection.NEUTRAL and abs(ret) < 0.005)
        )
        results[horizon] = correct / len(valid)
    return results


def run_report(fixture: bool = False) -> str:
    if fixture:
        events, candles, sm_states = _make_fixture_data()
    else:
        print(
            "ERROR: Real data mode requires API keys and DB. Use --fixture for CI.",
            file=sys.stderr,
        )
        sys.exit(1)

    scorer = NewsScorer()
    scores = [scorer.score(ev, candles) for ev in events]

    sm_scorer = SmartMoneyScorer()
    sm_scores: list[SmartMoneyScore] = []
    for i in range(len(sm_states) - 1):
        sm_scores.extend(sm_scorer.score(sm_states[i], sm_states[i + 1], {"BTC/USDT": candles}))

    accuracy = _directional_accuracy(scores)

    lines = [
        "# Phase 3 Shadow Signal Gate Report",
        "",
        f"**Generated:** {datetime.now(UTC).isoformat()}",
        f"**Mode:** {'fixture' if fixture else 'real'}",
        "",
        "## A3 News Signal — Directional Accuracy",
        "",
        "| Horizon | Events scored | Directional accuracy |",
        "|---------|--------------|----------------------|",
    ]
    for horizon in ("1h", "4h", "24h"):
        valid_count = sum(1 for s in scores if getattr(s, f"return_{horizon}") is not None)
        acc = accuracy.get(horizon, float("nan"))
        acc_str = f"{acc:.1%}" if not math.isnan(acc) else "N/A"
        lines.append(f"| {horizon} | {valid_count} | {acc_str} |")

    lines += [
        "",
        "## A4 Smart Money Signal",
        "",
        f"Shifts scored: {len(sm_scores)}",
        "",
    ]
    if sm_scores:
        for sc in sm_scores:
            before_after = f"{sc.net_long_pct_before:.0%} → {sc.net_long_pct_after:.0%}"
            parts = []
            for lbl, val in (("1h", sc.return_1h), ("4h", sc.return_4h), ("24h", sc.return_24h)):
                parts.append(f"{lbl}={val:.2%}" if val is not None else f"{lbl}=N/A")
            detail = " ".join(parts)
            lines.append(f"- {sc.symbol}: {before_after} | {detail}")

    lines += [
        "",
        "## Gate Assessment",
        "",
        "Shadow mode: both signals collecting data. Severe-event risk-off",
        "triggers regardless. Non-zero Coordinator weight requires ≥3 months",
        "out-of-sample data and statistically meaningful directional accuracy.",
        "",
        "**Status: SHADOW MODE ACTIVE — collecting data for future gate evaluation.**",
    ]

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 3 gate report")
    parser.add_argument("--fixture", action="store_true", help="Use synthetic fixture data")
    args = parser.parse_args()

    report = run_report(fixture=args.fixture)
    print(report)

    out_path = (
        Path(__file__).parent.parent.parent
        / "research"
        / "reports"
        / "phase3_shadow_signal_report.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport written to {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
