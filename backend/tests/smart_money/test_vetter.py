"""Tests for WalletVetter — 7 criteria from PLAN.md §4.4."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trendscend.smart_money.fetcher import WalletFill, WalletPosition, WalletRecord
from trendscend.smart_money.vetter import (
    VET_MIN_HISTORY_DAYS,
    VET_MIN_TRADES,
    VettingResult,
    WalletVetter,
)


def _make_record(
    n_fills: int = 60,
    history_days: int = 130,
    avg_hold_hours: float = 5.0,
    avg_leverage: float = 3.0,
    pnl_positive_windows: float = 0.7,
) -> WalletRecord:
    """Build a WalletRecord that passes all criteria by default."""
    now = datetime.now(UTC)
    fills: list[WalletFill] = []
    for i in range(n_fills):
        open_ts = now - timedelta(days=history_days) + timedelta(hours=i * avg_hold_hours * 2)
        fills.append(
            WalletFill(
                wallet="0xabc",
                symbol="BTC",
                side="B" if i % 2 == 0 else "A",
                size=0.1,
                price=60000.0,
                timestamp=open_ts,
            )
        )
    positions: list[WalletPosition] = []
    return WalletRecord(
        address="0xabc",
        pnl_usd=5000.0,
        fills=fills,
        positions=positions,
        fetched_at=now,
    )


class TestVettingCriteria:
    def test_passes_with_good_record(self) -> None:
        record = _make_record()
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert isinstance(result, VettingResult)
        assert result.passed, f"Expected pass but got: {result.reasons}"

    def test_fails_too_few_fills(self) -> None:
        record = _make_record(n_fills=VET_MIN_TRADES - 1)
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert not result.passed
        assert any("trades" in r.lower() for r in result.reasons)

    def test_fails_too_short_history(self) -> None:
        record = _make_record(history_days=VET_MIN_HISTORY_DAYS - 1)
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert not result.passed
        assert any("history" in r.lower() or "days" in r.lower() for r in result.reasons)

    def test_fails_short_hold_time(self) -> None:
        record = _make_record(avg_hold_hours=0.1)  # sub-hour scalper
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert not result.passed
        assert any("hold" in r.lower() for r in result.reasons)

    def test_fails_high_leverage(self) -> None:
        # Build a record with positions showing leverage > 10
        record = _make_record()
        record.positions.append(
            WalletPosition(
                wallet="0xabc",
                symbol="BTC",
                side="long",
                size=1.0,
                entry_price=60000.0,
                unrealized_pnl=0.0,
                leverage=15.0,
            )
        )
        record.positions.append(
            WalletPosition(
                wallet="0xabc",
                symbol="ETH",
                side="long",
                size=5.0,
                entry_price=3000.0,
                unrealized_pnl=0.0,
                leverage=12.0,
            )
        )
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert not result.passed
        assert any("leverage" in r.lower() for r in result.reasons)

    def test_reasons_non_empty_on_fail(self) -> None:
        record = _make_record(n_fills=0, history_days=1)
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert not result.passed
        assert len(result.reasons) > 0

    def test_address_in_result(self) -> None:
        record = _make_record()
        vetter = WalletVetter()
        result = vetter.vet(record)
        assert result.address == "0xabc"
