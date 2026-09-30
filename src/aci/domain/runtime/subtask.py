"""SubtaskContract + RunResult (harness.md §34.1, §41.1): the client boundary.

The client global orchestrator owns the global Task DAG; a service agent owns
exactly one delegated objective (§2.1). Revision is delta-based (§29A/§57) —
never replay the entire global conversation.
"""

from datetime import datetime

from pydantic import BaseModel, Field

from aci.domain.runtime.evidence import EvidencePack
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason


class AcceptanceCriterion(BaseModel):
    model_config = {"frozen": True}

    criterion_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    verifier_hint: str = ""


class SubtaskContract(BaseModel):
    """§34.1 — what the client delegates. Frozen at request time."""

    model_config = {"frozen": True}

    task_id: str = Field(min_length=1)
    parent_task_id: str | None = None
    objective: str = Field(min_length=1)
    global_context: str = ""
    constraints: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)
    acceptance_criteria: list[AcceptanceCriterion] = Field(default_factory=list)
    input_artifacts: list[str] = Field(default_factory=list)
    requested_profile: str = "coder"
    risk_level: int = Field(default=1, ge=0, le=4)  # §38 R0–R4
    budget: BudgetLedger | None = None
    created_at: datetime


class RunUsage(BaseModel):
    """§41.1 usage block — cost/tokens/latency recorded per run (§52)."""

    model_config = {"frozen": True}

    turns: int = Field(default=0, ge=0)
    model_input_tokens: int = Field(default=0, ge=0)
    model_output_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    wall_time_seconds: float = Field(default=0.0, ge=0.0)


class RunResult(BaseModel):
    """§41.1 — the terminal/paused runtime result (§0.2 naming)."""

    model_config = {"frozen": True}

    run_id: str = Field(min_length=1)
    status: RunStatus
    stop_reason: StopReason | None = None
    detail_code: str | None = None
    summary: str = ""
    artifacts: list[str] = Field(default_factory=list)
    evidence: EvidencePack | None = None
    usage: RunUsage = Field(default_factory=RunUsage)
    trace_ref: str | None = None
    spec: RuntimeSpec | None = None  # reproducibility (§52) — pinned versions
