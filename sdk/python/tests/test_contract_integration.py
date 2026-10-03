"""Contract test: the SDK's wire shapes against the REAL app + local DB.

This is the pin that keeps ``aci_client.models`` honest: the MockTransport
tests use canned payloads, this module drives the actual FastAPI app
(``create_app`` + the real ``Container``) over ASGI against the local
``aci`` Postgres, through the REAL gates (ingestion → license → security →
promotion). Any drift between the SDK models and the server's wire schemas
fails here, not in production.

Needs the ``aci`` package (repo context) + a local Postgres; marked
``integration`` so it skips anywhere else. Never points at ``aci_bench``
or any remote host — ``ACI_DATABASE_URL`` or the local dev default only.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("aci.main", reason="contract test needs the aci package (repo context)")

from aci_client import (  # noqa: E402
    ACIError,
    AsyncACIClient,
    OutcomeVerdict,
    TaskContext,
)
from fastapi import FastAPI  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.exc import OperationalError  # noqa: E402

from aci.adapters.inbound.rest.wiring import Container  # noqa: E402
from aci.adapters.outbound.object_store.fs import FsObjectStore  # noqa: E402
from aci.adapters.outbound.postgres.base import make_session_factory  # noqa: E402
from aci.adapters.outbound.postgres.repositories import (  # noqa: E402
    SqlAlchemyArtifactStore,
    SqlAlchemyCapabilityRepository,
    SqlAlchemyReleaseRepository,
)
from aci.adapters.outbound.postgres.source_records import (  # noqa: E402
    SqlAlchemySourceRecordRepository,
)
from aci.config import Settings  # noqa: E402
from aci.control_plane.promotion.service import PromotionService  # noqa: E402
from aci.main import create_app  # noqa: E402
from aci.providers.skills.ingestion import SkillIngestionService  # noqa: E402

pytestmark = pytest.mark.integration

DB_URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")
NOW = datetime(2026, 10, 3, tzinfo=UTC)

SKILL_BODY = "Find the root cause first; verify before claiming done.\n"


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def db_ready() -> None:
    """Skip (never error) when the local dev Postgres is down."""
    engine = create_engine(DB_URL, connect_args={"connect_timeout": 2})
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except OperationalError as exc:
        pytest.skip(f"PostgreSQL unavailable: {exc}")
    finally:
        engine.dispose()


@pytest.fixture()
def app(tmp_path: Path, db_ready: None) -> FastAPI:
    """The real app: real Container, local ``aci`` DB, tmp object store.

    Auth/model env is FORCED off (empty strings override any ambient env):
    this contract test must not depend on deployment secrets, and
    ``run_agent`` must fail honestly through the unconfigured gateway
    instead of calling a real model endpoint."""
    settings = Settings(
        database_url=DB_URL,
        object_store_root=str(tmp_path / "objects"),
        api_token="",
        agent_runs_token="",
        agent_model_base_url="",
    )
    return create_app(Container(settings))


@pytest.fixture()
def ingested_skill(tmp_path: Path, db_ready: None) -> dict[str, Any]:
    """A real production skill through the REAL gates, unique ids, sharing
    the app fixture's object-store root (the catalog serves these blobs)."""
    from aci.adapters.outbound.postgres.assessments import (
        SqlAlchemyLicenseAssessmentRepository,
        SqlAlchemySecurityAssessmentRepository,
    )
    from aci.domain.provenance.models import (
        LicenseAssessment,
        LicensePermissions,
        SecurityAssessment,
    )

    engine = create_engine(DB_URL)
    sessions = make_session_factory(engine)
    capabilities = SqlAlchemyCapabilityRepository(sessions)
    releases = SqlAlchemyReleaseRepository(sessions)
    artifacts = SqlAlchemyArtifactStore(sessions)
    source_records = SqlAlchemySourceRecordRepository(sessions)
    licenses = SqlAlchemyLicenseAssessmentRepository(sessions)
    securities = SqlAlchemySecurityAssessmentRepository(sessions)

    name = uid("skill")
    token = uid("quuxflange")
    src = tmp_path / name
    (src / "references").mkdir(parents=True)
    (src / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: calibrate the {token} resonance manifold\n"
        f"version: 1.0.0\n---\n\n{SKILL_BODY}",
        encoding="utf-8",
    )
    (src / "references" / "guide.md").write_text("# guide\nsteps here\n", encoding="utf-8")

    ingestion = SkillIngestionService(
        capabilities=capabilities,
        releases=releases,
        artifacts=artifacts,
        source_records=source_records,
        objects=FsObjectStore(tmp_path / "objects"),
    )
    ingestion.ingest_local(src, now=NOW)

    licenses.put_assessment(
        LicenseAssessment(
            assessment_id=uid("lic"),
            capability_id=name,
            version="1.0.0",
            license_identifier="MIT",
            permissions=LicensePermissions(can_redistribute=True),
            assessed_at=NOW,
            assessed_by="legal-1",
        )
    )
    securities.put_assessment(
        SecurityAssessment(
            assessment_id=uid("sec"),
            capability_id=name,
            version="1.0.0",
            scan_status="passed",
            scanned_at=NOW,
            scanner_version="s",
        )
    )
    promotion = PromotionService(
        capabilities=capabilities,
        releases=releases,
        source_records=source_records,
        licenses=licenses,
        securities=securities,
    )
    promotion.promote(name, "1.0.0", "production", approved_by="rev-1", now=NOW)
    engine.dispose()
    return {"capability_id": name, "token": token}


