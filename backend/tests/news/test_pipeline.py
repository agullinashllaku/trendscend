"""Tests for NewsPipeline and build_news_state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from trendscend.news.classifier import ClassificationCost, ClassificationResult, NewsClassifier
from trendscend.news.fetcher import NewsFetcher, RawNewsItem, make_url_hash
from trendscend.news.pipeline import NewsPipeline, PipelineResult, build_news_state
from trendscend.schemas.news import NewsDirection, NewsEvent, NewsEventType


def _raw(url: str = "https://a.com/1") -> RawNewsItem:
    return RawNewsItem(
        url=url,
        title="T",
        body=None,
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="test",
        url_hash=make_url_hash(url),
    )


def _event(
    severity: int = 3,
    direction: NewsDirection = NewsDirection.BULLISH,
    assets: list[str] | None = None,
) -> NewsEvent:
    return NewsEvent(
        id=uuid4(),
        source="test",
        url="https://a.com/1",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        assets=assets or ["BTC"],
        event_type=NewsEventType.OTHER,
        severity=severity,
        direction=direction,
        half_life_hours=4.0,
        confidence=0.8,
        is_new_information=True,
        llm_model="claude-haiku-4-5-20251001",
        raw_hash="abc",
        classified_at=datetime.now(UTC),
    )


def _make_fetcher(items: list[RawNewsItem]) -> NewsFetcher:
    async def fake_get(url: str, params: dict) -> dict:
        return {"results": []}

    fetcher = NewsFetcher(http_get=fake_get)

    # Patch fetch_cryptopanic directly
    async def fake_fetch(api_key: str, filter: str = "hot", limit: int = 50) -> list[RawNewsItem]:
        return items

    fetcher.fetch_cryptopanic = fake_fetch  # type: ignore[method-assign]
    return fetcher


def _make_classifier(event: NewsEvent | None = None, fallback: bool = False) -> NewsClassifier:
    async def fake_llm(system: str, user: str, model: str) -> str:
        return ""

    clf = NewsClassifier(llm_complete=fake_llm)

    async def fake_classify(item: RawNewsItem, affected_assets: list[str]) -> ClassificationResult:
        cost = ClassificationCost(
            input_tokens=100, output_tokens=50, model="claude-haiku-4-5-20251001"
        )
        if fallback or event is None:
            return ClassificationResult(event=None, cost=cost, fallback_used=True)
        return ClassificationResult(event=event, cost=cost, fallback_used=False)

    clf.classify = fake_classify  # type: ignore[method-assign]
    return clf


class TestBuildNewsState:
    def test_empty_events_returns_state(self) -> None:
        state = build_news_state([], ["BTC/USDT"])
        assert isinstance(state.per_asset, dict)

    def test_severe_negative_flag_set(self) -> None:
        ev = _event(severity=4, direction=NewsDirection.BEARISH, assets=["BTC"])
        state = build_news_state([ev], ["BTC/USDT"])
        assert state.per_asset["BTC/USDT"].has_severe_negative is True

    def test_max_severity_24h_populated(self) -> None:
        events = [
            _event(severity=2, assets=["BTC"]),
            _event(severity=4, assets=["BTC"]),
        ]
        state = build_news_state(events, ["BTC/USDT"])
        assert state.per_asset["BTC/USDT"].max_severity_24h == 4

    def test_computed_at_is_recent(self) -> None:
        state = build_news_state([], ["BTC/USDT"])
        assert (datetime.now(UTC) - state.computed_at).total_seconds() < 5


class TestNewsPipeline:
    def test_run_returns_pipeline_result(self) -> None:
        ev = _event()
        pipeline = NewsPipeline(
            fetcher=_make_fetcher([_raw()]),
            classifier=_make_classifier(event=ev),
            assets=["BTC/USDT"],
        )
        result = asyncio.run(pipeline.run("fake_key"))
        assert isinstance(result, PipelineResult)
        assert len(result.events) == 1

    def test_seen_hashes_deduped(self) -> None:
        item = _raw("https://example.com/1")
        pipeline = NewsPipeline(
            fetcher=_make_fetcher([item]),
            classifier=_make_classifier(event=_event()),
            assets=["BTC/USDT"],
        )
        result = asyncio.run(pipeline.run("key", seen_hashes={item.url_hash}))
        assert len(result.events) == 0

    def test_fallback_counted(self) -> None:
        pipeline = NewsPipeline(
            fetcher=_make_fetcher([_raw()]),
            classifier=_make_classifier(fallback=True),
            assets=["BTC/USDT"],
        )
        result = asyncio.run(pipeline.run("key"))
        assert result.fallback_count == 1
        assert len(result.events) == 0

    def test_new_hashes_returned(self) -> None:
        item = _raw("https://example.com/new")
        pipeline = NewsPipeline(
            fetcher=_make_fetcher([item]),
            classifier=_make_classifier(event=_event()),
            assets=["BTC/USDT"],
        )
        result = asyncio.run(pipeline.run("key"))
        assert item.url_hash in result.new_hashes
