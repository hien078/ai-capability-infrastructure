"""Runtime state contracts (harness.md §8): one authoritative state model.

StateManager is the ONLY owner of mutable runtime state (INV-01); everything
else reads immutable snapshots and emits events. Snapshots are frozen —
mutation goes through versioned commits (§8.5).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import EvidenceItem
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.tools import ToolCall


class BudgetLedger(BaseModel):
    """§22.2 — limits/consumed/reserved/remaining per dimension."""

    model_config = {"frozen": True}

    max_turns: int = Field(default=40, ge=1)
    max_total_tokens: int = Field(default=180_000, ge=1)
    max_output_tokens: int = Field(default=30_000, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_wall_time_seconds: int = Field(default=1_800, ge=1)
    max_cost_usd: float = Field(default=2.50, ge=0.0)
    max_recoveries: int = Field(default=8, ge=0)

    consumed_turns: int = Field(default=0, ge=0)
    consumed_input_tokens: int = Field(default=0, ge=0)
    consumed_output_tokens: int = Field(default=0, ge=0)
    consumed_tool_calls: int = Field(default=0, ge=0)
    consumed_wall_time_seconds: float = Field(default=0.0, ge=0.0)
    consumed_cost_usd: float = Field(default=0.0, ge=0.0)
    consumed_recoveries: int = Field(default=0, ge=0)
    reserved_tokens: int = Field(default=0, ge=0)
    reserved_cost_usd: float = Field(default=0.0, ge=0.0)


class RunState(BaseModel):
    """§8.2 — the run's lifecycle record."""

    model_config = {"frozen": True}

    run_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    status: RunStatus = RunStatus.CREATED
    stop_reason: StopReason | None = None
    detail_code: str | None = None
    current_turn: int = Field(default=0, ge=0)
    version: int = Field(default=1, ge=1)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class TaskState(BaseModel):
    """§8.4 — the delegated objective + acceptance criteria."""

    model_config = {"frozen": True}

    task_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    current_phase: str = "understand"
    progress: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    completion_claim: str | None = None


class PlanItem(BaseModel):
    """§10.3 — planner proposes; StateManager commits."""

    model_config = {"frozen": True}

    item_id: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    status: Literal["pending", "running", "blocked", "done", "failed"] = "pending"
    dependencies: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


#: How a capability came to be activated in a run (2026-10-01
#: exposure-origin fix): "preload" = the kernel's run-start preload
#: (``preload_capabilities``); "model_request" = the model asked for it
#: (the text-JSON ``capability_request`` action or the ``request_capability``
#: tool). One activation can carry BOTH — a preloaded skill the model then
#: asked for again keeps ONE entry whose ``origins`` accumulate.
CapabilityOrigin = Literal["preload", "model_request"]


class CapabilityActivation(BaseModel):
    """§49.3 CapabilityHandle — pinned immutable version per run (§11.7)."""

    model_config = {"frozen": True}

    capability_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    #: sha256 of the ENTRY file bytes (``SKILL.md``) the instructions were
    #: decoded from after digest verification — the ENTRY digest, NOT the
    #: package digest (``artifact.package_digest`` hashes the whole manifest).
    digest: str = Field(min_length=1)
    activation_id: str = Field(min_length=1)
    status: str = "ACTIVE"  # §49.1 lifecycle states
    loaded_tools: list[str] = Field(default_factory=list)
    context_tokens: int = Field(default=0, ge=0)
    activated_at: datetime | None = None
    #: Registry provenance of the selection that produced this activation:
    #: the §14 route run + routed bundle that chose this exact version. None
    #: for selections from clients that carry no provenance (fakes, tests)
    #: and for pre-provenance checkpoints (backward compatible).
    route_run_id: str | None = None
    bundle_id: str | None = None
    #: The capability's instruction payload (``SKILL.md``), decoded from the
    #: digest-VERIFIED bytes only and bounded by CapabilityRuntime. It lives
    #: in authoritative run state (INV-01) so checkpoints carry it (INV-13)
    #: and ContextEngine re-renders it every turn. Reference material only —
    #: it never grants authority (AuthorityManager never reads it). Empty for
    #: activations recorded before payloads were carried (backward compat).
    instructions: str = ""
    #: Acquisition origins of this activation, in order. Set by the kernel
    #: (preload path vs model-request path); when the SAME capability is
    #: activated again the state manager keeps ONE entry whose origins
    #: accumulate — the origin history is never dropped. Empty for
    #: activations recorded before origins existed: legacy state and
    #: checkpoints parse and resume unchanged (never a guess, never
    #: fabricated).
    origins: list[CapabilityOrigin] = Field(default_factory=list)


class TranscriptEntry(BaseModel):
    """§7.4 — one conversation fragment. The transcript is authoritative run
    state (INV-01) and serializable (INV-13): checkpoints carry it, and the
    kernel re-assembles every model request from it within the context budget
    (INV-09) instead of keeping a private chat log."""

    model_config = {"frozen": True}

    role: Literal["user", "assistant", "tool"]
    content: str = ""
    #: Assistant turns that requested tools (the provider wire needs them).
    tool_calls: list[ToolCall] = Field(default_factory=list)
    #: Tool results bind to the call that produced them.
    tool_call_id: str | None = None
    turn: int = Field(default=0, ge=0)


class RuntimeStateSnapshot(BaseModel):
    """The read-only view other components receive (INV-01)."""

    model_config = {"frozen": True}

    run: RunState
    task: TaskState
    budget: BudgetLedger
    grants: GrantEnvelope
    plan: list[PlanItem] = Field(default_factory=list)
    active_capabilities: list[CapabilityActivation] = Field(default_factory=list)
    workspace_id: str | None = None
    depth: int = Field(default=0, ge=0)
    #: §41.2 confirmed side effects observed on the tool path (e.g.
    #: ``file:src/app.py``) — the evidence the verifier reconciles claims
    #: against (INV-08), never the model's own report.
    changed_resources: list[str] = Field(default_factory=list)
    #: Evidence observed on the tool path, in order (reads, command exits).
    observed_evidence: list[EvidenceItem] = Field(default_factory=list)
    transcript: list[TranscriptEntry] = Field(default_factory=list)
