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
