"""Phase 0 domain contracts (plan §§6-9, 11, 19, 33, 45).

No protocol, web, or DB imports allowed in this module.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from aci.domain.taxonomy.models import validate_facets

CapabilityKind = Literal["skill", "resource", "tool", "workflow", "service", "agent"]
OwnerScope = Literal["global", "organization", "workspace", "private"]
ReleaseChannel = Literal["raw", "candidate", "canonical", "staging", "production"]
ReleaseStatus = Literal["active", "disabled", "deprecated", "revoked"]
VerdictSource = Literal[
    "agent_self_report",
    "client_report",
    "test_harness",
    "static_analysis",
    "external_evaluator",
    "human_review",
    "production_signal",
]


class Compatibility(BaseModel):
    """Client/protocol compatibility declaration (plan §48). A filter, not a preference."""

    model_config = {"frozen": True}

    supported_clients: list[str] | None = None  # None = any client
    minimum_client_features: list[str] = Field(default_factory=list)
    supported_languages: list[str] = Field(default_factory=list)
    supported_frameworks: list[str] = Field(default_factory=list)


class SkillRequirements(BaseModel):
    context: list[str] = Field(default_factory=list)
    optional_context: list[str] = Field(default_factory=list)


class SkillSpec(BaseModel):
    kind: Literal["skill"] = "skill"
    entrypoint: str = "SKILL.md"
    artifacts: list[str] = Field(default_factory=lambda: ["SKILL.md"])
    provides: list[str] = Field(default_factory=list)
    requirements: SkillRequirements = Field(default_factory=SkillRequirements)
    routing_hints: dict[str, list[str]] = Field(default_factory=dict)
    side_effects: Literal["none"] = "none"
    requested_tools: list[str] = Field(default_factory=list)


class ToolSpec(BaseModel):
    kind: Literal["tool"] = "tool"
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    side_effects: Literal["none", "read-only", "local_write", "remote_write", "external_effect"] = (
        "read-only"
    )


class ResourceSpec(BaseModel):
    kind: Literal["resource"] = "resource"
    media_type: str = "text/markdown"
    mutability: Literal["immutable", "mutable"] = "immutable"
    access_mode: Literal["read"] = "read"


class WorkflowSpec(BaseModel):
    kind: Literal["workflow"] = "workflow"
    execution_mode: Literal["client-orchestrated"] = "client-orchestrated"
    resume_supported: bool = False


class ServiceSpec(BaseModel):
    kind: Literal["service"] = "service"
    operation: str
    execution_mode: Literal["remote", "read_only"] = "remote"
    side_effects: str = "read-only"


class AgentSpec(BaseModel):
    kind: Literal["agent"] = "agent"
    provides: list[str] = Field(default_factory=list)
    execution_semantics: Literal["delegated-autonomy"] = "delegated-autonomy"


CapabilitySpec = Annotated[
    SkillSpec | ToolSpec | ResourceSpec | WorkflowSpec | ServiceSpec | AgentSpec,
    Field(discriminator="kind"),
]


class Capability(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    kind: CapabilityKind
    created_at: datetime
    owner_scope: OwnerScope = "global"
    owner_scope_id: str | None = None  # org/workspace/principal id when scope is not global

    @model_validator(mode="after")
    def _scope_id_required(self) -> "Capability":
        """A scoped capability without its scope id would match every request
        that also lacks one (None == None) — reject it at the boundary (§27)."""
        if self.owner_scope != "global" and self.owner_scope_id is None:
            raise ValueError(f"owner_scope={self.owner_scope!r} requires owner_scope_id")
        return self


class CapabilityVersion(BaseModel):
    model_config = {"frozen": True}

    capability_id: str
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    kind: CapabilityKind
    schema_version: int = 1
    content_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
    display_name: str = ""
    description: str = ""
    facets: dict[str, list[str]] = Field(default_factory=dict)
    compatibility: Compatibility | None = None
    spec: CapabilitySpec

    @field_validator("facets")
    @classmethod
    def _check_facets(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        validate_facets(v)
        return v


class CapabilityRelease(BaseModel):
    capability_id: str
    version: str
    channel: ReleaseChannel
    status: ReleaseStatus = "active"
    promoted_at: datetime | None = None
    approved_by: str | None = None
    policy_snapshot_id: str | None = None


class CapabilityBinding(BaseModel):
    binding_id: str
    capability_id: str
    version: str
    binding_type: str
    visibility_scope: str = "public-production"
    config: dict[str, Any] = Field(default_factory=dict)


class ArtifactFile(BaseModel):
    """One file inside an immutable skill package (plan §20)."""

    model_config = {"frozen": True}

    path: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=0)


class CapabilityArtifact(BaseModel):
    """Immutable content-package metadata for one version (plan §20)."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    manifest: dict[str, Any] = Field(default_factory=dict)
    files: list[ArtifactFile] = Field(default_factory=list)


