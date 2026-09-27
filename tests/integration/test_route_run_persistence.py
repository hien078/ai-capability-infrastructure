"""Phase 9 persistence acceptance: route runs, bundles, outcomes (§§36, 41).

Roundtrips against the live DB plus the §41.1 invariant: bundle items are
foreign-keyed to exact immutable versions — a bundle may never reference a
version that does not exist.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from aci.adapters.outbound.postgres.bundles import SqlAlchemyBundleRepository
from aci.adapters.outbound.postgres.outcomes import SqlAlchemyOutcomeRecorder
from aci.adapters.outbound.postgres.repositories import SqlAlchemyCapabilityRepository
from aci.adapters.outbound.postgres.route_runs import SqlAlchemyRouteRunRepository
from aci.domain.capability.models import (
    BundleItem,
    Capability,
    CapabilityBundle,
    CapabilityVersion,
    OutcomeEvidence,
    OutcomeVerdict,
    SkillSpec,
)
from aci.domain.routing.models import RouteRun

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def make_run(**overrides: object) -> RouteRun:
    defaults: dict[str, object] = {
        "route_run_id": uid("route"),
        "request_id": uid("req"),
        "trace_id": uid("trc"),
        "created_at": NOW,
        "principal_id": "p-1",
        "client_type": "rest-client",
        "protocol_type": "rest",
        "task_text": "calibrate the resonance manifold",
        "eligible_count": 2,
        "stages": {"eligibility": {"kept": 2, "excluded": []}, "rerank": {"input_count": 2}},
        "reranker_implementation": "heuristic-reranker",
        "reranker_version": "1",
        "composer_implementation": "minimal-bundle-composer",
        "composer_version": "1",
        "latency_ms": 42,
    }
    return RouteRun.model_validate({**defaults, **overrides})


def make_version(cid: str) -> CapabilityVersion:
    return CapabilityVersion(
        capability_id=cid,
        version="1.0.0",
        kind="skill",
        content_digest=f"sha256:{uuid.uuid4().hex}{uuid.uuid4().hex}",
        created_at=NOW,
        spec=SkillSpec(),
    )


def make_bundle(run_id: str, items: list[BundleItem]) -> CapabilityBundle:
    return CapabilityBundle(
        bundle_id=uid("bun"),
        route_run_id=run_id,
        created_at=NOW,
        items=items,
        execution_order=[i.capability_id for i in items],
    )


def test_route_run_roundtrip(route_run_repo: SqlAlchemyRouteRunRepository) -> None:
    run = make_run()
    route_run_repo.put_route_run(run)

    got = route_run_repo.get_route_run(run.route_run_id)
    assert got == run
    assert got is not None
    assert got.stages["eligibility"]["kept"] == 2
    assert got.latency_ms == 42

    assert route_run_repo.get_route_run(uid("route")) is None


def test_bundle_roundtrip_pins_exact_versions(
    route_run_repo: SqlAlchemyRouteRunRepository,
    bundle_repo: SqlAlchemyBundleRepository,
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    run = make_run()
    route_run_repo.put_route_run(run)

    caps = []
    for key in ("alpha", "beta"):
        cid = uid(f"cap-{key}")
        capability_repo.create_capability(Capability(id=cid, kind="skill", created_at=NOW))
        capability_repo.create_version(make_version(cid))
        caps.append(cid)

    bundle = make_bundle(
        run.route_run_id,
        [
            BundleItem(
                capability_id=caps[0],
                version="1.0.0",
                digest=f"sha256:{'a' * 64}",
                kind="skill",
                role="primary",
                reason_code="rank",
            ),
            BundleItem(
                capability_id=caps[1],
                version="1.0.0",
                digest=f"sha256:{'b' * 64}",
                kind="skill",
                role="check",
                reason_code="checks",
            ),
        ],
    )
    bundle_repo.put_bundle(bundle)

    got = bundle_repo.get_bundle(bundle.bundle_id)
    assert got == bundle
    assert got is not None
    assert [i.capability_id for i in got.items] == caps
    assert [i.role for i in got.items] == ["primary", "check"]
    assert got.execution_order == caps
    assert bundle_repo.get_bundle(uid("bun")) is None


def test_bundle_items_fk_rejects_unknown_version(
    route_run_repo: SqlAlchemyRouteRunRepository,
    bundle_repo: SqlAlchemyBundleRepository,
) -> None:
    """§41.1: bundle items FK to exact versions — no dangling pins, ever."""
    run = make_run()
    route_run_repo.put_route_run(run)

    ghost = BundleItem(
        capability_id=uid("cap-ghost"),
        version="9.9.9",
        digest=f"sha256:{'c' * 64}",
        kind="skill",
    )
    with pytest.raises(IntegrityError):
        bundle_repo.put_bundle(make_bundle(run.route_run_id, [ghost]))


def test_outcome_roundtrip_preserves_verdict_order(
    route_run_repo: SqlAlchemyRouteRunRepository,
    bundle_repo: SqlAlchemyBundleRepository,
    outcome_recorder: SqlAlchemyOutcomeRecorder,
    capability_repo: SqlAlchemyCapabilityRepository,
) -> None:
    run = make_run()
    route_run_repo.put_route_run(run)

    cid = uid("cap")
    capability_repo.create_capability(Capability(id=cid, kind="skill", created_at=NOW))
    capability_repo.create_version(make_version(cid))
    bundle = make_bundle(
        run.route_run_id,
        [
            BundleItem(
                capability_id=cid,
                version="1.0.0",
                digest=f"sha256:{'a' * 64}",
                kind="skill",
            )
        ],
    )
    bundle_repo.put_bundle(bundle)

    evidence = OutcomeEvidence(
        outcome_id=uid("out"),
        route_run_id=run.route_run_id,
        bundle_id=bundle.bundle_id,
        received_at=NOW,
        verdicts=[
            OutcomeVerdict(source="test_harness", status="success", confidence="high"),
            OutcomeVerdict(source="static_analysis", status="failure", confidence="medium"),
            OutcomeVerdict(source="agent_self_report", status="unknown", confidence="low"),
        ],
        tests_before={"failed": 3},
        tests_after={"failed": 1},
        latency_ms=2500,
    )
    outcome_recorder.record(evidence)

    got = outcome_recorder.get_outcome(evidence.outcome_id)
    assert got == evidence
    assert got is not None
    # Multi-source order survives the roundtrip; unknown stays unknown (ADR-010).
    assert [(v.source, v.status) for v in got.verdicts] == [
        ("test_harness", "success"),
        ("static_analysis", "failure"),
        ("agent_self_report", "unknown"),
    ]
    assert outcome_recorder.get_outcome(uid("out")) is None
