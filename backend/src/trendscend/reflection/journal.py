"""U5 Reflection Agent — daily/weekly trade journal and parameter proposals.

Reads DecisionRecord lists to extract trade statistics, then calls Claude to
write a plain-language journal entry and optionally propose parameter changes.

Security:
  - Only sanitised trade statistics (counts, rates, strategy IDs) are sent to
    the LLM.  User identifiers, balances, and API keys are never included.
  - Trade data is framed as structured statistics, not as instructions.
  - Proposals never auto-deploy; they are returned with status="proposed".
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from anthropic import Anthropic, APITimeoutError

from trendscend.schemas.decisions import DecisionRecord
from trendscend.schemas.reflection import JournalEntry, ParamChangeProposal

_LOG = logging.getLogger(__name__)
_FALLBACK_CONTENT = "Reflection unavailable — LLM call failed or no client configured."


def _compute_stats(records: list[DecisionRecord]) -> dict:  # type: ignore[type-arg]
    """Extract trade summary statistics from a list of DecisionRecords.

    Returns a plain dict with:
      - total_cycles: total decision cycles
      - trades_executed: cycles where an order was placed
      - rejection_reasons: list of most common rejection reasons
      - top_strategies: strategy IDs sorted by signal frequency (descending)
    """
    total_cycles = len(records)
    trades_executed = sum(1 for r in records if r.order_ids)

    rejection_counts: Counter[str] = Counter()
    for r in records:
        if r.risk_decision and r.risk_decision.rejection_reason:
            rejection_counts[r.risk_decision.rejection_reason] += 1
    rejection_reasons = [reason for reason, _ in rejection_counts.most_common(5)]

    strategy_counts: Counter[str] = Counter()
    for r in records:
        for sig in r.signals:
            strategy_counts[sig.strategy_id] += 1
    top_strategies = [sid for sid, _ in strategy_counts.most_common(5)]

    return {
        "total_cycles": total_cycles,
        "trades_executed": trades_executed,
        "rejection_reasons": rejection_reasons,
        "top_strategies": top_strategies,
    }


def _build_prompt(
    stats: dict,  # type: ignore[type-arg]
    period_start: datetime,
    period_end: datetime,
    period_type: str,
) -> str:
    rejection_str = ", ".join(stats["rejection_reasons"]) or "none"
    strategy_str = ", ".join(stats["top_strategies"]) or "none"
    lines = [
        "You are a trading system reflection assistant. Analyze the following"
        " trading period statistics and provide a journal entry.",
        "",
        f"PERIOD: {period_start.date()} to {period_end.date()} ({period_type})",
        "TRADE STATISTICS:",
        f"- Total decision cycles: {stats['total_cycles']}",
        f"- Trades executed: {stats['trades_executed']}",
        f"- Top rejection reasons: {rejection_str}",
        f"- Active strategies: {strategy_str}",
        "",
        "Respond with JSON only (no markdown, no text outside JSON):",
        "{",
        '  "journal": "<2-3 paragraph trade journal entry summarising the period>",',
        '  "proposals": [',
        "    {",
        '      "strategy_id": "<strategy_id>",',
        '      "parameter": "<parameter_name>",',
        '      "current_value": "<current>",',
        '      "proposed_value": "<proposed>",',
        '      "rationale": "<why>"',
        "    }",
        "  ]",
        "}",
        "",
        "Rules:",
        "- Journal must be written in third person describing the system's behaviour",
        "- Do not include user identity, account balances, or personal information",
        "- proposals may be an empty array if no improvements are identified",
        "- Proposals must never be automatically deployed; they are for human review",
    ]
    return "\n".join(lines)


class ReflectionAgent:
    """Generates trade journal entries and parameter proposals from DecisionRecords.

    Usage::

        agent = ReflectionAgent(llm_client=Anthropic())
        entry, proposals = agent.generate_journal(
            records=decision_records,
            period_start=yesterday_start,
            period_end=yesterday_end,
            period_type="daily",
            bot_id=bot_id,
        )
    """

    def __init__(
        self,
        model: str | None = None,
        llm_client: Anthropic | None = None,
    ) -> None:
        self._model = model or os.environ.get("REFLECTION_MODEL", "claude-sonnet-4-6")
        self._client = llm_client

    def generate_journal(
        self,
        records: list[DecisionRecord],
        period_start: datetime,
        period_end: datetime,
        period_type: Literal["daily", "weekly"],
        bot_id: UUID,
    ) -> tuple[JournalEntry, list[ParamChangeProposal]]:
        """Generate a journal entry and parameter proposals from decision records.

        Returns a stub entry with content="Reflection unavailable" when the LLM
        call fails or no client is configured.  Always returns empty proposals
        on failure.  Proposals are never auto-deployed.

        Args:
            records:      DecisionRecords for the period to reflect on.
            period_start: UTC start of the reflection period.
            period_end:   UTC end of the reflection period.
            period_type:  "daily" or "weekly".
            bot_id:       Bot identifier forwarded to JournalEntry.

        Returns:
            Tuple of (JournalEntry, list[ParamChangeProposal]).
        """
        stats = _compute_stats(records)
        now = datetime.now(UTC)

        if self._client is None:
            entry = JournalEntry(
                bot_id=bot_id,
                period_start=period_start,
                period_end=period_end,
                period_type=period_type,
                content=_FALLBACK_CONTENT,
                trade_count=stats["trades_executed"],
                generated_at=now,
                llm_model="none",
            )
            return entry, []

        prompt = _build_prompt(stats, period_start, period_end, period_type)
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                timeout=30.0,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text.strip()
            data = json.loads(raw_text)
            journal_content = str(data["journal"])
            raw_proposals = data.get("proposals", [])
        except (APITimeoutError, Exception):  # noqa: BLE001
            _LOG.warning("ReflectionAgent: LLM call failed; returning stub entry")
            entry = JournalEntry(
                bot_id=bot_id,
                period_start=period_start,
                period_end=period_end,
                period_type=period_type,
                content=_FALLBACK_CONTENT,
                trade_count=stats["trades_executed"],
                generated_at=now,
                llm_model=self._model,
            )
            return entry, []

        entry = JournalEntry(
            bot_id=bot_id,
            period_start=period_start,
            period_end=period_end,
            period_type=period_type,
            content=journal_content,
            trade_count=stats["trades_executed"],
            generated_at=now,
            llm_model=self._model,
        )

        proposals: list[ParamChangeProposal] = []
        journal_id = entry.id
        for p in raw_proposals:
            try:
                proposals.append(
                    ParamChangeProposal(
                        journal_entry_id=journal_id,
                        strategy_id=str(p["strategy_id"]),
                        parameter=str(p["parameter"]),
                        current_value=str(p["current_value"]),
                        proposed_value=str(p["proposed_value"]),
                        rationale=str(p["rationale"]),
                        proposed_at=now,
                    )
                )
            except (KeyError, ValueError):
                _LOG.warning("ReflectionAgent: skipping malformed proposal: %s", p)

        return entry, proposals
