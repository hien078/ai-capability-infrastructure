"""Mutation-audit killing tests for ``src/aci/routing/eligibility.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants from the mutmut before-run
(eligibility: 76 mutants, 54 killed, 22 survived). The behaviors below had
NO test in the module's relevant subset:

* the §27 canary split derivation is REPRODUCIBLE — the hash input and the
  ``% 100 < percent`` mapping are part of the telemetry contract (a silent
  change drifts the canary population between releases), and the boundary is
  EXCLUSIVE (``h == percent`` is not selected — ``<`` not ``<=``);
* a task that declares NO frameworks stays eligible for a capability that
  declares ``supported_frameworks`` (only a declared-incompatible task
  excludes, §15).

The remaining survivors are Exclusion ``detail`` prose — §45 makes the stable
``ErrorCode`` the contract, not the message text.
"""

from __future__ import annotations

import hashlib

from aci.domain.capability.models import Compatibility
from aci.domain.policy.models import (
    EligibleCandidate,
    PolicyRules,
    RoutingRequestContext,
)
from aci.routing.eligibility import DefaultEligibilityPolicy, _canary_selected

DIGEST = "sha256:" + "ab" * 32


def candidate(**overrides: object) -> EligibleCandidate:
    base: dict = {
        "capability_id": "c-1",
        "version": "1.0.0",
        "digest": DIGEST,
        "kind": "skill",
        "channel": "production",
        "status": "active",
    }
    base.update(overrides)
    return EligibleCandidate.model_validate(base)


def context(**overrides: object) -> RoutingRequestContext:
    base: dict = {
        "client": {"type": "opencode", "supported_features": ["skills"]},
        "scope": {"principal_id": "p-1", "organization_id": "org-1", "workspace_id": "ws-1"},
        "request_id": "req-1",
    }
    base.update(overrides)
    return RoutingRequestContext.model_validate(base)


class TestCanarySplit:
    def test_derivation_is_reproducible(self) -> None:
        """§27: the canary population must be stable across releases — the
        hash input ``"{request_id}:{capability_id}"`` and the
        ``int(digest[:8], 16) % 100`` mapping are pinned so any change to the
        derivation is a LOUD change (silent drift corrupts telemetry
        comparisons)."""
        assert _canary_selected("req-1", "cap-x", 50) is True
        assert _canary_selected("req-2", "cap-y", 50) is False
        assert _canary_selected("req-3", "cap-z", 50) is False

    def test_boundary_is_exclusive(self) -> None:
        """``h == percent`` is NOT selected: the share is exactly
        ``canary_percent`` percent of the hash space, never percent+1."""
        # a pair whose hash slot is exactly 50 (found by construction, stable)
        digest = hashlib.sha256(b"boundary-64:cap-b").hexdigest()
        assert int(digest[:8], 16) % 100 == 50
        assert _canary_selected("boundary-64", "cap-b", 50) is False
        assert _canary_selected("boundary-64", "cap-b", 51) is True


class TestFrameworkCompatibility:
    def test_undeclared_task_frameworks_stay_eligible(self) -> None:
        """§15: only a DECLARED incompatibility excludes — a task with no
        framework declaration must not be excluded by a capability that
        declares ``supported_frameworks``."""
        compat = Compatibility(supported_frameworks=["django"])
        decision = DefaultEligibilityPolicy().filter(
            [candidate(compatibility=compat)],
            context(task={"frameworks": []}),
            PolicyRules(),
            allowed_kinds=["skill"],
        )
        assert [c.capability_id for c in decision.kept] == ["c-1"]

    def test_declared_disjoint_frameworks_exclude(self) -> None:
        compat = Compatibility(supported_frameworks=["django"])
        decision = DefaultEligibilityPolicy().filter(
            [candidate(compatibility=compat)],
            context(task={"frameworks": ["rails"]}),
            PolicyRules(),
            allowed_kinds=["skill"],
        )
        assert decision.kept == []
        assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"
