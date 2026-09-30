"""ModelAction union (harness.md §47): provider outputs normalized into a
small action union — the runtime never parses natural-language control flow.

Model output is untrusted input (INV-07): every action is validated before
dispatch; provider-specific details stay inside ModelGateway.
"""

from typing import Literal

from pydantic import BaseModel, Field

from aci.domain.runtime.tools import ToolCall


class FinalCandidate(BaseModel):
    """§47.1 — the model PROPOSES completion; VerificationManager gates it."""

    model_config = {"frozen": True}

    type: Literal["final_candidate"] = "final_candidate"
    summary: str = ""
    changes: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    criteria_addressed: list[str] = Field(default_factory=list)


class ToolCallBatchAction(BaseModel):
    model_config = {"frozen": True}

    type: Literal["tool_calls"] = "tool_calls"
    calls: list[ToolCall] = Field(min_length=1)


class CapabilityRequest(BaseModel):
    """§47.1 — mid-run capability acquisition (bounded by refresh policy)."""

    model_config = {"frozen": True}

    type: Literal["capability_request"] = "capability_request"
    objective: str = Field(min_length=1)
    reason: str = ""
    desired_kinds: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)


class DelegationRequest(BaseModel):
    model_config = {"frozen": True}

    type: Literal["delegation_request"] = "delegation_request"
    subtask_objective: str = Field(min_length=1)
    required_output: str = ""
    requested_budget_tokens: int = Field(default=0, ge=0)
    requested_budget_tool_calls: int = Field(default=0, ge=0)


class PlanUpdateRequest(BaseModel):
    model_config = {"frozen": True}

    type: Literal["plan_update"] = "plan_update"
    items: list[dict[str, str]] = Field(min_length=1)


class ClarificationRequest(BaseModel):
    model_config = {"frozen": True}

    type: Literal["clarification"] = "clarification"
    question: str = Field(min_length=1)


class ContinueAction(BaseModel):
    model_config = {"frozen": True}

    type: Literal["continue"] = "continue"


ModelAction = (
    FinalCandidate
    | ToolCallBatchAction
    | CapabilityRequest
    | DelegationRequest
    | PlanUpdateRequest
    | ClarificationRequest
    | ContinueAction
)
