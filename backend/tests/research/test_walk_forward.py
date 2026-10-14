"""Tests for the walk-forward framework.

Critical invariant under test:
    split.test_start > split.train_end  (STRICTLY) for every split.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trendscend.backtester.models import BacktestResult
from trendscend.research.walk_forward import (
    WalkForwardReport,
    generate_wf_splits,
    run_walk_forward,
)
from trendscend.schemas.market import OHLCV
from trendscend.schemas.signals import Signal

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_candles(
    start: datetime,
    n_days: int,
    price: float = 100.0,
) -> list[OHLCV]:
    """Generate *n_days* daily candles starting from *start*."""
    candles = []
    for i in range(n_days):
        ts = start + timedelta(days=i)
        p = Decimal(str(price))
        candles.append(OHLCV(timestamp=ts, open=p, high=p, low=p, close=p, volume=Decimal("1000")))
    return candles


class _HoldStrategy:
    """Simple hold-always strategy for testing."""

    strategy_id: str = "hold_test"

    def generate_signal(self, symbol: str, candles: list[OHLCV]) -> Signal:  # type: ignore[override]
        ts = candles[-1].timestamp if candles else datetime.now(UTC)
        return Signal(
            strategy_id=self.strategy_id,
            symbol=symbol,
            target_exposure=1.0,
            conviction=1.0,
            timeframe="1d",
            generated_at=ts,
        )


# ---------------------------------------------------------------------------
# generate_wf_splits — split structure
# ---------------------------------------------------------------------------


class TestGenerateWfSplits:
    def test_train_end_strictly_less_than_test_start_always(self) -> None:
        """CRITICAL: test_start > train_end strictly for every split."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)  # 3 years
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)

        assert len(splits) > 0, "Expected at least one split for 3 years of data"

        for i, split in enumerate(splits):
            assert split.test_start > split.train_end, (
                f"Split {i}: test_start ({split.test_start}) must be strictly "
                f"greater than train_end ({split.train_end})"
            )

    def test_two_years_produces_at_least_three_splits(self) -> None:
        """2 years of data with 12m/3m windows should produce >= 3 splits."""
        start = datetime(2021, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)  # 2 years
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)

        assert (
            len(splits) >= 3
        ), f"Expected >= 3 splits for 2 years 12m/3m, got {len(splits)}: {splits}"

    def test_three_years_split_count(self) -> None:
        """3 years, 12m train, 3m test → 4 OOS windows (Jan–Mar, Apr–Jun, Jul–Sep, Oct–Dec)."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        # 3 years = 36 months; 12 train → 24 months of OOS available → 8 windows
        assert len(splits) >= 4

    def test_test_window_duration_approximately_test_months(self) -> None:
        """Each OOS window should be approximately *test_months* months long."""
        start = datetime(2021, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)

        for split in splits:
            duration_days = (split.test_end - split.test_start).days
            # 3 months ≈ 89–92 days; allow ±2 days tolerance for month boundaries
            assert (
                87 <= duration_days <= 94
            ), f"OOS window duration {duration_days} days is outside 87–94 range"

    def test_splits_step_forward_by_test_months(self) -> None:
        """Consecutive splits' train_start should differ by test_months."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2023, 6, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)

        for i in range(1, len(splits)):
            prev_start = splits[i - 1].train_start
            curr_start = splits[i].train_start
            # Should differ by approximately 3 months (89–94 days)
            diff_days = (curr_start - prev_start).days
            assert (
                87 <= diff_days <= 94
            ), f"Consecutive train_starts differ by {diff_days} days, expected ~91"

    def test_empty_range_returns_no_splits(self) -> None:
        """Range too short for even one split returns an empty list."""
        start = datetime(2022, 1, 1, tzinfo=UTC)
        end = datetime(2022, 6, 1, tzinfo=UTC)  # only 5 months — train=12 doesn't fit
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        assert splits == []

    def test_all_splits_have_valid_ordering(self) -> None:
        """All time-ordering invariants hold for every split."""
        start = datetime(2019, 1, 1, tzinfo=UTC)
        end = datetime(2023, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)

        for split in splits:
            assert split.train_start < split.train_end
            assert split.test_start < split.test_end
            assert split.test_start > split.train_end  # critical invariant
            assert split.train_start < split.test_end


# ---------------------------------------------------------------------------
# run_walk_forward
# ---------------------------------------------------------------------------


class TestRunWalkForward:
    def test_produces_walk_forward_report(self) -> None:
        """run_walk_forward returns a WalkForwardReport instance."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2022, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        candles = _make_candles(start, n_days=730)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

        assert isinstance(report, WalkForwardReport)

    def test_num_splits_at_least_one(self) -> None:
        """WalkForwardReport.num_splits >= 1 for adequate data."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2022, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        candles = _make_candles(start, n_days=730)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

        assert report.num_splits >= 1

    def test_per_split_results_length_matches_num_splits(self) -> None:
        """per_split_results length == num_splits."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2022, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        candles = _make_candles(start, n_days=730)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

        assert len(report.per_split_results) == report.num_splits

    def test_each_per_split_result_is_backtest_result(self) -> None:
        """Each element of per_split_results is a BacktestResult."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2022, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        candles = _make_candles(start, n_days=730)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=Decimal("10000"),
            exchange_id="binance",
            timeframe="1d",
            symbol="BTC/USDT",
        )

        for result in report.per_split_results:
            assert isinstance(result, BacktestResult)

    def test_empty_splits_returns_zero_num_splits(self) -> None:
        """Passing an empty splits list produces a report with num_splits == 0."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        candles = _make_candles(start, n_days=365)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=[],
            initial_capital=Decimal("10000"),
        )

        assert report.num_splits == 0
        assert report.per_split_results == []

    def test_strategy_id_propagated(self) -> None:
        """WalkForwardReport.strategy_id matches the strategy."""
        start = datetime(2020, 1, 1, tzinfo=UTC)
        end = datetime(2022, 1, 1, tzinfo=UTC)
        splits = generate_wf_splits(start, end, train_months=12, test_months=3)
        candles = _make_candles(start, n_days=730)
        strategy = _HoldStrategy()

        report = run_walk_forward(
            strategy=strategy,
            candles=candles,
            splits=splits,
            initial_capital=Decimal("10000"),
        )

        assert report.strategy_id == strategy.strategy_id
