"""AuthorityManager (harness.md §13): policy evaluation, grant ledger, approvals, envelopes.

Authority decides what is permitted; Workspace/ToolRuntime/Sandbox enforce (INV-04).
Approvals extend a grant within policy but never beyond it (INV-02); a changed
operation invalidates old approvals (§27.4).
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorityDecision,
    AuthorityDecisionKind,
    AuthorityRequirement,
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
    ProcessScope,
)
from aci.domain.runtime.tools import SideEffectClass


class AuthorityPolicy(BaseModel):
    """Policy config (§13.2): what is permitted in principle.

    `default` is the decision when a requirement exceeds current grants but stays
    within `ceiling` (the policy maximum — INV-02). `deny_classes` overrides
    everything; `approval_classes` always requires approval.
    """

    model_config = {"frozen": True}

    default: AuthorityDecisionKind = AuthorityDecisionKind.REQUIRE_APPROVAL
    approval_classes: set[SideEffectClass] = Field(default_factory=set)
    deny_classes: set[SideEffectClass] = Field(default_factory=set)
    ceiling: GrantEnvelope = Field(default_factory=GrantEnvelope)


def _now() -> datetime:
    return datetime.now(UTC)


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    if expires_at is None:
        return False
    exp = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
    return now >= exp


def _has_traversal(path: str) -> bool:
    return ".." in path.split("/")


def _within_scope(path: str, scopes: Sequence[str]) -> bool:
    """Segment-aware prefix match: `/repo/src` is within `/repo`, `/repoother` is not."""
    for scope in scopes:
        trimmed = scope.rstrip("/")
        if trimmed in ("", "/"):
            return True
        if path == trimmed or path.startswith(trimmed + "/"):
            return True
    return False


def _prefix_allowed(prefix: str, allowed: Sequence[str]) -> bool:
    """`git status` matches allowed prefix `git` (token-wise extension)."""
    for candidate in allowed:
        trimmed = candidate.rstrip()
        if prefix == trimmed or prefix.startswith(trimmed + " "):
            return True
    return False


class PolicyEvaluator:
    """Evaluates an AuthorityRequirement against grants + policy (§13.3, §58)."""

    def evaluate(
        self,
        requirement: AuthorityRequirement,
        grants: GrantEnvelope,
        policy: AuthorityPolicy,
        side_effect_class: SideEffectClass | None = None,
    ) -> AuthorityDecision:
        if side_effect_class is not None and side_effect_class in policy.deny_classes:
            return AuthorityDecision(
                kind=AuthorityDecisionKind.DENY,
                reason=f"side-effect class {side_effect_class} is denied by policy",
            )
        if side_effect_class is not None and side_effect_class in policy.approval_classes:
            return AuthorityDecision(
                kind=AuthorityDecisionKind.REQUIRE_APPROVAL,
                reason=f"side-effect class {side_effect_class} requires approval",
                requested_delta=requirement,
            )

        delta = AuthorityRequirement()
        for path in requirement.filesystem_read:
            decision = self._check_path(
                path, grants.filesystem.read, policy.ceiling.filesystem.read
            )
            if decision == "DENY":
                return AuthorityDecision(
                    kind=AuthorityDecisionKind.DENY, reason=f"read path {path!r} denied by policy"
                )
            if decision == "CEILING":
                delta.filesystem_read.append(path)
        for path in requirement.filesystem_write:
            decision = self._check_path(
                path, grants.filesystem.write, policy.ceiling.filesystem.write
            )
            if decision == "DENY":
                return AuthorityDecision(
                    kind=AuthorityDecisionKind.DENY, reason=f"write path {path!r} denied by policy"
                )
            if decision == "CEILING":
                delta.filesystem_write.append(path)
        for host in requirement.network_hosts:
            if grants.network.enabled and host in grants.network.allowed_hosts:
                continue
            if policy.ceiling.network.enabled and host in policy.ceiling.network.allowed_hosts:
                delta.network_hosts.append(host)
            else:
                return AuthorityDecision(
                    kind=AuthorityDecisionKind.DENY,
                    reason=f"network host {host!r} denied by policy",
                )
        for prefix in requirement.process_prefixes:
            if _prefix_allowed(prefix, grants.process.allowed_prefixes):
                continue
            if _prefix_allowed(prefix, policy.ceiling.process.allowed_prefixes):
                delta.process_prefixes.append(prefix)
            else:
                return AuthorityDecision(
                    kind=AuthorityDecisionKind.DENY,
                    reason=f"process prefix {prefix!r} denied by policy",
                )
        for ref in requirement.secret_refs:
            if ref in grants.secrets_allowed_refs:
                continue
            if ref in policy.ceiling.secrets_allowed_refs:
                delta.secret_refs.append(ref)
            else:
                return AuthorityDecision(
                    kind=AuthorityDecisionKind.DENY, reason=f"secret ref {ref!r} denied by policy"
                )

        if (
            delta.filesystem_read
            or delta.filesystem_write
            or delta.network_hosts
            or delta.process_prefixes
            or delta.secret_refs
        ):
            return AuthorityDecision(
                kind=policy.default,
                reason="requirement exceeds current grants but is within policy",
                requested_delta=delta,
            )
        return AuthorityDecision(kind=AuthorityDecisionKind.ALLOW, reason="within current grants")

    @staticmethod
    def _check_path(path: str, granted: Sequence[str], ceiling: Sequence[str]) -> str:
        if _has_traversal(path):
            return "DENY"
        if _within_scope(path, granted):
            return "GRANTED"
        if _within_scope(path, ceiling):
            return "CEILING"
        return "DENY"


def _union(base: GrantEnvelope, delta: AuthorityRequirement) -> GrantEnvelope:
    return GrantEnvelope(
        filesystem=FilesystemScope(
            read=sorted(set(base.filesystem.read) | set(delta.filesystem_read)),
            write=sorted(set(base.filesystem.write) | set(delta.filesystem_write)),
        ),
        network=NetworkScope(
            enabled=base.network.enabled or bool(delta.network_hosts),
            allowed_hosts=sorted(set(base.network.allowed_hosts) | set(delta.network_hosts)),
        ),
        process=ProcessScope(
            allowed_prefixes=sorted(
                set(base.process.allowed_prefixes) | set(delta.process_prefixes)
            ),
        ),
        secrets_allowed_refs=sorted(set(base.secrets_allowed_refs) | set(delta.secret_refs)),
        expires_at=base.expires_at,
    )


def _clip_to_ceiling(grants: GrantEnvelope, ceiling: GrantEnvelope) -> GrantEnvelope:
    """INV-02: extended grants never exceed policy maximums.

    Filesystem scopes are prefix-scoped (a path under a ceiling scope is
    within the ceiling); hosts/prefixes/secret refs are exact-match.
    """
    return GrantEnvelope(
        filesystem=FilesystemScope(
            read=[p for p in grants.filesystem.read if _within_scope(p, ceiling.filesystem.read)],
            write=[
                p for p in grants.filesystem.write if _within_scope(p, ceiling.filesystem.write)
            ],
        ),
        network=NetworkScope(
            enabled=grants.network.enabled and ceiling.network.enabled,
            allowed_hosts=[
                h for h in grants.network.allowed_hosts if h in ceiling.network.allowed_hosts
            ],
        ),
        process=ProcessScope(
            allowed_prefixes=[
                p
                for p in grants.process.allowed_prefixes
                if _prefix_allowed(p, ceiling.process.allowed_prefixes)
            ],
        ),
        secrets_allowed_refs=[
            s for s in grants.secrets_allowed_refs if s in ceiling.secrets_allowed_refs
        ],
        expires_at=grants.expires_at,
    )


class GrantLedger:
    """Mutable ledger of the run's current GrantEnvelope + approval history (§13.6)."""

    def __init__(
        self, policy: AuthorityPolicy, initial_grants: GrantEnvelope | None = None
    ) -> None:
        self._policy = policy
        self._evaluator = PolicyEvaluator()
        if initial_grants is None:
            self._grants = GrantEnvelope()
        else:
            self._grants = _clip_to_ceiling(initial_grants, policy.ceiling)
        self._requests: dict[str, ApprovalRequest] = {}
        self._applied: set[str] = set()

    @property
    def grants(self) -> GrantEnvelope:
        return self._grants

    def evaluate(
        self,
        requirement: AuthorityRequirement,
        side_effect_class: SideEffectClass | None = None,
    ) -> AuthorityDecision:
        return self._evaluator.evaluate(requirement, self._grants, self._policy, side_effect_class)

    def register_request(self, request: ApprovalRequest) -> None:
        self._requests[request.approval_id] = request

    def apply_approval(self, decision: ApprovalDecision, request: ApprovalRequest) -> GrantEnvelope:
        stored = self._requests.get(request.approval_id)
        if stored is None:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED,
                f"approval request {request.approval_id} is not live",
            )
        if decision.approval_id != request.approval_id:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED,
                "approval decision does not bind to the request",
            )
        if request.operation_hash != stored.operation_hash:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED,
                "operation changed since approval was requested (§27.4)",
            )
        if request.approval_id in self._applied:
            raise DomainError(
                ErrorCode.APPROVAL_REPLAY_INVALID,
                f"approval {request.approval_id} was already applied",
            )
        if not decision.approved:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED, f"approval {request.approval_id} was not granted"
            )
        if _is_expired(request.expires_at, _now()) or _is_expired(stored.expires_at, _now()):
            raise DomainError(
                ErrorCode.AUTHORITY_EXPIRED,
                f"approval {request.approval_id} expired before it was applied",
            )
        extended = _union(self._grants, request.requested_delta)
        # INV-02: the extended grant never exceeds policy maximums.
        self._grants = _clip_to_ceiling(extended, self._policy.ceiling)
        self._applied.add(request.approval_id)
        return self._grants


