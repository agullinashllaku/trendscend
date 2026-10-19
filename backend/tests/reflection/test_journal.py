from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

from trendscend.schemas.decisions import DecisionRecord
from trendscend.schemas.market import MarketSnapshot
from trendscend.schemas.risk import RiskDecision, RiskOutcome
from trendscend.schemas.signals import Signal, TradeAction, TradeProposal


def _record(
    approved: bool = True,
    strategy_ids: list[str] | None = None,
) -> DecisionRecord:

    sids = strategy_ids or ["tsmom"]
    signals = [
        Signal(
            strategy_id=sid,
            symbol="BTC/USDT",
            target_exposure=0.8,
            conviction=0.8,
            timeframe="4h",
            generated_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        for sid in sids
    ]
    proposal = TradeProposal(
        bot_id=uuid4(),
        symbol="BTC/USDT",
        action=TradeAction.BUY,
        size_pct=0.4,
        stop_pct=0.02,
        rationale="test",
        contributing_signals=signals,
        proposed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    risk_dec = RiskDecision(
        id=uuid4(),
        proposal_id=proposal.id,
        outcome=RiskOutcome.APPROVED if approved else RiskOutcome.REJECTED,
        approved_size_pct=0.4 if approved else None,
        rejection_reason=None if approved else "max_trades_per_day",
        checks_run=["kill_switch_state", "drawdown_kill_switch"],
        decided_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    snapshot = MarketSnapshot(
        exchange="binance",
        symbol="BTC/USDT",
        timeframe="4h",
        candles=[],
        last_trade_price=Decimal("100"),
        fetched_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return DecisionRecord(
        bot_id=uuid4(),
        cycle_triggered_by="candle_close",
        triggered_at=datetime(2024, 1, 1, tzinfo=UTC),
        market_snapshots=[snapshot],
        regime_labels=[],
        signals=signals,
        proposal=proposal,
        risk_decision=risk_dec,
        order_ids=["order-1"] if approved else [],
        completed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mock_llm(
    journal_text: str = "Normal trading day.", proposals: list[dict] | None = None
) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    content = MagicMock()
    content.text = json.dumps(
        {
            "journal": journal_text,
            "proposals": proposals or [],
        }
    )
    msg.content = [content]
    client.messages.create.return_value = msg
    return client


class TestComputeStats:
    def test_trade_count(self) -> None:
        from trendscend.reflection.journal import _compute_stats

        records = [_record(approved=True), _record(approved=False), _record(approved=True)]
        stats = _compute_stats(records)
        assert stats["total_cycles"] == 3
        assert stats["trades_executed"] == 2

    def test_rejection_reasons_aggregated(self) -> None:
        from trendscend.reflection.journal import _compute_stats

        records = [_record(approved=False), _record(approved=False)]
        stats = _compute_stats(records)
        assert "max_trades_per_day" in stats["rejection_reasons"]

    def test_top_strategies(self) -> None:
        from trendscend.reflection.journal import _compute_stats

        records = [
            _record(strategy_ids=["tsmom", "breakout"]),
            _record(strategy_ids=["tsmom"]),
        ]
        stats = _compute_stats(records)
        # tsmom appears 3 times, breakout 1 time
        assert stats["top_strategies"][0] == "tsmom"


class TestReflectionAgent:
    def test_generate_journal_returns_entry(self) -> None:
        from trendscend.reflection.journal import ReflectionAgent

        agent = ReflectionAgent(llm_client=_mock_llm())
        records = [_record() for _ in range(5)]
        entry, proposals = agent.generate_journal(
            records=records,
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            bot_id=uuid4(),
        )
        assert entry.period_type == "daily"
        assert entry.trade_count >= 0
        assert isinstance(proposals, list)

    def test_journal_content_from_llm(self) -> None:
        from trendscend.reflection.journal import ReflectionAgent

        agent = ReflectionAgent(llm_client=_mock_llm("Great trading day!"))
        entry, _ = agent.generate_journal(
            records=[_record()],
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            bot_id=uuid4(),
        )
        assert "Great trading day!" in entry.content

    def test_proposals_parsed_from_llm(self) -> None:
        from trendscend.reflection.journal import ReflectionAgent

        proposals_data = [
            {
                "strategy_id": "tsmom",
                "parameter": "fast_ema",
                "current_value": "5",
                "proposed_value": "8",
                "rationale": "Wider window reduces noise.",
            }
        ]
        agent = ReflectionAgent(llm_client=_mock_llm(proposals=proposals_data))
        _, proposals = agent.generate_journal(
            records=[_record()],
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            bot_id=uuid4(),
        )
        assert len(proposals) == 1
        assert proposals[0].strategy_id == "tsmom"
        assert proposals[0].status == "proposed"

    def test_llm_failure_returns_stub_entry(self) -> None:
        from anthropic import APITimeoutError

        from trendscend.reflection.journal import ReflectionAgent

        client = MagicMock()
        client.messages.create.side_effect = APITimeoutError(request=MagicMock())
        agent = ReflectionAgent(llm_client=client)
        entry, proposals = agent.generate_journal(
            records=[_record()],
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            bot_id=uuid4(),
        )
        assert "unavailable" in entry.content.lower()
        assert proposals == []

    def test_no_client_returns_stub_entry(self) -> None:
        from trendscend.reflection.journal import ReflectionAgent

        agent = ReflectionAgent(llm_client=None)
        entry, proposals = agent.generate_journal(
            records=[_record()],
            period_start=datetime(2024, 1, 1, tzinfo=UTC),
            period_end=datetime(2024, 1, 2, tzinfo=UTC),
            period_type="daily",
            bot_id=uuid4(),
        )
        assert "unavailable" in entry.content.lower()
        assert proposals == []
