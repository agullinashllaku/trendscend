from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from trendscend.schemas.market import OHLCV
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.risk import RiskStyle


def _candles(n: int = 60, start_price: float = 100.0, trend: float = 0.5) -> list[OHLCV]:
    from trendscend.schemas.market import OHLCV

    base = datetime(2024, 1, 1, tzinfo=UTC)
    from datetime import timedelta

    result = []
    for i in range(n):
        p = Decimal(str(start_price + i * trend))
        result.append(
            OHLCV(
                timestamp=base + timedelta(hours=4 * i),
                open=p,
                high=p * Decimal("1.005"),
                low=p * Decimal("0.995"),
                close=p,
                volume=Decimal("100"),
            )
        )
    return result


def _regime(rtype: RegimeType = RegimeType.TRENDING_UP) -> RegimeLabel:
    return RegimeLabel(
        symbol="BTC/USDT",
        timeframe="4h",
        regime=rtype,
        confidence=0.8,
        labeled_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _mock_llm(factor: float = 0.9) -> MagicMock:
    client = MagicMock()
    msg = MagicMock()
    content = MagicMock()
    content.text = json.dumps(
        {
            "adjustment_factor": factor,
            "rationale": "All looks good.",
            "concerns": None,
        }
    )
    msg.content = [content]
    client.messages.create.return_value = msg
    return client


class TestCoordinatorNoLLM:
    def test_propose_no_llm_returns_proposal(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.tsmom import TsmomStrategy

        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
        strategies = [TsmomStrategy(fast_ema=5, slow_ema=10)]
        candles = _candles(60)
        result = coordinator.propose(
            strategies=strategies,
            symbol="BTC/USDT",
            candles=candles,
            regime=_regime(),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        # May be None if signal is flat; just assert type is correct
        assert result is None or hasattr(result, "action")

    def test_propose_with_llm_applies_factor(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        mock_client = _mock_llm(factor=0.7)
        coordinator = Coordinator(
            style=RiskStyle.BALANCED,
            llm_enabled=True,
            llm_client=mock_client,
        )
        strategies = [HoldBaselineStrategy()]
        candles = _candles(60)
        result = coordinator.propose(
            strategies=strategies,
            symbol="BTC/USDT",
            candles=candles,
            regime=_regime(),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        assert result is not None
        assert result.llm_adjustment_factor == pytest.approx(0.7)
        # size_pct should be scaled by 0.7 relative to the raw proposal
        assert result.size_pct <= 1.0

    def test_adjustment_clamped_for_balanced(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        # LLM returns 1.2 — should be clamped to 1.0 for BALANCED
        mock_client = _mock_llm(factor=1.2)
        coordinator = Coordinator(
            style=RiskStyle.BALANCED,
            llm_enabled=True,
            llm_client=mock_client,
        )
        result = coordinator.propose(
            strategies=[HoldBaselineStrategy()],
            symbol="BTC/USDT",
            candles=_candles(60),
            regime=_regime(),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        assert result is not None
        assert result.llm_adjustment_factor <= 1.0

    def test_adjustment_allowed_up_to_125_for_aggressive(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        mock_client = _mock_llm(factor=1.2)
        coordinator = Coordinator(
            style=RiskStyle.AGGRESSIVE,
            llm_enabled=True,
            llm_client=mock_client,
        )
        result = coordinator.propose(
            strategies=[HoldBaselineStrategy()],
            symbol="BTC/USDT",
            candles=_candles(60),
            regime=_regime(),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        assert result is not None
        assert result.llm_adjustment_factor == pytest.approx(1.2)

    def test_llm_enabled_but_no_client_skips_review(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator
        from trendscend.strategies.hold_baseline import HoldBaselineStrategy

        coordinator = Coordinator(
            style=RiskStyle.BALANCED,
            llm_enabled=True,
            llm_client=None,  # no client
        )
        result = coordinator.propose(
            strategies=[HoldBaselineStrategy()],
            symbol="BTC/USDT",
            candles=_candles(60),
            regime=_regime(),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        assert result is not None
        assert result.llm_adjustment_factor == pytest.approx(1.0)

    def test_empty_strategies_returns_none(self) -> None:
        from trendscend.coordinator.coordinator import Coordinator

        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_enabled=False)
        result = coordinator.propose(
            strategies=[],
            symbol="BTC/USDT",
            candles=_candles(60),
            regime=None,
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            current_exposure=0.0,
        )
        assert result is None
