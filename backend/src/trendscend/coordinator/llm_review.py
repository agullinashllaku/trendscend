"""LLM review step for U2 Coordinator.

Calls Claude with a 30-second timeout to review a TradeProposal and suggest
an adjustment factor in [0.0, 1.25].  Falls back to factor=0.5 on timeout or
parse error so the system degrades gracefully.

Security:
  - News and smart-money summaries contain only structured data (event_type,
    direction, severity, aggregate percentages). Raw article text, URLs, and
    source names are never included in the prompt.
  - User identity, balances, and API keys are never passed here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from anthropic import Anthropic, APITimeoutError

from trendscend.schemas.news import NewsState
from trendscend.schemas.risk import RiskStyle
from trendscend.schemas.signals import TradeProposal
from trendscend.schemas.smart_money import SmartMoneyState

LLM_TIMEOUT_SECS: float = 30.0
_FALLBACK_FACTOR: float = 0.5


@dataclass
class LLMReviewResult:
    adjustment_factor: float
    rationale: str = ""
    concerns: str | None = None
    timed_out: bool = False
    parse_error: bool = False


def summarise_news(news_state: NewsState | None, symbol: str) -> str:
    """Build a safe, sanitised news summary (no raw text or URLs).

    Only includes event_type, direction, and severity — never source names,
    raw headlines, or URLs.
    """
    if news_state is None:
        return "no news data available"
    asset_state = news_state.per_asset.get(symbol)
    if asset_state is None or not asset_state.recent_events:
        return "no recent news events"
    parts = []
    for ev in asset_state.recent_events[:5]:  # cap at 5 events
        parts.append(f"{ev.event_type.value}|{ev.direction.value}|severity={ev.severity}")
    return "; ".join(parts)


def summarise_smart_money(sm_state: SmartMoneyState | None, symbol: str) -> str:
    """Build a safe smart-money summary (aggregate stats only).

    Only includes aggregate percentages and wallet count — never wallet
    addresses, labels, or individual positions.
    """
    if sm_state is None:
        return "no smart-money data available"
    asset = sm_state.per_asset.get(symbol)
    if asset is None:
        return "no smart-money data for symbol"
    return (
        f"net_long={asset.net_long_pct:.0%} change_24h={asset.change_24h:+.1%} "
        f"wallets={sm_state.vetted_wallet_count}"
    )


def _build_prompt(
    proposal: TradeProposal,
    news_summary: str,
    sm_summary: str,
    style: RiskStyle,
) -> str:
    max_factor = "1.25" if style == RiskStyle.AGGRESSIVE else "1.0"
    header = "You are a portfolio review assistant. Review this trade proposal and return a JSON adjustment."  # noqa: E501
    rule1 = f"- 1.0 = keep proposed size unchanged; values < 1.0 reduce it; {max_factor} is the maximum allowed"  # noqa: E501
    rule2 = f"- Only reduce size unless you have strong positive confirmation (aggressive style only allows up to {max_factor})"  # noqa: E501
    return f"""{header}

PROPOSAL:
- Symbol: {proposal.symbol}
- Action: {proposal.action.value}
- Size: {proposal.size_pct:.0%} of allocation
- Code rationale: {proposal.rationale}

MARKET CONTEXT:
- News (last 24h): {news_summary}
- Smart money positioning: {sm_summary}

Respond with JSON only (no markdown, no explanation outside the JSON):
{{
  "adjustment_factor": <float 0.0 to {max_factor}>,
  "rationale": "<1-2 sentence explanation>",
  "concerns": "<specific concern or null>"
}}

Rules:
{rule1}
{rule2}
- "concerns" should be null if there are no significant concerns
- Do not include user identity, account balance, or personal information in your response"""


class _LLMResponseSchema:
    """Validates the LLM JSON response."""

    def __init__(self, data: dict) -> None:  # type: ignore[type-arg]
        raw_factor = float(data["adjustment_factor"])
        if not (0.0 <= raw_factor <= 1.25):
            raise ValueError(f"adjustment_factor {raw_factor} out of bounds [0, 1.25]")
        self.adjustment_factor = raw_factor
        self.rationale = str(data["rationale"])
        raw_concerns = data.get("concerns")
        self.concerns: str | None = str(raw_concerns) if raw_concerns else None


def review_proposal(
    proposal: TradeProposal,
    news_summary: str,
    sm_summary: str,
    style: RiskStyle,
    model: str,
    client: Anthropic,
) -> LLMReviewResult:
    """Call the LLM to review a trade proposal and return an adjustment result.

    On APITimeoutError or any other exception: returns factor=0.5 with timed_out=True.
    On JSON parse failure or schema error: returns factor=0.5 with parse_error=True.
    """
    prompt = _build_prompt(proposal, news_summary, sm_summary, style)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=256,
            timeout=LLM_TIMEOUT_SECS,
            messages=[{"role": "user", "content": prompt}],
        )
        from anthropic.types import TextBlock  # noqa: PLC0415

        raw_text = next((b.text for b in response.content if isinstance(b, TextBlock)), "").strip()
    except APITimeoutError:
        return LLMReviewResult(
            adjustment_factor=_FALLBACK_FACTOR,
            rationale="LLM review timed out — position halved as precaution",
            timed_out=True,
        )
    except Exception:  # noqa: BLE001
        return LLMReviewResult(
            adjustment_factor=_FALLBACK_FACTOR,
            rationale="LLM review failed — position halved as precaution",
            timed_out=True,
        )

    try:
        data = json.loads(raw_text)
        parsed = _LLMResponseSchema(data)
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return LLMReviewResult(
            adjustment_factor=_FALLBACK_FACTOR,
            rationale="LLM response could not be parsed — position halved as precaution",
            parse_error=True,
        )

    return LLMReviewResult(
        adjustment_factor=parsed.adjustment_factor,
        rationale=parsed.rationale,
        concerns=parsed.concerns,
    )
