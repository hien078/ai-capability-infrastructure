"""Contract tests: mutable/immutable boundaries, no protocol leakage (plan §52 Phase 0)."""

import pathlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aci.domain.capability.errors import ErrorCode
from aci.domain.capability.models import (
    BundleItem,
    Capability,
    CapabilityBundle,
    CapabilityMetrics,
    CapabilityRelease,
    CapabilityVersion,
    OutcomeEvidence,
    OutcomeVerdict,
    RouteCapabilitiesCommand,
    SkillSpec,
)

NOW = datetime(2026, 9, 27, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


def test_version_is_immutable() -> None:
    v = CapabilityVersion(
        capability_id="systematic-debugging",
        version="2.4.1",
        kind="skill",
        content_digest=DIGEST,
        created_at=NOW,
        spec=SkillSpec(),
    )
    with pytest.raises(ValidationError):
        v.version = "9.9.9"  # type: ignore[misc]


def test_bundle_allows_zero_items_and_rejects_six() -> None:
    b0 = CapabilityBundle(bundle_id="bun_1", route_run_id="route_1", created_at=NOW, items=[])
    assert b0.items == []
    items = [
        BundleItem(
            capability_id=f"c-{i}",
            version="1.0.0",
            digest=DIGEST,
            kind="skill",
        )
        for i in range(6)
    ]
    with pytest.raises(ValidationError):
        CapabilityBundle(bundle_id="bun_2", route_run_id="route_1", created_at=NOW, items=items)


def test_route_defaults_are_minimal() -> None:
    cmd = RouteCapabilitiesCommand(task_text="Fix race")
    assert cmd.max_items <= 5
    assert cmd.allowed_kinds == ["skill"]


def test_capability_id_and_version_format() -> None:
    with pytest.raises(ValidationError):
        Capability(id="Bad Name!", kind="skill", created_at=NOW)
    with pytest.raises(ValidationError):
        CapabilityVersion(
            capability_id="x",
            version="v1",
            kind="skill",
            content_digest="nope",
            created_at=NOW,
            spec=SkillSpec(),
        )


def test_outcome_requires_verdict_source() -> None:
    with pytest.raises(ValidationError):
        OutcomeEvidence(outcome_id="o", route_run_id="r", bundle_id="b", verdicts=[])
    ev = OutcomeEvidence(
        outcome_id="o",
        route_run_id="r",
        bundle_id="b",
        verdicts=[OutcomeVerdict(source="test_harness", status="success", confidence="high")],
    )
    assert ev.verdicts[0].source == "test_harness"


def test_release_promotion_does_not_mutate_version() -> None:
    v = CapabilityVersion(
        capability_id="c",
        version="1.0.0",
        kind="skill",
        content_digest=DIGEST,
        created_at=NOW,
        spec=SkillSpec(),
    )
    r = CapabilityRelease(capability_id="c", version="1.0.0", channel="staging", status="active")
    r.channel = "production"
    assert v.version == "1.0.0"


def test_domain_has_no_protocol_imports() -> None:
    src = pathlib.Path(__file__).parents[2] / "src" / "aci" / "domain"
    banned = ("fastapi", "mcp", "sqlalchemy", "opencode", "a2a")
    for f in src.rglob("*.py"):
        text = f.read_text().lower()
        assert not any(b in text for b in banned), f"{f} imports protocol/db layer"


def test_error_codes_stable() -> None:
    assert ErrorCode.CAPABILITY_REVOKED == "CAPABILITY_REVOKED"
    assert ErrorCode.POLICY_DENIED == "POLICY_DENIED"


def test_version_carries_faceted_taxonomy() -> None:
    v = CapabilityVersion(
        capability_id="systematic-debugging",
        version="2.4.1",
        kind="skill",
        content_digest=DIGEST,
        created_at=NOW,
        spec=SkillSpec(provides=["root-cause-analysis"]),
        facets={"domain": ["software-engineering"], "task_type": ["debugging"]},
    )
    assert v.facets["domain"] == ["software-engineering"]
    assert v.spec.provides == ["root-cause-analysis"]


def test_skill_spec_requirements_and_routing_hints() -> None:
    s = SkillSpec(routing_hints={"task_types": ["debugging"]})
    assert s.requirements.context == []
    assert s.requirements.optional_context == []
    assert s.routing_hints == {"task_types": ["debugging"]}
    assert s.side_effects == "none"


def test_release_carries_promotion_audit_trail() -> None:
    r = CapabilityRelease(
        capability_id="c",
        version="1.0.0",
        channel="production",
        promoted_at=NOW,
        approved_by="reviewer-1",
        policy_snapshot_id="pol_1",
    )
    assert r.promoted_at == NOW
    assert r.approved_by == "reviewer-1"


def test_metrics_are_frozen_snapshots_separate_from_version() -> None:
    m = CapabilityMetrics(capability_id="c", version="1.0.0", computed_at=NOW, usage_count=7)
    with pytest.raises(ValidationError):
        m.usage_count = 8  # type: ignore[misc]
    assert "usage_count" not in set(CapabilityVersion.model_fields)
    assert "verified_success_rate" not in set(CapabilityVersion.model_fields)
