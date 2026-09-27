"""Unit tests for the eligibility engine and facet schema (plan §§9, 15; ADR-009)."""

import pytest
from pydantic import ValidationError

from aci.domain.capability.models import Compatibility
from aci.domain.policy.models import (
    EligibleCandidate,
    PolicyRules,
    RoutingRequestContext,
)
from aci.domain.taxonomy.models import validate_facets
from aci.routing.eligibility import DefaultEligibilityPolicy

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
    }
    base.update(overrides)
    return RoutingRequestContext.model_validate(base)


def test_active_production_candidate_passes() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [candidate()], context(), PolicyRules(), allowed_kinds=["skill"]
    )
    assert [c.capability_id for c in decision.kept] == ["c-1"]
    assert decision.excluded == []


def test_revoked_and_disabled_never_reach_reranker() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [candidate(status="revoked"), candidate(capability_id="c-2", status="disabled")],
        context(),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert decision.kept == []
    reasons = {e.capability_id: e.reason for e in decision.excluded}
    assert reasons["c-1"] == "CAPABILITY_REVOKED"
    assert reasons["c-2"] == "CAPABILITY_NOT_ELIGIBLE"


def test_wrong_channel_excluded() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [candidate(channel="staging")], context(), PolicyRules(), allowed_kinds=["skill"]
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"


def test_kind_filter_and_deny_list() -> None:
    rules = PolicyRules(denied_capability_ids=frozenset({"c-2"}))
    decision = DefaultEligibilityPolicy().filter(
        [candidate(), candidate(capability_id="c-2"), candidate(capability_id="c-3", kind="tool")],
        context(),
        rules,
        allowed_kinds=["skill"],
    )
    assert [c.capability_id for c in decision.kept] == ["c-1"]
    by_reason = {e.capability_id: e.reason for e in decision.excluded}
    assert by_reason["c-2"] == "POLICY_DENIED"
    assert by_reason["c-3"] == "CAPABILITY_NOT_ELIGIBLE"


def test_trust_tier_filter() -> None:
    rules = PolicyRules(min_trust_tier="verified")
    decision = DefaultEligibilityPolicy().filter(
        [candidate(trust_tier="standard"), candidate(capability_id="c-2", trust_tier="verified")],
        context(),
        rules,
        allowed_kinds=["skill"],
    )
    assert [c.capability_id for c in decision.kept] == ["c-2"]


def test_license_blocked_excluded() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [candidate(license_blocked=True)], context(), PolicyRules(), allowed_kinds=["skill"]
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "POLICY_DENIED"


def test_scope_authorization() -> None:
    cases = [
        ("global", None, True),
        ("organization", "org-1", True),
        ("organization", "org-2", False),
        ("workspace", "ws-1", True),
        ("workspace", "ws-2", False),
        ("private", "p-1", True),
        ("private", "p-2", False),
    ]
    for scope, scope_id, expected in cases:
        decision = DefaultEligibilityPolicy().filter(
            [candidate(owner_scope=scope, owner_scope_id=scope_id)],  # type: ignore[arg-type]
            context(),
            PolicyRules(),
            allowed_kinds=["skill"],
        )
        assert (len(decision.kept) == 1) is expected, f"{scope}/{scope_id}"
        if not expected:
            assert decision.excluded[0].reason == "PERMISSION_DENIED"


