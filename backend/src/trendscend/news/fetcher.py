"""A3 News ingestion: CryptoPanic HTTP client and URL-hash deduplication."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime


def make_url_hash(url: str) -> str:
    """SHA-256 hex digest of a URL — used as a dedup key."""
    return hashlib.sha256(url.encode()).hexdigest()


@dataclass
class RawNewsItem:
    url: str
    title: str
    body: str | None
    published_at: datetime
    source: str
    url_hash: str


_CRYPTOPANIC_BASE = "https://cryptopanic.com/api/v1/posts/"


class NewsFetcher:
    """Fetches raw news from CryptoPanic and deduplicates by URL hash.

    All HTTP calls go through the injected ``http_get`` adapter so tests
    never hit the real API.
    """

    def __init__(
        self,
        http_get: Callable[[str, dict], Awaitable[dict]] | None = None,
    ) -> None:
        if http_get is None:
            import httpx

            async def _default(url: str, params: dict) -> dict:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    r = await client.get(url, params=params)
                    r.raise_for_status()
                    return dict(r.json())

            self._http_get: Callable[[str, dict], Awaitable[dict]] = _default
        else:
            self._http_get = http_get

    async def fetch_cryptopanic(
        self,
        api_key: str,
        filter: str = "hot",
        limit: int = 50,
    ) -> list[RawNewsItem]:
        """Fetch recent posts from CryptoPanic.

        Args:
            api_key: CryptoPanic API key (from env, never logged).
            filter:  CryptoPanic filter param (hot, rising, bullish, bearish, …).
            limit:   Max number of items to return (CryptoPanic max = 50).

        Returns:
            List of raw news items. Body may be None for articles without a summary.
        """
        # NOTE: CryptoPanic requires auth_token as a query parameter — no header auth supported.
        # The key will appear in server-side access logs on CryptoPanic's end. Treat this
        # key as low-privilege and rotatable; ensure proxy logs are scrubbed or not retained.
        params = {
            "auth_token": api_key,
            "filter": filter,
            "public": "true",
            "limit": str(limit),
        }
        data = await self._http_get(_CRYPTOPANIC_BASE, params)
        results: list[dict] = data.get("results", [])
        items: list[RawNewsItem] = []
        for r in results:
            url: str = r.get("url", "")
            published_raw: str = r.get("published_at", "")
            try:
                published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00")).replace(
                    tzinfo=UTC
                )
            except (ValueError, AttributeError):
                published_at = datetime.now(UTC)
            items.append(
                RawNewsItem(
                    url=url,
                    title=r.get("title", ""),
                    body=r.get("body"),
                    published_at=published_at,
                    source=r.get("source", {}).get("title", "unknown"),
                    url_hash=make_url_hash(url),
                )
            )
        return items

    def dedup(
        self,
        items: list[RawNewsItem],
        seen_hashes: set[str],
    ) -> list[RawNewsItem]:
        """Return only items whose url_hash is not in seen_hashes."""
        return [i for i in items if i.url_hash not in seen_hashes]
