"""aci console (ops UI) — contract tests over the real app.

The console is a new EXPOSURE surface, so the §61 rules apply to it like
every other client surface: staging must be invisible, unknown ids 404,
and the inspector must run REAL routes (telemetry persisted, principal
'console') — never a shadow path.
"""

import re
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

NOW = datetime(2026, 9, 30, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    settings = Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"))
    return TestClient(create_app(Container(settings)))


def test_every_page_renders(client: TestClient) -> None:
    for path in ("/ui/", "/ui/routes", "/ui/corpus", "/ui/try"):
        response = client.get(path)
        assert response.status_code == 200
        assert "aci" in response.text
    # the stylesheet is served from the app itself (no CDN, no build step)
    css = client.get("/ui/static/ui.css")
    assert css.status_code == 200
    assert b"--accent" in css.content


def test_try_runs_a_real_route_and_lands_on_its_detail_page(client: TestClient) -> None:
    """The inspector is not a shadow path: POST /try persists a real
    route_run (principal 'console') and redirects to its detail page."""
    posted = client.post(
        "/ui/try",
        data={
            "task": f"calibrate {uid('probe')} resonance manifolds",
            "language": "python",
            "frameworks": "pytest",
            "phase": "debugging",
        },
        follow_redirects=False,
    )
    assert posted.status_code == 303
    location = posted.headers["location"]
    assert re.fullmatch(r"/ui/routes/route_[a-f0-9]+", location)

    detail = client.get(location)
    assert detail.status_code == 200
    assert "console" in detail.text
    # stage traces render — the §14 pipeline data made visible
    assert "eligibility" in detail.text
    assert "retrieval" in detail.text
    # the run shows up in the routes list like any other client's
    listing = client.get("/ui/routes?principal=console")
    assert listing.status_code == 200
    assert location.rsplit("/", 1)[-1] in listing.text


def test_unknown_ids_404(client: TestClient) -> None:
    assert client.get("/ui/routes/route_doesnotexist").status_code == 404
    assert client.get("/ui/corpus/cap-doesnotexist").status_code == 404


def test_staging_release_is_invisible_in_the_console(
    client: TestClient,
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    tmp_path: Path,
) -> None:
    """§61 exposure boundary, console edition: a staging-only release
    (what auto-staging produces, ADR-013) must not appear in /ui/corpus —
    the console reads production only, same as every other surface."""

    name = uid("ui-staging")
    src = tmp_path / name
    src.mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: calibrates resonance manifolds\n"
        f"version: 1.0.0\n---\n\nharmless body\n",
        encoding="utf-8",
    )
    result = ingestion.ingest_local(src, now=NOW)
    promotion.promote(result.capability_id, "1.0.0", "staging", approved_by="test", now=NOW)

    corpus = client.get("/ui/corpus")
    assert corpus.status_code == 200
    assert result.capability_id not in corpus.text
    # and the capability detail page still renders (registry row exists)
    detail = client.get(f"/ui/corpus/{result.capability_id}")
    assert detail.status_code == 200
    assert "staging" in detail.text
