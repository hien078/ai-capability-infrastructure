"""Tool contracts (harness.md §12): ToolSpec, ToolCall, ToolObservation.

All side effects go through explicit ToolRuntime (INV-06); tool output is
bounded (INV-10) — every tool declares an output policy with truncation and
artifact spill.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

SideEffectClass = Literal[
    "PURE", "READ_ONLY", "LOCAL_MUTATION", "EXTERNAL_MUTATION", "DESTRUCTIVE", "PRIVILEGED"
]
IdempotencyClass = Literal["IDEMPOTENT", "IDEMPOTENT_WITH_KEY", "NON_IDEMPOTENT", "UNKNOWN"]
CancellationSupport = Literal["COOPERATIVE", "KILLABLE", "NONE"]


class OutputPolicy(BaseModel):
    """§12.3/§9.8 — no unbounded stdout or search results."""

    model_config = {"frozen": True}

    max_inline_chars: int = Field(default=12_000, ge=100)
    max_inline_tokens: int = Field(default=3_000, ge=10)
    truncation: Literal["head_tail", "head", "tail"] = "head_tail"
    spill_to_artifact: bool = True


class ToolSpec(BaseModel):
    """§12.3 tool contract. Frozen — registered tools are immutable."""

    model_config = {"frozen": True}

    tool_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    side_effect_class: SideEffectClass = "READ_ONLY"
    authority_requirements: dict[str, Any] = Field(default_factory=dict)
    workspace_requirements: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = Field(default=120_000, ge=1)
    cancellation_support: CancellationSupport = "COOPERATIVE"
    idempotency_class: IdempotencyClass = "UNKNOWN"
    output_policy: OutputPolicy = Field(default_factory=OutputPolicy)
    concurrency_safe: bool = False


class ToolCall(BaseModel):
    """A model-requested tool invocation (INV-07: untrusted until validated)."""

    model_config = {"frozen": True}

    call_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolCallBatch(BaseModel):
    model_config = {"frozen": True}

    calls: list[ToolCall] = Field(min_length=1)


class SideEffectReport(BaseModel):
    """§41.2 — what a tool execution changed, for evidence + recovery."""

    model_config = {"frozen": True}

    state: Literal["none", "possible", "confirmed"] = "none"
    resources_changed: list[str] = Field(default_factory=list)


class ToolObservation(BaseModel):
    """Normalized result of one tool execution (§41.2)."""

    model_config = {"frozen": True}

    tool_call_id: str = Field(min_length=1)
    tool_id: str = Field(min_length=1)
    status: Literal["success", "error", "blocked", "denied", "timeout"] = "success"
    summary: str = ""
    inline_output: str = ""
    artifact_ref: str | None = None
    side_effects: SideEffectReport = Field(default_factory=SideEffectReport)
    error_class: str | None = None
    duration_ms: int = Field(default=0, ge=0)
