from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from trendscend.schemas.news import (
    AssetNewsState,
    NewsDirection,
    NewsEvent,
    NewsEventType,
    NewsState,
)
from trendscend.schemas.risk import RiskStyle
from trendscend.schemas.signals import TradeAction, TradeProposal
from trendscend.schemas.smart_money import SmartMoneyAssetState, SmartMoneyState


def _proposal(size_pct: float = 0.45) -> TradeProposal:
    from trendscend.schemas.signals import Signal

    sig = Signal(
        strategy_id="tsmom",
        symbol="BTC/USDT",
        target_exposure=0.8,
        conviction=0.9,
        timeframe="4h",
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    return TradeProposal(
        bot_id=uuid4(),
        symbol="BTC/USDT",
        action=TradeAction.BUY,
        size_pct=size_pct,
        stop_pct=0.02,
        rationale="coordinator[code]: tsmom(0.90) regime=trending_up",
        contributing_signals=[sig],
        proposed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mock_client(response_text: str) -> MagicMock:
    """Minimal Anthropic client mock returning response_text in content[0].text."""
    client = MagicMock()
    message = MagicMock()
    content_block = MagicMock()
    content_block.text = response_text
    message.content = [content_block]
    client.messages.create.return_value = message
    return client


class TestReviewProposal:
    def test_valid_response_parsed(self) -> None:
        from trendscend.coordinator.llm_review import review_proposal

        payload = json.dumps(
            {
                "adjustment_factor": 0.9,
                "rationale": "Strong trend confirmed by news.",
                "concerns": None,
            }
        )
        client = _mock_client(payload)

        result = review_proposal(
            proposal=_proposal(),
            news_summary="1 bullish event, severity 3",
            sm_summary="65% net long",
            style=RiskStyle.BALANCED,
            model="claude-sonnet-4-6",
            client=client,
        )

        assert result.adjustment_factor == pytest.approx(0.9)
        assert "trend" in result.rationale.lower()
        assert not result.timed_out
        assert not result.parse_error

    def test_timeout_returns_half_factor(self) -> None:
        from anthropic import APITimeoutError

        from trendscend.coordinator.llm_review import review_proposal

        client = MagicMock()
        client.messages.create.side_effect = APITimeoutError(request=MagicMock())

        result = review_proposal(
            proposal=_proposal(),
            news_summary="",
            sm_summary="",
            style=RiskStyle.BALANCED,
            model="claude-sonnet-4-6",
            client=client,
        )

        assert result.adjustment_factor == pytest.approx(0.5)
        assert result.timed_out is True

    def test_parse_error_returns_half_factor(self) -> None:
        from trendscend.coordinator.llm_review import review_proposal

        client = _mock_client("not valid json at all")

        result = review_proposal(
            proposal=_proposal(),
            news_summary="",
            sm_summary="",
            style=RiskStyle.BALANCED,
            model="claude-sonnet-4-6",
            client=client,
        )

        assert result.adjustment_factor == pytest.approx(0.5)
        assert result.parse_error is True

    def test_concerns_field_populated(self) -> None:
        from trendscend.coordinator.llm_review import review_proposal

        payload = json.dumps(
            {
                "adjustment_factor": 0.5,
                "rationale": "Reducing due to ETF outflow news.",
                "concerns": "ETF outflow contradicts long bias.",
            }
        )
        client = _mock_client(payload)

        result = review_proposal(
            proposal=_proposal(),
            news_summary="bearish ETF outflow",
            sm_summary="",
            style=RiskStyle.BALANCED,
            model="claude-sonnet-4-6",
            client=client,
        )

        assert result.concerns is not None
        assert "ETF" in result.concerns

    def test_non_dict_json_returns_parse_error(self) -> None:
        from trendscend.coordinator.llm_review import review_proposal

        # LLM returns a JSON array instead of an object
        client = _mock_client("[]")

        result = review_proposal(
            proposal=_proposal(),
            news_summary="",
            sm_summary="",
            style=RiskStyle.BALANCED,
            model="claude-sonnet-4-6",
            client=client,
        )

        assert result.adjustment_factor == pytest.approx(0.5)
        assert result.parse_error is True

    def test_summarise_news_no_raw_headlines(self) -> None:
        from trendscend.coordinator.llm_review import summarise_news as _summarise_news

        event = NewsEvent(
            source="CryptoPanic",
            url="https://example.com/news/1",
            published_at=datetime(2024, 1, 1, tzinfo=UTC),
            assets=["BTC/USDT"],
            event_type=NewsEventType.EXPLOIT_HACK,
            severity=4,
            direction=NewsDirection.BEARISH,
            half_life_hours=24.0,
            confidence=0.9,
            is_new_information=True,
            llm_model="claude-haiku-4-5-20251001",
            raw_hash="abc123",
            classified_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        state = NewsState(
            per_asset={
                "BTC/USDT": AssetNewsState(
                    symbol="BTC/USDT",
                    recent_events=[event],
                    max_severity_24h=4,
                    net_direction=NewsDirection.BEARISH,
                    has_severe_negative=True,
                )
            },
            computed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        summary = _summarise_news(state, "BTC/USDT")
        # Must NOT contain raw URL or raw source text
        assert "example.com" not in summary
        assert "CryptoPanic" not in summary
        # Should contain event_type and direction
        assert "exploit_hack" in summary
        assert "bearish" in summary.lower()


class TestSummariseFunctions:
    def test_summarise_smart_money(self) -> None:
        from trendscend.coordinator.llm_review import (
            summarise_smart_money as _summarise_smart_money,
        )

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
            vetted_wallet_count=42,
            computed_at=datetime(2024, 1, 1, tzinfo=UTC),
        )
        summary = _summarise_smart_money(state, "BTC/USDT")
        assert "65" in summary or "0.65" in summary

    def test_summarise_news_no_asset(self) -> None:
        from trendscend.coordinator.llm_review import summarise_news as _summarise_news

        state = NewsState(per_asset={}, computed_at=datetime(2024, 1, 1, tzinfo=UTC))
        summary = _summarise_news(state, "ETH/USDT")
        assert "no recent" in summary.lower() or summary == "" or "none" in summary.lower()
