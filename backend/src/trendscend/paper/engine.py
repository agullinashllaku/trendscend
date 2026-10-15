"""Paper trading engine.

Processes one candle at a time: classify regime → generate signal →
build proposal → risk check → simulate fill → log DecisionRecord.

This is the same code path as the backtester but with the Risk Manager
integrated and DecisionRecord logging.  Strategy code sees no difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from trendscend.backtester.fees import FeeModel
from trendscend.backtester.portfolio import InsufficientFundsError, Portfolio
from trendscend.risk.manager import RiskManager
from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.decisions import DecisionRecord
from trendscend.schemas.market import OHLCV, MarketSnapshot
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.risk import RiskDecision, RiskOutcome
from trendscend.schemas.signals import Signal, TradeAction, TradeProposal
from trendscend.strategies.base import Strategy


@dataclass
class PaperCycleResult:
    """Output of one paper trading cycle."""

    signal: Signal
    proposal: TradeProposal | None
    risk_decision: RiskDecision | None
    fill_executed: bool
    decision_record: DecisionRecord


def _signal_to_proposal(
    signal: Signal,
    current_exposure: float,
    bot_id: UUID,
) -> TradeProposal | None:
    """Convert a Signal to a TradeProposal.

    Returns None when the delta is below 1% (insignificant) or the action
    would be HOLD.

    The stop_pct defaults to the signal's suggested_stop_pct or 0.02 (2%)
    if none is provided.
    """
    delta = signal.target_exposure - current_exposure
    if abs(delta) < 0.01:
        return None

    stop_pct = signal.suggested_stop_pct if signal.suggested_stop_pct is not None else 0.02

    if signal.target_exposure <= 0.0 and current_exposure > 0.0:
        action = TradeAction.CLOSE
        size_pct = abs(current_exposure)
    elif delta > 0.0:
        action = TradeAction.BUY
        size_pct = min(1.0, abs(delta))
    else:
        action = TradeAction.SELL
        size_pct = min(1.0, abs(delta))

    return TradeProposal(
        bot_id=bot_id,
        symbol=signal.symbol,
        action=action,
        size_pct=size_pct,
        stop_pct=stop_pct,
        rationale=f"signal:{signal.strategy_id} target={signal.target_exposure:.2f}",
        llm_adjustment_factor=1.0,
        contributing_signals=[signal],
        proposed_at=signal.generated_at,
    )


class PaperEngine:
    """Step-by-step paper trading simulator.

    Usage::

        engine = PaperEngine(bot_id=my_bot_id)
        for i in range(1, len(candles)):
            history = candles[:i]
            fill_price = candles[i].open
            result, state = engine.step(
                symbol, history, fill_price, candles[i].timestamp,
                strategy, state, timeframe="4h"
            )
    """

    def __init__(self, bot_id: UUID, exchange_id: str = "binance") -> None:
        self.bot_id = bot_id
        self._exchange_id = exchange_id
        self._risk_manager = RiskManager()
        self._fee_model = FeeModel(exchange_id=exchange_id)
        self._portfolio: Portfolio | None = None
        self._current_exposure: float = 0.0
        self._decision_log: list[DecisionRecord] = []

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def portfolio(self) -> Portfolio:
        if self._portfolio is None:
            raise RuntimeError("Portfolio not initialised — call step() first.")
        return self._portfolio

    @property
    def decision_log(self) -> list[DecisionRecord]:
        return list(self._decision_log)

    # ── Public step interface ────────────────────────────────────────────────

    def step(
        self,
        symbol: str,
        history: list[OHLCV],
        fill_price: Decimal,
        fill_timestamp: datetime,
        strategy: Strategy,
        bot_state: BotRiskState,
        timeframe: str = "4h",
        regime: RegimeLabel | None = None,
    ) -> tuple[PaperCycleResult, BotRiskState]:
        """Process one candle step.

        Args:
            symbol:         Trading pair, e.g. "BTC/USDT".
            history:        Closed candles up to and including the signal bar.
            fill_price:     Price to simulate the fill at (next bar's open).
            fill_timestamp: Timestamp of the fill bar.
            strategy:       Strategy implementing the Strategy protocol.
            bot_state:      Current bot risk and portfolio state.
            timeframe:      Bar duration string.
            regime:         Optional pre-computed regime label.

        Returns:
            ``(PaperCycleResult, updated_BotRiskState)``
        """
        # Initialise portfolio on first call
        if self._portfolio is None:
            self._portfolio = Portfolio(initial_cash=bot_state.allocation)
            self._portfolio.mark_to_market(
                {symbol: history[-1].close if history else fill_price},
                history[-1].timestamp if history else fill_timestamp,
            )

        triggered_at = history[-1].timestamp if history else fill_timestamp

        # 1. Generate signal
        signal = strategy.generate_signal(symbol, history, regime)

        # 2. Build trade proposal
        proposal = _signal_to_proposal(signal, self._current_exposure, self.bot_id)

        # 3. Risk check
        risk_decision: RiskDecision | None = None
        fill_executed = False

        if proposal is not None:
            risk_decision = self._risk_manager.evaluate(proposal, bot_state)

            # 4. Execute fill if approved/resized
            if risk_decision.outcome in {RiskOutcome.APPROVED, RiskOutcome.RESIZED}:
                approved_size = (
                    risk_decision.approved_size_pct
                    if risk_decision.approved_size_pct is not None
                    else proposal.size_pct
                )
                fill_executed = self._execute_fill(
                    symbol=symbol,
                    action=proposal.action,
                    approved_size=approved_size,
                    fill_price=fill_price,
                    fill_timestamp=fill_timestamp,
                )

        # 5. Mark portfolio to market at fill price
        self._portfolio.mark_to_market({symbol: fill_price}, fill_timestamp)

        # 6. Update bot state
        new_state = self._update_state(bot_state, symbol, fill_executed, fill_price)

        # 7. Build and log DecisionRecord
        signal_bar_ts = history[-1].timestamp if history else fill_timestamp
        snapshot = MarketSnapshot(
            exchange=self._exchange_id,
            symbol=symbol,
            timeframe=timeframe,
            candles=history[-5:] if len(history) >= 5 else history,
            last_trade_price=fill_price,
            fetched_at=signal_bar_ts,
        )
        record = DecisionRecord(
            bot_id=self.bot_id,
            cycle_triggered_by="candle_close",
            triggered_at=triggered_at,
            market_snapshots=[snapshot],
            regime_labels=[regime] if regime is not None else [],
            signals=[signal],
            proposal=proposal,
            risk_decision=risk_decision,
            order_ids=[],
            completed_at=fill_timestamp,
        )
        self._decision_log.append(record)

        result = PaperCycleResult(
            signal=signal,
            proposal=proposal,
            risk_decision=risk_decision,
            fill_executed=fill_executed,
            decision_record=record,
        )
        return result, new_state

    # ── Private helpers ──────────────────────────────────────────────────────

    def _execute_fill(
        self,
        symbol: str,
        action: TradeAction,
        approved_size: float,
        fill_price: Decimal,
        fill_timestamp: datetime,
    ) -> bool:
        """Apply a fill to the internal portfolio. Returns True if filled."""
        assert self._portfolio is not None

        if action == TradeAction.CLOSE:
            pos = self._portfolio.positions.get(symbol, Decimal("0"))
            if pos <= Decimal("0"):
                return False
            slipped = self._fee_model.apply_slippage(fill_price, "sell")
            fee = self._fee_model.compute_fee(pos, slipped, "taker")
            self._portfolio.apply_fill(symbol, "sell", pos, slipped, fee, fill_timestamp)
            self._current_exposure = 0.0
            return True

        if action in {TradeAction.BUY, TradeAction.SELL}:
            side = "buy" if action == TradeAction.BUY else "sell"
            slipped = self._fee_model.apply_slippage(fill_price, side)

            # Size: approved_size fraction of initial capital
            initial_cap = self._portfolio.cash + sum(
                self._portfolio.positions.get(symbol, Decimal("0")) * fill_price for _ in [None]
            )
            notional = initial_cap * Decimal(str(approved_size))
            size = notional / slipped

            if size <= Decimal("0"):
                return False

            fee = self._fee_model.compute_fee(size, slipped, "taker")

            if side == "buy":
                affordable = self._portfolio.cash - fee
                if affordable <= Decimal("0"):
                    return False
                if size * slipped > affordable:
                    size = affordable / slipped
                    fee = self._fee_model.compute_fee(size, slipped, "taker")

            try:
                self._portfolio.apply_fill(symbol, side, size, slipped, fee, fill_timestamp)
            except InsufficientFundsError:
                return False

            if action == TradeAction.BUY:
                self._current_exposure = min(1.0, self._current_exposure + approved_size)
            else:
                self._current_exposure = max(0.0, self._current_exposure - approved_size)
            return True

        return False

    def _update_state(
        self,
        state: BotRiskState,
        symbol: str,
        fill_executed: bool,
        fill_price: Decimal,
    ) -> BotRiskState:
        """Return an updated BotRiskState after the current cycle."""
        assert self._portfolio is not None

        current_equity = self._portfolio.equity({symbol: fill_price})
        new_peak = max(state.equity_peak, current_equity)

        pos_size = self._portfolio.positions.get(symbol, Decimal("0"))
        pos_fraction = (
            float(pos_size * fill_price / state.allocation) if state.allocation > 0 else 0.0
        )
        new_positions = {**state.open_positions, symbol: pos_fraction}

        return BotRiskState(
            bot_id=state.bot_id,
            style=state.style,
            allocation=state.allocation,
            current_equity=current_equity,
            equity_peak=new_peak,
            daily_start_equity=state.daily_start_equity,
            open_positions=new_positions,
            trades_today=state.trades_today + (1 if fill_executed else 0),
            is_paused=state.is_paused,
            is_killed=state.is_killed,
            last_updated=datetime.now(UTC),
        )
