"""Phase 11 protocol contract (§62): the OpenCode plugin's HTTP surface.

The plugin itself is TypeScript living inside OpenCode (ADR-005) and cannot
be exercised by pytest. What pytest CAN pin is the exact HTTP contract it
depends on: the request shape it sends, the response fields it reads, the
stable §45 error bodies it parses while failing open, and the outcome
instrumentation path it stashes ids for. If the REST API drifts, this test
fails before any user sees a broken plugin.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.main import create_app
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


@pytest.fixture()
def plugin_client(tmp_path: Path, engine: Engine) -> TestClient:
    """App whose object store matches the ingestion fixture's tmp root, so
    catalog lazy loads resolve the very blobs this test ingested.

    Depends on the conftest ``engine`` fixture so this suite SKIPS (never
    errors) when PostgreSQL is down — the app talks to the real DB."""
    settings = Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    with TestClient(create_app(Container(settings))) as client:
        yield client


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


def plugin_route_body(task_text: str) -> dict[str, object]:
    """Byte-for-byte the body aci-router.ts sends (keep them in sync)."""
    return {
        "task": {"text": task_text},
        "context": {"language": "python", "frameworks": ["fastapi"], "phase": "debugging"},
        "constraints": {
            "max_items": 5,
            "max_context_tokens": 6000,
            "allowed_kinds": ["skill"],
        },
        "principal_id": "opencode",
        "client_type": "opencode",
    }


def test_plugin_route_contract_round_trip(
    plugin_client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    token = uid("quuxflange")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)

    routed = plugin_client.post("/v1/routes", json=plugin_route_body(f"calibrate {token} now"))
    assert routed.status_code == 201, routed.text
    body = routed.json()

    # Fields the plugin reads: run id for metadata, bundle id + item ids to inject.
    assert body["route_run_id"].startswith("route_")
    assert body["bundle"]["bundle_id"].startswith("bun_")
    assert body["bundle"]["items"], "expected the promoted skill to be selected"
    assert body["bundle"]["items"][0]["capability_id"] == cap
    for item in body["bundle"]["items"]:
        assert set(item) >= {"capability_id", "version", "digest", "kind", "role", "load_mode"}

    # The injected ids must resolve through the catalog the plugin points at
    # (README step 2): OpenCode lazy-loads <base>/opencode/skills/<id>/<file>.
    index = plugin_client.get("/opencode/skills/index.json")
    assert index.status_code == 200
    assert cap in {e["name"] for e in index.json()["skills"]}
    entry_file = plugin_client.get(f"/opencode/skills/{cap}/{cap}.md")
    assert entry_file.status_code == 200

    # Outcome instrumentation path: ids stashed in admission metadata are
    # accepted by POST /v1/outcomes (multi-source, never a lone flag).
    outcome = plugin_client.post(
        "/v1/outcomes",
        json={
            "route_run_id": body["route_run_id"],
            "bundle_id": body["bundle"]["bundle_id"],
            "verdicts": [
                {"source": "client_report", "status": "success", "confidence": "low"},
                {"source": "agent_self_report", "status": "unknown", "confidence": "low"},
            ],
            "latency_ms": 1500,
        },
    )
    assert outcome.status_code == 201, outcome.text
    assert outcome.json()["verdicts"][0]["source"] == "client_report"


def test_plugin_fail_open_error_contract(rest_client: TestClient) -> None:
    """Non-2xx responses carry stable §45 bodies the plugin logs on its fail-open path."""
    missing = rest_client.get(f"/v1/routes/{uid('route')}")
    assert missing.status_code == 404
    error = missing.json()["error"]
    assert error["code"] == "ROUTE_RUN_NOT_FOUND"
    assert isinstance(error["message"], str)

    # Transport validation failures (422) still admit nothing — the plugin
    # treats any non-2xx identically: log + continue without routed skills.
    invalid = rest_client.post("/v1/routes", json={"task": {}})
    assert invalid.status_code == 422
