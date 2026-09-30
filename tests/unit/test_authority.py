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
    narrow_grants,
    path_within_scopes,
    prefix_within_prefixes,
    scope_within_scopes,
)
from aci.domain.runtime.tools import ToolAuthority, ToolSpec
from aci.runtime.authority import (
    ApprovalCoordinator,
    AuthorityManager,
    AuthorityPolicy,
    GrantLedger,
    PolicyEvaluator,
    _prefix_allowed,
    derive_requirement,
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

    def test_dot_grant_covers_every_workspace_relative_path(self) -> None:
        """The real run path grants ``read=["."]``: that is the whole workspace
        root (as WorkspaceManager resolves it), not the literal path ``.``."""
        ev = PolicyEvaluator()
        grants = _grants(read=["."], write=["."])
        for path in ("src/app.py", ".", "a", "deep/er/x.txt", "./src/../src/a.py"):
            decision = ev.evaluate(_req(filesystem_read=[path]), grants, _policy())
            expected = AuthorityDecisionKind.DENY if ".." in path else AuthorityDecisionKind.ALLOW
            assert decision.kind == expected, path
        assert (
            ev.evaluate(_req(filesystem_write=["src/app.py"]), grants, _policy()).kind
            == AuthorityDecisionKind.ALLOW
        )

    def test_dot_grant_never_covers_absolute_paths(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(read=["."])
        for path in ("/etc/passwd", "/", "/workspace/src/app.py"):
            decision = ev.evaluate(_req(filesystem_read=[path]), grants, _policy())
            assert decision.kind == AuthorityDecisionKind.DENY, path

    def test_match_all_scope_only_when_explicit(self) -> None:
        for scope in ("", "/"):
            assert path_within_scopes("/etc/passwd", [scope])
            assert path_within_scopes("src/app.py", [scope])
        assert not path_within_scopes("/etc/passwd", ["out", "."])
        assert not path_within_scopes("src/x", ["out"])

    def test_relative_scope_is_normalized_and_segment_aware(self) -> None:
        assert path_within_scopes("src/app.py", ["./src/"])
        assert path_within_scopes("src", ["./src/"])
        assert not path_within_scopes("srcother/app.py", ["src"])
        assert not path_within_scopes("out/x", ["out/.."])  # traversal scope admits nothing

    def test_deny_by_ceiling_wins_over_approval_class(self) -> None:
        """An approval class never turns an out-of-policy operation into an
        approvable one (INV-02: the ceiling is the maximum)."""
        ev = PolicyEvaluator()
        policy = AuthorityPolicy(approval_classes={"DESTRUCTIVE"}, ceiling=_grants(write=["/repo"]))
        outside = ev.evaluate(
            _req(filesystem_write=["/etc/passwd"]),
            _grants(),
            policy,
            side_effect_class="DESTRUCTIVE",
        )
        assert outside.kind == AuthorityDecisionKind.DENY
        inside = ev.evaluate(
            _req(filesystem_write=["/repo/x"]), _grants(), policy, side_effect_class="DESTRUCTIVE"
        )
        assert inside.kind == AuthorityDecisionKind.REQUIRE_APPROVAL


class TestDeriveRequirement:
    def _tool(self) -> ToolSpec:
        return ToolSpec(
            tool_id="t",
            version="1",
            authority_requirements=ToolAuthority(read_path_args=["path"], command_args=["cmd"]),
        )

    def test_paths_normalized_but_traversal_kept_raw(self) -> None:
        req = derive_requirement(self._tool(), {"path": "./src//a/"})
        assert req.filesystem_read == ["src/a"]
        req = derive_requirement(self._tool(), {"path": "src/../etc"})
        assert req.filesystem_read == ["src/../etc"]

    def test_argv_joined_and_matched_token_wise(self) -> None:
        req = derive_requirement(self._tool(), {"cmd": ["python", "-m", "pytest", "-q"]})
        assert req.process_prefixes == ["python -m pytest -q"]
        assert _prefix_allowed(req.process_prefixes[0], ["python -m pytest"])
        assert not _prefix_allowed(req.process_prefixes[0], ["python -m pytest-evil"])
        assert not prefix_within_prefixes("git-evil status", ["git"])
        assert not prefix_within_prefixes("", ["git"])
        assert not prefix_within_prefixes("git status", [""])


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

    def test_approval_applies_only_the_registered_delta(self) -> None:
        """§27.4: the approval binds to the delta the reviewer saw. A request
        object with the same id + operation hash but a wider delta must not
        extend the grant by that delta."""
        ledger = self._ledger(_grants(read=["/repo/src"]), _grants(read=["/repo"], write=["/repo"]))
        request = _make_request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        tampered = request.model_copy(update={"requested_delta": _req(filesystem_write=["/repo"])})
        with pytest.raises(DomainError) as exc:
            ledger.apply_approval(_decision(request.approval_id), tampered)
        assert exc.value.code == ErrorCode.APPROVAL_REJECTED
        assert ledger.grants.filesystem.write == []
        # The genuine request still applies afterwards (nothing was consumed).
        grants = ledger.apply_approval(_decision(request.approval_id), request)
        assert "/repo/tests/x.py" in grants.filesystem.read
        assert grants.filesystem.write == []

    def test_ceiling_expiry_bounds_extended_grants(self) -> None:
        """A ceiling that expires caps the grant in time as well as in scope."""
        expiry = datetime.now(UTC) + timedelta(minutes=5)
        ceiling = _grants(read=["/repo"]).model_copy(update={"expires_at": expiry})
        ledger = GrantLedger(_policy(ceiling), _grants(read=["/repo/src"]))
        assert ledger.grants.expires_at == expiry
        later = _grants(read=["/repo"]).model_copy(
            update={"expires_at": expiry + timedelta(hours=1)}
        )
        assert GrantLedger(_policy(ceiling), later).grants.expires_at == expiry
        earlier = _grants(read=["/repo"]).model_copy(
            update={"expires_at": expiry - timedelta(minutes=1)}
        )
        assert GrantLedger(_policy(ceiling), earlier).grants.expires_at == earlier.expires_at

    def test_initial_grants_kept_when_within_ceiling_by_prefix(self) -> None:
        ledger = self._ledger(
            _grants(read=["./out/", "out/sub"], prefixes=["git status"]),
            _grants(read=["out"], prefixes=["git"]),
        )
        assert ledger.grants.filesystem.read == ["./out/", "out/sub"]
        assert ledger.grants.process.allowed_prefixes == ["git status"]


class TestApprovalCoordinator:
    def test_register_reaches_the_ledger_and_apply_binds_workspace(self) -> None:
        ledger = GrantLedger(_policy(_grants(read=["/repo"])), _grants(read=["/repo/src"]))
        coordinator = ApprovalCoordinator(ledger)
        request = ApprovalRequest(
            approval_id="apr-1",
            run_id="run-1",
            operation_hash="op-1",
            workspace_id="ws-1",
            reason="need tests",
            requested_delta=_req(filesystem_read=["/repo/tests/x.py"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        coordinator.register(request)
        assert coordinator.pending_for_run("run-1") == [request]
        with pytest.raises(DomainError) as exc:
            coordinator.apply(_decision("apr-1"), operation_hash="op-1", workspace_id="ws-2")
        assert exc.value.code == ErrorCode.APPROVAL_REPLAY_INVALID
        grants = coordinator.apply(_decision("apr-1"), operation_hash="op-1", workspace_id="ws-1")
        assert "/repo/tests/x.py" in grants.filesystem.read
        assert coordinator.pending_for_run("run-1") == []
        with pytest.raises(DomainError):
            coordinator.apply(_decision("apr-1"), operation_hash="op-1", workspace_id="ws-1")

    def test_expired_request_refused_by_clock(self) -> None:
        ledger = GrantLedger(_policy(_grants(read=["/repo"])), _grants(read=["/repo/src"]))
        coordinator = ApprovalCoordinator(ledger)
        request = ApprovalRequest(
            approval_id="apr-2",
            run_id="run-1",
            operation_hash="op-1",
            reason="x",
            requested_delta=_req(filesystem_read=["/repo/tests/x.py"]),
            expires_at=datetime.now(UTC) + timedelta(seconds=60),
        )
        coordinator.register(request)
        with pytest.raises(DomainError) as exc:
            coordinator.apply(
                _decision("apr-2"),
                operation_hash="op-1",
                workspace_id="",
                now=datetime.now(UTC) + timedelta(minutes=5),
            )
        assert exc.value.code == ErrorCode.AUTHORITY_EXPIRED


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
        assert intersect_grants(_grants(read=["out"]), _grants(read=["."])).filesystem.read == []
        assert intersect_grants(_grants(read=["."]), _grants(read=[""])).filesystem.read == []
        assert (
            intersect_grants(
                _grants(prefixes=["git status"]), _grants(prefixes=["git"])
            ).process.allowed_prefixes
            == []
        )

    def test_child_sub_scope_within_parent_is_kept(self) -> None:
        """A narrower child scope is not dropped: `src/app` ⊆ `src`, `git status`
        ⊆ `git`, anything relative ⊆ `.` (the semantics PolicyEvaluator uses)."""
        parent = _grants(read=["src", "."], write=["src"], prefixes=["git", "pytest"])
        child = intersect_grants(
            parent, _grants(read=["src/app", "docs"], write=["./src/"], prefixes=["git status"])
        )
        assert child.filesystem.read == ["src/app", "docs"]
        assert child.filesystem.write == ["./src/"]
        assert child.process.allowed_prefixes == ["git status"]
        assert scope_within_scopes(".", ["/"]) and not scope_within_scopes("", ["."])

    def test_child_expiry_is_the_earliest(self) -> None:
        soon = datetime.now(UTC) + timedelta(minutes=1)
        later = soon + timedelta(hours=1)
        parent = _grants(read=["/repo"]).model_copy(update={"expires_at": later})
        requested = _grants(read=["/repo"]).model_copy(update={"expires_at": soon})
        assert intersect_grants(parent, requested).expires_at == soon
        assert intersect_grants(requested, parent).expires_at == soon
        ceiling = _grants(read=["/repo"]).model_copy(
            update={"expires_at": soon - timedelta(seconds=30)}
        )
        assert intersect_grants(parent, requested, ceiling).expires_at == ceiling.expires_at
        assert narrow_grants(parent, _grants(read=["/repo"])).expires_at == later
