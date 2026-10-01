"""Persistence projections for agent runs (§41.1, the §36-equivalent for the
HarnessKernel plane).

A run row is written ONCE, at terminal state, from the frozen RunResult —
the row is a projection, never a source of truth for the live kernel (the
StateManager stays the only mutable authority, INV-01). Events are the
harness telemetry (INV-15 made real): the EventBus history flattened into
ordered rows.

The models are deliberately loose (`dict`) where the source models are
frozen pydantic with discriminated unions (EvidencePack, RunUsage,
RuntimeSpec): the row stores `model_dump()` output and readers
`model_validate()` back — schema evolution is a migration, not a code path.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AgentRunRecord(BaseModel):
    """One finished (or failed) agent run as persisted."""

    model_config = {"frozen": True}

    run_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    profile_id: str = ""
    objective: str = ""
    workspace: str | None = None
    status: str
    stop_reason: str | None = None
    detail_code: str | None = None
    summary: str = ""
    #: EvidencePack.model_dump() — None when the run produced no evidence.
    evidence: dict[str, Any] | None = None
    #: RunUsage.model_dump()
    usage: dict[str, Any] = Field(default_factory=dict)
    #: RuntimeSpec.model_dump() — reproducibility (§52): pinned versions.
    spec: dict[str, Any] = Field(default_factory=dict)
    verification_command: list[str] | None = None
    created_at: datetime
    finished_at: datetime | None = None


class AgentRunEventRecord(BaseModel):
    """One harness event, flattened from the EventBus history."""

    model_config = {"frozen": True}

    event_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    #: Ordering within the run — the bus history is append-ordered.
    seq: int = Field(ge=0)
    event_type: str = Field(min_length=1)
    turn_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    recorded_at: datetime
