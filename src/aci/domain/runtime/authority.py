"""Authority contracts (harness.md §13): policy, grants, approvals, envelopes.

Authority decides what is permitted (AuthorityManager); WorkspaceManager /
ToolRuntime / SandboxBackend enforce that decision (INV-04). A capability
manifest may *declare* required permissions but can never grant them (§11.6).
"""

import posixpath
from collections.abc import Sequence
from datetime import UTC, datetime
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


def has_traversal(path: str) -> bool:
    return ".." in path.split("/")


def _normalize_scope(scope: str) -> str | None:
    """``""``/``"/"`` → ``""`` (explicit match-all); a ``..`` segment → None
    (admits nothing); otherwise the normpath (``./out/`` → ``out``, ``./`` → ``.``)."""
    if scope.rstrip("/") == "":
        return ""
    if has_traversal(scope):
        return None
    return posixpath.normpath(scope)


def path_within_scopes(path: str, scopes: Sequence[str]) -> bool:
    """§13.4 segment-aware prefix match: ``/repo/src`` is within ``/repo``,
    ``/repoother`` is not. ``"."`` covers every relative path (the workspace
    root, as WorkspaceManager resolves it) but never an absolute one; ``""``
    and ``"/"`` match everything only when explicitly present; a path or scope
    with a ``..`` segment never matches."""
    if has_traversal(path):
        return False
    norm = posixpath.normpath(path)
    for scope in scopes:
        trimmed = _normalize_scope(scope)
        if trimmed is None:
            continue
        if trimmed == "":
            return True
        if trimmed == ".":
            if not norm.startswith("/"):
                return True
            continue
        if norm == trimmed or norm.startswith(trimmed + "/"):
            return True
    return False


def scope_within_scopes(scope: str, scopes: Sequence[str]) -> bool:
    """True when every path ``scope`` admits is admitted by ``scopes``."""
    trimmed = _normalize_scope(scope)
    if trimmed is None:
        return False
    if trimmed == "":
        return any(_normalize_scope(other) == "" for other in scopes)
    return path_within_scopes(trimmed, scopes)


def prefix_within_prefixes(prefix: str, allowed: Sequence[str]) -> bool:
    """Token-wise process scope (§13.4): ``git status`` is within ``git``,
    ``git-evil`` is not; an empty prefix on either side admits nothing."""
    tokens = prefix.split()
    if not tokens:
        return False
    for candidate in allowed:
        wanted = candidate.split()
        if wanted and tokens[: len(wanted)] == wanted:
            return True
    return False


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def min_expiry(*expiries: datetime | None) -> datetime | None:
    """Earliest of the given expiries; None means "never" and never wins."""
    earliest: datetime | None = None
    for candidate in expiries:
        if candidate is None:
            continue
        if earliest is None or _aware(candidate) < _aware(earliest):
            earliest = candidate
    return earliest


def narrow_grants(grants: GrantEnvelope, bound: GrantEnvelope) -> GrantEnvelope:
    """``grants`` ∩ ``bound`` (INV-02): keep each grant scope that lies within
    some bound scope (filesystem prefix-scoped, process token-scoped, hosts and
    secret refs exact) and the earlier expiry. Scope strings are kept as given."""
    return GrantEnvelope(
        filesystem=FilesystemScope(
            read=[
                p for p in grants.filesystem.read if scope_within_scopes(p, bound.filesystem.read)
            ],
            write=[
                p for p in grants.filesystem.write if scope_within_scopes(p, bound.filesystem.write)
            ],
        ),
        network=NetworkScope(
            enabled=grants.network.enabled and bound.network.enabled,
            allowed_hosts=[
                h for h in grants.network.allowed_hosts if h in bound.network.allowed_hosts
            ],
        ),
        process=ProcessScope(
            allowed_prefixes=[
                p
                for p in grants.process.allowed_prefixes
                if prefix_within_prefixes(p, bound.process.allowed_prefixes)
            ],
        ),
        secrets_allowed_refs=[
            s for s in grants.secrets_allowed_refs if s in bound.secrets_allowed_refs
        ],
        expires_at=min_expiry(grants.expires_at, bound.expires_at),
    )


def intersect_grants(
    parent: GrantEnvelope,
    requested: GrantEnvelope,
    policy_ceiling: GrantEnvelope | None = None,
) -> GrantEnvelope:
    """Child grants = parent ∩ requested ∩ policy ceiling (§13.8, INV-02:
    never wider than either parent or the policy maximum)."""
    effective = narrow_grants(requested, parent)
    if policy_ceiling is None:
        return effective
    return narrow_grants(effective, policy_ceiling)
