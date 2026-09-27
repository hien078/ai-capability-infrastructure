"""Phase 10 integration acceptance (§52): OpenCode catalog over live DB.

Acceptance: OpenCode can discover/load platform-hosted production skills;
catalog content derives from registry/release state; no duplicated
lifecycle state. The catalog app is built with the SAME object-store root
the ingestion fixture wrote blobs to, proving serving goes through the
content-addressed store — never a second copy of skill bytes.
"""

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.main import create_app
from aci.providers.skills.ingestion import SkillIngestionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def ingest_skill(
    ingestion: SkillIngestionService, tmp_path: Path, name: str, description: str
) -> str:
    src = tmp_path / name
    (src / "references").mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\nversion: 1.0.0\n---\n\nbody\n",
        encoding="utf-8",
    )
    (src / "references" / "guide.md").write_text("# guide\nsteps here\n", encoding="utf-8")
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


@pytest.fixture()
def catalog_client(tmp_path: Path) -> TestClient:
    """App wired to the same object-store root the ingestion fixture uses."""
    settings = Settings(
        database_url=DB_URL,
        object_store_root=str(tmp_path / "objects"),
    )
    app = create_app(Container(settings))
    with TestClient(app) as client:
        yield client


def test_catalog_end_to_end_discover_and_lazy_load(
    catalog_client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    token = uid("quuxflange")
    cap = ingest_skill(
        ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold"
    )
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    # A raw-quarantined skill must never appear in the production catalog.
    raw_only = ingest_skill(ingestion, tmp_path, uid("skill"), "unreviewed draft skill")

    index = catalog_client.get("/opencode/skills/index.json")
    assert index.status_code == 200, index.text
    entries = {e["name"]: e for e in index.json()["skills"]}
    assert cap in entries
    assert raw_only not in entries
    entry = entries[cap]
    assert entry["version"] == "1.0.0"
    assert entry["files"] == [f"{cap}.md", "references/guide.md"]

    # Lazy load: entry file serves the canonical SKILL.md bytes verbatim.
    entry_file = catalog_client.get(f"/opencode/skills/{cap}/{cap}.md")
    assert entry_file.status_code == 200, entry_file.text
    assert (
        entry_file.content
        == (
            f"---\nname: {cap}\ndescription: calibrate the {token} resonance manifold"
            f"\nversion: 1.0.0\n---\n\nbody\n"
        ).encode()
    )
    assert entry_file.headers["content-type"].startswith("text/markdown")

    # Nested reference files serve too (same manifest, same origin).
    guide = catalog_client.get(f"/opencode/skills/{cap}/references/guide.md")
    assert guide.status_code == 200
    assert guide.content == b"# guide\nsteps here\n"

    # Unknown skill / unknown file / unsafe path: stable 404s, no leakage.
    assert catalog_client.get(f"/opencode/skills/{uid('nope')}/x.md").status_code == 404
    assert catalog_client.get(f"/opencode/skills/{cap}/nope.md").status_code == 404
    traversal = catalog_client.get(f"/opencode/skills/{cap}/..%2f..%2fetc%2fpasswd")
    assert traversal.status_code == 404


def test_catalog_revocation_drops_skill_immediately(
    catalog_client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    cap = ingest_skill(ingestion, tmp_path, uid("skill"), "temporary catalog skill")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)

    assert cap in {
        e["name"] for e in catalog_client.get("/opencode/skills/index.json").json()["skills"]
    }

    # Revocation is a release-pointer flip; the projection re-reads live state
    # so the skill disappears without any catalog-side lifecycle to update.
    promotion.revoke(cap, "production", approved_by="rev-1")

    entries = {
        e["name"] for e in catalog_client.get("/opencode/skills/index.json").json()["skills"]
    }
    assert cap not in entries
    assert catalog_client.get(f"/opencode/skills/{cap}/{cap}.md").status_code == 404
