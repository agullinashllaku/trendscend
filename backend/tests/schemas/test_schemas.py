"""Schema validation tests — written before schema implementation (TDD)."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from trendscend.schemas.bot import BotRiskState
from trendscend.schemas.decisions import DecisionRecord  # noqa: E402
from trendscend.schemas.market import OHLCV, MarketSnapshot
from trendscend.schemas.news import (
    AssetNewsState,
    NewsDirection,
    NewsEvent,
    NewsEventType,
    NewsState,
)
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.risk import RiskDecision, RiskOutcome, RiskStyle
from trendscend.schemas.signals import Signal, TradeAction, TradeProposal
from trendscend.schemas.smart_money import SmartMoneyAssetState, SmartMoneyState

NOW = datetime.now(UTC)
BOT_ID = uuid4()


# ── helpers ──────────────────────────────────────────────────────────────────


def make_candle() -> OHLCV:
    return OHLCV(
        timestamp=NOW,
        open=Decimal("50000"),
        high=Decimal("51000"),
        low=Decimal("49000"),
        close=Decimal("50500"),
        volume=Decimal("100"),
    )


def make_snapshot() -> MarketSnapshot:
    return MarketSnapshot(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        candles=[make_candle()],
        last_trade_price=Decimal("50500"),
        fetched_at=NOW,
    )


def make_regime() -> RegimeLabel:
    return RegimeLabel(
        symbol="BTC/USDT",
        timeframe="4h",
        regime=RegimeType.TRENDING_UP,
        confidence=0.85,
        labeled_at=NOW,
    )


def make_signal() -> Signal:
    return Signal(
        strategy_id="tsmom",
        symbol="BTC/USDT",
        target_exposure=0.5,
        conviction=0.8,
        timeframe="4h",
        generated_at=NOW,
    )


# ── MarketSnapshot ────────────────────────────────────────────────────────────


class TestMarketSnapshot:
    def test_valid_snapshot(self) -> None:
        snap = make_snapshot()
        assert snap.exchange == "binance"
        assert snap.symbol == "BTC/USDT"
        assert snap.is_stale is False

    def test_optional_fields_default_none(self) -> None:
        snap = make_snapshot()
        assert snap.bid is None
        assert snap.ask is None
        assert snap.funding_rate is None
        assert snap.open_interest is None

    def test_close_is_decimal(self) -> None:
        candle = make_candle()
        assert isinstance(candle.close, Decimal)

    def test_stale_flag_can_be_set(self) -> None:
        snap = make_snapshot()
        stale = snap.model_copy(update={"is_stale": True})
        assert stale.is_stale is True


# ── RegimeLabel ───────────────────────────────────────────────────────────────


class TestRegimeLabel:
    def test_all_regime_types(self) -> None:
        for regime in RegimeType:
            label = RegimeLabel(
                symbol="BTC/USDT",
                timeframe="4h",
                regime=regime,
                confidence=0.7,
                labeled_at=NOW,
            )
            assert label.regime == regime

    def test_confidence_must_be_between_0_and_1(self) -> None:
        with pytest.raises(ValidationError):
            RegimeLabel(
                symbol="BTC/USDT",
                timeframe="4h",
                regime=RegimeType.RANGING,
                confidence=1.5,
                labeled_at=NOW,
            )

    def test_confidence_at_boundaries(self) -> None:
        for val in (0.0, 1.0):
            label = RegimeLabel(
                symbol="ETH/USDT",
                timeframe="1d",
                regime=RegimeType.RANGING,
                confidence=val,
                labeled_at=NOW,
            )
            assert label.confidence == val


# ── NewsEvent / NewsState ─────────────────────────────────────────────────────


class TestNewsEvent:
    def test_valid_event(self) -> None:
        event = NewsEvent(
            source="cryptopanic",
            url="https://example.com/news/1",
            published_at=NOW,
            assets=["BTC"],
            event_type=NewsEventType.EXPLOIT_HACK,
            severity=5,
            direction=NewsDirection.BEARISH,
            half_life_hours=24.0,
            confidence=0.9,
            is_new_information=True,
            llm_model="claude-haiku-4-5-20251001",
            raw_hash="abc123" * 8,
            classified_at=NOW,
        )
        assert event.severity == 5
        assert event.id is not None  # auto-generated UUID

    def test_severity_too_high_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NewsEvent(
                source="test",
                url="https://example.com",
                published_at=NOW,
                assets=[],
                event_type=NewsEventType.OTHER,
                severity=6,
                direction=NewsDirection.NEUTRAL,
                half_life_hours=1.0,
                confidence=0.5,
                is_new_information=False,
                llm_model="test",
                raw_hash="hash",
                classified_at=NOW,
            )

    def test_severity_too_low_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NewsEvent(
                source="test",
                url="https://example.com",
                published_at=NOW,
                assets=[],
                event_type=NewsEventType.OTHER,
                severity=0,
                direction=NewsDirection.NEUTRAL,
                half_life_hours=1.0,
                confidence=0.5,
                is_new_information=False,
                llm_model="test",
                raw_hash="hash",
                classified_at=NOW,
            )

    def test_all_event_types_valid(self) -> None:
        for et in NewsEventType:
            event = NewsEvent(
                source="test",
                url="https://example.com",
                published_at=NOW,
                assets=["BTC"],
                event_type=et,
                severity=1,
                direction=NewsDirection.NEUTRAL,
                half_life_hours=1.0,
                confidence=0.5,
                is_new_information=False,
                llm_model="test",
                raw_hash="hash",
                classified_at=NOW,
            )
            assert event.event_type == et

    def test_news_state_serializes(self) -> None:
        event = NewsEvent(
            source="cryptopanic",
            url="https://example.com",
            published_at=NOW,
            assets=["ETH"],
            event_type=NewsEventType.REGULATORY_POSITIVE,
            severity=3,
            direction=NewsDirection.BULLISH,
            half_life_hours=48.0,
            confidence=0.75,
            is_new_information=True,
            llm_model="claude-haiku-4-5-20251001",
            raw_hash="x" * 64,
            classified_at=NOW,
        )
        state = NewsState(
            per_asset={
                "ETH": AssetNewsState(
                    symbol="ETH",
                    recent_events=[event],
                    max_severity_24h=3,
                    net_direction=NewsDirection.BULLISH,
                    has_severe_negative=False,
                )
            },
            computed_at=NOW,
        )
        data = state.model_dump()
        assert "ETH" in data["per_asset"]


# ── SmartMoneyState ───────────────────────────────────────────────────────────


class TestSmartMoneyState:
    def test_valid_state(self) -> None:
        state = SmartMoneyState(
            per_asset={
                "BTC/USDT": SmartMoneyAssetState(
                    symbol="BTC/USDT",
                    net_long_pct=0.65,
                    net_short_pct=0.35,
                    change_24h=0.05,
                    concentration=0.3,
                )
            },
            vetted_wallet_count=47,
            computed_at=NOW,
        )
        assert state.vetted_wallet_count == 47

    def test_net_long_pct_bounds(self) -> None:
        with pytest.raises(ValidationError):
            SmartMoneyAssetState(
                symbol="BTC/USDT",
                net_long_pct=1.1,  # invalid
                net_short_pct=0.0,
                change_24h=0.0,
                concentration=0.0,
            )


# ── Signal / TradeProposal ────────────────────────────────────────────────────


class TestSignal:
    def test_valid_signal(self) -> None:
        signal = make_signal()
        assert signal.target_exposure == 0.5
        assert signal.id is not None

    def test_exposure_over_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Signal(
                strategy_id="tsmom",
                symbol="BTC/USDT",
                target_exposure=1.5,
                conviction=0.8,
                timeframe="4h",
                generated_at=NOW,
            )

    def test_exposure_under_minus1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            Signal(
                strategy_id="tsmom",
                symbol="BTC/USDT",
                target_exposure=-1.1,
                conviction=0.8,
                timeframe="4h",
                generated_at=NOW,
            )

    def test_full_short_exposure_valid(self) -> None:
        signal = Signal(
            strategy_id="tsmom",
            symbol="ETH/USDT",
            target_exposure=-1.0,
            conviction=0.9,
            timeframe="1d",
            generated_at=NOW,
        )
        assert signal.target_exposure == -1.0

    def test_trade_proposal_llm_factor_bounds(self) -> None:
        with pytest.raises(ValidationError):
            TradeProposal(
                bot_id=BOT_ID,
                symbol="BTC/USDT",
                action=TradeAction.BUY,
                size_pct=0.5,
                rationale="test",
                llm_adjustment_factor=1.5,  # invalid: > 1.25
                contributing_signals=[make_signal()],
                proposed_at=NOW,
            )

    def test_trade_proposal_valid(self) -> None:
        proposal = TradeProposal(
            bot_id=BOT_ID,
            symbol="BTC/USDT",
            action=TradeAction.BUY,
            size_pct=0.3,
            rationale="tsmom signal strong in trending regime",
            contributing_signals=[make_signal()],
            proposed_at=NOW,
        )
        assert proposal.llm_adjustment_factor == 1.0  # default
        assert proposal.llm_concerns is None


# ── RiskDecision ──────────────────────────────────────────────────────────────


class TestRiskDecision:
    def test_approved_decision(self) -> None:
        decision = RiskDecision(
            id=uuid4(),
            proposal_id=uuid4(),
            outcome=RiskOutcome.APPROVED,
            approved_size_pct=0.8,
            checks_run=["allocation_check", "position_count", "daily_loss"],
            decided_at=NOW,
        )
        assert decision.outcome == RiskOutcome.APPROVED
        assert decision.rejection_reason is None

    def test_rejected_decision(self) -> None:
        decision = RiskDecision(
            id=uuid4(),
            proposal_id=uuid4(),
            outcome=RiskOutcome.REJECTED,
            rejection_reason="Daily loss limit reached (4% hit)",
            checks_run=["daily_loss_check"],
            decided_at=NOW,
        )
        assert decision.approved_size_pct is None

    def test_kill_switch_decision(self) -> None:
        decision = RiskDecision(
            id=uuid4(),
            proposal_id=uuid4(),
            outcome=RiskOutcome.KILL_SWITCH,
            rejection_reason="Drawdown exceeded 20% kill switch threshold",
            checks_run=["drawdown_check"],
            decided_at=NOW,
        )
        assert decision.outcome == RiskOutcome.KILL_SWITCH

    def test_approved_size_pct_bounds(self) -> None:
        with pytest.raises(ValidationError):
            RiskDecision(
                id=uuid4(),
                proposal_id=uuid4(),
                outcome=RiskOutcome.APPROVED,
                approved_size_pct=1.1,  # invalid: > 1.0
                checks_run=["check"],
                decided_at=NOW,
            )


# ── RiskStyle ─────────────────────────────────────────────────────────────────


class TestRiskStyle:
    def test_all_risk_style_values(self) -> None:
        assert RiskStyle.CONSERVATIVE == "conservative"
        assert RiskStyle.BALANCED == "balanced"
        assert RiskStyle.AGGRESSIVE == "aggressive"
        assert len(list(RiskStyle)) == 3


# ── DecisionRecord ────────────────────────────────────────────────────────────


class TestDecisionRecord:
    def test_minimal_record(self) -> None:
        record = DecisionRecord(
            bot_id=BOT_ID,
            cycle_triggered_by="candle_close",
            triggered_at=NOW,
            market_snapshots=[make_snapshot()],
            regime_labels=[make_regime()],
        )
        assert record.id is not None
        assert record.proposal is None
        assert record.risk_decision is None
        assert record.error is None
        assert record.order_ids == []

    def test_full_record_with_all_fields(self) -> None:
        signal = make_signal()
        proposal = TradeProposal(
            bot_id=BOT_ID,
            symbol="BTC/USDT",
            action=TradeAction.BUY,
            size_pct=0.3,
            rationale="test",
            contributing_signals=[signal],
            proposed_at=NOW,
        )
        decision = RiskDecision(
            id=uuid4(),
            proposal_id=proposal.id,
            outcome=RiskOutcome.APPROVED,
            approved_size_pct=0.3,
            checks_run=["all"],
            decided_at=NOW,
        )
        record = DecisionRecord(
            bot_id=BOT_ID,
            cycle_triggered_by="candle_close",
            triggered_at=NOW,
            market_snapshots=[make_snapshot()],
            regime_labels=[make_regime()],
            signals=[signal],
            proposal=proposal,
            risk_decision=decision,
            order_ids=["binance-order-123"],
            completed_at=NOW,
        )
        assert record.risk_decision.outcome == RiskOutcome.APPROVED
        assert len(record.order_ids) == 1

    def test_record_serialises_to_json(self) -> None:
        record = DecisionRecord(
            bot_id=BOT_ID,
            cycle_triggered_by="news_event",
            triggered_at=NOW,
            market_snapshots=[make_snapshot()],
            regime_labels=[make_regime()],
        )
        json_str = record.model_dump_json()
        assert "candle_close" not in json_str  # triggered_by is news_event
        assert "news_event" in json_str


class TestBotRiskState:
    def _valid_state(self) -> "BotRiskState":
        return BotRiskState(
            bot_id=uuid4(),
            style=RiskStyle.BALANCED,
            allocation=Decimal("10000"),
            current_equity=Decimal("10000"),
            equity_peak=Decimal("10000"),
            daily_start_equity=Decimal("10000"),
            open_positions={},
            trades_today=0,
            is_paused=False,
            is_killed=False,
            last_updated=datetime.now(UTC),
        )

    def test_valid_state_constructs(self) -> None:
        state = self._valid_state()
        assert state.style == RiskStyle.BALANCED
        assert state.allocation == Decimal("10000")

    def test_open_positions_defaults_to_empty_dict(self) -> None:
        state = self._valid_state()
        assert state.open_positions == {}

    def test_negative_allocation_raises(self) -> None:
        with pytest.raises(ValidationError):
            BotRiskState(
                bot_id=uuid4(),
                style=RiskStyle.BALANCED,
                allocation=Decimal("-1"),
                current_equity=Decimal("0"),
                equity_peak=Decimal("0"),
                daily_start_equity=Decimal("0"),
                trades_today=0,
                is_paused=False,
                is_killed=False,
                last_updated=datetime.now(UTC),
            )

    def test_negative_trades_today_raises(self) -> None:
        with pytest.raises(ValidationError):
            state = self._valid_state()
            BotRiskState(**{**state.model_dump(), "trades_today": -1})


from trendscend.schemas.scoring import NewsEventScore, SmartMoneyScore  # noqa: E402


class TestNewsEventScore:
    def test_valid_score_constructs(self) -> None:
        score = NewsEventScore(
            event_id=uuid4(),
            symbol="BTC/USDT",
            direction=NewsDirection.BEARISH,
            severity=4,
            return_1h=-0.02,
            return_4h=-0.05,
            return_24h=-0.08,
            scored_at=datetime.now(UTC),
        )
        assert score.severity == 4
        assert score.return_1h == -0.02

    def test_none_returns_allowed(self) -> None:
        score = NewsEventScore(
            event_id=uuid4(),
            symbol="ETH/USDT",
            direction=NewsDirection.BULLISH,
            severity=2,
            return_1h=None,
            return_4h=None,
            return_24h=None,
            scored_at=datetime.now(UTC),
        )
        assert score.return_24h is None

    def test_invalid_severity_raises(self) -> None:
        with pytest.raises(ValidationError):
            NewsEventScore(
                event_id=uuid4(),
                symbol="BTC/USDT",
                direction=NewsDirection.NEUTRAL,
                severity=6,  # invalid
                return_1h=None,
                return_4h=None,
                return_24h=None,
                scored_at=datetime.now(UTC),
            )


class TestSmartMoneyScore:
    def test_valid_score_constructs(self) -> None:
        score = SmartMoneyScore(
            snapshot_at=datetime.now(UTC),
            symbol="BTC/USDT",
            net_long_pct_before=0.6,
            net_long_pct_after=0.7,
            return_1h=0.01,
            return_4h=0.03,
            return_24h=0.05,
            scored_at=datetime.now(UTC),
        )
        assert score.net_long_pct_before == 0.6

    def test_pct_out_of_range_raises(self) -> None:
        with pytest.raises(ValidationError):
            SmartMoneyScore(
                snapshot_at=datetime.now(UTC),
                symbol="BTC/USDT",
                net_long_pct_before=1.5,  # invalid
                net_long_pct_after=0.7,
                return_1h=None,
                return_4h=None,
                return_24h=None,
                scored_at=datetime.now(UTC),
            )


class TestJournalEntry:
    def test_valid_daily(self) -> None:
        from trendscend.schemas.reflection import JournalEntry

        entry = JournalEntry(
            bot_id=uuid4(),
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            content="Quiet day, one trade executed.",
            trade_count=1,
            win_rate=1.0,
            period_pnl_pct=0.015,
            generated_at=datetime(2024, 1, 2, 1, tzinfo=UTC),
            llm_model="claude-sonnet-4-6",
        )
        assert entry.period_type == "daily"
        assert entry.trade_count == 1

    def test_win_rate_none_allowed(self) -> None:
        from trendscend.schemas.reflection import JournalEntry

        entry = JournalEntry(
            bot_id=uuid4(),
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 8, tzinfo=UTC),
            period_type="weekly",
            content="No trades this week.",
            trade_count=0,
            win_rate=None,
            period_pnl_pct=None,
            generated_at=datetime(2024, 1, 8, tzinfo=UTC),
            llm_model="claude-sonnet-4-6",
        )
        assert entry.win_rate is None

    def test_invalid_period_type_rejected(self) -> None:
        from trendscend.schemas.reflection import JournalEntry

        with pytest.raises(ValidationError):
            JournalEntry(
                bot_id=uuid4(),
                period_start=datetime(2024, 1, 1, tzinfo=UTC),
                period_end=datetime(2024, 1, 2, tzinfo=UTC),
                period_type="monthly",  # invalid
                content="x",
                trade_count=0,
                generated_at=datetime(2024, 1, 2, tzinfo=UTC),
                llm_model="claude-sonnet-4-6",
            )


class TestParamChangeProposal:
    def test_valid_proposal(self) -> None:
        from trendscend.schemas.reflection import ParamChangeProposal

        p = ParamChangeProposal(
            journal_entry_id=uuid4(),
            strategy_id="tsmom",
            parameter="fast_ema",
            current_value="5",
            proposed_value="8",
            rationale="Slow EMA captures trend better.",
            proposed_at=datetime(2024, 1, 8, tzinfo=UTC),
        )
        assert p.status == "proposed"
        assert p.strategy_id == "tsmom"

    def test_status_default_is_proposed(self) -> None:
        from trendscend.schemas.reflection import ParamChangeProposal

        p = ParamChangeProposal(
            journal_entry_id=uuid4(),
            strategy_id="breakout",
            parameter="lookback",
            current_value="20",
            proposed_value="30",
            rationale="Wider window reduces false breakouts.",
            proposed_at=datetime(2024, 1, 8, tzinfo=UTC),
        )
        assert p.status == "proposed"

    def test_invalid_status_rejected(self) -> None:
        from trendscend.schemas.reflection import ParamChangeProposal

        with pytest.raises(ValidationError):
            ParamChangeProposal(
                journal_entry_id=uuid4(),
                strategy_id="tsmom",
                parameter="fast_ema",
                current_value="5",
                proposed_value="8",
                rationale="x",
                status="deployed",  # not a valid status
                proposed_at=datetime(2024, 1, 8, tzinfo=UTC),
            )