class CapabilityMetrics(BaseModel):
    """Derived point-in-time snapshot (plan §6.5). Never mutates a CapabilityVersion."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    computed_at: datetime
    usage_count: int = 0
    verified_success_rate: float | None = None
    human_override_rate: float | None = None
    avg_context_tokens: float | None = None
    avg_total_tokens: float | None = None
    avg_latency_ms: float | None = None
    error_rate: float | None = None
    regression_rate: float | None = None


class TaskContext(BaseModel):
    language: str | None = None
    frameworks: list[str] = Field(default_factory=list)
    phase: str | None = None
    file_hints: list[str] = Field(default_factory=list)
    repository_summary: str = Field(default="", max_length=2000)


def _default_allowed_kinds() -> list[CapabilityKind]:
    return ["skill"]


class SearchCapabilitiesQuery(BaseModel):
    """Typed search query (plan §11.2)."""

    model_config = {"frozen": True}

    query: str = Field(default="", max_length=2000)
    kinds: list[CapabilityKind] = Field(default_factory=_default_allowed_kinds)
    domains: list[str] = Field(default_factory=list)
    limit: int = Field(default=20, ge=1, le=100)


class RouteCapabilitiesCommand(BaseModel):
    task_text: str = Field(min_length=1, max_length=8000)
    context: TaskContext = Field(default_factory=TaskContext)
    max_items: int = Field(default=5, ge=0, le=5)
    max_context_tokens: int = Field(default=6000, ge=0)
    allowed_kinds: list[CapabilityKind] = Field(default_factory=_default_allowed_kinds)


class BundleItem(BaseModel):
    model_config = {"frozen": True}

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind
    role: Literal["primary", "check", "support"] = "primary"
    load_mode: Literal["lazy"] = "lazy"
    reason_code: str = ""


RelationType = Literal["requires", "conflicts_with", "checks"]


class CapabilityRelation(BaseModel):
    """Directed relation between capabilities (plan §18; V1 subset only).

    Version constraints are exact-match strings in V1 (``None`` = any version).
    """

    model_config = {"frozen": True}

    relation_id: str
    source_capability_id: str
    source_version_constraint: str | None = None
    target_capability_id: str
    target_version_constraint: str | None = None
    relation: RelationType
    metadata: dict[str, Any] = Field(default_factory=dict)


def version_matches(version: str, constraint: str | None) -> bool:
    """V1 constraint semantics: ``None`` = any version, else exact match."""
    return constraint is None or constraint == version


class BundleBudget(BaseModel):
    """Budget the composer enforced for one bundle (plan §19.1)."""

    model_config = {"frozen": True}

    max_items: int = Field(default=5, ge=0, le=5)
    max_context_tokens: int = Field(default=6000, ge=0)


class CapabilityBundle(BaseModel):
    model_config = {"frozen": True}

    bundle_id: str
    route_run_id: str
    created_at: datetime
    items: list[BundleItem] = Field(max_length=5)
    execution_order: list[str] = Field(default_factory=list)
    budget: BundleBudget | None = None
    policy_snapshot_id: str | None = None


class OutcomeVerdict(BaseModel):
    model_config = {"frozen": True}

    source: VerdictSource
    status: Literal["success", "failure", "unknown"]
    confidence: Literal["high", "medium", "low"] = "low"


class OutcomeEvidence(BaseModel):
    model_config = {"frozen": True}

    outcome_id: str
    route_run_id: str
    bundle_id: str
    received_at: datetime
    verdicts: list[OutcomeVerdict] = Field(min_length=1)
    tests_before: dict[str, Any] = Field(default_factory=dict)
    tests_after: dict[str, Any] = Field(default_factory=dict)
    latency_ms: int | None = None
