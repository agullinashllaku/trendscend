from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trendscend.schemas.market import OHLCV


def _candles(n: int, trend: float = 0.0) -> list[OHLCV]:
    base = datetime(2020, 1, 1, tzinfo=UTC)
    result = []
    for i in range(n):
        p = Decimal(str(100.0 + i * trend))
        result.append(
            OHLCV(
                timestamp=base + timedelta(days=i),
                open=p,
                high=p * Decimal("1.01"),
                low=p * Decimal("0.99"),
                close=p,
                volume=Decimal("1000"),
            )
        )
    return result


class TestRankStrategies:
    def test_returns_list_sorted_by_score(self) -> None:
        from trendscend.recommendation.engine import rank_strategies
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy
        from trendscend.strategies.tsmom import TsmomStrategy

        candles = _candles(550, trend=0.1)  # ~18 months of trending data
        strategies = [HoldBaselineStrategy(), TsmomStrategy()]
        rankings = rank_strategies(
            strategies=strategies,
            candles=candles,
            symbol="BTC/USDT",
            timeframe="1d",
            initial_capital=Decimal("10000"),
        )

        assert len(rankings) == 2
        # Sorted descending by score
        scores = [r.score for r in rankings]
        assert scores == sorted(scores, reverse=True)

    def test_score_formula(self) -> None:
        from trendscend.recommendation.engine import StrategyRanking

        r = StrategyRanking(
            strategy_id="test",
            oos_sharpe_mean=1.0,
            oos_max_drawdown_worst=0.25,
            oos_cagr_mean=0.15,
            score=1.0 / (1 + 0.25),
            num_splits=3,
        )
        assert r.score == pytest.approx(0.8)

    def test_empty_candles_returns_empty(self) -> None:
        from trendscend.recommendation.engine import rank_strategies
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        rankings = rank_strategies(
            strategies=[HoldBaselineStrategy()],
            candles=[],
            symbol="BTC/USDT",
            timeframe="1d",
            initial_capital=Decimal("10000"),
        )
        assert rankings == []

    def test_too_short_candles_returns_empty(self) -> None:
        from trendscend.recommendation.engine import rank_strategies
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        # Only 30 days — not enough for 12-month train + 3-month test
        candles = _candles(30)
        rankings = rank_strategies(
            strategies=[HoldBaselineStrategy()],
            candles=candles,
            symbol="BTC/USDT",
            timeframe="1d",
            initial_capital=Decimal("10000"),
        )
        assert rankings == []

    def test_disclaimer_field_present(self) -> None:
        from trendscend.recommendation.engine import StrategyRanking

        r = StrategyRanking(
            strategy_id="test",
            oos_sharpe_mean=0.5,
            oos_max_drawdown_worst=0.1,
            oos_cagr_mean=0.05,
            score=0.5 / 1.1,
            num_splits=2,
        )
        assert hasattr(r, "disclaimer")
        assert "past data" in r.disclaimer.lower()

    def test_hold_baseline_always_present(self) -> None:
        """hold_baseline must appear in rankings even when run_walk_forward returns num_splits=0."""
        from unittest.mock import patch

        from trendscend.recommendation.engine import rank_strategies
        from trendscend.research.walk_forward import WalkForwardReport
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy
        from trendscend.strategies.tsmom import TsmomStrategy

        # Enough candles to pass generate_wf_splits (splits != []), but we mock
        # run_walk_forward so that hold_baseline returns num_splits=0.
        candles = _candles(550)

        hold_baseline_report = WalkForwardReport(
            strategy_id="hold_baseline",
            symbol="BTC/USDT",
            timeframe="1d",
            num_splits=0,
            oos_sharpe_mean=0.0,
            oos_sharpe_std=0.0,
            oos_max_drawdown_mean=0.0,
            oos_max_drawdown_worst=0.0,
            oos_cagr_mean=0.0,
            per_split_results=[],
        )
        tsmom_report = WalkForwardReport(
            strategy_id="tsmom",
            symbol="BTC/USDT",
            timeframe="1d",
            num_splits=2,
            oos_sharpe_mean=0.5,
            oos_sharpe_std=0.1,
            oos_max_drawdown_mean=0.1,
            oos_max_drawdown_worst=0.15,
            oos_cagr_mean=0.12,
            per_split_results=[],
        )

        def fake_run_walk_forward(strategy, **kwargs):  # type: ignore[no-untyped-def]
            if strategy.strategy_id == "hold_baseline":
                return hold_baseline_report
            return tsmom_report

        with patch(
            "trendscend.recommendation.engine.run_walk_forward",
            side_effect=fake_run_walk_forward,
        ):
            result = rank_strategies(
                strategies=[HoldBaselineStrategy(), TsmomStrategy()],
                candles=candles,
                symbol="BTC/USDT",
                timeframe="1d",
                initial_capital=Decimal("10000"),
            )

        assert any(r.strategy_id == "hold_baseline" for r in result), (
            "hold_baseline must always be present in rankings"
        )
        hold_entry = next(r for r in result if r.strategy_id == "hold_baseline")
        assert hold_entry.score == pytest.approx(0.0)
