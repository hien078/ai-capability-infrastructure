"""AuthorityManager/GrantLedger tests (harness.md §13, §58; INV-02, §27.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ApprovalDecision,
    ApprovalRequest,
    AuthorityDecision,
    AuthorityDecisionKind,
    AuthorityRequirement,
    FilesystemScope,
    GrantEnvelope,
    NetworkScope,
    ProcessScope,
    intersect_grants,
)
from aci.runtime.authority import (
    AuthorityManager,
    AuthorityPolicy,
    GrantLedger,
    PolicyEvaluator,
)


def _policy(
    ceiling: GrantEnvelope | None = None,
    default: AuthorityDecisionKind = AuthorityDecisionKind.REQUIRE_APPROVAL,
) -> AuthorityPolicy:
    return AuthorityPolicy(default=default, ceiling=ceiling or GrantEnvelope())


def _grants(
    read: list[str] | None = None,
    write: list[str] | None = None,
    hosts: list[str] | None = None,
    prefixes: list[str] | None = None,
) -> GrantEnvelope:
    return GrantEnvelope(
        filesystem=FilesystemScope(read=read or [], write=write or []),
        network=NetworkScope(enabled=bool(hosts), allowed_hosts=hosts or []),
        process=ProcessScope(allowed_prefixes=prefixes or []),
    )


def _req(**over: object) -> AuthorityRequirement:
    base: dict[str, object] = {
        "filesystem_read": [],
        "filesystem_write": [],
        "network_hosts": [],
        "process_prefixes": [],
        "secret_refs": [],
    }
    base.update(over)
    return AuthorityRequirement(**base)  # type: ignore[arg-type]


class TestPolicyEvaluator:
    def test_child_path_allowed_within_scope(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo"])
        decision = ev.evaluate(_req(filesystem_read=["/repo/src/main.py"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.ALLOW

    def test_sibling_path_requires_approval_within_ceiling(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo/src"])
        ceiling = _grants(read=["/repo"])
        decision = ev.evaluate(
            _req(filesystem_read=["/repo/tests/test_x.py"]), grants, _policy(ceiling)
        )
        assert decision.kind == AuthorityDecisionKind.REQUIRE_APPROVAL
        assert decision.requested_delta is not None
        assert decision.requested_delta.filesystem_read == ["/repo/tests/test_x.py"]

    def test_sibling_path_outside_ceiling_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo/src"])
        decision = ev.evaluate(_req(filesystem_read=["/etc/passwd"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.DENY

    def test_dotdot_rejected_even_within_scope_string(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo"])
        decision = ev.evaluate(_req(filesystem_read=["/repo/../etc/passwd"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.DENY

    def test_absolute_path_outside_grant_and_ceiling_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(write=["/workspace/out"])
        decision = ev.evaluate(_req(filesystem_write=["/etc/shadow"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.DENY

    def test_prefix_match_is_segment_aware(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo"])
        assert (
            ev.evaluate(_req(filesystem_read=["/repoother/file"]), grants, _policy()).kind
            == AuthorityDecisionKind.DENY
        )

    def test_network_disabled_any_host_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants()  # network.enabled False
        decision = ev.evaluate(_req(network_hosts=["pypi.org"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.DENY

    def test_network_host_in_grants_allowed(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(hosts=["pypi.org"])
        decision = ev.evaluate(_req(network_hosts=["pypi.org"]), grants, _policy())
        assert decision.kind == AuthorityDecisionKind.ALLOW

    def test_network_host_in_ceiling_requires_approval(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(hosts=["pypi.org"])
        ceiling = _grants(hosts=["pypi.org", "github.com"])
        decision = ev.evaluate(_req(network_hosts=["github.com"]), grants, _policy(ceiling))
        assert decision.kind == AuthorityDecisionKind.REQUIRE_APPROVAL

    def test_process_prefix_match(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(prefixes=["git"])
        assert (
            ev.evaluate(_req(process_prefixes=["git status"]), grants, _policy()).kind
            == AuthorityDecisionKind.ALLOW
        )
        assert (
            ev.evaluate(_req(process_prefixes=["sudo rm"]), grants, _policy()).kind
            == AuthorityDecisionKind.DENY
        )

    def test_deny_class_overrides_everything(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo"])
        policy = AuthorityPolicy(deny_classes={"DESTRUCTIVE"}, ceiling=_grants(read=["/repo"]))
        decision = ev.evaluate(
            _req(filesystem_read=["/repo/x"]), grants, policy, side_effect_class="DESTRUCTIVE"
        )
        assert decision.kind == AuthorityDecisionKind.DENY

    def test_approval_class_forces_require_approval_even_when_granted(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo"])
        policy = AuthorityPolicy(
            approval_classes={"EXTERNAL_MUTATION"}, ceiling=_grants(read=["/repo"])
        )
        decision = ev.evaluate(
            _req(filesystem_read=["/repo/x"]), grants, policy, side_effect_class="EXTERNAL_MUTATION"
        )
        assert decision.kind == AuthorityDecisionKind.REQUIRE_APPROVAL

    def test_default_allow_policy(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo/src"])
        ceiling = _grants(read=["/repo"])
        decision = ev.evaluate(
            _req(filesystem_read=["/repo/tests/x.py"]),
            grants,
            _policy(ceiling, default=AuthorityDecisionKind.ALLOW_WITH_RESTRICTIONS),
        )
        assert decision.kind == AuthorityDecisionKind.ALLOW_WITH_RESTRICTIONS


class TestGrantLedger:
    def _ledger(self, grants: GrantEnvelope, ceiling: GrantEnvelope) -> GrantLedger:
        return GrantLedger(_policy(ceiling), grants)

    def test_initial_grants_intersected_with_ceiling(self) -> None:
        ledger = self._ledger(_grants(read=["/repo", "/etc"]), _grants(read=["/repo"]))
        assert ledger.grants.filesystem.read == ["/repo"]

    def test_evaluate_delegates_to_policy(self) -> None:
        ledger = self._ledger(_grants(read=["/repo"]), _grants(read=["/repo"]))
        decision = ledger.evaluate(_req(filesystem_read=["/repo/x"]))
        assert decision.kind == AuthorityDecisionKind.ALLOW

    def test_approval_flow_extends_grant_within_policy(self) -> None:
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        req_delta = _req(filesystem_read=["/repo/tests/test_a.py"])
        request = _make_request(ledger, req_delta, "op-hash-1")
        decision = ApprovalDecision(
            approval_id=request.approval_id,
            approved=True,
            decided_by="reviewer",
            decided_at=datetime.now(UTC),
        )
        grants = ledger.apply_approval(decision, request)
        assert "/repo/tests/test_a.py" in grants.filesystem.read
        assert (
            ledger.evaluate(_req(filesystem_read=["/repo/tests/test_a.py"])).kind
            == AuthorityDecisionKind.ALLOW
        )

    def test_approval_replay_rejected(self) -> None:
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        decision = _decision(request.approval_id)
        ledger.apply_approval(decision, request)
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(decision, request)
        assert exc.value.code == ErrorCode.APPROVAL_REPLAY_INVALID

    def test_approval_for_changed_operation_rejected(self) -> None:
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        decision = _decision(request.approval_id)
        tampered = request.model_copy(update={"operation_hash": "op-2"})
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(decision, tampered)
        assert exc.value.code == ErrorCode.APPROVAL_REJECTED

    def test_expired_approval_rejected(self) -> None:
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        stale = request.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
        ledger._requests[stale.approval_id] = stale
        decision = _decision(stale.approval_id)
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(decision, stale)
        assert exc.value.code == ErrorCode.AUTHORITY_EXPIRED

    def test_unapproved_decision_rejected(self) -> None:
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        decision = _decision(request.approval_id, approved=False)
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(decision, request)
        assert exc.value.code == ErrorCode.APPROVAL_REJECTED

    def test_unknown_request_rejected(self) -> None:
        ledger = self._ledger(_grants(read=["/repo"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/x"]), "op-1")
        ghost = request.model_copy(update={"approval_id": "ghost"})
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(_decision("ghost"), ghost)
        assert exc.value.code == ErrorCode.APPROVAL_REJECTED

    def test_extended_grant_never_exceeds_ceiling(self) -> None:
        # A hand-crafted approval requesting a path OUTSIDE the ceiling must
        # still be clipped (INV-02): the granted path is dropped, not kept.
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/etc/passwd"]), "op-1")
        grants = ledger.apply_approval(_decision(request.approval_id), request)
        assert "/etc/passwd" not in grants.filesystem.read
        assert all(p.startswith("/repo") for p in grants.filesystem.read)


def _make_request(
    ledger: GrantLedger, delta: AuthorityRequirement, op_hash: str
) -> ApprovalRequest:
    request = ApprovalRequest(
        approval_id=f"apr-{op_hash}",
        run_id="run-1",
        operation_hash=op_hash,
        reason="test approval",
        requested_delta=delta,
        expires_at=datetime.now(UTC) + timedelta(seconds=60),
    )
    ledger.register_request(request)
    return request


def _decision(approval_id: str, approved: bool = True) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=approval_id,
        approved=approved,
        decided_by="reviewer",
        decided_at=datetime.now(UTC),
    )


class TestAuthorityManager:
    def test_facade_approval_flow(self) -> None:
        manager = AuthorityManager(
            policy=_policy(_grants(read=["/repo"])),
            initial_grants=_grants(read=["/repo/src"]),
        )
        requirement = _req(filesystem_read=["/repo/tests/test_b.py"])
        decision = manager.evaluate(requirement)
        assert decision.kind == AuthorityDecisionKind.REQUIRE_APPROVAL
        request = manager.make_approval_request("run-1", requirement, "need tests", "ophash")
        assert request.run_id == "run-1"
        assert request.operation_hash == "ophash"
        assert request.expires_at is not None
        grants = manager.apply_approval(_decision(request.approval_id), request)
        assert "/repo/tests/test_b.py" in grants.filesystem.read
        envelope = manager.build_execution_envelope("run-1", "ws-1", decision, grants)
        assert envelope.run_id == "run-1"
        assert envelope.workspace_id == "ws-1"
        assert envelope.filesystem.read == grants.filesystem.read

    def test_facade_deny_no_envelope(self) -> None:
        manager = AuthorityManager(policy=_policy(), initial_grants=_grants())
        decision = manager.evaluate(_req(network_hosts=["evil.example"]))
        assert decision.kind == AuthorityDecisionKind.DENY
        deny = AuthorityDecision(kind=AuthorityDecisionKind.DENY, reason="no")
        with pytest.raises(DomainError) as exc:
            manager.build_execution_envelope("run-1", "ws-1", deny)
        assert exc.value.code == ErrorCode.PERMISSION_DENIED


class TestIntersectGrants:
    def test_child_grant_intersection(self) -> None:
        parent = _grants(read=["/repo", "/other"], hosts=["pypi.org", "github.com"])
        child_req = _grants(read=["/repo", "/private"], hosts=["github.com", "evil.com"])
        child = intersect_grants(parent, child_req)
        assert child.filesystem.read == ["/repo"]
        assert child.network.allowed_hosts == ["github.com"]
        assert child.network.enabled

    def test_child_grant_never_wider_than_parent(self) -> None:
        parent = _grants(write=["/repo/src"])
        child = intersect_grants(parent, _grants(write=["/"]))
        assert child.filesystem.write == []
