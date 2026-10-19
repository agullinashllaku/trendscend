"""Code-side signal weighter for U2 Coordinator.

Combines multiple strategy signals into a single TradeProposal using
regime-fit scores and conviction weights. No LLM calls here.

Severe negative news (severity >= 4, has_severe_negative=True) on the
traded symbol forces target_exposure to 0.0, overriding all signals.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from trendscend.schemas.news import NewsState
from trendscend.schemas.regime import RegimeLabel, RegimeType
from trendscend.schemas.risk import RiskStyle
from trendscend.schemas.signals import Signal, TradeAction, TradeProposal
from trendscend.schemas.smart_money import SmartMoneyState

# Maps strategy_id prefix to the regimes where it is expected to work well.
STRATEGY_REGIME_FIT: dict[str, frozenset[RegimeType]] = {
    "tsmom": frozenset({RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN}),
    "breakout": frozenset({RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN}),
    "grid_range": frozenset({RegimeType.RANGING}),
    "mean_rev": frozenset({RegimeType.RANGING}),
    "dca_trend": frozenset({RegimeType.TRENDING_UP, RegimeType.RANGING}),
    "rebalance_core": frozenset(
        {RegimeType.TRENDING_UP, RegimeType.TRENDING_DOWN, RegimeType.RANGING}
    ),
    "hold_baseline": frozenset(
        {
            RegimeType.TRENDING_UP,
            RegimeType.TRENDING_DOWN,
            RegimeType.RANGING,
            RegimeType.HIGH_VOL_CHAOS,
        }
    ),
}

_CHAOS_SCORE = 0.2
_IN_REGIME_SCORE = 1.0
_OUT_OF_REGIME_SCORE = 0.3
_UNKNOWN_SCORE = 0.5


def _regime_fit_score(strategy_id: str, regime_type: RegimeType | None) -> float:
    """Return a regime-fit multiplier in (0, 1] for a strategy in the current regime.

    Strategies running outside their preferred regime receive a penalty of 0.3.
    All strategies receive 0.2 in HIGH_VOL_CHAOS. Unknown strategy IDs get 0.5.
    """
    if regime_type is None:
        return _UNKNOWN_SCORE
    # Match on strategy_id prefix so "tsmom_fast" matches "tsmom"
    matched_key = next(
        (k for k in STRATEGY_REGIME_FIT if strategy_id == k or strategy_id.startswith(k + "_")),
        None,
    )
    if matched_key is None:
        return _UNKNOWN_SCORE
    if regime_type == RegimeType.HIGH_VOL_CHAOS:
        return _CHAOS_SCORE
    return (
        _IN_REGIME_SCORE
        if regime_type in STRATEGY_REGIME_FIT[matched_key]
        else _OUT_OF_REGIME_SCORE
    )


def weight_signals(
    signals: list[Signal],
    regime: RegimeLabel | None,
    news_state: NewsState | None,
    smart_money_state: SmartMoneyState | None,
    bot_id: UUID,
    symbol: str,
    current_exposure: float,
    style: RiskStyle,
) -> TradeProposal | None:
    """Combine strategy signals into a single TradeProposal using regime-fit weights.

    Returns None when the weighted delta vs current exposure is below 1%
    (no meaningful change) or all signals are flat.

    Args:
        signals:            Strategy signals for this symbol at this cycle.
        regime:             Current regime label (None -> neutral weights).
        news_state:         Aggregated news state; severe negative news forces flat.
        smart_money_state:  Not used for sizing in Phase 4 (reserved for future).
        bot_id:             Bot identifier forwarded to TradeProposal.
        symbol:             Trading pair, e.g. "BTC/USDT".
        current_exposure:   Current fraction of allocation deployed (-1..+1).
        style:              Risk style (used for rationale metadata only here).

    Returns:
        TradeProposal with llm_adjustment_factor=1.0, or None if no action needed.
    """
    if not signals:
        return None

    regime_type = regime.regime if regime is not None else None

    # Compute weighted average target_exposure
    total_weight = 0.0
    weighted_sum = 0.0
    for sig in signals:
        fit = _regime_fit_score(sig.strategy_id, regime_type)
        w = sig.conviction * fit
        total_weight += w
        weighted_sum += w * sig.target_exposure

    if total_weight > 0.0:
        target_exposure = weighted_sum / total_weight
    else:
        # All weights zero — unweighted average
        target_exposure = sum(s.target_exposure for s in signals) / len(signals)

    # Severe negative news override: force flat
    if news_state is not None:
        asset_state = news_state.per_asset.get(symbol)
        if asset_state is not None and asset_state.has_severe_negative:
            target_exposure = 0.0

    # Clamp to [-1, 1]
    target_exposure = max(-1.0, min(1.0, target_exposure))

    delta = target_exposure - current_exposure
    if abs(delta) < 0.01:
        return None

    stop_pct = 0.02  # default 2% stop; strategies may have suggested via signal metadata
    suggested_stops = [s.suggested_stop_pct for s in signals if s.suggested_stop_pct is not None]
    if suggested_stops:
        stop_pct = sum(suggested_stops) / len(suggested_stops)

    if target_exposure <= 0.0 and current_exposure > 0.0:
        action = TradeAction.CLOSE
        size_pct = abs(current_exposure)
    elif delta > 0.0:
        action = TradeAction.BUY
        size_pct = min(1.0, abs(delta))
    else:
        action = TradeAction.SELL
        size_pct = min(1.0, abs(delta))

    regime_str = regime_type.value if regime_type is not None else "unknown"
    signal_summary = "+".join(f"{s.strategy_id}({s.conviction:.2f})" for s in signals)
    rationale = (
        f"coordinator[code]: {signal_summary} regime={regime_str} target={target_exposure:.2f}"
    )

    return TradeProposal(
        bot_id=bot_id,
        symbol=symbol,
        action=action,
        size_pct=size_pct,
        stop_pct=stop_pct,
        rationale=rationale,
        llm_adjustment_factor=1.0,
        llm_concerns=None,
        contributing_signals=list(signals),
        proposed_at=datetime.now(UTC),
    )
