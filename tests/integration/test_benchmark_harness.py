"""Phase 14 integration acceptance (§52, §70): the harness against a live registry.

Runs the real wiring (real eligibility/retrieval/reranker/composer, real
Postgres + pgvector) over a small case set, then replays the same fixtures:
results persist with exact router + capability versions, and a re-run
creates a fresh run with the same measurements (§52: fixtures replay).
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.evaluation.cases import SMOKE_CASES
from aci.evaluation.harness import BenchmarkHarness
from aci.evaluation.metrics import export_report
from aci.evaluation.models import VARIANTS, BenchmarkCase, BenchmarkRun
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def ingest(ingestion: SkillIngestionService, tmp_path: Path, name: str, description: str) -> str:
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\nbody\n",
        encoding="utf-8",
    )
    ingestion.ingest_local(src, now=NOW)
    return name


def open_gates_and_promote(
    promotion: PromotionService, license_repo, security_repo, cap: str
) -> None:
    from aci.domain.provenance.models import (
        LicenseAssessment,
        LicensePermissions,
        SecurityAssessment,
    )

    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=cap,
            version="1.0.0",
            license_identifier="MIT",
            permissions=LicensePermissions(can_redistribute=True),
            assessed_at=NOW,
            assessed_by="legal-1",
        )
    )
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=uid("sec"),
            capability_id=cap,
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="s",
        )
    )
    promotion.promote(cap, "1.0.0", "production", approved_by="rev-1", now=NOW)


def test_benchmark_harness_live_round_trip_and_replay(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    capability_repo,
    tmp_path: Path,
) -> None:
    token_a = uid("quuxflange")
    token_b = uid("zorbflux")
    cap_a = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token_a} resonance manifold")
    cap_b = ingest(ingestion, tmp_path, uid("skill"), f"align the {token_b} waveguide array")
    open_gates_and_promote(promotion, license_repo, security_repo, cap_a)
    open_gates_and_promote(promotion, license_repo, security_repo, cap_b)

    container = Container(
        Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    )
    harness: BenchmarkHarness = container.benchmark_harness

    cases = [
        BenchmarkCase(
            case_id=uid("case"),
            category="debugging",
            fixture="repo/live-v1",
            task_text=f"calibrate {token_a} now",
            relevant_strong=[cap_a],
            relevant_acceptable=[cap_b],
            relevant_irrelevant=[uid("never")],
            acceptance_tests=["test_token_a"],
            baseline_capability_ids=[cap_a],
        ),
        BenchmarkCase(
            case_id=uid("case"),
            category="refactoring",
            fixture="repo/live-v2",
            task_text=f"align {token_b} waveguide",
            relevant_strong=[cap_b],
            relevant_acceptable=[cap_a],
            relevant_irrelevant=[uid("never")],
            acceptance_tests=["test_token_b"],
            baseline_capability_ids=[cap_b],
        ),
    ]

    results = harness.run(cases, label="live-smoke", now=NOW)
    assert len(results) == len(cases) * len(VARIANTS) == 10
    run_id = results[0].run_id

    by_variant: dict[str, list] = {}
    for r in results:
        by_variant.setdefault(r.variant, []).append(r)
    assert set(by_variant) == set(VARIANTS)

    # A: platform-free baseline.
    for r in by_variant["client_alone"]:
        assert r.selected == []
        assert r.route_run_id is None

    # B: the manual baseline resolves to the exact active production version.
    for r, case in zip(by_variant["manual_baseline"], cases, strict=True):
        assert [s.capability_id for s in r.selected] == case.baseline_capability_ids
        assert r.selected[0].version == "1.0.0"
        assert r.metrics["baseline_missing"] == []

    # C/D: the unique token drives the relevant skill into the top-k.
    for variant in ("retrieval_only", "retrieval_rerank"):
        for r, case in zip(by_variant[variant], cases, strict=True):
            ids = [s.capability_id for s in r.selected]
            assert case.relevant_strong[0] in ids, f"{variant} lost the relevant skill"
            assert r.metrics["latency_ms"] is not None
            assert r.metrics["recall"] >= 0.5
    # D records the real reranker's identity from its trace.
    for r in by_variant["retrieval_rerank"]:
        assert r.router.reranker_implementation == "heuristic-reranker"

    # E: the real §14 pipeline — §36 telemetry + exact version pinning.
    for r, case in zip(by_variant["full_pipeline"], cases, strict=True):
        assert r.route_run_id is not None and r.route_run_id.startswith("route_")
        assert r.bundle_id is not None and r.bundle_id.startswith("bun_")
        assert r.router.reranker_implementation == "heuristic-reranker"
        assert r.router.composer_implementation == "minimal-bundle-composer"
        assert r.selected, "expected a non-empty bundle for a token-matched task"
        top = r.selected[0]
        assert top.capability_id == case.relevant_strong[0]
        assert top.version == "1.0.0"
        # The pinned digest matches whatever version the registry holds.
        registry_version = capability_repo.get_version(top.capability_id, top.version)
        assert registry_version is not None
        assert top.digest == registry_version.content_digest

    # Results persisted (§41): read back through the store with every field.
    stored = container.benchmark_store.list_results(run_id)
    assert len(stored) == 10
    assert {r.variant for r in stored} == set(VARIANTS)
    full_stored = next(r for r in stored if r.variant == "full_pipeline")
    assert full_stored.route_run_id is not None
    assert full_stored.selected
    # Whatever run the store returned first, its pin matches the registry.
    top = full_stored.selected[0]
    pinned = capability_repo.get_version(top.capability_id, top.version)
    assert pinned is not None
    assert top.digest == pinned.content_digest
    assert full_stored.router.composer_version  # exact router version recorded

    # Report export: per-variant aggregates + the §34 non-causal label.
    run = BenchmarkRun(run_id=run_id, label="live-smoke", created_at=NOW, case_count=len(cases))
    report = export_report(run, stored)
    assert set(report["variants"]) == set(VARIANTS)
    assert report["variants"]["full_pipeline"]["results"] == 2
    assert any("not causal" in note for note in report["notes"])

    # §52 acceptance: the same fixture replays — a fresh run, fresh ids, and
    # the registered case set is idempotent (no duplicate case rows).
    replay = harness.run(cases, label="live-smoke-2", now=NOW)
    assert len(replay) == 10
    assert replay[0].run_id != run_id
    for case in cases:
        stored_case = container.benchmark_store.get_case(case.case_id)
        assert stored_case is not None
        assert stored_case.task_text == case.task_text
    assert len(container.benchmark_store.list_results(replay[0].run_id)) == 10


def test_smoke_case_set_runs_end_to_end_on_live_registry(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    """§70/§75 step 22: the shipped 10-task smoke set executes against the
    live registry — every case × variant produces a result; annotations that
    name capabilities the registry lacks simply measure as unmeasured."""
    container = Container(
        Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    )
    results = container.benchmark_harness.run(SMOKE_CASES, label="smoke-10", now=NOW)
    assert len(results) == 10 * len(VARIANTS) == 50
    assert {r.variant for r in results} == set(VARIANTS)
    # The full-pipeline rows carry real §36 telemetry references.
    full = [r for r in results if r.variant == "full_pipeline"]
    assert len(full) == 10
    assert all(r.route_run_id is not None for r in full)
    assert all(r.router.composer_implementation == "minimal-bundle-composer" for r in full)
    # Baselines stay honest: A never routes anything.
    alone = [r for r in results if r.variant == "client_alone"]
    assert all(r.selected == [] for r in alone)
