"""Unit and property-based tests for the U3 Risk Manager.

Every test that exercises a specific rule is marked with the rule name in its
docstring so failures are instantly traceable to Section 6 of PLAN.md.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from hypothesis import given, settings
from hypothesis import strategies as st

from trendscend.risk.manager import BASE_TAKER_FEE, RISK_PARAMS, RiskManager
from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.risk import RiskOutcome, RiskStyle
from trendscend.schemas.signals import TradeAction, TradeProposal

# ── Helpers ────────────────────────────────────────────────────────────────────


def _proposal(
    action: TradeAction = TradeAction.BUY,
    size_pct: float = 0.10,
    stop_pct: float = 0.02,
    symbol: str = "BTC/USDT",
) -> TradeProposal:
    return TradeProposal(
        bot_id=uuid4(),
        symbol=symbol,
        action=action,
        size_pct=size_pct,
        stop_pct=stop_pct,
        rationale="test",
        contributing_signals=[],
        proposed_at=datetime.now(UTC),
    )


def _state(
    style: RiskStyle = RiskStyle.BALANCED,
    allocation: Decimal = Decimal("10000"),
    current_equity: Decimal = Decimal("10000"),
    equity_peak: Decimal = Decimal("10000"),
    daily_start_equity: Decimal = Decimal("10000"),
    open_positions: dict[str, float] | None = None,
    trades_today: int = 0,
    is_paused: bool = False,
    is_killed: bool = False,
) -> BotRiskState:
    return BotRiskState(
        bot_id=uuid4(),
        style=style,
        allocation=allocation,
        current_equity=current_equity,
        equity_peak=equity_peak,
        daily_start_equity=daily_start_equity,
        open_positions=open_positions or {},
        trades_today=trades_today,
        is_paused=is_paused,
        is_killed=is_killed,
        last_updated=datetime.now(UTC),
    )


# ── Unit tests — kill switch ───────────────────────────────────────────────────


class TestKillSwitch:
    def test_is_killed_rejects_all_actions(self) -> None:
        """Rule: is_killed → REJECTED immediately."""
        rm = RiskManager()
        for action in [TradeAction.BUY, TradeAction.SELL, TradeAction.HOLD]:
            state = _state(is_killed=True)
            decision = rm.evaluate(_proposal(action=action), state)
            assert decision.outcome == RiskOutcome.REJECTED

    def test_is_killed_does_not_block_close(self) -> None:
        """Rule: CLOSE actions still pass when kill switch is active (protective)."""
        rm = RiskManager()
        state = _state(is_killed=True)
        decision = rm.evaluate(_proposal(action=TradeAction.CLOSE), state)
        assert decision.outcome == RiskOutcome.APPROVED

    def test_drawdown_at_kill_threshold_triggers_kill_switch(self) -> None:
        """Rule: drawdown ≥ drawdown_kill_pct → KILL_SWITCH."""
        rm = RiskManager()
        # Balanced: kill at 20% drawdown
        equity = Decimal("8000")
        peak = Decimal("10000")  # drawdown = 20%
        state = _state(
            style=RiskStyle.BALANCED,
            current_equity=equity,
            equity_peak=peak,
        )
        decision = rm.evaluate(_proposal(), state)
        assert decision.outcome == RiskOutcome.KILL_SWITCH

    def test_drawdown_just_below_threshold_does_not_kill(self) -> None:
        """Rule: drawdown < drawdown_kill_pct → not a kill switch."""
        rm = RiskManager()
        # Balanced: kill at 20%. Use 19.9%.
        equity = Decimal("8010")
        peak = Decimal("10000")
        state = _state(
            style=RiskStyle.BALANCED,
            current_equity=equity,
            equity_peak=peak,
        )
        decision = rm.evaluate(_proposal(), state)
        assert decision.outcome != RiskOutcome.KILL_SWITCH


# ── Unit tests — daily loss pause ─────────────────────────────────────────────


class TestDailyLossLimit:
    def test_is_paused_rejects_buy(self) -> None:
        """Rule: is_paused → REJECTED for BUY."""
        rm = RiskManager()
        state = _state(is_paused=True)
        decision = rm.evaluate(_proposal(action=TradeAction.BUY), state)
        assert decision.outcome == RiskOutcome.REJECTED

    def test_is_paused_allows_close(self) -> None:
        """Rule: CLOSE always passes even when paused."""
        rm = RiskManager()
        state = _state(is_paused=True)
        decision = rm.evaluate(_proposal(action=TradeAction.CLOSE), state)
        assert decision.outcome == RiskOutcome.APPROVED

    def test_daily_loss_at_limit_returns_paused(self) -> None:
        """Rule: daily loss ≥ daily_loss_limit_pct → PAUSED outcome."""
        rm = RiskManager()
        # Conservative: 2% daily limit
        state = _state(
            style=RiskStyle.CONSERVATIVE,
            current_equity=Decimal("9800"),
            daily_start_equity=Decimal("10000"),  # -2.0% exactly
        )
        decision = rm.evaluate(_proposal(), state)
        assert decision.outcome == RiskOutcome.PAUSED

    def test_daily_loss_just_below_limit_continues(self) -> None:
        """Rule: daily loss < daily_loss_limit_pct → not paused."""
        rm = RiskManager()
        state = _state(
            style=RiskStyle.CONSERVATIVE,
            current_equity=Decimal("9810"),  # -1.9%
            daily_start_equity=Decimal("10000"),
        )
        decision = rm.evaluate(_proposal(), state)
        assert decision.outcome not in {RiskOutcome.PAUSED, RiskOutcome.KILL_SWITCH}


# ── Unit tests — max trades per day ───────────────────────────────────────────


class TestMaxTradesPerDay:
    def test_at_max_trades_rejects_buy(self) -> None:
        """Rule: trades_today ≥ max_trades_per_day → REJECTED for BUY."""
        rm = RiskManager()
        # Balanced: max 8 trades
        state = _state(style=RiskStyle.BALANCED, trades_today=8)
        decision = rm.evaluate(_proposal(action=TradeAction.BUY), state)
        assert decision.outcome == RiskOutcome.REJECTED

    def test_at_max_trades_allows_close(self) -> None:
        """Rule: CLOSE is allowed even when max trades reached."""
        rm = RiskManager()
        state = _state(style=RiskStyle.BALANCED, trades_today=8)
        decision = rm.evaluate(_proposal(action=TradeAction.CLOSE), state)
        assert decision.outcome == RiskOutcome.APPROVED

    def test_below_max_trades_allowed(self) -> None:
        rm = RiskManager()
        state = _state(style=RiskStyle.BALANCED, trades_today=7)
        decision = rm.evaluate(_proposal(action=TradeAction.BUY), state)
        assert decision.outcome not in {RiskOutcome.REJECTED}


# ── Unit tests — max open positions ───────────────────────────────────────────


class TestMaxOpenPositions:
    def test_at_max_positions_rejects_new_position(self) -> None:
        """Rule: open_positions count ≥ max → REJECTED for new position BUY."""
        rm = RiskManager()
        # Conservative: max 3 positions
        positions = {"BTC/USDT": 0.3, "ETH/USDT": 0.3, "SOL/USDT": 0.1}
        state = _state(style=RiskStyle.CONSERVATIVE, open_positions=positions)
        decision = rm.evaluate(_proposal(symbol="BNB/USDT"), state)
        assert decision.outcome == RiskOutcome.REJECTED

    def test_adding_to_existing_position_allowed_when_at_max(self) -> None:
        """Rule: adding to an existing position does not count as 'new'."""
        rm = RiskManager()
        positions = {"BTC/USDT": 0.3, "ETH/USDT": 0.3, "SOL/USDT": 0.1}
        state = _state(style=RiskStyle.CONSERVATIVE, open_positions=positions)
        # BUY BTC/USDT — already in positions
        decision = rm.evaluate(_proposal(symbol="BTC/USDT", size_pct=0.05), state)
        assert decision.outcome in {RiskOutcome.APPROVED, RiskOutcome.RESIZED}


# ── Unit tests — per-asset allocation cap ─────────────────────────────────────


class TestPerAssetCap:
    def test_oversized_proposal_gets_resized(self) -> None:
        """Rule: size_pct > max_allocation_per_asset → RESIZED."""
        rm = RiskManager()
        # Balanced: max 50% per asset
        state = _state(style=RiskStyle.BALANCED)
        decision = rm.evaluate(_proposal(size_pct=0.80, stop_pct=0.02), state)
        assert decision.outcome in {RiskOutcome.RESIZED, RiskOutcome.APPROVED}
        assert decision.approved_size_pct is not None
        assert decision.approved_size_pct <= 0.50 + 1e-9

    def test_undersized_proposal_not_changed(self) -> None:
        """Rule: size_pct < max_allocation_per_asset → not resized by cap alone."""
        rm = RiskManager()
        state = _state(style=RiskStyle.BALANCED)
        decision = rm.evaluate(_proposal(size_pct=0.20, stop_pct=0.02), state)
        if decision.outcome == RiskOutcome.APPROVED:
            assert decision.approved_size_pct is not None
            assert abs(decision.approved_size_pct - 0.20) < 1e-9


# ── Unit tests — risk per trade ────────────────────────────────────────────────


class TestRiskPerTrade:
    def test_high_risk_trade_gets_resized(self) -> None:
        """Rule: size × stop > risk_per_trade_pct → size shrunk proportionally."""
        rm = RiskManager()
        # Balanced: risk_per_trade_pct = 1%. size=0.40, stop=0.05 → risk=2% > 1%
        state = _state(style=RiskStyle.BALANCED)
        decision = rm.evaluate(_proposal(size_pct=0.40, stop_pct=0.05), state)
        assert decision.approved_size_pct is not None
        # Approved risk = approved_size × 0.05 ≤ 0.01 + epsilon
        assert decision.approved_size_pct * 0.05 <= 0.01 + 1e-9

    def test_no_stop_rejects_buy(self) -> None:
        """Rule: BUY/SELL without a stop → REJECTED."""
        rm = RiskManager()
        state = _state()
        proposal = TradeProposal(
            bot_id=uuid4(),
            symbol="BTC/USDT",
            action=TradeAction.BUY,
            size_pct=0.10,
            stop_pct=None,  # no stop!
            rationale="test",
            contributing_signals=[],
            proposed_at=datetime.now(UTC),
        )
        decision = rm.evaluate(proposal, state)
        assert decision.outcome == RiskOutcome.REJECTED


# ── Unit tests — min edge vs fees ─────────────────────────────────────────────


class TestMinEdgeVsFees:
    def test_stop_too_tight_vs_fees_rejected(self) -> None:
        """Rule: stop_pct < min_edge_multiplier × round_trip_fee → REJECTED."""
        rm = RiskManager()
        round_trip = 2.0 * BASE_TAKER_FEE  # 0.2%
        # Conservative: multiplier = 3.0; min_stop = 0.6%
        min_stop = 3.0 * round_trip  # 0.006
        state = _state(style=RiskStyle.CONSERVATIVE)
        decision = rm.evaluate(_proposal(stop_pct=min_stop * 0.5), state)
        assert decision.outcome == RiskOutcome.REJECTED

    def test_stop_at_min_edge_approved(self) -> None:
        """Rule: stop_pct ≥ min edge threshold → passes this check."""
        rm = RiskManager()
        round_trip = 2.0 * BASE_TAKER_FEE
        min_stop = 3.0 * round_trip  # 0.006
        state = _state(style=RiskStyle.CONSERVATIVE, allocation=Decimal("10000"))
        # size_pct × stop_pct ≤ 0.5% risk limit; stop=0.6%, so max size = 0.5%/0.6% ≈ 0.83
        decision = rm.evaluate(_proposal(size_pct=0.05, stop_pct=min_stop), state)
        assert decision.outcome in {RiskOutcome.APPROVED, RiskOutcome.RESIZED}


# ── Unit tests — CLOSE and HOLD pass-through ──────────────────────────────────


class TestPassthrough:
    def test_close_always_approved(self) -> None:
        """Rule: CLOSE proposals always approved (protective close)."""
        rm = RiskManager()
        # Even with terrible state
        state = _state(
            is_paused=True,
            trades_today=100,
            open_positions={"BTC/USDT": 0.9, "ETH/USDT": 0.9, "SOL/USDT": 0.5},
        )
        decision = rm.evaluate(_proposal(action=TradeAction.CLOSE), state)
        assert decision.outcome == RiskOutcome.APPROVED
        assert decision.approved_size_pct == 1.0

    def test_hold_always_approved(self) -> None:
        """Rule: HOLD proposals (no trade) always approved."""
        rm = RiskManager()
        state = _state(is_paused=True)
        decision = rm.evaluate(_proposal(action=TradeAction.HOLD), state)
        assert decision.outcome == RiskOutcome.APPROVED


# ── Checks run audit ──────────────────────────────────────────────────────────


class TestChecksRunAudit:
    def test_approved_decision_has_non_empty_checks(self) -> None:
        """All decisions must document which checks were run."""
        rm = RiskManager()
        state = _state()
        decision = rm.evaluate(_proposal(size_pct=0.10, stop_pct=0.02), state)
        assert len(decision.checks_run) > 0

    def test_kill_switch_decision_includes_kill_check(self) -> None:
        rm = RiskManager()
        state = _state(
            style=RiskStyle.BALANCED,
            current_equity=Decimal("8000"),
            equity_peak=Decimal("10000"),
        )
        decision = rm.evaluate(_proposal(), state)
        assert "drawdown_kill_switch" in decision.checks_run


# ── Hypothesis property-based tests ───────────────────────────────────────────


def _st_proposal(
    action: TradeAction = TradeAction.BUY,
) -> st.SearchStrategy[TradeProposal]:
    return st.builds(
        TradeProposal,
        bot_id=st.just(uuid4()),
        symbol=st.just("BTC/USDT"),
        action=st.just(action),
        size_pct=st.floats(min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False),
        stop_pct=st.floats(min_value=0.0001, max_value=0.5, allow_nan=False, allow_infinity=False),
        rationale=st.just("hypothesis"),
        contributing_signals=st.just([]),
        proposed_at=st.just(datetime.now(UTC)),
    )


def _st_state(style: RiskStyle) -> st.SearchStrategy[BotRiskState]:
    alloc = st.decimals(
        min_value=Decimal("100"),
        max_value=Decimal("1000000"),
        places=2,
        allow_nan=False,
        allow_infinity=False,
    ).filter(lambda d: d > 0)
    equity_ratio = st.floats(min_value=0.5, max_value=1.5, allow_nan=False)
    peak_ratio = st.floats(min_value=1.0, max_value=2.0, allow_nan=False)
    daily_ratio = st.floats(min_value=0.9, max_value=1.1, allow_nan=False)
    trades = st.integers(min_value=0, max_value=50)
    paused = st.booleans()
    killed = st.booleans()

    @st.composite
    def build(draw: Any) -> BotRiskState:
        a = draw(alloc)
        eq = Decimal(str(round(float(a) * draw(equity_ratio), 2)))
        if eq <= 0:
            eq = Decimal("1")
        pk = max(eq, Decimal(str(round(float(eq) * draw(peak_ratio), 2))))
        ds = Decimal(str(round(float(a) * draw(daily_ratio), 2)))
        if ds <= 0:
            ds = Decimal("1")
        return BotRiskState(
            bot_id=uuid4(),
            style=style,
            allocation=a,
            current_equity=eq,
            equity_peak=pk,
            daily_start_equity=ds,
            open_positions={},
            trades_today=draw(trades),
            is_paused=draw(paused),
            is_killed=draw(killed),
            last_updated=datetime.now(UTC),
        )

    return build()


@given(
    style=st.sampled_from(list(RiskStyle)),
    proposal=_st_proposal(TradeAction.BUY),
    state=st.one_of(*[_st_state(s) for s in RiskStyle]),
)
@settings(max_examples=500)
def test_approved_size_never_exceeds_per_asset_cap(
    style: RiskStyle,
    proposal: TradeProposal,
    state: BotRiskState,
) -> None:
    """Invariant: approved_size_pct ≤ max_allocation_per_asset for any input."""
    decision = RiskManager().evaluate(proposal, state)
    if decision.outcome == RiskOutcome.APPROVED:
        assert decision.approved_size_pct is not None
        cap = RISK_PARAMS[state.style]["max_allocation_per_asset"]
        assert decision.approved_size_pct <= cap + 1e-9


@given(
    proposal=_st_proposal(TradeAction.BUY),
    state=st.one_of(*[_st_state(s) for s in RiskStyle]).filter(
        lambda s: not s.is_killed and not s.is_paused
    ),
)
@settings(max_examples=300)
def test_approved_risk_per_trade_never_exceeds_limit(
    proposal: TradeProposal,
    state: BotRiskState,
) -> None:
    """Invariant: approved_size × stop ≤ risk_per_trade_pct for any input."""
    decision = RiskManager().evaluate(proposal, state)
    if decision.outcome == RiskOutcome.APPROVED and proposal.stop_pct is not None:
        assert decision.approved_size_pct is not None
        actual_risk = decision.approved_size_pct * proposal.stop_pct
        limit = RISK_PARAMS[state.style]["risk_per_trade_pct"]
        assert actual_risk <= limit + 1e-9, (
            f"risk {actual_risk:.4%} > limit {limit:.4%} for {state.style}"
        )


@given(
    proposal=_st_proposal(TradeAction.BUY),
    state=st.one_of(*[_st_state(s) for s in RiskStyle]).map(
        lambda s: BotRiskState(**{**s.model_dump(), "is_killed": True})
    ),
)
@settings(max_examples=200)
def test_killed_state_never_approves_buy(
    proposal: TradeProposal,
    state: BotRiskState,
) -> None:
    """Invariant: is_killed=True → BUY is never APPROVED."""
    decision = RiskManager().evaluate(proposal, state)
    assert decision.outcome == RiskOutcome.REJECTED


@given(
    style=st.sampled_from(list(RiskStyle)),
    state=st.one_of(*[_st_state(s) for s in RiskStyle]),
)
@settings(max_examples=200)
def test_close_always_approved_regardless_of_state(
    style: RiskStyle,
    state: BotRiskState,
) -> None:
    """Invariant: CLOSE is always APPROVED."""
    proposal = _proposal(action=TradeAction.CLOSE)
    decision = RiskManager().evaluate(proposal, state)
    assert decision.outcome == RiskOutcome.APPROVED
