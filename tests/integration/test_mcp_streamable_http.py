"""Streamable-HTTP protocol acceptance (§52/§62; ADR-006): the OFFICIAL MCP
streamable-HTTP transport mounted at ``/mcp`` inside the FastAPI app — one
process with REST/catalog/A2A, stateless (§29.4).

Like ``test_mcp_adapter`` this drives a *generic* SDK client (ClientSession
over the SDK's own streamable-HTTP transport, ``httpx2`` ASGI transport —
no server-side shortcuts, no real sockets): negotiate, list the stable tools,
route, read skill bytes, report an outcome, and the §45 error contract over
the wire. New here vs the stdio round trip:

- the ``ACI_API_TOKEN`` gate over the transport (401 before any MCP message),
- the ``ACI_MCP_AGENT_RUNS`` opt-in: the agent tools appear in tools/list and
  answer with the honest defaults (unknown run → ROUTE_RUN_NOT_FOUND; no
  model gateway → a FAILED run result, never a silent default model),
- §29.4 statelessness over HTTP: a second, fresh session sees the same
  registry rows.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS, PaginatedRequestParams, Request

from aci.adapters.inbound.mcp.skills import (
    EXTENSION_ID,
    SkillsListResult,
)
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.control_plane.promotion.service import PromotionService
from aci.main import create_app
from aci.providers.skills.ingestion import SkillIngestionService
from tests.integration.test_mcp_adapter import ingest, open_gates_and_promote, uid

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 3, tzinfo=UTC)
DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"
BASE = "http://testserver"


def mcp_app(tmp_path: Path, **settings: Any) -> Any:
    """The full app (REST + /mcp) over a Container matching the fixture's
    object-store root."""
    return create_app(
        Container(
            Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"), **settings)
        )
    )


def _http_client(app: Any, token: str | None = None) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else None
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url=BASE, headers=headers
    )


def test_streamable_http_generic_client_round_trip(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: Any,
    security_repo: Any,
    tmp_path: Path,
) -> None:
    token = uid("quuxflange")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"calibrate the {token} resonance manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    app = mcp_app(tmp_path)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with _http_client(app) as http:
                async with streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        discovered = await session.discover()
                        caps = discovered.capabilities
                        assert caps is not None
                        assert EXTENSION_ID in (caps.extensions or {})
                        tools = {t.name for t in (await session.list_tools()).tools}
                        assert tools == {
                            "route_capabilities",
                            "search_capabilities",
                            "report_outcome",
                        }

                        routed = await session.call_tool(
                            "route_capabilities", {"task_text": f"calibrate {token} now"}
                        )
                        assert not routed.is_error, routed.content
                        payload: dict[str, Any] = routed.structured_content
                        assert payload["route_run_id"].startswith("route_")
                        bundle = payload["bundle"]
                        assert bundle["items"], "expected the promoted skill to be selected"
                        assert bundle["items"][0]["capability_id"] == cap

                        listed = await session.send_request(
                            Request[PaginatedRequestParams, str](
                                method="skills/list", params=PaginatedRequestParams()
                            ),
                            SkillsListResult,
                        )
                        entry = next(e for e in listed.skills if e.uri == f"skill://{cap}/SKILL.md")
                        assert entry.frontmatter["name"] == cap

                        got = await session.read_resource(f"skill://{cap}/SKILL.md")
                        assert f"name: {cap}" in got.contents[0].text

                        with pytest.raises(MCPError) as exc:
                            await session.read_resource(f"skill://{cap}/nope.md")
                        assert exc.value.code == INVALID_PARAMS

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

    asyncio.run(scenario())


def test_streamable_http_stateless_second_session(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: Any,
    security_repo: Any,
    tmp_path: Path,
) -> None:
    """§29.4 over HTTP: no in-memory session state — a brand-new session
    (fresh transport, fresh handshake) routes against the same registry rows."""
    token = uid("zorbflux")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"align the {token} waveguide")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    app = mcp_app(tmp_path)

    async def route_once(http: httpx2.AsyncClient) -> dict[str, Any]:
        async with streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                routed = await session.call_tool(
                    "route_capabilities", {"task_text": f"align {token} waveguide"}
                )
                assert not routed.is_error, routed.content
                return routed.structured_content

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with _http_client(app) as http:
                first = await route_once(http)
                second = await route_once(http)
        return first, second

    first, second = asyncio.run(scenario())
    assert first["bundle"]["items"][0]["capability_id"] == cap
    assert second["bundle"]["items"][0]["capability_id"] == cap
    assert first["route_run_id"] != second["route_run_id"]


def test_streamable_http_gate_rejects_unauthenticated_mcp(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: Any,
    security_repo: Any,
    tmp_path: Path,
) -> None:
    """ACI_API_TOKEN set: the transport answers 401 before any MCP message
    is parsed; the same request with the Bearer header completes the
    handshake. (The full gate matrix — wrong bearers, constant-time compare —
    lives in tests/security/test_mcp_http_gate.py.)"""
    token = uid("gatekeep")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"gate the {token} manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    app = mcp_app(tmp_path, api_token="s3cret-mcp")

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with _http_client(app) as http:
                response = await http.post(
                    "/mcp",
                    json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                )
                assert response.status_code == 401
                assert response.json() == {"detail": "invalid or missing token"}
            async with _http_client(app, token="s3cret-mcp") as http:
                async with streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        result = await session.initialize()
                        assert result.server_info.name == "aci"

    asyncio.run(scenario())


def test_streamable_http_agent_tools_opt_in(
    ingestion: SkillIngestionService,
    promotion: PromotionService,
    license_repo: Any,
    security_repo: Any,
    tmp_path: Path,
) -> None:
    """ACI_MCP_AGENT_RUNS=on: the three agent tools ride the transport and
    answer with the honest defaults — unknown run → the stable
    ROUTE_RUN_NOT_FOUND code; run_agent_task without a model gateway → a
    FAILED run result (caller-visible, never a silent default model)."""
    token = uid("agenttool")
    cap = ingest(ingestion, tmp_path, uid("skill"), f"agent the {token} manifold")
    open_gates_and_promote(promotion, license_repo, security_repo, cap)
    app = mcp_app(tmp_path, mcp_agent_runs=True)

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            async with _http_client(app) as http:
                async with streamable_http_client(f"{BASE}/mcp", http_client=http) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = {t.name for t in (await session.list_tools()).tools}
                        assert {
                            "run_agent_task",
                            "get_agent_run",
                            "cancel_agent_run",
                        } <= tools

                        missing = await session.call_tool("get_agent_run", {"run_id": "run_nope"})
                        assert missing.is_error
                        assert "ROUTE_RUN_NOT_FOUND" in missing.content[0].text

                        cancelled = await session.call_tool(
                            "cancel_agent_run", {"run_id": "run_nope"}
                        )
                        assert not cancelled.is_error
                        assert cancelled.structured_content == {"cancelled": False}

                        # No ACI_AGENT_MODEL_BASE_URL on this app: the run fails
                        # caller-visibly (the honest default), never silently.
                        run = await session.call_tool(
                            "run_agent_task", {"objective": "do anything at all", "max_turns": 2}
                        )
                        assert not run.is_error, run.content
                        result: dict[str, Any] = run.structured_content
                        assert result["status"] == "failed"
                        assert result["stop_reason"] == "MODEL_FAILURE"

    asyncio.run(scenario())
