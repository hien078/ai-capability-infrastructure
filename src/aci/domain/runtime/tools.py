"""Tool contracts (harness.md §12): ToolSpec, ToolCall, ToolObservation.

All side effects go through explicit ToolRuntime (INV-06); tool output is
bounded (INV-10) — every tool declares an output policy with truncation and
artifact spill.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field

from aci.domain.runtime.evidence import EvidenceItem

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


class ToolAuthority(BaseModel):
    """§58 ``tool.derive_authority(args)``: which validated arguments name the
    resources a call touches. A mutating tool that declares nothing cannot be
    scoped, so it is denied (INV-06, fail closed)."""

    model_config = {"frozen": True}

    read_path_args: list[str] = Field(default_factory=list)
    write_path_args: list[str] = Field(default_factory=list)
    command_args: list[str] = Field(default_factory=list)
    host_args: list[str] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.read_path_args or self.write_path_args or self.command_args or self.host_args
        )


class ToolSpec(BaseModel):
    """§12.3 tool contract. Frozen — registered tools are immutable."""

    model_config = {"frozen": True}

    tool_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] | None = None
    side_effect_class: SideEffectClass = "READ_ONLY"
    authority_requirements: ToolAuthority = Field(default_factory=ToolAuthority)
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


MUTATING_CLASSES: frozenset[SideEffectClass] = frozenset(
    {"LOCAL_MUTATION", "EXTERNAL_MUTATION", "DESTRUCTIVE", "PRIVILEGED"}
)


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
    #: §12.1 step 11 — what the call demonstrably observed (file read,
    #: command exit status); the verifier grounds claims in these (INV-08).
    evidence: list[EvidenceItem] = Field(default_factory=list)
    error_class: str | None = None
    duration_ms: int = Field(default=0, ge=0)
