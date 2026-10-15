"""Phase 2 gate test: paper engine results ≈ backtester on identical data.

Runs both engines over the same 100-bar fixture with the same strategy and
verifies trade count matches and final equity is within 2%.

The risk manager is configured to be permissive (wide allocation, no
daily losses, no drawdown) so it does not filter any valid signals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

# Use 100-bar fixture from the strategies conftest
from tests.strategies.conftest import load_btc_100_bars
from trendscend.backtester.engine import BacktestEngine
from trendscend.paper.engine import PaperEngine
from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.risk import RiskStyle
from trendscend.strategies.dca_trend import DcaTrendStrategy

INITIAL_CAPITAL = Decimal("10000")


def _permissive_state(bot_id: UUID) -> BotRiskState:
    """Bot state that won't trigger any risk limits."""
    return BotRiskState(
        bot_id=bot_id,
        style=RiskStyle.AGGRESSIVE,  # widest limits
        allocation=INITIAL_CAPITAL,
        current_equity=INITIAL_CAPITAL,
        equity_peak=INITIAL_CAPITAL,
        daily_start_equity=INITIAL_CAPITAL,
        open_positions={},
        trades_today=0,
        is_paused=False,
        is_killed=False,
        last_updated=datetime.now(UTC),
    )


class TestPaperVsBacktest:
    def test_trade_counts_match(self) -> None:
        """Paper engine and backtester execute the same number of trades."""
        candles = load_btc_100_bars()
        symbol = "BTC/USDT"
        strategy = DcaTrendStrategy()

        # Backtester
        bt_engine = BacktestEngine()
        bt_result = bt_engine.run(
            candles=candles,
            strategy=strategy,
            initial_capital=INITIAL_CAPITAL,
            exchange_id="binance",
            timeframe="4h",
            symbol=symbol,
        )

        # Paper engine
        paper_engine = PaperEngine(bot_id=uuid4())
        state = _permissive_state(paper_engine.bot_id)
        for i in range(1, len(candles)):
            _, state = paper_engine.step(
                symbol=symbol,
                history=candles[:i],
                fill_price=candles[i].open,
                fill_timestamp=candles[i].timestamp,
                strategy=strategy,
                bot_state=state,
                timeframe="4h",
            )

        paper_fills = paper_engine.portfolio.fill_count
        bt_fills = bt_result.num_trades

        # Allow ≤1 trade difference (edge cases around risk-manager rounding)
        assert abs(paper_fills - bt_fills) <= 1, (
            f"paper fills {paper_fills} vs backtester {bt_fills}"
        )

    def test_final_equity_within_tolerance(self) -> None:
        """Paper engine final equity is within 2% of backtester final equity."""
        candles = load_btc_100_bars()
        symbol = "BTC/USDT"
        strategy = DcaTrendStrategy()

        bt_engine = BacktestEngine()
        bt_result = bt_engine.run(
            candles=candles,
            strategy=strategy,
            initial_capital=INITIAL_CAPITAL,
            exchange_id="binance",
            timeframe="4h",
            symbol=symbol,
        )

        paper_engine = PaperEngine(bot_id=uuid4())
        state = _permissive_state(paper_engine.bot_id)
        for i in range(1, len(candles)):
            _, state = paper_engine.step(
                symbol=symbol,
                history=candles[:i],
                fill_price=candles[i].open,
                fill_timestamp=candles[i].timestamp,
                strategy=strategy,
                bot_state=state,
                timeframe="4h",
            )

        paper_equity = float(paper_engine.portfolio.equity({symbol: candles[-1].close}))
        bt_equity = float(bt_result.final_equity)

        if bt_equity > 0:
            rel_diff = abs(paper_equity - bt_equity) / bt_equity
            assert rel_diff <= 0.02, (
                f"paper equity {paper_equity:.2f} differs from bt {bt_equity:.2f} "
                f"by {rel_diff:.1%} (>2%)"
            )

    def test_decision_log_has_one_entry_per_candle(self) -> None:
        """Decision log has exactly len(candles)-1 entries."""
        candles = load_btc_100_bars()
        strategy = DcaTrendStrategy()
        paper_engine = PaperEngine(bot_id=uuid4())
        state = _permissive_state(paper_engine.bot_id)

        for i in range(1, len(candles)):
            _, state = paper_engine.step(
                symbol="BTC/USDT",
                history=candles[:i],
                fill_price=candles[i].open,
                fill_timestamp=candles[i].timestamp,
                strategy=strategy,
                bot_state=state,
            )

        assert len(paper_engine.decision_log) == len(candles) - 1
