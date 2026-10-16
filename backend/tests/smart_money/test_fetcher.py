"""Tests for HyperliquidFetcher — no real HTTP calls."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from trendscend.smart_money.fetcher import (
    HyperliquidFetcher,
    WalletRecord,
)


def _leaderboard_response(n: int = 3) -> dict:
    return {
        "leaderboardRows": [
            {"ethAddress": f"0x{'a' * 39}{i}", "accountValue": "10000", "vlp": {"pnl": "500"}}
            for i in range(n)
        ]
    }


def _clearinghouse_response(address: str = "0xabc") -> dict:
    return {
        "assetPositions": [
            {
                "position": {
                    "coin": "BTC",
                    "szi": "0.5",
                    "entryPx": "60000",
                    "unrealizedPnl": "250",
                    "leverage": {"value": "2"},
                }
            }
        ]
    }


def _fills_response() -> dict:
    return {
        "fills": [
            {
                "coin": "BTC",
                "side": "B",
                "sz": "0.1",
                "px": "60000",
                "time": 1704067200000,
            },
            {
                "coin": "ETH",
                "side": "A",
                "sz": "1.0",
                "px": "3000",
                "time": 1704067200000,
            },
        ]
    }


class TestHyperliquidFetcherLeaderboard:
    def test_returns_list_of_addresses(self) -> None:
        async def fake_post(url: str, payload: dict) -> dict:
            return _leaderboard_response(5)

        fetcher = HyperliquidFetcher(http_post=fake_post)
        addresses = asyncio.run(fetcher.fetch_leaderboard(top_n=5))
        assert len(addresses) == 5
        assert all(isinstance(a, str) for a in addresses)

    def test_top_n_limits_results(self) -> None:
        async def fake_post(url: str, payload: dict) -> dict:
            return _leaderboard_response(10)

        fetcher = HyperliquidFetcher(http_post=fake_post)
        addresses = asyncio.run(fetcher.fetch_leaderboard(top_n=3))
        assert len(addresses) == 3

    def test_empty_leaderboard_returns_empty(self) -> None:
        async def fake_post(url: str, payload: dict) -> dict:
            return {"leaderboardRows": []}

        fetcher = HyperliquidFetcher(http_post=fake_post)
        addresses = asyncio.run(fetcher.fetch_leaderboard())
        assert addresses == []


class TestHyperliquidFetcherWallet:
    def _make_fetcher(self, address: str = "0xabc") -> HyperliquidFetcher:
        call_count = [0]

        async def fake_post(url: str, payload: dict) -> dict:
            call_count[0] += 1
            if payload.get("type") == "clearinghouseState":
                return _clearinghouse_response(address)
            if payload.get("type") == "userFills":
                return _fills_response()
            return {}

        return HyperliquidFetcher(http_post=fake_post)

    def test_returns_wallet_record(self) -> None:
        fetcher = self._make_fetcher()
        record = asyncio.run(fetcher.fetch_wallet("0xabc"))
        assert isinstance(record, WalletRecord)
        assert record.address == "0xabc"

    def test_fills_parsed(self) -> None:
        fetcher = self._make_fetcher()
        record = asyncio.run(fetcher.fetch_wallet("0xabc"))
        assert len(record.fills) == 2
        assert record.fills[0].symbol == "BTC"
        assert record.fills[0].side == "B"

    def test_positions_parsed(self) -> None:
        fetcher = self._make_fetcher()
        record = asyncio.run(fetcher.fetch_wallet("0xabc"))
        assert len(record.positions) == 1
        assert record.positions[0].symbol == "BTC"
        assert record.positions[0].size == 0.5

    def test_fetched_at_is_recent(self) -> None:
        fetcher = self._make_fetcher()
        record = asyncio.run(fetcher.fetch_wallet("0xabc"))
        assert (datetime.now(UTC) - record.fetched_at).total_seconds() < 5
