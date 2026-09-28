"""Policy & eligibility contracts (plan §§15, 27, 48; ADR-009).

Eligibility runs BEFORE retrieval and rerank; the reranker must never rescue
ineligible content. Every exclusion carries a stable reason (§45) so route
traces can explain each drop (§14).
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from aci.domain.capability.models import (
    CapabilityKind,
    Compatibility,
    OwnerScope,
    ReleaseChannel,
    ReleaseStatus,
    TaskContext,
)

TrustTier = Literal["untrusted", "standard", "verified"]

_TRUST_RANK: dict[str, int] = {"untrusted": 0, "standard": 1, "verified": 2}


class ClientDescriptor(BaseModel):
    model_config = {"frozen": True}

    type: str = Field(min_length=1)  # client type name, e.g. coding agent / web app
    version: str | None = None
    supported_features: list[str] = Field(default_factory=list)


class ScopeContext(BaseModel):
    model_config = {"frozen": True}

    principal_id: str = Field(min_length=1)
    organization_id: str | None = None
    workspace_id: str | None = None


class RoutingRequestContext(BaseModel):
    """Eligibility input context (§11.1 RequestContext arrives with REST).

    Carries the client, the requesting scope, and the normalized task context
    so §15 exclusions (unsupported language/framework) can run before retrieval.
    """

    model_config = {"frozen": True}

    client: ClientDescriptor
    scope: ScopeContext
    task: TaskContext = Field(default_factory=TaskContext)
    #: §27 canary split key — the request's stable id (deterministic
    #: hash input; same request → same canary decision, always).
    request_id: str = ""


class ProtocolDescriptor(BaseModel):
    model_config = {"frozen": True}

    type: str = Field(min_length=1)  # "rest", "mcp", ...
    version: str | None = None


class RequestContext(BaseModel):
    """Request envelope identity (plan §11.1). Transport adapters build this;
    application services never trust it for authorization beyond scope fields.
    """

    model_config = {"frozen": True}

    request_id: str = Field(min_length=1)
    trace_id: str = Field(min_length=1)
    principal_id: str = Field(min_length=1)
    organization_id: str | None = None
    workspace_id: str | None = None
    client: ClientDescriptor
    protocol: ProtocolDescriptor
    deadline: datetime | None = None
    locale: str | None = None

    def to_routing_context(self, task: TaskContext) -> RoutingRequestContext:
        """Project the envelope onto the eligibility/routing context."""
        return RoutingRequestContext(
            client=self.client,
            scope=self.scope(),
            task=task,
            request_id=self.request_id,
        )

    def scope(self) -> ScopeContext:
        return ScopeContext(
            principal_id=self.principal_id,
            organization_id=self.organization_id,
            workspace_id=self.workspace_id,
        )


class EligibleCandidate(BaseModel):
    """A version annotated with everything eligibility needs to decide."""

    model_config = {"frozen": True}

    capability_id: str
    version: str
    digest: str
    kind: CapabilityKind
    facets: dict[str, list[str]] = Field(default_factory=dict)
    channel: ReleaseChannel
    status: ReleaseStatus
    canary_percent: int | None = None
    trust_tier: TrustTier = "untrusted"
    license_blocked: bool = False
    owner_scope: OwnerScope = "global"
    owner_scope_id: str | None = None
    compatibility: Compatibility | None = None


class Exclusion(BaseModel):
    model_config = {"frozen": True}

    capability_id: str
    version: str
    reason: str  # stable ErrorCode value (§45)
    detail: str = ""


class EligibilityDecision(BaseModel):
    """Trace data for the eligibility stage (§14): kept + why others dropped."""

    model_config = {"frozen": True}

    kept: list[EligibleCandidate] = Field(default_factory=list)
    excluded: list[Exclusion] = Field(default_factory=list)


class PolicyRules(BaseModel):
    """Snapshot-able eligibility configuration (§46: cache by snapshot id)."""

    model_config = {"frozen": True}

    required_channel: ReleaseChannel = "production"
    min_trust_tier: TrustTier = "untrusted"
    denied_capability_ids: frozenset[str] = Field(default_factory=frozenset)

    def trust_ok(self, tier: str) -> bool:
        return _TRUST_RANK.get(tier, 0) >= _TRUST_RANK[self.min_trust_tier]


class PolicySnapshot(BaseModel):
    model_config = {"frozen": True}

    snapshot_id: str
    created_at: datetime
    rules: PolicyRules
