"""U3 Risk Manager — deterministic, final authority on every trade proposal.

All limits are taken verbatim from PLAN.md Section 6.  No LLM calls here.
The manager checks a ``TradeProposal`` against the current ``BotRiskState``
and returns a ``RiskDecision``.

Check order (important — earlier checks short-circuit later ones):
  1. Kill-switch state (is_killed)       — CLOSE still allowed as protective exit
  2. CLOSE / HOLD pass-throughs          — always approved so positions can be exited
  3. Drawdown kill-switch threshold      — blocks new entries only
  4. Pause state (is_paused)
  5. Daily-loss limit
  6. Max trades per day
  7. Max open positions
  8. Per-asset allocation cap  → may resize
  9. Risk-per-trade limit       → may resize
 10. Min stop vs round-trip fees
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.risk import RiskDecision, RiskOutcome, RiskStyle
from trendscend.schemas.signals import TradeAction, TradeProposal

BASE_TAKER_FEE: float = 0.001  # 0.1% — Binance/Bybit taker fee used for edge checks

RISK_PARAMS: dict[RiskStyle, dict[str, Any]] = {
    RiskStyle.CONSERVATIVE: {
        "max_allocation_per_asset": 0.40,
        "max_open_positions": 3,
        "risk_per_trade_pct": 0.005,
        "daily_loss_limit_pct": 0.02,
        "drawdown_kill_pct": 0.10,
        "max_trades_per_day": 3,
        "min_edge_multiplier": 3.0,
    },
    RiskStyle.BALANCED: {
        "max_allocation_per_asset": 0.50,
        "max_open_positions": 5,
        "risk_per_trade_pct": 0.010,
        "daily_loss_limit_pct": 0.04,
        "drawdown_kill_pct": 0.20,
        "max_trades_per_day": 8,
        "min_edge_multiplier": 2.0,
    },
    RiskStyle.AGGRESSIVE: {
        "max_allocation_per_asset": 0.60,
        "max_open_positions": 8,
        "risk_per_trade_pct": 0.020,
        "daily_loss_limit_pct": 0.07,
        "drawdown_kill_pct": 0.30,
        "max_trades_per_day": 20,
        "min_edge_multiplier": 1.5,
    },
}


def _drawdown(state: BotRiskState) -> float:
    if state.equity_peak == Decimal("0"):
        return 0.0
    return float((state.equity_peak - state.current_equity) / state.equity_peak)


def _daily_loss(state: BotRiskState) -> float:
    if state.daily_start_equity == Decimal("0"):
        return 0.0
    loss = (state.daily_start_equity - state.current_equity) / state.daily_start_equity
    return float(max(Decimal("0"), loss))


def _decision(
    outcome: RiskOutcome,
    proposal_id: Any,
    checks_run: list[str],
    *,
    approved_size_pct: float | None = None,
    rejection_reason: str | None = None,
) -> RiskDecision:
    return RiskDecision(
        id=uuid4(),
        proposal_id=proposal_id,
        outcome=outcome,
        approved_size_pct=approved_size_pct,
        rejection_reason=rejection_reason,
        checks_run=list(checks_run),
        decided_at=datetime.now(UTC),
    )


class RiskManager:
    """Evaluates a TradeProposal against a BotRiskState and returns a RiskDecision.

    Stateless — safe to instantiate once and reuse across many calls.
    """

    def evaluate(
        self,
        proposal: TradeProposal,
        state: BotRiskState,
    ) -> RiskDecision:
        """Run all risk checks and return the final decision.

        Args:
            proposal: The trade proposal from the Coordinator.
            state:    Current bot risk state (equity, positions, limits).

        Returns:
            A :class:`~trendscend.schemas.risk.RiskDecision` with one of:
            APPROVED, RESIZED, REJECTED, PAUSED, KILL_SWITCH.
        """
        params = RISK_PARAMS[state.style]
        checks: list[str] = []

        # 1. Kill switch state — CLOSE is still allowed (protective exit)
        checks.append("kill_switch_state")
        if state.is_killed:
            if proposal.action == TradeAction.CLOSE:
                return _decision(RiskOutcome.APPROVED, proposal.id, checks, approved_size_pct=1.0)
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason="kill_switch_active",
            )

        # 2. CLOSE and HOLD always pass — protective exits must never be blocked.
        # Placed before the drawdown kill-switch so we can still exit positions
        # when drawdown has hit the threshold.
        if proposal.action == TradeAction.CLOSE:
            return _decision(RiskOutcome.APPROVED, proposal.id, checks, approved_size_pct=1.0)
        if proposal.action == TradeAction.HOLD:
            return _decision(
                RiskOutcome.APPROVED,
                proposal.id,
                checks,
                approved_size_pct=proposal.size_pct,
            )

        # 3. Drawdown kill switch (new entries only — CLOSE/HOLD already handled)
        checks.append("drawdown_kill_switch")
        drawdown = _drawdown(state)
        if drawdown >= params["drawdown_kill_pct"]:
            return _decision(
                RiskOutcome.KILL_SWITCH,
                proposal.id,
                checks,
                rejection_reason=(
                    f"drawdown {drawdown:.1%} >= kill threshold {params['drawdown_kill_pct']:.0%}"
                ),
            )

        # 4. Pause state
        checks.append("pause_state")
        if state.is_paused:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason="daily_loss_limit_pause_active",
            )

        # 5. Daily loss limit
        checks.append("daily_loss_limit")
        daily_loss = _daily_loss(state)
        if daily_loss >= params["daily_loss_limit_pct"]:
            return _decision(
                RiskOutcome.PAUSED,
                proposal.id,
                checks,
                rejection_reason=(
                    f"daily loss {daily_loss:.1%} >= limit {params['daily_loss_limit_pct']:.0%}"
                ),
            )

        # 6. Max trades per day
        checks.append("max_trades_per_day")
        if state.trades_today >= params["max_trades_per_day"]:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason=(
                    f"trades_today {state.trades_today} >= max {params['max_trades_per_day']}"
                ),
            )

        # 7. Max open positions
        checks.append("max_open_positions")
        open_count = sum(1 for v in state.open_positions.values() if v > 0.0)
        is_new = (
            proposal.symbol not in state.open_positions
            or state.open_positions.get(proposal.symbol, 0.0) == 0.0
        )
        if is_new and open_count >= params["max_open_positions"]:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason=(
                    f"open positions {open_count} >= max {params['max_open_positions']}"
                ),
            )

        # Protective stop required for BUY/SELL
        checks.append("stop_required")
        if proposal.stop_pct is None or proposal.stop_pct <= 0.0:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason="stop_price_required: BUY/SELL must include stop_pct",
            )

        stop = proposal.stop_pct
        size = proposal.size_pct

        # 8. Per-asset allocation cap
        checks.append("per_asset_cap")
        cap = params["max_allocation_per_asset"]
        if size > cap:
            size = cap

        # 9. Risk per trade: size × stop ≤ risk_per_trade_pct
        checks.append("risk_per_trade")
        risk_limit = params["risk_per_trade_pct"]
        if size * stop > risk_limit:
            size = risk_limit / stop

        size = max(0.0, min(size, 1.0))

        if size <= 0.0:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason="size_zeroed_after_risk_sizing",
            )

        # 10. Min stop vs round-trip fees
        checks.append("min_edge_vs_fees")
        round_trip_fee = 2.0 * BASE_TAKER_FEE
        min_stop = params["min_edge_multiplier"] * round_trip_fee
        if stop < min_stop:
            return _decision(
                RiskOutcome.REJECTED,
                proposal.id,
                checks,
                rejection_reason=(
                    f"stop {stop:.3%} < min {min_stop:.3%} "
                    f"({params['min_edge_multiplier']}× round-trip fees)"
                ),
            )

        was_resized = abs(size - proposal.size_pct) > 1e-9
        outcome = RiskOutcome.RESIZED if was_resized else RiskOutcome.APPROVED
        return _decision(outcome, proposal.id, checks, approved_size_pct=size)