class ExecutionEnvelopeBuilder:
    """Projects granted scopes into the restrictions for ONE operation (§13.2)."""

    def build(
        self,
        run_id: str,
        workspace_id: str,
        decision: AuthorityDecision,
        grants: GrantEnvelope,
    ) -> ExecutionEnvelope:
        if decision.kind == AuthorityDecisionKind.DENY:
            raise DomainError(ErrorCode.PERMISSION_DENIED, decision.reason)
        return ExecutionEnvelope(
            run_id=run_id,
            workspace_id=workspace_id,
            filesystem=FilesystemScope(
                read=list(grants.filesystem.read),
                write=list(grants.filesystem.write),
            ),
            network=NetworkScope(
                enabled=grants.network.enabled,
                allowed_hosts=list(grants.network.allowed_hosts),
            ),
            process=ProcessScope(allowed_prefixes=list(grants.process.allowed_prefixes)),
            expires_at=grants.expires_at,
        )


class AuthorityManager:
    """Facade over PolicyEvaluator + GrantLedger + ExecutionEnvelopeBuilder (§13.1)."""

    def __init__(
        self,
        policy: AuthorityPolicy,
        initial_grants: GrantEnvelope | None = None,
        approval_ttl_seconds: float = 300.0,
    ) -> None:
        self._policy = policy
        self._evaluator = PolicyEvaluator()
        self._ledger = GrantLedger(policy, initial_grants)
        self._builder = ExecutionEnvelopeBuilder()
        self._approval_ttl = timedelta(seconds=approval_ttl_seconds)

    @property
    def grants(self) -> GrantEnvelope:
        return self._ledger.grants

    def evaluate(
        self,
        requirement: AuthorityRequirement,
        side_effect_class: SideEffectClass | None = None,
    ) -> AuthorityDecision:
        return self._ledger.evaluate(requirement, side_effect_class)

    def make_approval_request(
        self,
        run_id: str,
        requirement: AuthorityRequirement,
        reason: str,
        operation_hash: str,
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            approval_id=uuid.uuid4().hex,
            run_id=run_id,
            operation_hash=operation_hash,
            reason=reason,
            requested_delta=requirement,
            expires_at=_now() + self._approval_ttl,
        )
        self._ledger.register_request(request)
        return request

    def apply_approval(self, decision: ApprovalDecision, request: ApprovalRequest) -> GrantEnvelope:
        return self._ledger.apply_approval(decision, request)

    def build_execution_envelope(
        self,
        run_id: str,
        workspace_id: str,
        decision: AuthorityDecision,
        grants: GrantEnvelope | None = None,
    ) -> ExecutionEnvelope:
        effective = self._ledger.grants if grants is None else grants
        return self._builder.build(run_id, workspace_id, decision, effective)


