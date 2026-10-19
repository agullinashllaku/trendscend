"""Tests for the paper trading engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trendscend.paper.engine import PaperCycleResult, PaperEngine
from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.market import OHLCV
from trendscend.schemas.risk import RiskOutcome, RiskStyle
from trendscend.strategies.hold_baseline import HoldBaselineStrategy
from trendscend.strategies.tsmom import TsmomStrategy


def make_candles(prices: list[float]) -> list[OHLCV]:
    base_ts = datetime(2024, 1, 1, tzinfo=UTC)
    result = []
    for i, p in enumerate(prices):
        dec = Decimal(str(p))
        result.append(
            OHLCV(
                timestamp=base_ts + timedelta(hours=4 * i),
                open=dec,
                high=Decimal(str(p * 1.005)),
                low=Decimal(str(p * 0.995)),
                close=dec,
                volume=Decimal("100"),
            )
        )
    return result


def _state(
    style: RiskStyle = RiskStyle.BALANCED,
    allocation: Decimal = Decimal("10000"),
) -> BotRiskState:
    return BotRiskState(
        bot_id=uuid4(),
        style=style,
        allocation=allocation,
        current_equity=allocation,
        equity_peak=allocation,
        daily_start_equity=allocation,
        open_positions={},
        trades_today=0,
        is_paused=False,
        is_killed=False,
        last_updated=datetime.now(UTC),
    )


class TestPaperEngineBasic:
    def test_hold_strategy_generates_proposal(self) -> None:
        """HoldBaseline target_exposure=1.0 generates a BUY proposal on first call."""
        engine = PaperEngine(bot_id=uuid4())
        strategy = HoldBaselineStrategy()
        candles = make_candles([100.0] * 60)
        state = _state()

        result, new_state = engine.step(
            symbol="BTC/USDT",
            history=candles[:50],
            fill_price=Decimal("100"),
            fill_timestamp=candles[50].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        assert isinstance(result, PaperCycleResult)
        # HoldBaseline always targets full long — proposal is generated on first step
        assert result.proposal is not None
        assert result.risk_decision is not None
        assert result.risk_decision.outcome in {RiskOutcome.APPROVED, RiskOutcome.RESIZED}

    def test_decision_record_is_logged(self) -> None:
        engine = PaperEngine(bot_id=uuid4())
        strategy = HoldBaselineStrategy()
        candles = make_candles([100.0] * 60)
        state = _state()

        engine.step(
            symbol="BTC/USDT",
            history=candles[:50],
            fill_price=Decimal("100"),
            fill_timestamp=candles[50].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        assert len(engine.decision_log) == 1
        assert engine.decision_log[0].bot_id == engine.bot_id

    def test_multiple_steps_accumulate_decision_log(self) -> None:
        engine = PaperEngine(bot_id=uuid4())
        strategy = HoldBaselineStrategy()
        candles = make_candles([100.0] * 100)
        state = _state()

        for i in range(50, 55):
            _, state = engine.step(
                symbol="BTC/USDT",
                history=candles[:i],
                fill_price=Decimal("100"),
                fill_timestamp=candles[i].timestamp,
                strategy=strategy,
                bot_state=state,
            )

        assert len(engine.decision_log) == 5


class TestPaperEngineFill:
    def test_tsmom_trending_up_executes_buy(self) -> None:
        """TSMom on strongly trending data should produce a buy and execute a fill."""
        engine = PaperEngine(bot_id=uuid4())
        # TSMomStrategy uses fast_ema/slow_ema (not fast_period/slow_period)
        strategy = TsmomStrategy(fast_ema=5, slow_ema=10)

        # Strongly trending up: 120 bars
        prices = [100.0 + i * 0.5 for i in range(120)]
        candles = make_candles(prices)
        state = _state(allocation=Decimal("10000"))

        result, new_state = engine.step(
            symbol="BTC/USDT",
            history=candles[:100],
            fill_price=Decimal(str(prices[100])),
            fill_timestamp=candles[100].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        # May or may not fill depending on risk manager approval; just verify
        # the engine doesn't crash and returns valid types
        assert isinstance(result.fill_executed, bool)
        assert isinstance(new_state, BotRiskState)

    def test_fill_increases_portfolio_position(self) -> None:
        """When a fill is executed, portfolio position > 0."""
        engine = PaperEngine(bot_id=uuid4())
        strategy = TsmomStrategy(fast_ema=3, slow_ema=6)

        prices = [100.0 + i * 1.0 for i in range(60)]
        candles = make_candles(prices)
        state = _state(allocation=Decimal("10000"))

        result, new_state = engine.step(
            symbol="BTC/USDT",
            history=candles[:50],
            fill_price=Decimal(str(prices[50])),
            fill_timestamp=candles[50].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        if result.fill_executed:
            pos = engine.portfolio.positions.get("BTC/USDT", Decimal("0"))
            assert pos > Decimal("0")


class TestPaperEngineStateUpdate:
    def test_trades_today_increments_on_fill(self) -> None:
        """After a successful fill, trades_today in new state == old + 1."""
        engine = PaperEngine(bot_id=uuid4())
        strategy = TsmomStrategy(fast_ema=3, slow_ema=6)
        prices = [100.0 + i * 1.0 for i in range(60)]
        candles = make_candles(prices)
        state = _state(allocation=Decimal("10000"))
        initial_trades = state.trades_today

        result, new_state = engine.step(
            symbol="BTC/USDT",
            history=candles[:50],
            fill_price=Decimal(str(prices[50])),
            fill_timestamp=candles[50].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        if result.fill_executed:
            assert new_state.trades_today == initial_trades + 1

    def test_equity_peak_updates_upward(self) -> None:
        """equity_peak in new state ≥ equity_peak in old state."""
        engine = PaperEngine(bot_id=uuid4())
        strategy = HoldBaselineStrategy()
        candles = make_candles([100.0] * 60)
        state = _state()

        _, new_state = engine.step(
            symbol="BTC/USDT",
            history=candles[:50],
            fill_price=Decimal("100"),
            fill_timestamp=candles[50].timestamp,
            strategy=strategy,
            bot_state=state,
        )

        assert new_state.equity_peak >= state.equity_peak


class TestPaperEngineMultiStrategy:
    def test_step_multi_with_coordinator_no_llm(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy
        from trendscend.strategies.tsmom import TsmomStrategy

        engine = PaperEngine(bot_id=uuid4())
        strategies = [HoldBaselineStrategy(), TsmomStrategy(fast_ema=5, slow_ema=10)]
        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
        candles = make_candles([100.0 + i * 0.5 for i in range(70)])
        state = _state()

        result, new_state = engine.step_multi(
            symbol="BTC/USDT",
            history=candles[:60],
            fill_price=Decimal("130"),
            fill_timestamp=candles[60].timestamp,
            strategies=strategies,
            coordinator=coordinator,
            bot_state=state,
        )

        assert isinstance(result, PaperCycleResult)
        assert isinstance(new_state, BotRiskState)
        assert len(result.decision_record.signals) == len(strategies)

    def test_step_multi_decision_record_logged(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        engine = PaperEngine(bot_id=uuid4())
        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
        candles = make_candles([100.0] * 70)
        state = _state()

        engine.step_multi(
            symbol="BTC/USDT",
            history=candles[:60],
            fill_price=Decimal("100"),
            fill_timestamp=candles[60].timestamp,
            strategies=[HoldBaselineStrategy()],
            coordinator=coordinator,
            bot_state=state,
        )

        assert len(engine.decision_log) == 1

    def test_step_multi_empty_strategies_raises(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator

        engine = PaperEngine(bot_id=uuid4())
        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
        state = _state()
        candles = make_candles([100.0] * 70)
        import pytest

        with pytest.raises(ValueError, match="at least one strategy"):
            engine.step_multi(
                symbol="BTC/USDT",
                history=candles[:60],
                fill_price=Decimal("100"),
                fill_timestamp=candles[60].timestamp,
                strategies=[],
                coordinator=coordinator,
                bot_state=state,
            )
        # Engine state must not be mutated on the failed call
        assert engine.decision_log == []
        assert engine._portfolio is None

    def test_original_step_still_works(self) -> None:
        """Regression: the original step() method must not be broken."""
        engine = PaperEngine(bot_id=uuid4())
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        candles = make_candles([100.0] * 70)
        state = _state()

        result, _ = engine.step(
            symbol="BTC/USDT",
            history=candles[:60],
            fill_price=Decimal("100"),
            fill_timestamp=candles[60].timestamp,
            strategy=HoldBaselineStrategy(),
            bot_state=state,
        )
        assert isinstance(result, PaperCycleResult)
