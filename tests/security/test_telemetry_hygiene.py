"""§61 cases 4, 10 — integrity of served bytes, hygiene of stored telemetry.

Case 4: a skill package hash mismatch is detected on every read — the
        catalog re-verifies manifest digests against the object store, so
        tampered bytes can never be served as a trusted skill.
Case 10: telemetry never persists context hints — a secret-like token in
        TaskContext (repository_summary, file_hints) is used for routing
        only; the stored RouteRun carries the bounded task text and stage
        traces, never the hints (§25.4, §36).
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest.wiring import Container
from aci.adapters.outbound.postgres.assessments import (
    SqlAlchemyLicenseAssessmentRepository,
    SqlAlchemySecurityAssessmentRepository,
)
from aci.adapters.outbound.postgres.route_runs import SqlAlchemyRouteRunRepository
from aci.application.route_capabilities import RouteCapabilitiesService
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.domain.capability.models import RouteCapabilitiesCommand, TaskContext
from aci.domain.policy.models import ClientDescriptor, ProtocolDescriptor, RequestContext
from aci.domain.provenance.models import (
    LicenseAssessment,
    LicensePermissions,
    SecurityAssessment,
)
from aci.main import create_app
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def write_skill(tmp_path: Path, name: str, description: str, body: str) -> Path:
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return src


def open_gates(
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    cap: str,
) -> None:
    license_repo.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=cap,
            version="1.0.0",
            license_identifier="MIT",
            permissions=LicensePermissions(can_redistribute=True),
            assessed_at=NOW,
            assessed_by="sec-suite",
        )
    )
    security_repo.put_assessment(
        SecurityAssessment(
            assessment_id=uid("sec"),
            capability_id=cap,
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="sec-suite",
        )
    )
    promotion.promote(cap, "1.0.0", "production", approved_by="sec-suite", now=NOW)


def test_hash_mismatch_never_serves_tampered_bytes(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: SqlAlchemyLicenseAssessmentRepository,
    security_repo: SqlAlchemySecurityAssessmentRepository,
    tmp_path: Path,
) -> None:
    """§61 case 4: a tampered blob fails digest re-verification on read."""

    name = uid("sec-tamper")
    src = write_skill(tmp_path, name, "calibrates resonance manifolds", "harmless body")
    result = ingestion.ingest_local(src, now=NOW)
    open_gates(promotion, license_repo, security_repo, result.capability_id)

    # Corrupt the stored blob behind the manifest's back.
    blob_root = tmp_path / "objects"
    blobs = sorted(blob_root.rglob("*"))
    payload = next(p for p in blobs if p.is_file())
    payload.write_bytes(b"tampered contents\n")

    settings = Settings(database_url=DB_URL, object_store_root=str(blob_root))
    app = create_app(Container(settings))
    with TestClient(app) as client:
        response = client.get(f"/opencode/skills/{result.capability_id}/{result.capability_id}.md")
        assert response.status_code == 409, response.text
        assert response.json()["error"]["code"] == "ARTIFACT_INTEGRITY_ERROR"


def test_telemetry_never_persists_context_hints(
    route_service: RouteCapabilitiesService,
    route_run_repo: SqlAlchemyRouteRunRepository,
) -> None:
    """§61 case 10: a secret-like token in context hints stays out of telemetry.

    TaskContext hints (repository_summary, file_hints) feed the routing
    pipeline only; the persisted RouteRun (§36) stores the bounded task
    text plus stage traces — a full row dump must not contain the secret.
    """
    secret = "sk-SECURITY-TEST-TOKEN-7f3a9"
    command = RouteCapabilitiesCommand(
        task_text="fix the flaky integration test",
        context=TaskContext(
            language="python",
            repository_summary=f"repo config: AWS_SECRET_ACCESS_KEY={secret}",
            file_hints=[".env", "secrets/prod.yaml"],
        ),
    )
    request = RequestContext(
        request_id=f"req_{uid('r')}",
        trace_id=f"trc_{uid('t')}",
        principal_id="sec-suite",
        client=ClientDescriptor(type="sec-suite"),
        protocol=ProtocolDescriptor(type="test", version="1"),
    )
    context = request.to_routing_context(command.context)
    routed = route_service.route(command, context, request=request, now=NOW)

    run = route_run_repo.get_route_run(routed.route_run_id)
    assert run is not None
    dump = repr(run.model_dump(mode="json"))
    assert secret not in dump
    assert "AWS_SECRET_ACCESS_KEY" not in dump
    assert "prod.yaml" not in dump
    assert run.task_text == "fix the flaky integration test"
