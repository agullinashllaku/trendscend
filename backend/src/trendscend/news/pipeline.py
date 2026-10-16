"""A3 News pipeline: fetch → dedup → classify → build NewsState."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from trendscend.news.classifier import NewsClassifier
from trendscend.news.fetcher import NewsFetcher
from trendscend.schemas.news import AssetNewsState, NewsDirection, NewsEvent, NewsState


@dataclass
class PipelineResult:
    events: list[NewsEvent]
    state: NewsState
    new_hashes: set[str]
    total_cost_tokens: int
    fallback_count: int


def build_news_state(events: list[NewsEvent], assets: list[str]) -> NewsState:
    """Build NewsState from a list of NewsEvents for the given asset list.

    An asset key in per_asset is matched if the event's assets list contains
    either the full symbol (e.g. 'BTC/USDT') or the base asset (e.g. 'BTC').
    """
    per_asset: dict[str, AssetNewsState] = {}
    for asset in assets:
        base = asset.split("/")[0]
        relevant = [e for e in events if asset in e.assets or base in e.assets]
        max_sev = max((e.severity for e in relevant), default=0)
        directions = [e.direction for e in relevant]
        bullish = sum(1 for d in directions if d == NewsDirection.BULLISH)
        bearish = sum(1 for d in directions if d == NewsDirection.BEARISH)
        if bullish > bearish:
            net = NewsDirection.BULLISH
        elif bearish > bullish:
            net = NewsDirection.BEARISH
        else:
            net = NewsDirection.NEUTRAL
        has_severe_neg = any(
            e.severity >= 4 and e.direction == NewsDirection.BEARISH for e in relevant
        )
        asset_key = asset if asset in [e_a for e in events for e_a in e.assets] else base
        per_asset[asset_key] = AssetNewsState(
            symbol=asset_key,
            recent_events=relevant,
            max_severity_24h=max_sev,
            net_direction=net,
            has_severe_negative=has_severe_neg,
        )
    return NewsState(per_asset=per_asset, computed_at=datetime.now(UTC))


class NewsPipeline:
    """Orchestrates fetch → dedup → classify → NewsState for A3."""

    def __init__(
        self,
        fetcher: NewsFetcher,
        classifier: NewsClassifier,
        assets: list[str],
    ) -> None:
        self._fetcher = fetcher
        self._classifier = classifier
        self._assets = assets

    async def run(
        self,
        api_key: str,
        seen_hashes: set[str] | None = None,
    ) -> PipelineResult:
        """Fetch, dedup, classify, and build NewsState.

        Args:
            api_key:      CryptoPanic API key.
            seen_hashes:  Already-processed URL hashes to skip.

        Returns:
            PipelineResult with new events and updated NewsState.
        """
        seen = seen_hashes or set()
        raw_items = await self._fetcher.fetch_cryptopanic(api_key)
        new_items = self._fetcher.dedup(raw_items, seen)

        events: list[NewsEvent] = []
        total_tokens = 0
        fallback_count = 0
        new_hashes: set[str] = set()

        for item in new_items:
            result = await self._classifier.classify(item, self._assets)
            total_tokens += result.cost.input_tokens + result.cost.output_tokens
            if result.fallback_used or result.event is None:
                fallback_count += 1
            else:
                events.append(result.event)
                new_hashes.add(item.url_hash)

        state = build_news_state(events, self._assets)
        return PipelineResult(
            events=events,
            state=state,
            new_hashes=new_hashes,
            total_cost_tokens=total_tokens,
            fallback_count=fallback_count,
        )
