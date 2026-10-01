"""Mutation-audit killing tests for ``src/aci/runtime/authority.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants from the mutmut before-run
(authority: 129 mutants, 61 killed + 15 suspicious, 53 survived). The
behaviors below had NO test in the module's relevant subset:

* the fail-closed policy default (REQUIRE_APPROVAL) and the frozen policy model;
* expiry semantics: no-expiry requests never expire, the boundary is inclusive
  (``now == expires_at`` is expired), and tz-aware non-UTC expiries compare as
  INSTANTS (not wall digits);
* multi-element requirements check EVERY element (a granted first element must
  not short-circuit a denied second one) for network/process/secrets;
* a disabled network ceiling denies even when the host is listed;
* secret refs flow through grants → ceiling → deny (completely untested before);
* the write-path ceiling delta (only the read path was pinned);
* ``derive_requirement``: wrong-typed args raise, every declared arg is checked;
* approval extension adds the delta in EVERY dimension (write/network/process/
  secrets), not the intersection;
* the facade without initial grants / without explicit grants.

Message-text mutants (DomainError ``message`` strings) are deliberately NOT
pinned: §45 makes the stable ``ErrorCode`` the wire contract, not the prose.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from pydantic import ValidationError

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
)
from aci.domain.runtime.tools import ToolAuthority, ToolSpec
from aci.runtime.authority import (
    ApprovalCoordinator,
    AuthorityManager,
    AuthorityPolicy,
    GrantLedger,
    PolicyEvaluator,
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
    secrets: list[str] | None = None,
    network_enabled: bool | None = None,
) -> GrantEnvelope:
    enabled = bool(hosts) if network_enabled is None else network_enabled
    return GrantEnvelope(
        filesystem=FilesystemScope(read=read or [], write=write or []),
        network=NetworkScope(enabled=enabled, allowed_hosts=hosts or []),
        process=ProcessScope(allowed_prefixes=prefixes or []),
        secrets_allowed_refs=secrets or [],
    )


def _req(**over: Any) -> AuthorityRequirement:
    base: dict[str, Any] = {
        "filesystem_read": [],
        "filesystem_write": [],
        "network_hosts": [],
        "process_prefixes": [],
        "secret_refs": [],
    }
    base.update(over)
    return AuthorityRequirement(**base)


def _decision(approval_id: str, approved: bool = True) -> ApprovalDecision:
    return ApprovalDecision(
        approval_id=approval_id,
        approved=approved,
        decided_by="reviewer",
        decided_at=datetime.now(UTC),
    )


def _request(
    ledger: GrantLedger, delta: AuthorityRequirement, op_hash: str, **over: Any
) -> ApprovalRequest:
    fields: dict[str, Any] = {
        "approval_id": f"apr-{op_hash}",
        "run_id": "run-1",
        "operation_hash": op_hash,
        "reason": "test approval",
        "requested_delta": delta,
        "expires_at": datetime.now(UTC) + timedelta(seconds=60),
    }
    fields.update(over)
    request = ApprovalRequest(**fields)
    ledger.register_request(request)
    return request


class TestPolicyDefaults:
    def test_default_is_require_approval(self) -> None:
        """§13.2 fail-closed default: beyond grants but within ceiling → the
        DEFAULT decision, which must be REQUIRE_APPROVAL, never None/ALLOW."""
        policy = AuthorityPolicy()  # no explicit default
        assert policy.default is AuthorityDecisionKind.REQUIRE_APPROVAL
        ev = PolicyEvaluator()
        grants = _grants(read=["/repo/src"])
        ceiling = _grants(read=["/repo"])
        decision = ev.evaluate(
            _req(filesystem_read=["/repo/tests/x.py"]), grants, AuthorityPolicy(ceiling=ceiling)
        )
        assert decision.kind is AuthorityDecisionKind.REQUIRE_APPROVAL

    def test_policy_model_is_frozen(self) -> None:
        """Policy is configuration: a running manager must not be able to
        rewrite it (a mutable policy would let a compromised component widen
        its own ceiling)."""
        policy = AuthorityPolicy()
        with pytest.raises(ValidationError):
            policy.default = AuthorityDecisionKind.ALLOW  # type: ignore[misc]


class TestExpiry:
    def _ledger(self) -> GrantLedger:
        return GrantLedger(_policy(_grants(read=["/repo"])), _grants(read=["/repo/src"]))

    def test_no_expiry_request_never_expires(self) -> None:
        """``expires_at=None`` means no expiry: applying it must succeed."""
        ledger = self._ledger()
        request = _request(
            ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1", expires_at=None
        )
        grants = ledger.apply_approval(_decision(request.approval_id), request)
        assert "/repo/tests/x.py" in grants.filesystem.read

    def test_expiry_boundary_is_inclusive(self) -> None:
        """``now == expires_at`` is EXPIRED (>=), not one microsecond of grace."""
        ledger = self._ledger()
        coordinator = ApprovalCoordinator(ledger)
        at = datetime.now(UTC) + timedelta(seconds=60)
        request = _request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        coordinator.register(request.model_copy(update={"expires_at": at}))
        with pytest.raises(DomainError) as exc:
            coordinator.apply(
                _decision(request.approval_id), operation_hash="op-1", workspace_id="", now=at
            )
        assert exc.value.code == ErrorCode.AUTHORITY_EXPIRED

    def test_tz_aware_non_utc_expiry_compares_as_instant(self) -> None:
        """A tz-aware expiry in a WEST-of-UTC zone is a later instant than its
        wall digits suggest; it must not be treated as already expired."""
        ledger = self._ledger()
        coordinator = ApprovalCoordinator(ledger)
        west = timezone(-timedelta(hours=2))
        expires = (datetime.now(UTC) + timedelta(seconds=60)).astimezone(west)
        request = _request(ledger, _req(filesystem_read=["/repo/tests/x.py"]), "op-1")
        coordinator.register(request.model_copy(update={"expires_at": expires}))
        grants = coordinator.apply(
            _decision(request.approval_id), operation_hash="op-1", workspace_id=""
        )
        assert "/repo/tests/x.py" in grants.filesystem.read


class TestDeltaCompleteness:
    """Every element of a multi-element requirement is checked — a granted
    first element must never short-circuit a denied second one."""

    def test_network_mixed_granted_and_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(hosts=["pypi.org"])
        decision = ev.evaluate(_req(network_hosts=["pypi.org", "evil.example"]), grants, _policy())
        assert decision.kind is AuthorityDecisionKind.DENY

    def test_process_mixed_granted_and_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(prefixes=["git"])
        decision = ev.evaluate(
            _req(process_prefixes=["git status", "sudo rm -rf /"]), grants, _policy()
        )
        assert decision.kind is AuthorityDecisionKind.DENY

    def test_secrets_mixed_granted_and_denied(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(secrets=["secret://allowed"])
        decision = ev.evaluate(
            _req(secret_refs=["secret://allowed", "secret://forbidden"]), grants, _policy()
        )
        assert decision.kind is AuthorityDecisionKind.DENY

    def test_disabled_ceiling_network_denies_even_when_host_listed(self) -> None:
        """A ceiling with ``enabled=False`` but a stale host list denies — the
        host list alone must not make the host approvable."""
        ev = PolicyEvaluator()
        ceiling = _grants(hosts=["pypi.org"], network_enabled=False)
        decision = ev.evaluate(_req(network_hosts=["pypi.org"]), _grants(), _policy(ceiling))
        assert decision.kind is AuthorityDecisionKind.DENY


class TestSecretRefs:
    def test_granted_ref_allows(self) -> None:
        ev = PolicyEvaluator()
        grants = _grants(secrets=["secret://db"])
        decision = ev.evaluate(_req(secret_refs=["secret://db"]), grants, _policy())
        assert decision.kind is AuthorityDecisionKind.ALLOW

    def test_ceiling_ref_requires_approval_with_delta(self) -> None:
        ev = PolicyEvaluator()
        ceiling = _grants(secrets=["secret://db"])
        decision = ev.evaluate(_req(secret_refs=["secret://db"]), _grants(), _policy(ceiling))
        assert decision.kind is AuthorityDecisionKind.REQUIRE_APPROVAL
        assert decision.requested_delta is not None
        assert decision.requested_delta.secret_refs == ["secret://db"]

    def test_unknown_ref_denied(self) -> None:
        ev = PolicyEvaluator()
        decision = ev.evaluate(_req(secret_refs=["secret://nope"]), _grants(), _policy())
        assert decision.kind is AuthorityDecisionKind.DENY


class TestWriteCeiling:
    def test_write_path_within_ceiling_requires_approval_with_delta(self) -> None:
        """Only the READ ceiling delta was pinned; the write one must be too."""
        ev = PolicyEvaluator()
        grants = _grants(write=["/repo/src"])
        ceiling = _grants(write=["/repo"])
        decision = ev.evaluate(
            _req(filesystem_write=["/repo/tests/x.py"]), grants, _policy(ceiling)
        )
        assert decision.kind is AuthorityDecisionKind.REQUIRE_APPROVAL
        assert decision.requested_delta is not None
        assert decision.requested_delta.filesystem_write == ["/repo/tests/x.py"]


class TestDeriveRequirement:
    def _tool(self, read_args: list[str]) -> ToolSpec:
        return ToolSpec(
            tool_id="t",
            version="1",
            authority_requirements=ToolAuthority(read_path_args=read_args),
        )

    def test_wrong_typed_path_arg_raises(self) -> None:
        """A declared path arg holding a list is an invalid argument, never a
        silently joined string (under-reporting the requirement)."""
        with pytest.raises(DomainError) as exc:
            derive_requirement(self._tool(["path"]), {"path": ["a", "b"]})
        assert exc.value.code == ErrorCode.TOOL_ARGUMENT_INVALID

    def test_every_declared_arg_is_checked(self) -> None:
        """A missing first declared arg skips; a present later one is still
        reported (break-after-miss would under-report)."""
        req = derive_requirement(self._tool(["missing", "present"]), {"present": "/etc/x"})
        assert req.filesystem_read == ["/etc/x"]


class TestApprovalExtension:
    def test_approval_extends_every_dimension(self) -> None:
        """Applying an approval UNIONs the delta into the grants in every
        dimension (write, network enable+host, process, secrets) — never the
        intersection."""
        ceiling = _grants(
            read=["/repo"],
            write=["/repo"],
            hosts=["h.example"],
            prefixes=["git"],
            secrets=["secret://db"],
        )
        ledger = GrantLedger(_policy(ceiling), _grants(read=["/repo/src"]))
        delta = _req(
            filesystem_write=["/repo/out"],
            network_hosts=["h.example"],
            process_prefixes=["git"],
            secret_refs=["secret://db"],
        )
        request = _request(ledger, delta, "op-1")
        grants = ledger.apply_approval(_decision(request.approval_id), request)
        assert grants.filesystem.write == ["/repo/out"]
        assert grants.network.enabled is True
        assert grants.network.allowed_hosts == ["h.example"]
        assert grants.process.allowed_prefixes == ["git"]
        assert grants.secrets_allowed_refs == ["secret://db"]
        # and the extended grant actually satisfies each requirement
        for requirement in (
            _req(filesystem_write=["/repo/out"]),
            _req(network_hosts=["h.example"]),
            _req(process_prefixes=["git"]),
            _req(secret_refs=["secret://db"]),
        ):
            assert ledger.evaluate(requirement).kind is AuthorityDecisionKind.ALLOW


class TestManagerFacade:
    def test_manager_without_initial_grants(self) -> None:
        """``initial_grants=None`` is the honest empty start: evaluate works and
        denies, and ``grants`` is the property (not the method object)."""
        manager = AuthorityManager(policy=_policy())
        assert manager.grants.filesystem.read == []
        decision = manager.evaluate(_req(filesystem_read=["/etc/passwd"]))
        assert decision.kind is AuthorityDecisionKind.DENY

    def test_envelope_defaults_to_current_grants(self) -> None:
        """``build_execution_envelope`` without explicit grants projects the
        ledger's CURRENT grants."""
        manager = AuthorityManager(
            policy=_policy(_grants(read=["/repo"])), initial_grants=_grants(read=["/repo/src"])
        )
        allow = AuthorityDecision(kind=AuthorityDecisionKind.ALLOW, reason="ok")
        envelope = manager.build_execution_envelope("run-1", "ws-1", allow)
        assert envelope.filesystem.read == ["/repo/src"]

    def test_approval_request_defaults(self) -> None:
        """``make_approval_request`` without a workspace binds the empty
        sentinel (§27.4 binding is by equality, so the default must be '')."""
        manager = AuthorityManager(policy=_policy())
        request = manager.make_approval_request("run-1", _req(), "reason", "op-hash")
        assert request.workspace_id == ""
        assert request.expires_at is not None
