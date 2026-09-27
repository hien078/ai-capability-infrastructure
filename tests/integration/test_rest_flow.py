"""Phase 9 acceptance (plan §52): REST runtime API over the live DB.

Acceptance: the API is independent of OpenCode — a second client (here:
FastAPI's TestClient speaking plain HTTP) drives the whole V1 loop through
the public contract only: route -> bundle lookup -> telemetry -> outcome
ingestion -> registry reads -> search. No core adapter changes involved.

The live DB is shared and accumulates rows across runs, so every fixture
uses unique ids (`uid`) and unique description tokens — the routing test
must win on token overlap, never on global emptiness.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

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


def test_rest_v1_loop_end_to_end(
    rest_client,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    capability_repo,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    # Unique tokens so this run's skill outranks anything accumulated in the shared DB.
    token = uid("quuxflange")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)

    # 1. POST /v1/routes — the full §14 pipeline behind one HTTP call.
    routed = rest_client.post(
        "/v1/routes",
        json={
            "task": {"text": f"calibrate the {token} resonance manifold before launch"},
            "context": {"language": "python", "phase": "debugging"},
            "constraints": {
                "max_items": 5,
                "max_context_tokens": 6000,
                "allowed_kinds": ["skill"],
            },
            "principal_id": "p-rest",
        },
    )
    assert routed.status_code == 201, routed.text
    body = routed.json()
    route_run_id = body["route_run_id"]
    bundle = body["bundle"]
    assert bundle["items"], "expected the promoted skill to be selected"
    assert bundle["items"][0]["capability_id"] == cap
    assert bundle["items"][0]["version"] == "1.0.0"
    assert bundle["items"][0]["load_mode"] == "lazy"
    # Version + digest pinning against the live registry (§52).
    version = capability_repo.get_version(cap, "1.0.0")
    assert version is not None
    assert bundle["items"][0]["digest"] == version.content_digest
    bundle_id = bundle["bundle_id"]

    # 2. GET /v1/bundles/{id} — replayable, immutable.
    got_bundle = rest_client.get(f"/v1/bundles/{bundle_id}")
    assert got_bundle.status_code == 200, got_bundle.text
    assert got_bundle.json()["bundle_id"] == bundle_id
    items = got_bundle.json()["items"]
    # The shared DB routes over every active production skill: our unique-token
    # skill must lead the rank order, and the bundle stays minimal (≤ 5).
    assert items[0]["capability_id"] == cap
    assert len(items) <= 5

    # 3. GET /v1/routes/{id} — §36 telemetry with per-stage traces.
    got_run = rest_client.get(f"/v1/routes/{route_run_id}")
    assert got_run.status_code == 200, got_run.text
    run = got_run.json()
    assert run["principal_id"] == "p-rest"
    assert run["protocol_type"] == "rest"
    assert run["bundle_id"] == bundle_id
    assert run["eligible_count"] >= 1
    assert run["error_code"] is None
    assert run["reranker_implementation"] == "heuristic-reranker"
    assert run["composer_implementation"] == "minimal-bundle-composer"
    assert "eligibility" in run["stages"]
    assert "retrieval" in run["stages"]
    assert "rerank" in run["stages"]

    # 4. POST /v1/outcomes — multi-source evidence attaches to the run.
    outcome = rest_client.post(
        "/v1/outcomes",
        json={
            "route_run_id": route_run_id,
            "bundle_id": bundle_id,
            "verdicts": [
                {"source": "test_harness", "status": "success", "confidence": "high"},
                {"source": "agent_self_report", "status": "unknown", "confidence": "low"},
            ],
            "tests_before": {"failed": 2},
            "tests_after": {"failed": 0},
            "latency_ms": 900,
        },
    )
    assert outcome.status_code == 201, outcome.text
    assert outcome.json()["verdicts"][0]["source"] == "test_harness"
    assert outcome.json()["verdicts"][1]["status"] == "unknown"

    # 5. GET /v1/capabilities/{id} — registry read.
    detail = rest_client.get(f"/v1/capabilities/{cap}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["capability"]["id"] == cap
    assert any(v["version"] == "1.0.0" for v in detail.json()["versions"])

    # 6. GET /v1/capabilities/{id}/versions/{version} — resolve to pinned artifact.
    resolved = rest_client.get(f"/v1/capabilities/{cap}/versions/1.0.0")
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["version"]["content_digest"] == version.content_digest
    assert resolved.json()["artifact"]["capability_id"] == cap

    # 7. POST /v1/capabilities/search — discovery finds it by unique tokens.
    searched = rest_client.post(
        "/v1/capabilities/search",
        json={"query": f"{token} resonance", "filters": {"kinds": ["skill"]}, "limit": 10},
    )
    assert searched.status_code == 200, searched.text
    hits = searched.json()
    assert hits, "expected at least one search hit"
    assert any(h["capability_id"] == cap for h in hits)


def test_rest_error_contract_is_stable(rest_client) -> None:
    """§45: stable machine-readable codes, correct statuses, no leakage."""
    missing_cap = rest_client.get(f"/v1/capabilities/{uid('nope')}")
    assert missing_cap.status_code == 404
    assert missing_cap.json()["error"]["code"] == "CAPABILITY_NOT_FOUND"

    missing_version = rest_client.get(f"/v1/capabilities/{uid('nope')}/versions/1.0.0")
    assert missing_version.status_code == 404
    assert missing_version.json()["error"]["code"] == "CAPABILITY_VERSION_NOT_FOUND"

    missing_run = rest_client.get(f"/v1/routes/{uid('route')}")
    assert missing_run.status_code == 404
    assert missing_run.json()["error"]["code"] == "ROUTE_RUN_NOT_FOUND"

    missing_bundle = rest_client.get(f"/v1/bundles/{uid('bun')}")
    assert missing_bundle.status_code == 404
    assert missing_bundle.json()["error"]["code"] == "BUNDLE_NOT_FOUND"

    bogus_outcome = rest_client.post(
        "/v1/outcomes",
        json={
            "route_run_id": uid("route"),
            "bundle_id": uid("bun"),
            "verdicts": [{"source": "test_harness", "status": "success"}],
        },
    )
    assert bogus_outcome.status_code == 404
    assert bogus_outcome.json()["error"]["code"] == "BUNDLE_NOT_FOUND"

    invalid_body = rest_client.post("/v1/routes", json={"task": {}})
    assert invalid_body.status_code == 422  # transport validation, pydantic


def test_rest_zero_max_items_yields_empty_bundle(rest_client) -> None:
    """ADR-008: a 0-item bundle over HTTP is 201, not an error (§19.1)."""
    routed = rest_client.post(
        "/v1/routes",
        json={
            "task": {"text": f"calibrate the {uid('zzz')} resonance manifold"},
            "constraints": {"max_items": 0},
        },
    )
    assert routed.status_code == 201, routed.text
    body = routed.json()
    assert body["bundle"]["items"] == []
    assert body["bundle"]["budget"]["max_items"] == 0
    assert body["route_run_id"].startswith("route_")
