"""Phase 12 protocol acceptance (§52, §62; ADR-006): generic MCP client round trip.

The §52 acceptance for this phase: a *generic* MCP test client (the official
SDK's ClientSession over the in-memory transport — no server-side shortcuts)
can route a task and load a production skill end to end: negotiate, list the
stable tools, route, discover skills via the Skills extension, read file
bytes via resources/read, and report an outcome. Also pins the §45 error
contract (unknown skill/file → -32602; domain errors → tool execution errors
carrying the stable code) and §29.4 statelessness (a second, fresh session
sees the same registry state — nothing lives in MCP session memory).
"""

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from mcp.client._memory import InMemoryTransport
from mcp.client.session import ClientSession
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, Request

from aci.adapters.inbound.mcp.server import create_mcp_server
from aci.adapters.inbound.mcp.skills import (
    EXTENSION_ID,
    SkillsGetRequestParams,
    SkillsGetResult,
    SkillsListResult,
)
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
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


def mcp_container(tmp_path: Path) -> Container:
    """Container whose object store matches the ingestion fixture's tmp root."""
    return Container(Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects")))


def test_mcp_generic_client_routes_and_loads_production_skill(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    token = uid("quuxflange")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    server = create_mcp_server(mcp_container(tmp_path))

    async def scenario() -> None:
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                # Negotiation (§29: current stateless model): server/discover
                # advertises the Skills extension under capabilities.extensions.
                discovered = await session.discover()
                caps = discovered.capabilities
                assert caps is not None
                assert EXTENSION_ID in (caps.extensions or {})
                assert caps.tools, "routing tools must be advertised"
                assert caps.resources, "skill:// template must be advertised"

                # §29.2: small stable tool set — never one tool per skill.
                tools = await session.list_tools()
                assert {t.name for t in tools.tools} == {
                    "route_capabilities",
                    "search_capabilities",
                    "report_outcome",
                }

                # Route through the §14 pipeline; read the fields the §45
                # contract promises (run id, pinned bundle items).
                routed = await session.call_tool(
                    "route_capabilities", {"task_text": f"calibrate {token} now"}
                )
                assert not routed.is_error, routed.content
                payload: dict[str, Any] = routed.structured_content
                assert payload["route_run_id"].startswith("route_")
                bundle = payload["bundle"]
                assert bundle["bundle_id"].startswith("bun_")
                assert bundle["items"], "expected the promoted skill to be selected"
                assert bundle["items"][0]["capability_id"] == cap

                # Skills extension: list → entry with raw frontmatter + manifest.
                listed = await session.send_request(
                    Request[PaginatedRequestParams, str](
                        method="skills/list", params=PaginatedRequestParams()
                    ),
                    SkillsListResult,
                )
                entry = next(e for e in listed.skills if e.uri == f"skill://{cap}/SKILL.md")
                assert entry.frontmatter["name"] == cap
                manifest_uris = {r.uri for r in entry.resources}
                assert f"skill://{cap}/SKILL.md" in manifest_uris
                assert all(r.digest.startswith("sha256:") for r in entry.resources)

                # skills/get by entry URI → the same entry.
                got = await session.send_request(
                    Request[SkillsGetRequestParams, str](
                        method="skills/get", params=SkillsGetRequestParams(uri=entry.uri)
                    ),
                    SkillsGetResult,
                )
                assert got.skill.uri == entry.uri

                # File bytes over the core resources/read (§29.3).
                read = await session.read_resource(f"skill://{cap}/SKILL.md")
                assert f"name: {cap}" in read.contents[0].text

                # Unknown skill file → -32602 (SEP-2164 / SEP-2640).
                with pytest.raises(MCPError) as exc:
                    await session.read_resource(f"skill://{cap}/nope.md")
                assert exc.value.code == INVALID_PARAMS

                # Outcome instrumentation with the ids the route produced.
                reported = await session.call_tool(
                    "report_outcome",
                    {
                        "route_run_id": payload["route_run_id"],
                        "bundle_id": bundle["bundle_id"],
                        "verdicts": [
                            {
                                "source": "test_harness",
                                "status": "success",
                                "confidence": "high",
                            }
                        ],
                        "tests_after": {"failed": 0},
                    },
                )
                assert not reported.is_error, reported.content
                assert reported.structured_content["outcome_id"].startswith("out_")

                # §45 error contract over tools: domain errors are tool
                # execution errors carrying the stable code (SEP-1303), and
                # argument violations are tool errors the model can correct.
                missing = await session.call_tool(
                    "report_outcome",
                    {
                        "route_run_id": "route_nope",
                        "bundle_id": "bun_nope",
                        "verdicts": [{"source": "client_report", "status": "unknown"}],
                    },
                )
                assert missing.is_error
                assert "BUNDLE_NOT_FOUND" in missing.content[0].text

                bad_args = await session.call_tool("route_capabilities", {"task_text": ""})
                assert bad_args.is_error

    asyncio.run(scenario())


def test_mcp_stateless_second_session_sees_same_registry(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo,
    security_repo,
    tmp_path: Path,
) -> None:
    """§29.4: no in-memory session state — a brand-new session (fresh
    transport, fresh handshake) routes against the same registry rows."""
    token = uid("zorbflux")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"align the {token} waveguide")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    server = create_mcp_server(mcp_container(tmp_path))

    async def route_once() -> dict[str, Any]:
        async with InMemoryTransport(server) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                routed = await session.call_tool(
                    "route_capabilities", {"task_text": f"align {token} waveguide"}
                )
                assert not routed.is_error, routed.content
                return routed.structured_content

    first = asyncio.run(route_once())
    second = asyncio.run(route_once())
    assert first["bundle"]["items"][0]["capability_id"] == cap
    assert second["bundle"]["items"][0]["capability_id"] == cap
    # Distinct runs, distinct ids — state lives in the registry, not the session.
    assert first["route_run_id"] != second["route_run_id"]