def test_client_compatibility_filter() -> None:
    compat = Compatibility.model_validate(
        {"supported_clients": ["opencode"], "minimum_client_features": ["skills"]}
    )
    ok = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)], context(), PolicyRules(), allowed_kinds=["skill"]
    )
    assert len(ok.kept) == 1

    wrong_client = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(client={"type": "rest-app", "supported_features": ["skills"]}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert wrong_client.kept == []
    assert wrong_client.excluded[0].reason == "CLIENT_INCOMPATIBLE"

    missing_feature = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(client={"type": "opencode", "supported_features": []}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert missing_feature.kept == []
    assert missing_feature.excluded[0].reason == "CLIENT_INCOMPATIBLE"


def test_empty_candidate_list_is_valid_empty_decision() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [], context(), PolicyRules(), allowed_kinds=["skill"]
    )
    assert decision.kept == [] and decision.excluded == []


# ---------- scope fail-closed (§27) ----------


def test_scoped_candidate_without_scope_id_fails_closed() -> None:
    """owner_scope_id=None must never match a request that also lacks one."""
    for scope in ("organization", "workspace", "private"):
        decision = DefaultEligibilityPolicy().filter(
            [candidate(owner_scope=scope, owner_scope_id=None)],  # type: ignore[arg-type]
            context(scope={"principal_id": "p-1", "organization_id": None, "workspace_id": None}),
            PolicyRules(),
            allowed_kinds=["skill"],
        )
        assert decision.kept == [], scope
        assert decision.excluded[0].reason == "PERMISSION_DENIED"


def test_scope_mismatch_still_denies() -> None:
    decision = DefaultEligibilityPolicy().filter(
        [candidate(owner_scope="organization", owner_scope_id="org-2")],  # type: ignore[arg-type]
        context(),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "PERMISSION_DENIED"


# ---------- language/framework filter (§15) ----------


def test_unsupported_language_excludes_before_rerank() -> None:
    compat = Compatibility(supported_languages=["rust"])
    decision = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(task={"language": "python", "frameworks": []}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"


def test_undeclared_task_language_stays_eligible() -> None:
    compat = Compatibility(supported_languages=["rust"])
    decision = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(task={"language": None, "frameworks": []}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert len(decision.kept) == 1


def test_disjoint_frameworks_exclude() -> None:
    compat = Compatibility(supported_frameworks=["axum"])
    decision = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(task={"language": "rust", "frameworks": ["actix"]}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "CAPABILITY_NOT_ELIGIBLE"


def test_matching_language_and_framework_pass() -> None:
    compat = Compatibility(supported_languages=["rust"], supported_frameworks=["axum"])
    decision = DefaultEligibilityPolicy().filter(
        [candidate(compatibility=compat)],
        context(task={"language": "rust", "frameworks": ["axum", "tokio"]}),
        PolicyRules(),
        allowed_kinds=["skill"],
    )
    assert len(decision.kept) == 1


# ---------- license restriction is fail-closed on every channel (§15, §24) ----------


def test_license_blocked_excluded_on_non_production_channel_too() -> None:
    """Pinned decision: eligibility blocks restricted licenses everywhere;
    non-production content is reviewed via the control plane, not routed around."""
    decision = DefaultEligibilityPolicy().filter(
        [candidate(channel="staging", license_blocked=True)],
        context(),
        PolicyRules(required_channel="staging"),
        allowed_kinds=["skill"],
    )
    assert decision.kept == []
    assert decision.excluded[0].reason == "POLICY_DENIED"


# ---------- facets ----------


def test_validate_facets_accepts_known_and_hierarchical() -> None:
    validate_facets(
        {
            "domain": ["software-engineering"],
            "task_type": ["debugging"],
            "technology": ["typescript"],
            "concern": ["authentication"],
        }
    )
    validate_facets({"domain": ["software-engineering/debugging"]})


def test_validate_facets_rejects_unknown_and_malformed() -> None:
    with pytest.raises(ValueError, match="unknown facet"):
        validate_facets({"vibe": ["x"]})
    with pytest.raises(ValueError, match="invalid facet value"):
        validate_facets({"domain": ["Not A Slug"]})
    with pytest.raises(ValueError, match="must be a non-empty list"):
        validate_facets({"domain": []})


def test_capability_version_rejects_bad_facets() -> None:
    from datetime import UTC, datetime

    from aci.domain.capability.models import CapabilityVersion, SkillSpec

    with pytest.raises(ValidationError):
        CapabilityVersion(
            capability_id="c",
            version="1.0.0",
            kind="skill",
            content_digest=DIGEST,
            created_at=datetime(2026, 9, 27, tzinfo=UTC),
            facets={"unknown-facet": ["x"]},
            spec=SkillSpec(),
        )
