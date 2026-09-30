"""Normalized failure taxonomy (harness.md §18.1, §28 FailureEnvelope).

RecoveryManager never parses arbitrary exception strings (§28): every failure
is classified into ``FailureClass`` and carried as a frozen ``FailureEnvelope``
with severity, retryability, and side-effect state.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class FailureClass(StrEnum):
    """§18.1 failure taxonomy — the recovery policy matrix keys off this."""

    TRANSIENT_MODEL = "TRANSIENT_MODEL"
    RATE_LIMITED = "RATE_LIMITED"
    MODEL_MALFORMED_OUTPUT = "MODEL_MALFORMED_OUTPUT"

    TRANSIENT_TOOL = "TRANSIENT_TOOL"
    TOOL_INVALID_ARGUMENT = "TOOL_INVALID_ARGUMENT"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_SIDE_EFFECT_UNCERTAIN = "TOOL_SIDE_EFFECT_UNCERTAIN"

    AUTHORITY_BLOCKED = "AUTHORITY_BLOCKED"
    APPROVAL_REJECTED = "APPROVAL_REJECTED"

    WORKSPACE_UNAVAILABLE = "WORKSPACE_UNAVAILABLE"
    SANDBOX_DENIED = "SANDBOX_DENIED"

    CAPABILITY_NOT_FOUND = "CAPABILITY_NOT_FOUND"
    CAPABILITY_LOAD_FAILED = "CAPABILITY_LOAD_FAILED"
    CAPABILITY_INSUFFICIENT = "CAPABILITY_INSUFFICIENT"
    CAPABILITY_CONFLICT = "CAPABILITY_CONFLICT"

    CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
    CONTEXT_CORRUPTION = "CONTEXT_CORRUPTION"

    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    RESULT_CONTRACT_FAILED = "RESULT_CONTRACT_FAILED"

    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    CANCELLED = "CANCELLED"
    FATAL = "FATAL"


Severity = Literal["transient", "recoverable", "fatal"]
SideEffectState = Literal["none", "possible", "confirmed"]


class FailureEnvelope(BaseModel):
    """Normalized failure (§28). Frozen — failures are immutable facts."""

    model_config = {"frozen": True}

    failure_id: str = Field(min_length=1)
    failure_class: FailureClass
    severity: Severity = "recoverable"
    component: str = Field(min_length=1)
    message: str = Field(min_length=1)
    retryable: bool = False
    side_effect_state: SideEffectState = "none"
    evidence_refs: list[str] = Field(default_factory=list)
    cause_ref: str | None = None
