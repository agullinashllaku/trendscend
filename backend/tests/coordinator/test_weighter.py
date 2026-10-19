from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from trendscend.schemas.news import AssetNewsState, NewsDirection, NewsState
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.risk import RiskStyle
from trendscend.schemas.signals import Signal, TradeAction


def _signal(
    strategy_id: str,
    target_exposure: float,
    conviction: float = 0.8,
    symbol: str = "BTC/USDT",
) -> Signal:
    return Signal(
        strategy_id=strategy_id,
        symbol=symbol,
        target_exposure=target_exposure,
        conviction=conviction,
        timeframe="4h",
        generated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _regime(regime_type: RegimeType, symbol: str = "BTC/USDT") -> RegimeLabel:
    return RegimeLabel(
        symbol=symbol,
        timeframe="4h",
        regime=regime_type,
        confidence=0.8,
        labeled_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _news_state(symbol: str, has_severe_negative: bool = False) -> NewsState:
    return NewsState(
        per_asset={
            symbol: AssetNewsState(
                symbol=symbol,
                recent_events=[],
                max_severity_24h=5 if has_severe_negative else 0,
                net_direction=(
                    NewsDirection.BEARISH if has_severe_negative else NewsDirection.NEUTRAL
                ),
                has_severe_negative=has_severe_negative,
            )
        },
        computed_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


class TestWeightSignals:
    def test_single_buy_signal_returns_proposal(self) -> None:
        from trendscend.coordinator.weighter import weight_signals

        signals = [_signal("tsmom", 0.8)]
        result = weight_signals(
            signals=signals,
            regime=_regime(RegimeType.TRENDING_UP),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            symbol="BTC/USDT",
            current_exposure=0.0,
            style=RiskStyle.BALANCED,
        )
        assert result is not None
        assert result.action == TradeAction.BUY
        assert result.size_pct > 0.0

    def test_flat_signals_returns_none(self) -> None:
        from trendscend.coordinator.weighter import weight_signals

        signals = [_signal("tsmom", 0.0)]
        result = weight_signals(
            signals=signals,
            regime=None,
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            symbol="BTC/USDT",
            current_exposure=0.0,
            style=RiskStyle.BALANCED,
        )
        assert result is None

    def test_severe_negative_news_forces_flat(self) -> None:
        from trendscend.coordinator.weighter import weight_signals

        signals = [_signal("tsmom", 0.9, conviction=1.0)]
        result = weight_signals(
            signals=signals,
            regime=_regime(RegimeType.TRENDING_UP),
            news_state=_news_state("BTC/USDT", has_severe_negative=True),
            smart_money_state=None,
            bot_id=uuid4(),
            symbol="BTC/USDT",
            current_exposure=0.8,  # currently long
            style=RiskStyle.BALANCED,
        )
        # Severe negative news + current long → should generate CLOSE or SELL
        assert result is not None
        assert result.action in {TradeAction.CLOSE, TradeAction.SELL}

    def test_out_of_regime_strategy_gets_lower_weight(self) -> None:
        from trendscend.coordinator.weighter import _regime_fit_score
        from trendscend.schemas.regime import RegimeType

        # grid_range in trending regime should score low
        score_in = _regime_fit_score("grid_range", RegimeType.RANGING)
        score_out = _regime_fit_score("grid_range", RegimeType.TRENDING_UP)
        assert score_in > score_out

    def test_multi_signal_weighted_average(self) -> None:
        from trendscend.coordinator.weighter import weight_signals

        # Two signals — both strong buy in trending regime
        signals = [
            _signal("tsmom", 0.8, conviction=0.9),
            _signal("breakout", 0.7, conviction=0.8),
        ]
        result = weight_signals(
            signals=signals,
            regime=_regime(RegimeType.TRENDING_UP),
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            symbol="BTC/USDT",
            current_exposure=0.0,
            style=RiskStyle.BALANCED,
        )
        assert result is not None
        assert result.action == TradeAction.BUY
        assert len(result.contributing_signals) == 2

    def test_llm_adjustment_factor_is_1(self) -> None:
        from trendscend.coordinator.weighter import weight_signals

        signals = [_signal("tsmom", 0.8)]
        result = weight_signals(
            signals=signals,
            regime=None,
            news_state=None,
            smart_money_state=None,
            bot_id=uuid4(),
            symbol="BTC/USDT",
            current_exposure=0.0,
            style=RiskStyle.BALANCED,
        )
        assert result is not None
        assert result.llm_adjustment_factor == 1.0

    def test_chaos_regime_reduces_all_scores(self) -> None:
        from trendscend.coordinator.weighter import _regime_fit_score
        from trendscend.schemas.regime import RegimeType

        for strat in ["tsmom", "breakout", "grid_range", "dca_trend", "rebalance_core"]:
            score = _regime_fit_score(strat, RegimeType.HIGH_VOL_CHAOS)
            assert score < 0.5, f"{strat} should score <0.5 in chaos, got {score}"
