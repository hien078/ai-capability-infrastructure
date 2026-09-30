"""Authority contracts (harness.md §13): policy, grants, approvals, envelopes.

Authority decides what is permitted (AuthorityManager); WorkspaceManager /
ToolRuntime / SandboxBackend enforce that decision (INV-04). A capability
manifest may *declare* required permissions but can never grant them (§11.6).
"""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class AuthorityDecisionKind(StrEnum):
    ALLOW = "ALLOW"
    ALLOW_WITH_RESTRICTIONS = "ALLOW_WITH_RESTRICTIONS"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"


class FilesystemScope(BaseModel):
    """Path-prefix scopes (§13.4 GrantEnvelope)."""

    model_config = {"frozen": True}

    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class NetworkScope(BaseModel):
    model_config = {"frozen": True}

    enabled: bool = False
    allowed_hosts: list[str] = Field(default_factory=list)


class ProcessScope(BaseModel):
    model_config = {"frozen": True}

    allowed_prefixes: list[str] = Field(default_factory=list)


class GrantEnvelope(BaseModel):
    """What THIS run is currently allowed to do (§13.4). Frozen snapshot.

    Grants are the run-level ceiling intersected with policy; approvals may
    extend a grant within policy but never beyond it (INV-02).
    """

    model_config = {"frozen": True}

    filesystem: FilesystemScope = Field(default_factory=FilesystemScope)
    network: NetworkScope = Field(default_factory=NetworkScope)
    process: ProcessScope = Field(default_factory=ProcessScope)
    secrets_allowed_refs: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None


class AuthorityRequirement(BaseModel):
    """What one operation needs — derived from ToolSpec + validated args (§58)."""

    model_config = {"frozen": True}

    filesystem_read: list[str] = Field(default_factory=list)
    filesystem_write: list[str] = Field(default_factory=list)
    network_hosts: list[str] = Field(default_factory=list)
    process_prefixes: list[str] = Field(default_factory=list)
    secret_refs: list[str] = Field(default_factory=list)


class AuthorityDecision(BaseModel):
    """Result of PolicyEvaluator.evaluate(requirement, grants, policy)."""

    model_config = {"frozen": True}

    kind: AuthorityDecisionKind
    reason: str = Field(min_length=1)
    # Narrowest delta requested when REQUIRE_APPROVAL (§13.5 least privilege).
    requested_delta: AuthorityRequirement | None = None


class ApprovalRequest(BaseModel):
    """§41.3. Binds to run + operation hash + workspace so a changed operation
    or a different workspace invalidates an old approval (§27.4 approval race)."""

    model_config = {"frozen": True}

    approval_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    operation_hash: str = Field(min_length=1)
    workspace_id: str = Field(default="", min_length=0)
    reason: str = Field(min_length=1)
    requested_delta: AuthorityRequirement
    expires_at: datetime | None = None


class ApprovalDecision(BaseModel):
    model_config = {"frozen": True}

    approval_id: str = Field(min_length=1)
    approved: bool
    decided_by: str = Field(min_length=1)
    decided_at: datetime


class ExecutionEnvelope(BaseModel):
    """The exact restrictions enforced for ONE operation (§13.2)."""

    model_config = {"frozen": True}

    run_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    filesystem: FilesystemScope
    network: NetworkScope
    process: ProcessScope
    expires_at: datetime | None = None


def intersect_grants(
    parent: GrantEnvelope,
    requested: GrantEnvelope,
    policy_ceiling: GrantEnvelope | None = None,
) -> GrantEnvelope:
    """Child grants = parent ∩ requested ∩ policy ceiling (§13.8, INV-02:
    never wider than either parent or the policy maximum)."""
    effective = GrantEnvelope(
        filesystem=FilesystemScope(
            read=[p for p in requested.filesystem.read if p in parent.filesystem.read],
            write=[p for p in requested.filesystem.write if p in parent.filesystem.write],
        ),
        network=NetworkScope(
            enabled=parent.network.enabled and requested.network.enabled,
            allowed_hosts=[
                h for h in requested.network.allowed_hosts if h in parent.network.allowed_hosts
            ],
        ),
        process=ProcessScope(
            allowed_prefixes=[
                p
                for p in requested.process.allowed_prefixes
                if p in parent.process.allowed_prefixes
            ],
        ),
        secrets_allowed_refs=[
            s for s in requested.secrets_allowed_refs if s in parent.secrets_allowed_refs
        ],
        expires_at=parent.expires_at,
    )
    if policy_ceiling is None:
        return effective
    return GrantEnvelope(
        filesystem=FilesystemScope(
            read=[p for p in effective.filesystem.read if p in policy_ceiling.filesystem.read],
            write=[p for p in effective.filesystem.write if p in policy_ceiling.filesystem.write],
        ),
        network=NetworkScope(
            enabled=effective.network.enabled and policy_ceiling.network.enabled,
            allowed_hosts=[
                h
                for h in effective.network.allowed_hosts
                if h in policy_ceiling.network.allowed_hosts
            ],
        ),
        process=ProcessScope(
            allowed_prefixes=[
                p
                for p in effective.process.allowed_prefixes
                if p in policy_ceiling.process.allowed_prefixes
            ],
        ),
        secrets_allowed_refs=[
            s for s in effective.secrets_allowed_refs if s in policy_ceiling.secrets_allowed_refs
        ],
        expires_at=effective.expires_at,
    )
