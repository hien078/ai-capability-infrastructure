"""Phase 13 acceptance (§52, §33; ADR-010): the full evidence envelope round trips.

- ``agent_self_report`` and ``test_harness`` verdicts are stored as separate
  rows (never merged into one boolean before storage);
- the route run joins to the exact bundle/version/digest it produced;
- build/test/lint observations, the human correction flag, cost, and latency
  persist verbatim (§33) — each optional, never collapsed into one flag.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aci.control_plane.promotion.service import PromotionService
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)


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


def test_outcome_evidence_envelope_round_trips(
    rest_client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    capability_repo,
    outcome_service,
    tmp_path: Path,
) -> None:
    token = uid("quuxflange")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)

    routed = rest_client.post(
        "/v1/routes",
        json={
            "task": {"text": f"calibrate {token} now"},
            "context": {"language": "python", "frameworks": [], "phase": "debugging"},
            "constraints": {
                "max_items": 5,
                "max_context_tokens": 6000,
                "allowed_kinds": ["skill"],
            },
            "principal_id": "p-outcome",
        },
    )
    assert routed.status_code == 201, routed.text
    route_run_id = routed.json()["route_run_id"]
    bundle_id = routed.json()["bundle"]["bundle_id"]
    item = routed.json()["bundle"]["items"][0]
    assert item["capability_id"] == cap

    # The full §33 envelope: verdicts from two distinct sources, build/test/lint
    # observations, human correction, cost, latency, and client completion.
    outcome = rest_client.post(
        "/v1/outcomes",
        json={
            "route_run_id": route_run_id,
            "bundle_id": bundle_id,
            "verdicts": [
                {"source": "test_harness", "status": "success", "confidence": "high"},
                {"source": "agent_self_report", "status": "unknown", "confidence": "low"},
            ],
            "tests_before": {"passed": 31, "failed": 4},
            "tests_after": {"passed": 35, "failed": 0},
            "latency_ms": 4200,
            "client_status": "completed",
            "lint_passed": True,
            "build_passed": True,
            "changed_files": 3,
            "tool_calls": 17,
            "human_corrected": False,
            "input_tokens": 12_345,
            "output_tokens": 6_789,
            "estimated_usd": 0.42,
        },
    )
    assert outcome.status_code == 201, outcome.text
    outcome_id = outcome.json()["outcome_id"]

    # Read back through the service (not the HTTP response): every §33 field
    # persisted verbatim, nothing collapsed.
    stored = outcome_service.get(outcome_id)
    assert stored is not None
    assert stored.route_run_id == route_run_id
    assert stored.bundle_id == bundle_id
    # Verdicts stay per-source and ordered — agent_self_report and
    # test_harness are separate rows, `unknown` stays `unknown` (ADR-010).
    assert [(v.source, v.status, v.confidence) for v in stored.verdicts] == [
        ("test_harness", "success", "high"),
        ("agent_self_report", "unknown", "low"),
    ]
    assert stored.tests_before == {"passed": 31, "failed": 4}
    assert stored.tests_after == {"passed": 35, "failed": 0}
    assert stored.latency_ms == 4200
    assert stored.client_status == "completed"
    assert stored.lint_passed is True
    assert stored.build_passed is True
    assert stored.changed_files == 3
    assert stored.tool_calls == 17
    assert stored.human_corrected is False
    assert stored.input_tokens == 12_345
    assert stored.output_tokens == 6_789
    assert stored.estimated_usd == pytest.approx(0.42)

    # Join acceptance (§52): the outcome's route run resolves to the exact
    # bundle, and the bundle item pins the exact version + digest the
    # registry holds for that version.
    run = rest_client.get(f"/v1/routes/{route_run_id}")
    assert run.status_code == 200, run.text
    assert run.json()["bundle_id"] == bundle_id

    got_bundle = rest_client.get(f"/v1/bundles/{bundle_id}")
    assert got_bundle.status_code == 200, got_bundle.text
    pinned = got_bundle.json()["items"][0]
    assert pinned["capability_id"] == cap
    version = capability_repo.get_version(cap, pinned["version"])
    assert version is not None
    assert pinned["digest"] == version.content_digest
    assert pinned["version"] == "1.0.0"


def test_outcome_evidence_partial_stays_honest(
    rest_client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    outcome_service,
    tmp_path: Path,
) -> None:
    """Partial evidence is still honest evidence: unset §13 fields stay None —
    the platform never invents a build/lint verdict or cost that the caller
    did not report."""
    token = uid("zorbflux")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"align the {token} waveguide")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)

    routed = rest_client.post(
        "/v1/routes",
        json={
            "task": {"text": f"align {token} waveguide"},
            "constraints": {
                "max_items": 5,
                "max_context_tokens": 6000,
                "allowed_kinds": ["skill"],
            },
            "principal_id": "p-outcome",
        },
    )
    assert routed.status_code == 201, routed.text
    route_run_id = routed.json()["route_run_id"]
    bundle_id = routed.json()["bundle"]["bundle_id"]

    outcome = rest_client.post(
        "/v1/outcomes",
        json={
            "route_run_id": route_run_id,
            "bundle_id": bundle_id,
            "verdicts": [{"source": "client_report", "status": "unknown", "confidence": "low"}],
        },
    )
    assert outcome.status_code == 201, outcome.text
    stored = outcome_service.get(outcome.json()["outcome_id"])
    assert stored is not None
    assert stored.verdicts[0].source == "client_report"
    assert stored.verdicts[0].status == "unknown"
    for field in (
        "latency_ms",
        "client_status",
        "lint_passed",
        "build_passed",
        "changed_files",
        "tool_calls",
        "human_corrected",
        "input_tokens",
        "output_tokens",
        "estimated_usd",
    ):
        assert getattr(stored, field) is None, field
    assert stored.tests_before == {}
    assert stored.tests_after == {}
