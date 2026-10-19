"""Tests for NewsScorer — pure functions, no external calls."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from trendscend.schemas.market import OHLCV
from trendscend.schemas.news import NewsDirection, NewsEvent, NewsEventType
from trendscend.schemas.scoring import NewsEventScore
from trendscend.scoring.news_scorer import NewsScorer


def _candle(ts: datetime, close: float) -> OHLCV:
    d = Decimal(str(close))
    return OHLCV(timestamp=ts, open=d, high=d, low=d, close=d, volume=Decimal("100"))


def _event(published_at: datetime | None = None) -> NewsEvent:
    ts = published_at or datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
    return NewsEvent(
        id=uuid4(),
        source="test",
        url="https://example.com/1",
        published_at=ts,
        assets=["BTC"],
        event_type=NewsEventType.OTHER,
        severity=3,
        direction=NewsDirection.BULLISH,
        half_life_hours=4.0,
        confidence=0.8,
        is_new_information=True,
        llm_model="claude-haiku-4-5-20251001",
        raw_hash="abc",
        classified_at=ts,
    )


class TestNewsScorer:
    def test_returns_news_event_score(self) -> None:
        base = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)
        candles = [_candle(base + timedelta(hours=i), 100.0 + i) for i in range(30)]
        scorer = NewsScorer()
        score = scorer.score(_event(base), candles)
        assert isinstance(score, NewsEventScore)

    def test_positive_return_on_rising_candles(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        # price rises from 100 to 130 over 30 hours
        candles = [_candle(base + timedelta(hours=i), 100.0 + i) for i in range(30)]
        event = _event(base)
        scorer = NewsScorer()
        score = scorer.score(event, candles)
        assert score.return_1h is not None and score.return_1h > 0
        assert score.return_4h is not None and score.return_4h > 0

    def test_negative_return_on_falling_candles(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        candles = [_candle(base + timedelta(hours=i), 100.0 - i * 0.5) for i in range(30)]
        event = _event(base)
        scorer = NewsScorer()
        score = scorer.score(event, candles)
        assert score.return_1h is not None and score.return_1h < 0

    def test_none_return_when_not_enough_candles(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        candles = [_candle(base + timedelta(hours=i), 100.0) for i in range(2)]
        scorer = NewsScorer()
        score = scorer.score(_event(base), candles)
        # With only 2 candles, 24h return cannot be computed
        assert score.return_24h is None

    def test_event_id_in_score(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        candles = [_candle(base + timedelta(hours=i), 100.0) for i in range(30)]
        event = _event(base)
        scorer = NewsScorer()
        score = scorer.score(event, candles)
        assert score.event_id == event.id

    def test_severity_and_direction_preserved(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        candles = [_candle(base + timedelta(hours=i), 100.0) for i in range(30)]
        event = _event(base)
        scorer = NewsScorer()
        score = scorer.score(event, candles)
        assert score.severity == event.severity
        assert score.direction == event.direction
