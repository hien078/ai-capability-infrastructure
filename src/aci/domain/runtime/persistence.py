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
    #: RunResult.artifacts — the run's output artifact paths (§41.1).
    artifacts: list[str] = Field(default_factory=list)
    #: EvidencePack.model_dump() — None when the run produced no evidence.
    evidence: dict[str, Any] | None = None
    #: RunUsage.model_dump()
    usage: dict[str, Any] = Field(default_factory=dict)
    #: RuntimeSpec.model_dump() — reproducibility (§52): pinned versions.
    spec: dict[str, Any] = Field(default_factory=dict)
    #: RunResult.trace_ref — external trace link; None when the run set none.
    trace_ref: str | None = None
    verification_command: list[str] | None = None
    #: SubtaskContract.model_dump(mode="json") — what a §29A revision builds
    #: FROM after a restart (migration 0018). None on pre-0018 rows: such a
    #: run is readable but not revisable.
    contract: dict[str, Any] | None = None
    #: The client-chosen RunOptions (workspace, verification_command,
    #: write_scopes, command_prefixes, max_turns) as JSON. Requests, not
    #: grants: a revision re-derives every grant from the CURRENT server
    #: ceiling (INV-02) — nothing here is trusted as authority.
    run_options: dict[str, Any] | None = None
    #: Absolute path of the run's working copy — SERVER-SIDE ONLY (the copy
    #: source of a revision). Excluded from every dump/repr so no
    #: serialization of the record can carry it onto a wire.
    run_dir: str | None = Field(default=None, exclude=True, repr=False)
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
