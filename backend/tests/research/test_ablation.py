"""Tests for the Phase 4 ablation harness.

Verifies that:
1. _make_candles produces valid OHLCV data.
2. run_strategies_only runs without error on synthetic data and returns finite metrics.
3. run_with_coordinator runs without error on synthetic data and returns finite metrics.
4. run_buy_and_hold runs without error on synthetic data and returns finite metrics.
5. evaluate_gate correctly evaluates the gate criteria.
6. run_ablation produces a complete result dict with expected keys and OOS windows.
"""

from __future__ import annotations

import math
from decimal import Decimal

from research.phase4_ablation import (
    _make_candles,
    evaluate_gate,
    run_ablation,
    run_buy_and_hold,
    run_strategies_only,
    run_with_coordinator,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _short_candles(n_days: int = 100) -> list:  # type: ignore[type-arg]
    """Generate a short candle series for fast tests."""
    return _make_candles(n_days, seed_price=30_000.0, trend=8.0)


# ── Unit tests: _make_candles ─────────────────────────────────────────────────


def test_make_candles_length() -> None:
    candles = _make_candles(90)
    assert len(candles) == 90


def test_make_candles_timestamps_ascending() -> None:
    candles = _make_candles(30)
    for i in range(1, len(candles)):
        assert candles[i].timestamp > candles[i - 1].timestamp


def test_make_candles_prices_valid() -> None:
    candles = _make_candles(30, seed_price=1000.0, trend=10.0)
    for c in candles:
        assert c.high >= c.close >= c.low
        assert c.open > Decimal("0")
        assert c.volume > Decimal("0")


def test_make_candles_uptrend() -> None:
    candles = _make_candles(30, seed_price=1000.0, trend=10.0)
    assert float(candles[-1].close) > float(candles[0].close)


# ── Unit tests: evaluate_gate ─────────────────────────────────────────────────


def test_gate_passes_when_sharpe_higher() -> None:
    passed, reason = evaluate_gate(
        coordinator_sharpe=1.5,
        bah_sharpe=1.0,
        coordinator_max_dd=0.10,
        bah_max_dd=0.05,
    )
    assert passed
    assert "PASS" in reason


def test_gate_passes_when_drawdown_lower() -> None:
    passed, reason = evaluate_gate(
        coordinator_sharpe=0.5,
        bah_sharpe=1.0,
        coordinator_max_dd=0.04,  # 0.04 <= 0.05 * 0.9 = 0.045
        bah_max_dd=0.05,
    )
    assert passed
    assert "PASS" in reason


def test_gate_fails_both_criteria() -> None:
    passed, reason = evaluate_gate(
        coordinator_sharpe=0.5,
        bah_sharpe=1.0,
        coordinator_max_dd=0.10,  # 0.10 > 0.05 * 0.9 = 0.045
        bah_max_dd=0.05,
    )
    assert not passed
    assert "FAIL" in reason


def test_gate_passes_when_both_criteria_met() -> None:
    passed, reason = evaluate_gate(
        coordinator_sharpe=2.0,
        bah_sharpe=1.0,
        coordinator_max_dd=0.04,
        bah_max_dd=0.05,
    )
    assert passed
    # Both conditions met — reason should mention both
    assert "PASS" in reason


# ── Integration tests: paper run helpers ─────────────────────────────────────


def test_run_strategies_only_returns_valid_metrics() -> None:
    candles = _short_candles(90)
    result = run_strategies_only(candles)
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert "cagr" in result
    assert "trade_count" in result
    # All metrics must be finite floats
    assert math.isfinite(result["sharpe"])
    assert math.isfinite(result["max_drawdown"])
    assert math.isfinite(result["cagr"])
    assert result["max_drawdown"] >= 0.0
    assert result["trade_count"] >= 0


def test_run_with_coordinator_returns_valid_metrics() -> None:
    candles = _short_candles(90)
    result = run_with_coordinator(candles)
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert "cagr" in result
    assert "trade_count" in result
    assert math.isfinite(result["sharpe"])
    assert math.isfinite(result["max_drawdown"])
    assert math.isfinite(result["cagr"])
    assert result["max_drawdown"] >= 0.0


def test_run_buy_and_hold_returns_valid_metrics() -> None:
    candles = _short_candles(60)
    result = run_buy_and_hold(candles)
    assert "sharpe" in result
    assert "max_drawdown" in result
    assert "cagr" in result
    assert math.isfinite(result["sharpe"])
    assert math.isfinite(result["max_drawdown"])
    assert math.isfinite(result["cagr"])


def test_run_strategies_only_empty_candles_graceful() -> None:
    """Fewer than min_bars for TsmomStrategy — should not crash."""
    candles = _short_candles(10)
    result = run_strategies_only(candles)
    # Must return a dict with numeric metrics even when no trades execute
    assert math.isfinite(result["sharpe"])
    assert math.isfinite(result["max_drawdown"])


# ── Integration test: full ablation harness ───────────────────────────────────


def test_run_ablation_produces_oos_windows() -> None:
    """run_ablation with 550 synthetic candles should produce ≥1 OOS window."""
    result = run_ablation(n_days=550, seed_price=30_000.0, trend=8.0)

    assert "splits" in result
    assert "results_a" in result
    assert "results_b" in result
    assert "results_bah" in result
    assert "summary" in result
    assert "gate_passed" in result
    assert "gate_reason" in result

    # Must have at least one window
    assert len(result["results_a"]) >= 1
    assert len(result["results_b"]) >= 1
    assert len(result["results_bah"]) >= 1

    # All window counts must match
    assert len(result["results_a"]) == len(result["results_b"])
    assert len(result["results_a"]) == len(result["results_bah"])


def test_run_ablation_summary_keys() -> None:
    result = run_ablation(n_days=550, seed_price=30_000.0, trend=8.0)
    summary = result["summary"]
    expected_keys = {
        "a_sharpe",
        "b_sharpe",
        "bah_sharpe",
        "a_max_drawdown",
        "b_max_drawdown",
        "bah_max_drawdown",
        "a_cagr",
        "b_cagr",
        "bah_cagr",
        "a_trade_count",
        "b_trade_count",
    }
    assert expected_keys.issubset(set(summary.keys()))


def test_run_ablation_all_metrics_finite() -> None:
    result = run_ablation(n_days=550, seed_price=30_000.0, trend=8.0)
    summary = result["summary"]
    for key, value in summary.items():
        assert math.isfinite(value), f"Summary metric {key} is not finite: {value}"


def test_run_ablation_gate_result_is_bool() -> None:
    result = run_ablation(n_days=550, seed_price=30_000.0, trend=8.0)
    assert isinstance(result["gate_passed"], bool)
    assert isinstance(result["gate_reason"], str)
    assert len(result["gate_reason"]) > 0
