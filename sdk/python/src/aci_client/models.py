"""Typed wire models for the ACI REST API (plan §31).

These mirror the server's wire schemas — ``adapters/inbound/rest/schemas.py``
plus the domain models those routers project — WITHOUT importing ``aci``:
the SDK must work against a remote server, so every shape is re-declared
here. The contract test pins these against the live app's OpenAPI/JSON so
drift breaks loudly.

Deliberate deviations from a full mirror (documented per model):

- ``CapabilityVersionSummary`` keeps only the fields the SDK needs from the
  server's ``CapabilityVersion`` (the discriminated ``spec`` union is not
  re-declared); unknown fields are ignored so additive server changes parse.
- ``AgentRun.usage`` / ``AgentRun.changes`` are the p-agentrun-api additive
  read-model fields: both default to ``None`` so an OLDER server's
  response (without them) still parses — the SDK tolerates their absence.
"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Plan §7 capability kinds (server: domain.capability.models.CapabilityKind).
CapabilityKind = Literal["skill", "resource", "tool", "workflow", "service", "agent"]

#: Plan §33.1 verdict sources (server: domain.capability.models.VerdictSource).
VerdictSource = Literal[
    "agent_self_report",
    "client_report",
    "test_harness",
    "static_analysis",
    "external_evaluator",
    "human_review",
    "production_signal",
]

VerdictStatus = Literal["success", "failure", "unknown"]
VerdictConfidence = Literal["high", "medium", "low"]

#: Server default for the routing context budget
#: (adapters/inbound/rest/schemas.py RouteConstraintsIn).
DEFAULT_MAX_CONTEXT_TOKENS = 8000


# --- Routing (POST /v1/routes) -------------------------------------------------


class TaskContext(BaseModel):
    """Minimal routing context (§25.4): hints, never repo dumps or secrets."""

    language: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    phase: str | None = None
    file_hints: list[str] = Field(default_factory=list)
    repository_summary: str = Field(default="", max_length=2000)


class BundleItem(BaseModel):
    """One pinned, immutable capability selected into a bundle (§19.1)."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind
    role: Literal["primary", "check", "support"] = "primary"
    load_mode: Literal["lazy"] = "lazy"
    reason_code: str = ""


class BundleBudget(BaseModel):
    """Budget the composer enforced for one bundle (§19.1)."""

    model_config = ConfigDict(frozen=True)

    max_items: int = 5
    max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS


class CapabilityBundle(BaseModel):
    """A pinned, replayable bundle (§19.1); 0 items is a valid success."""

    model_config = ConfigDict(frozen=True)

    bundle_id: str
    route_run_id: str
    created_at: datetime
    items: list[BundleItem] = Field(default_factory=list)
    execution_order: list[str] = Field(default_factory=list)
    budget: BundleBudget | None = None
    policy_snapshot_id: str | None = None


class RouteResult(BaseModel):
    """What POST /v1/routes returns: run identity + the pinned bundle."""

    model_config = ConfigDict(frozen=True)

    route_run_id: str
    bundle: CapabilityBundle


# --- Search (POST /v1/capabilities/search) -------------------------------------


class CapabilitySearchResult(BaseModel):
    """One search hit (§11.2): trusted metadata + score."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind
    display_name: str = ""
    description: str = ""
    facets: dict[str, list[str]] = Field(default_factory=dict)
    score: float = 0.0


# --- Outcomes (POST /v1/outcomes) ----------------------------------------------


class OutcomeVerdict(BaseModel):
    """One verdict from one source (§33); never a lone success flag."""

    source: VerdictSource
    status: VerdictStatus
    confidence: VerdictConfidence = "low"


class OutcomeEvidence(BaseModel):
    """Multi-source evidence for one routed bundle run (§33, ADR-010)."""

    model_config = ConfigDict(frozen=True)

    outcome_id: str
    route_run_id: str
    bundle_id: str
    received_at: datetime
    verdicts: list[OutcomeVerdict] = Field(min_length=1)
    tests_before: dict[str, Any] = Field(default_factory=dict)
    tests_after: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
    # §33 evidence envelope: client completion, build/test/lint observations,
    # human correction, cost — all optional, never merged into one flag.
    client_status: str | None = None
    lint_passed: bool | None = None
    build_passed: bool | None = None
    changed_files: int | None = None
    tool_calls: int | None = None
    human_corrected: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_usd: float | None = None


# --- Skill content (catalog + resolve, used by get_skill) ---------------------


class ArtifactFile(BaseModel):
    """One file inside an immutable skill package (§20)."""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size_bytes: int = 0


class CapabilityArtifact(BaseModel):
    """Immutable content-package metadata for one version (§20, §39)."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    version: str
    package_digest: str
    manifest: dict[str, Any] = Field(default_factory=dict)
    files: list[ArtifactFile] = Field(default_factory=list)


