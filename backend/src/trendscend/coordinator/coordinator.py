"""U2 Coordinator — orchestrates code-side weighting and optional LLM review.

The Coordinator is the only component that may apply an LLM-derived size
adjustment.  LLM outputs are bounded and validated; on failure the system
falls back to factor=0.5 (never factor=0 to avoid accidentally closing
positions the risk manager already approved).
"""

from __future__ import annotations

import logging
import os
from uuid import UUID

from anthropic import Anthropic

from trendscend.coordinator.llm_review import (
    LLMReviewResult,
    _summarise_news,
    _summarise_smart_money,
    review_proposal,
)
from trendscend.coordinator.weighter import weight_signals
from trendscend.schemas.market import OHLCV
from trendscend.schemas.news import NewsState
from trendscend.schemas.regime import RegimeLabel
from trendscend.schemas.risk import RiskStyle
from trendscend.schemas.signals import TradeProposal
from trendscend.schemas.smart_money import SmartMoneyState
from trendscend.strategies.base import Strategy

_LOG = logging.getLogger(__name__)

_MAX_FACTOR_BY_STYLE: dict[RiskStyle, float] = {
    RiskStyle.CONSERVATIVE: 1.0,
    RiskStyle.BALANCED: 1.0,
    RiskStyle.AGGRESSIVE: 1.25,
}


class Coordinator:
    """Combines multiple strategy signals and applies an optional LLM review.

    The code-side weighter runs always. The LLM review runs only when
    ``llm_enabled=True`` and a client is provided.  LLM adjustments are
    clamped to [0.0, max_factor] where max_factor is 1.0 for Conservative
    and Balanced, 1.25 for Aggressive.

    Usage::

        coordinator = Coordinator(style=RiskStyle.BALANCED, llm_client=client)
        proposal = coordinator.propose(
            strategies=[tsmom, breakout],
            symbol="BTC/USDT",
            candles=candles,
            regime=regime,
            news_state=news,
            smart_money_state=sm,
            bot_id=bot_id,
            current_exposure=0.0,
        )
    """

    def __init__(
        self,
        style: RiskStyle,
        llm_enabled: bool = True,
        model: str | None = None,
        llm_client: Anthropic | None = None,
    ) -> None:
        self._style = style
        self._llm_enabled = llm_enabled
        self._model = model or os.environ.get("COORDINATOR_MODEL", "claude-sonnet-4-6")
        self._client = llm_client

    def propose(
        self,
        strategies: list[Strategy],
        symbol: str,
        candles: list[OHLCV],
        regime: RegimeLabel | None,
        news_state: NewsState | None,
        smart_money_state: SmartMoneyState | None,
        bot_id: UUID,
        current_exposure: float = 0.0,
    ) -> TradeProposal | None:
        """Run all enabled strategy agents and return a combined TradeProposal.

        Returns None when no actionable proposal can be built (e.g. all
        signals flat, or combined delta is below threshold).
        """
        if not strategies:
            return None

        # Generate one signal per strategy
        signals = [s.generate_signal(symbol, candles, regime) for s in strategies]

        # Code-side weighting
        proposal = weight_signals(
            signals=signals,
            regime=regime,
            news_state=news_state,
            smart_money_state=smart_money_state,
            bot_id=bot_id,
            symbol=symbol,
            current_exposure=current_exposure,
            style=self._style,
        )
        if proposal is None:
            return None

        # LLM review (optional)
        if not self._llm_enabled or self._client is None:
            if self._llm_enabled and self._client is None:
                _LOG.warning(
                    "Coordinator: llm_enabled=True but no client provided; skipping LLM review"
                )
            return proposal

        news_summary = _summarise_news(news_state, symbol)
        sm_summary = _summarise_smart_money(smart_money_state, symbol)

        review: LLMReviewResult = review_proposal(
            proposal=proposal,
            news_summary=news_summary,
            sm_summary=sm_summary,
            style=self._style,
            model=self._model,
            client=self._client,
        )

        # Clamp adjustment factor to style-specific maximum
        max_factor = _MAX_FACTOR_BY_STYLE[self._style]
        clamped_factor = max(0.0, min(review.adjustment_factor, max_factor))

        adjusted_size = proposal.size_pct * clamped_factor
        adjusted_size = max(0.0, min(1.0, adjusted_size))

        # Update proposal in-place would mutate shared state; build a new one
        return proposal.model_copy(
            update={
                "size_pct": adjusted_size,
                "llm_adjustment_factor": clamped_factor,
                "llm_concerns": review.concerns,
                "rationale": (
                    review.rationale
                    if not (review.timed_out or review.parse_error)
                    else proposal.rationale
                ),
            }
        )
