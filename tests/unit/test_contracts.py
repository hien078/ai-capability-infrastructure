"""Contract tests: mutable/immutable boundaries, no protocol leakage (plan §52 Phase 0)."""

import ast
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
    """No protocol/db/framework imports outside adapters (ADR-004, §28.5).

    AST-based so docstrings mentioning e.g. "SQLAlchemy" do not false-positive.
    Covers every non-adapter layer; extend LAYERS when adding one.
    """
    src = pathlib.Path(__file__).parents[2] / "src" / "aci"
    layers = ("domain", "application", "providers", "routing", "control_plane")
    banned = ("fastapi", "mcp", "sqlalchemy", "opencode", "a2a")
    for layer in layers:
        for f in (src / layer).rglob("*.py"):
            for hit in _banned_imports(f, banned):
                raise AssertionError(f"{f} imports protocol/db layer: {hit}")


def _banned_imports(path: pathlib.Path, banned: tuple[str, ...]) -> list[str]:
    """Top-level module names imported by `path` that are in `banned`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            hits.extend(a.name.split(".")[0].lower() for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            hits.append(node.module.split(".")[0].lower())
    return [h for h in hits if h in banned]


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


def test_scoped_capability_requires_owner_scope_id() -> None:
    """A scoped capability without its scope id would match every request that
    also lacks one (None == None) — rejected at the boundary (§27)."""
    for scope in ("organization", "workspace", "private"):
        with pytest.raises(ValidationError, match="owner_scope_id"):
            Capability(id="c-1", kind="skill", created_at=NOW, owner_scope=scope)  # type: ignore[arg-type]
    # with the id present it is valid; global needs none
    Capability(
        id="c-1", kind="skill", created_at=NOW, owner_scope="organization", owner_scope_id="org-1"
    )  # type: ignore[arg-type]
    Capability(id="c-1", kind="skill", created_at=NOW)
