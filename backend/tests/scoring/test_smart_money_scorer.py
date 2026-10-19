"""Tests for SmartMoneyScorer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trendscend.schemas.market import OHLCV
from trendscend.schemas.scoring import SmartMoneyScore
from trendscend.schemas.smart_money import SmartMoneyAssetState, SmartMoneyState
from trendscend.scoring.smart_money_scorer import SmartMoneyScorer


def _candle(ts: datetime, close: float) -> OHLCV:
    d = Decimal(str(close))
    return OHLCV(timestamp=ts, open=d, high=d, low=d, close=d, volume=Decimal("100"))


def _state(symbol: str, net_long: float, ts: datetime) -> SmartMoneyState:
    return SmartMoneyState(
        per_asset={
            symbol: SmartMoneyAssetState(
                symbol=symbol,
                net_long_pct=net_long,
                net_short_pct=1.0 - net_long,
                change_24h=0.0,
                concentration=0.1,
            )
        },
        vetted_wallet_count=5,
        computed_at=ts,
    )


class TestSmartMoneyScorer:
    def test_returns_list_of_scores(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        before = _state("BTC/USDT", 0.5, base)
        after = _state("BTC/USDT", 0.7, base + timedelta(hours=1))
        candles = {"BTC/USDT": [_candle(base + timedelta(hours=i), 100.0 + i) for i in range(30)]}
        scorer = SmartMoneyScorer()
        scores = scorer.score(before, after, candles)
        assert isinstance(scores, list)
        assert len(scores) == 1
        assert isinstance(scores[0], SmartMoneyScore)

    def test_no_change_no_score(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        before = _state("BTC/USDT", 0.6, base)
        after = _state("BTC/USDT", 0.6, base + timedelta(hours=1))  # no change
        candles = {"BTC/USDT": []}
        scorer = SmartMoneyScorer()
        scores = scorer.score(before, after, candles)
        assert scores == []

    def test_score_has_correct_before_after(self) -> None:
        base = datetime(2024, 1, 1, 0, 0, tzinfo=UTC)
        before = _state("BTC/USDT", 0.4, base)
        after = _state("BTC/USDT", 0.8, base + timedelta(hours=1))
        candles = {"BTC/USDT": [_candle(base + timedelta(hours=i), 100.0) for i in range(30)]}
        scorer = SmartMoneyScorer()
        scores = scorer.score(before, after, candles)
        assert scores[0].net_long_pct_before == 0.4
        assert scores[0].net_long_pct_after == 0.8
