"""Kernel manager Protocols (harness.md PART II) — shared seams between engine components."""

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from aci.domain.runtime.authority import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorityDecision,
    AuthorityRequirement,
    ExecutionEnvelope,
    GrantEnvelope,
)
from aci.domain.runtime.evidence import EvidenceItem
from aci.domain.runtime.tools import SideEffectClass, SideEffectReport, ToolObservation, ToolSpec
from aci.runtime.guardrails import GuardrailResult

if TYPE_CHECKING:
    from aci.runtime.context_engine import ContextItem
from aci.runtime.model_gateway import ModelRequest, ModelResponse


class ProcessResult(BaseModel):
    """Result of one workspace process execution (§16.3). Frozen observation."""

    model_config = {"frozen": True}

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    timed_out: bool = False


class Workspace(Protocol):
    """§16.3 — the rest of HarnessKernel must not care whether this is local, sandbox, or remote."""

    def read_file(self, path: str) -> str: ...

    def write_file(self, path: str, content: str) -> None: ...

    def list_dir(self, path: str) -> list[str]: ...

    def execute(self, command: list[str], timeout_ms: int) -> ProcessResult: ...

    def terminate(self) -> None: ...

    def snapshot(self) -> str: ...

    def diff(self, snapshot_id: str) -> str: ...


class ToolDispatchResult(BaseModel):
    """Raw result of one workspace/sandbox dispatch (§12.1 step 7)."""

    model_config = {"frozen": True}

    output: str = ""
    side_effects: SideEffectReport = Field(default_factory=SideEffectReport)
    duration_ms: int = Field(default=0, ge=0)
    #: What the dispatch demonstrably observed (§12.1 step 11); copied onto
    #: the success observation only.
    evidence: list[EvidenceItem] = Field(default_factory=list)


class PreToolGuardrails(Protocol):
    def check_pre_tool(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult: ...


class PostToolGuardrails(Protocol):
    def check_post_tool(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult: ...


class GuardrailManager(PreToolGuardrails, PostToolGuardrails, Protocol):
    """Both guardrail phases; ToolRuntime accepts one manager for both."""


class ToolDispatcher(Protocol):
    """Workspace/sandbox execution port (§12.1 steps 6-7, INV-04 enforcement)."""

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult: ...


class ArtifactSpill(Protocol):
    """Persists truncated-away tool output; returns the artifact reference."""

    def __call__(self, content: str, *, tool_id: str, call_id: str) -> str: ...


class ModelGateway(Protocol):
    """§20.1 — the only model seam; provider adapters implement this (SYNC)."""

    def invoke(self, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class CompactionSummarizer(Protocol):
    """§9.7 compaction summarizer — deterministic in v2 (no LLM default)."""

    def summarize(self, items: list["ContextItem"]) -> str:
        """Merge compacted items into one summary string."""
        ...


class AuthorityPort(Protocol):
    """§13 authority seam — the run loop evaluates requirements and applies
    approvals through this port; enforcement lives in Workspace/ToolRuntime."""

    def evaluate(
        self,
        requirement: AuthorityRequirement,
        side_effect_class: SideEffectClass | None = None,
    ) -> AuthorityDecision: ...

    def make_approval_request(
        self,
        run_id: str,
        requirement: AuthorityRequirement,
        reason: str,
        operation_hash: str,
    ) -> ApprovalRequest: ...

    def apply_approval(
        self, decision: ApprovalDecision, request: ApprovalRequest
    ) -> GrantEnvelope: ...

    def build_execution_envelope(
        self,
        run_id: str,
        workspace_id: str,
        decision: AuthorityDecision,
        grants: GrantEnvelope | None = None,
    ) -> ExecutionEnvelope: ...
