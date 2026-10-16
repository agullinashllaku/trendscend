"""Tests for NewsFetcher — no real HTTP calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from trendscend.news.fetcher import NewsFetcher, RawNewsItem, make_url_hash


def _fake_item(url: str = "https://example.com/1") -> RawNewsItem:
    return RawNewsItem(
        url=url,
        title="BTC hits new high",
        body="Bitcoin price surged...",
        published_at=datetime(2024, 1, 1, tzinfo=UTC),
        source="cryptopanic",
        url_hash=make_url_hash(url),
    )


def _make_cryptopanic_response(n: int = 2) -> dict:
    return {
        "results": [
            {
                "url": f"https://example.com/{i}",
                "title": f"News item {i}",
                "body": f"Body {i}",
                "published_at": "2024-01-01T00:00:00Z",
                "source": {"title": "CoinDesk"},
            }
            for i in range(n)
        ]
    }


class TestMakeUrlHash:
    def test_same_url_same_hash(self) -> None:
        assert make_url_hash("https://a.com") == make_url_hash("https://a.com")

    def test_different_urls_different_hash(self) -> None:
        assert make_url_hash("https://a.com") != make_url_hash("https://b.com")

    def test_returns_hex_string(self) -> None:
        h = make_url_hash("https://a.com")
        assert isinstance(h, str)
        assert len(h) == 64  # sha256 hex


class TestNewsFetcherDedup:
    def test_empty_seen_returns_all(self) -> None:
        items = [_fake_item(f"https://example.com/{i}") for i in range(3)]
        fetcher = NewsFetcher()
        result = fetcher.dedup(items, set())
        assert len(result) == 3

    def test_seen_hash_excluded(self) -> None:
        item = _fake_item("https://example.com/1")
        fetcher = NewsFetcher()
        result = fetcher.dedup([item], {item.url_hash})
        assert result == []

    def test_partial_dedup(self) -> None:
        items = [_fake_item(f"https://example.com/{i}") for i in range(5)]
        seen = {items[0].url_hash, items[2].url_hash}
        fetcher = NewsFetcher()
        result = fetcher.dedup(items, seen)
        assert len(result) == 3
        urls = [r.url for r in result]
        assert "https://example.com/0" not in urls
        assert "https://example.com/2" not in urls


class TestNewsFetcherFetchCryptopanic:
    def test_parses_response_correctly(self) -> None:
        resp = _make_cryptopanic_response(3)

        async def fake_get(url: str, params: dict) -> dict:
            return resp

        fetcher = NewsFetcher(http_get=fake_get)
        items = asyncio.run(fetcher.fetch_cryptopanic("fake_key"))
        assert len(items) == 3
        assert items[0].source == "CoinDesk"
        assert items[0].url == "https://example.com/0"
        assert items[0].published_at.tzinfo is not None

    def test_empty_results_returns_empty_list(self) -> None:
        async def fake_get(url: str, params: dict) -> dict:
            return {"results": []}

        fetcher = NewsFetcher(http_get=fake_get)
        items = asyncio.run(fetcher.fetch_cryptopanic("fake_key"))
        assert items == []

    def test_missing_body_defaults_to_none(self) -> None:
        async def fake_get(url: str, params: dict) -> dict:
            return {
                "results": [
                    {
                        "url": "https://example.com/1",
                        "title": "Title",
                        "published_at": "2024-01-01T00:00:00Z",
                        "source": {"title": "CoinDesk"},
                    }
                ]
            }

        fetcher = NewsFetcher(http_get=fake_get)
        items = asyncio.run(fetcher.fetch_cryptopanic("fake_key"))
        assert items[0].body is None

    def test_url_hash_populated(self) -> None:
        async def fake_get(url: str, params: dict) -> dict:
            return _make_cryptopanic_response(1)

        fetcher = NewsFetcher(http_get=fake_get)
        items = asyncio.run(fetcher.fetch_cryptopanic("fake_key"))
        expected = make_url_hash("https://example.com/0")
        assert items[0].url_hash == expected
