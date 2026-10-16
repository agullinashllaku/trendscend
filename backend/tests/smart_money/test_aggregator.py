"""Tests for SmartMoneyAggregator."""

from __future__ import annotations

from datetime import UTC, datetime

from trendscend.schemas.smart_money import SmartMoneyState
from trendscend.smart_money.aggregator import SmartMoneyAggregator
from trendscend.smart_money.fetcher import WalletPosition, WalletRecord


def _record(
    address: str, symbol: str = "BTC", side: str = "long", size: float = 1.0
) -> WalletRecord:
    return WalletRecord(
        address=address,
        pnl_usd=1000.0,
        fills=[],
        positions=[
            WalletPosition(
                wallet=address,
                symbol=symbol,
                side=side,
                size=size,
                entry_price=60000.0,
                unrealized_pnl=0.0,
                leverage=2.0,
            )
        ],
        fetched_at=datetime.now(UTC),
    )


class TestSmartMoneyAggregator:
    def test_returns_smart_money_state(self) -> None:
        agg = SmartMoneyAggregator()
        state = agg.aggregate([_record("0x1")], ["BTC/USDT"])
        assert isinstance(state, SmartMoneyState)

    def test_all_long_gives_high_net_long(self) -> None:
        records = [_record(f"0x{i}", side="long") for i in range(5)]
        agg = SmartMoneyAggregator()
        state = agg.aggregate(records, ["BTC/USDT"])
        asset = state.per_asset.get("BTC/USDT") or state.per_asset.get("BTC")
        assert asset is not None
        assert asset.net_long_pct > 0.5

    def test_all_short_gives_high_net_short(self) -> None:
        records = [_record(f"0x{i}", side="short") for i in range(5)]
        agg = SmartMoneyAggregator()
        state = agg.aggregate(records, ["BTC/USDT"])
        asset = state.per_asset.get("BTC/USDT") or state.per_asset.get("BTC")
        assert asset is not None
        assert asset.net_short_pct > 0.5

    def test_vetted_wallet_count(self) -> None:
        records = [_record(f"0x{i}") for i in range(4)]
        agg = SmartMoneyAggregator()
        state = agg.aggregate(records, ["BTC/USDT"])
        assert state.vetted_wallet_count == 4

    def test_empty_records_returns_state(self) -> None:
        agg = SmartMoneyAggregator()
        state = agg.aggregate([], ["BTC/USDT"])
        assert state.vetted_wallet_count == 0