class CapabilityVersionSummary(BaseModel):
    """The ``CapabilityVersion`` fields the SDK needs (see module docstring).

    ``extra="ignore"``: the server's full model carries a discriminated spec
    union the SDK has no use for; additive server fields must not break it.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    capability_id: str
    version: str
    kind: CapabilityKind
    content_digest: str = ""
    display_name: str = ""
    description: str = ""


class ResolvedVersion(BaseModel):
    """A pinned version plus its immutable artifact (resolve, §12)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    version: CapabilityVersionSummary
    artifact: CapabilityArtifact | None = None


class SkillFile(BaseModel):
    """One digest-verified file of a skill package, canonical artifact path."""

    model_config = ConfigDict(frozen=True)

    path: str
    sha256: str
    size_bytes: int = 0
    #: UTF-8 decoding of the bytes, or ``None`` when not decodable (binary).
    text: str | None = None


class SkillContent(BaseModel):
    """What ``get_skill`` returns: the entry file text + verified files."""

    model_config = ConfigDict(frozen=True)

    capability_id: str
    version: str
    package_digest: str
    #: The entry file (``SKILL.md``), decoded — this is what a client
    #: preloads into its system prompt.
    skill_md: str
    files: list[SkillFile] = Field(default_factory=list)

    @property
    def files_by_path(self) -> dict[str, SkillFile]:
        """The verified files keyed by canonical artifact path."""
        return {f.path: f for f in self.files}


# --- Agent runs (POST/GET /v1/agent-runs) --------------------------------------


class Budget(BaseModel):
    """Run budget limits (§22.2 ``BudgetLedger`` wire shape, limits only)."""

    max_turns: int = 40
    max_total_tokens: int = 180_000
    max_output_tokens: int = 30_000
    max_tool_calls: int = 100
    max_wall_time_seconds: int = 1_800
    max_cost_usd: float = 2.50
    max_recoveries: int = 8


class RunUsage(BaseModel):
    """p-agentrun-api additive usage block — absent on older servers."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    model_input_tokens: int = 0
    model_output_tokens: int = 0
    turns: int = 0
    tool_calls: int = 0
    wall_seconds: float = 0.0


class RunFileChange(BaseModel):
    """One workspace file change (p-agentrun-api read model).

    ``path`` is workspace-relative POSIX — never absolute, never ``..``.
    The server enforces it; the SDK re-checks on parse (fail closed: a
    hostile or inconsistent response cannot smuggle a host path in).
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    path: str
    status: Literal["added", "modified", "deleted"]
    sha256_before: str | None = None
    sha256_after: str | None = None
    size_after: int | None = None

    @field_validator("path")
    @classmethod
    def _relative_posix(cls, value: str) -> str:
        if value.startswith("/") or "\\" in value or ".." in value.split("/"):
            raise ValueError(f"workspace path must be relative POSIX: {value!r}")
        return value


class RunChanges(BaseModel):
    """Workspace change summary (p-agentrun-api read model)."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    files: list[RunFileChange] = Field(default_factory=list)
    diff: str | None = None
    truncated: bool = False


class AgentRun(BaseModel):
    """The agent-run read model (§29A compact state — never the transcript).

    ``usage`` and ``changes`` are the p-agentrun-api additive fields: both
    default to ``None`` so a response from an older server (without them)
    parses — callers must tolerate their absence.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    run_id: str
    status: str
    stop_reason: str | None = None
    detail_code: str | None = None
    summary: str = ""
    evidence_verdict: str | None = None
    checks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    wall_time_seconds: float = 0.0
    #: Set only while status == "interrupted_approval": name it in the
    #: server's resume endpoint to approve or deny the pending call.
    approval_id: str | None = None
    usage: RunUsage | None = None
    changes: RunChanges | None = None
