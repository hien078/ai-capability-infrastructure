"""RuntimeSpec (harness.md §6): immutable per-run configuration.

Profiles are DATA, not subsystems (§19A): one HarnessKernel serves all nine
profiles by composing a RuntimeSpec. Delegation is OFF by default (§0.3).
"""

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import ResultContract
from aci.domain.runtime.state import BudgetLedger


class LoopFamily(StrEnum):
    """§19 four loop families — profiles specialize behavior, not frameworks."""

    ENGINEERING = "engineering"
    RESEARCH = "research"
    ANALYSIS = "analysis"
    EVALUATION = "evaluation"


class AgentProfileId(StrEnum):
    """§20 nine specialized profiles (rollout order §Profile Rollout Overlay)."""

    CODER = "coder"
    DEBUGGER = "debugger"
    RESEARCHER = "researcher"
    REVIEWER = "reviewer"
    TESTER = "tester"
    DEVOPS_SRE = "devops_sre"
    DATA_ANALYST = "data_analyst"
    ARCHITECT = "architect"
    SECURITY_ANALYST = "security_analyst"


class ModelPolicy(BaseModel):
    """§6.2 — model selection constraints; classes, never hardcoded providers."""

    model_config = {"frozen": True}

    default_class: Literal["reasoning", "fast", "tool", "verifier"] = "reasoning"
    max_cost_usd: float = Field(default=2.50, ge=0.0)
    fallback_order: list[str] = Field(default_factory=list)


class LoopPolicy(BaseModel):
    """§6.3 — bounded autonomy."""

    model_config = {"frozen": True}

    max_turns: int = Field(default=40, ge=1)
    max_total_tokens: int = Field(default=180_000, ge=1)
    max_output_tokens: int = Field(default=30_000, ge=1)
    max_tool_calls: int = Field(default=100, ge=1)
    max_wall_time_seconds: int = Field(default=1_800, ge=1)
    max_recoveries: int = Field(default=8, ge=0)
    cancellation: Literal["cooperative"] = "cooperative"
    verify_before_success: bool = True


class PlanningPolicy(BaseModel):
    """§6.4 — adaptive planning; planner is a replaceable strategy (§10)."""

    model_config = {"frozen": True}

    mode: Literal["none", "todo", "structured", "adaptive"] = "adaptive"
    max_plan_items: int = Field(default=20, ge=1)
    max_replans: int = Field(default=2, ge=0)


class DelegationPolicy(BaseModel):
    """§0.3 — service-side recursive delegation OFF by default."""

    model_config = {"frozen": True}

    enabled: bool = False
    max_depth: int = Field(default=1, ge=0)
    max_children: int = Field(default=3, ge=0)


class RuntimeSpec(BaseModel):
    """§6 — immutable configuration for one run."""

    model_config = {"frozen": True}

    profile_id: AgentProfileId = AgentProfileId.CODER
    profile_version: str = "1.0.0"
    loop_family: LoopFamily = LoopFamily.ENGINEERING
    model_policy: ModelPolicy = Field(default_factory=ModelPolicy)
    loop_policy: LoopPolicy = Field(default_factory=LoopPolicy)
    planning_policy: PlanningPolicy = Field(default_factory=PlanningPolicy)
    delegation_policy: DelegationPolicy = Field(default_factory=DelegationPolicy)
    initial_grants: GrantEnvelope = Field(default_factory=GrantEnvelope)
    result_contract: ResultContract
    budget: BudgetLedger = Field(default_factory=BudgetLedger)
    harness_version: str = "0.1.0"
    created_at: datetime

    @model_validator(mode="after")
    def _budgets_consistent(self) -> "RuntimeSpec":
        lp, b = self.loop_policy, self.budget
        if (
            lp.max_turns != b.max_turns
            or lp.max_total_tokens != b.max_total_tokens
            or lp.max_output_tokens != b.max_output_tokens
            or lp.max_tool_calls != b.max_tool_calls
            or lp.max_wall_time_seconds != b.max_wall_time_seconds
            or lp.max_recoveries != b.max_recoveries
        ):
            raise ValueError("loop_policy and budget limits must agree")
        return self
