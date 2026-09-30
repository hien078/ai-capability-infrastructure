"""AuthorityManager (harness.md §13): policy evaluation, grant ledger, approvals, envelopes.

Authority decides what is permitted; Workspace/ToolRuntime/Sandbox enforce (INV-04).
Approvals extend a grant within policy but never beyond it (INV-02); a changed
operation invalidates old approvals (§27.4).
"""

import posixpath
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

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
    has_traversal,
    narrow_grants,
    path_within_scopes,
    prefix_within_prefixes,
)
from aci.domain.runtime.tools import SideEffectClass, ToolSpec


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


_has_traversal = has_traversal
_within_scope = path_within_scopes
_prefix_allowed = prefix_within_prefixes


def _is_empty(requirement: AuthorityRequirement) -> bool:
    return not (
        requirement.filesystem_read
        or requirement.filesystem_write
        or requirement.network_hosts
        or requirement.process_prefixes
        or requirement.secret_refs
    )


class PolicyEvaluator:
    """Evaluates an AuthorityRequirement against grants + policy (§13.3, §58).

    Scope checks run first, so anything outside the policy ceiling is DENY
    regardless of side-effect class (INV-02); an approval class then forces
    REQUIRE_APPROVAL even for fully granted operations."""

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
        delta = self._delta(requirement, grants, policy.ceiling)
        if isinstance(delta, str):
            return AuthorityDecision(kind=AuthorityDecisionKind.DENY, reason=delta)
        if side_effect_class is not None and side_effect_class in policy.approval_classes:
            return AuthorityDecision(
                kind=AuthorityDecisionKind.REQUIRE_APPROVAL,
                reason=f"side-effect class {side_effect_class} requires approval",
                requested_delta=requirement,
            )
        if not _is_empty(delta):
            return AuthorityDecision(
                kind=policy.default,
                reason="requirement exceeds current grants but is within policy",
                requested_delta=delta,
            )
        return AuthorityDecision(kind=AuthorityDecisionKind.ALLOW, reason="within current grants")

    def _delta(
        self, requirement: AuthorityRequirement, grants: GrantEnvelope, ceiling: GrantEnvelope
    ) -> AuthorityRequirement | str:
        """The part of ``requirement`` beyond current grants but within the
        ceiling, or the denial reason for the first item outside the ceiling."""
        delta = AuthorityRequirement()
        for path in requirement.filesystem_read:
            decision = self._check_path(path, grants.filesystem.read, ceiling.filesystem.read)
            if decision == "DENY":
                return f"read path {path!r} denied by policy"
            if decision == "CEILING":
                delta.filesystem_read.append(path)
        for path in requirement.filesystem_write:
            decision = self._check_path(path, grants.filesystem.write, ceiling.filesystem.write)
            if decision == "DENY":
                return f"write path {path!r} denied by policy"
            if decision == "CEILING":
                delta.filesystem_write.append(path)
        for host in requirement.network_hosts:
            if grants.network.enabled and host in grants.network.allowed_hosts:
                continue
            if ceiling.network.enabled and host in ceiling.network.allowed_hosts:
                delta.network_hosts.append(host)
            else:
                return f"network host {host!r} denied by policy"
        for prefix in requirement.process_prefixes:
            if prefix_within_prefixes(prefix, grants.process.allowed_prefixes):
                continue
            if prefix_within_prefixes(prefix, ceiling.process.allowed_prefixes):
                delta.process_prefixes.append(prefix)
            else:
                return f"process prefix {prefix!r} denied by policy"
        for ref in requirement.secret_refs:
            if ref in grants.secrets_allowed_refs:
                continue
            if ref in ceiling.secrets_allowed_refs:
                delta.secret_refs.append(ref)
            else:
                return f"secret ref {ref!r} denied by policy"
        return delta

    @staticmethod
    def _check_path(path: str, granted: Sequence[str], ceiling: Sequence[str]) -> str:
        if has_traversal(path):
            return "DENY"
        if path_within_scopes(path, granted):
            return "GRANTED"
        if path_within_scopes(path, ceiling):
            return "CEILING"
        return "DENY"


def derive_requirement(tool: ToolSpec, args: dict[str, Any]) -> AuthorityRequirement:
    """§58 — what ONE call touches, from the tool's declared argument roles.

    Paths are normalized so ``./out//a`` and ``out/a`` scope identically; a
    path with a ``..`` segment is kept raw so the evaluator denies it. A
    declared argument holding the wrong type is an invalid argument, never
    silently skipped (skipping would under-report the requirement)."""
    declared = tool.authority_requirements
    return AuthorityRequirement(
        filesystem_read=[_norm_path(p) for p in _strings(tool, args, declared.read_path_args)],
        filesystem_write=[_norm_path(p) for p in _strings(tool, args, declared.write_path_args)],
        process_prefixes=_strings(tool, args, declared.command_args, join_lists=True),
        network_hosts=_strings(tool, args, declared.host_args),
    )


def grants_from_envelope(envelope: ExecutionEnvelope) -> GrantEnvelope:
    """The grants an execution envelope carries, for per-call evaluation."""
    return GrantEnvelope(
        filesystem=envelope.filesystem,
        network=envelope.network,
        process=envelope.process,
        expires_at=envelope.expires_at,
    )


def _strings(
    tool: ToolSpec, args: dict[str, Any], names: Sequence[str], *, join_lists: bool = False
) -> list[str]:
    values: list[str] = []
    for name in names:
        if name not in args:
            continue
        value = args[name]
        if join_lists and isinstance(value, list) and all(isinstance(v, str) for v in value):
            value = " ".join(value)
        if not isinstance(value, str):
            raise DomainError(
                ErrorCode.TOOL_ARGUMENT_INVALID,
                f"{tool.tool_id}: authority argument {name!r} must be a string",
            )
        values.append(value)
    return values


def _norm_path(path: str) -> str:
    return path if has_traversal(path) else posixpath.normpath(path)


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
    """INV-02: extended grants never exceed policy maximums — in scope or in time."""
    return narrow_grants(grants, ceiling)


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
        """§27.4: the approval binds to the REGISTERED request (run, operation
        hash, delta, workspace, expiry) — only that delta is ever applied."""
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
        if request != stored:
            raise DomainError(
                ErrorCode.APPROVAL_REJECTED,
                "approval request does not match the registered request (§27.4)",
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
        if _is_expired(stored.expires_at, _now()):
            raise DomainError(
                ErrorCode.AUTHORITY_EXPIRED,
                f"approval {request.approval_id} expired before it was applied",
            )
        extended = _union(self._grants, stored.requested_delta)
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
        workspace_id: str = "",
    ) -> ApprovalRequest:
        request = ApprovalRequest(
            approval_id=uuid.uuid4().hex,
            run_id=run_id,
            operation_hash=operation_hash,
            workspace_id=workspace_id,
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
        """Track the request here AND in the ledger, so ``apply`` reaches the
        ledger's own binding checks with the same registered object."""
        self._pending[request.approval_id] = request
        self._ledger.register_request(request)

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
        if _is_expired(request.expires_at, now or _now()):
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
    "derive_requirement",
    "grants_from_envelope",
]