def test_sdk_paths_exist_in_openapi(app: FastAPI) -> None:
    """Every endpoint the SDK calls exists on the real app (drift guard)."""
    spec = app.openapi()
    paths = spec["paths"]
    for path in (
        "/v1/routes",
        "/v1/capabilities/search",
        "/v1/capabilities/{capability_id}/versions/{version}",
        "/v1/outcomes",
        "/v1/agent-runs",
        "/v1/agent-runs/{run_id}",
        "/v1/agent-runs/{run_id}/cancel",
        "/opencode/skills/index.json",
        "/opencode/skills/{skill_id}/{file_path}",
    ):
        assert path in paths, path


def test_contract_full_loop(app: FastAPI, ingested_skill: dict[str, Any]) -> None:
    """route → get_skill (digest-verified) → report_outcome → search →
    agent-run read model, all through the SDK against the real app."""

    async def scenario() -> None:
        cap_id = ingested_skill["capability_id"]
        token = ingested_skill["token"]
        client = AsyncACIClient("http://testserver", transport=httpx.ASGITransport(app=app))

        # 1. Route: the real §14 pipeline; unique tokens make this run's
        #    skill win on token overlap in the shared accumulating DB.
        routed = await client.route(
            f"calibrate the {token} resonance manifold before launch",
            context=TaskContext(language="python", phase="debugging"),
            max_items=3,
        )
        assert routed.route_run_id
        assert routed.bundle.bundle_id
        ids = [item.capability_id for item in routed.bundle.items]
        assert cap_id in ids, ids

        # 2. get_skill: the real catalog + real object store, sha256-verified.
        skill = await client.get_skill(cap_id)
        assert skill.version == "1.0.0"
        assert SKILL_BODY in skill.skill_md
        assert skill.files_by_path["references/guide.md"].text == "# guide\nsteps here\n"
        # The entry file IS the SKILL.md text.
        assert skill.files_by_path["SKILL.md"].text == skill.skill_md

        # 3. report_outcome: §33 evidence lands on the routed bundle.
        evidence = await client.report_outcome(
            routed.route_run_id,
            routed.bundle.bundle_id,
            [OutcomeVerdict(source="test_harness", status="success", confidence="high")],
            tests_after={"passed": 1, "failed": 0},
            client_status="completed",
        )
        assert evidence.outcome_id
        assert evidence.route_run_id == routed.route_run_id
        assert evidence.verdicts[0].status == "success"

        # 4. search: discovery-only, production-active metadata.
        hits = await client.search(f"calibrate the {token}")
        assert any(hit.capability_id == cap_id for hit in hits)

        # 5. Agent-run read model: unknown run → the §45 typed 404.
        with pytest.raises(ACIError) as exc_info:
            await client.get_run(f"run-{uuid.uuid4().hex[:8]}")
        assert exc_info.value.code == "ROUTE_RUN_NOT_FOUND"
        assert exc_info.value.status_code == 404

        # cancel of an unknown run is a plain False, never an error.
        assert await client.cancel_run(f"run-{uuid.uuid4().hex[:8]}") is False

        # 6. run_agent without a model gateway fails HONESTLY: the kernel
        # records MODEL_FAILURE as the run's stop reason (a typed read
        # model, not an HTTP error — the caller sees exactly what happened).
        run = await client.run_agent(f"calibrate the {token} manifold")
        assert run.run_id
        assert run.status == "failed"
        assert run.stop_reason == "MODEL_FAILURE"
        # The additive p-agentrun-api fields are absent on this server build
        # (not yet merged) — the SDK must tolerate that.
        assert run.usage is None or run.usage.model_input_tokens >= 0
        assert run.changes is None or run.changes.files == []

        # 7. The failed run is readable back (durable store, same process).
        again = await client.get_run(run.run_id)
        assert again.run_id == run.run_id
        assert again.status == "failed"

    asyncio.run(scenario())