class ApprovalCoordinator:
    """§13.1/§13.6 — tracks pending approval requests and applies decisions.

    An approval is bound to (run_id, operation_hash, workspace_id, expiry);
    a decision for a DIFFERENT operation or workspace never satisfies a
    pending request (§27.4), and an expired request is refused."""

    def __init__(self, ledger: GrantLedger) -> None:
        self._ledger = ledger
        self._pending: dict[str, ApprovalRequest] = {}

    def register(self, request: ApprovalRequest) -> None:
        self._pending[request.approval_id] = request

    def pending_for_run(self, run_id: str) -> list[ApprovalRequest]:
        return [r for r in self._pending.values() if r.run_id == run_id]

    def apply(
        self,
        decision: ApprovalDecision,
        *,
        operation_hash: str,
        workspace_id: str,
        now: datetime | None = None,
    ) -> GrantEnvelope:
        """Apply a decision ONLY if it matches the pending request's bound
        operation + workspace and is not expired; otherwise refuse."""
        request = self._pending.get(decision.approval_id)
        if request is None:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED,
                f"no pending approval {decision.approval_id}",
            )
        if request.operation_hash != operation_hash:
            raise DomainError(
                ErrorCode.APPROVAL_REPLAY_INVALID,
                "operation changed since approval was requested — re-approval required",
            )
        if request.workspace_id != workspace_id:
            raise DomainError(
                ErrorCode.APPROVAL_REPLAY_INVALID,
                "workspace changed since approval was requested",
            )
        current = now or datetime.now(UTC)
        if request.expires_at is not None and current >= request.expires_at:
            raise DomainError(ErrorCode.AUTHORITY_EXPIRED, "approval expired")
        grants = self._ledger.apply_approval(decision, request)
        self._pending.pop(decision.approval_id, None)
        return grants


__all__: list[str] = [
    "ApprovalCoordinator",
    "AuthorityManager",
    "AuthorityPolicy",
    "ExecutionEnvelopeBuilder",
    "GrantLedger",
    "PolicyEvaluator",
]
