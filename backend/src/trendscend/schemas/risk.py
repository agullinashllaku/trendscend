from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class RiskStyle(StrEnum):
    CONSERVATIVE = "conservative"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"


class RiskOutcome(StrEnum):
    APPROVED = "approved"
    RESIZED = "resized"
    REJECTED = "rejected"
    KILL_SWITCH = "kill_switch"
    PAUSED = "paused"


class RiskDecision(BaseModel):
    id: UUID
    proposal_id: UUID
    outcome: RiskOutcome
    approved_size_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    rejection_reason: str | None = None
    checks_run: list[str]
    decided_at: datetime
