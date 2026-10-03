"""§61 security cases for the MCP streamable-HTTP surface (ADR-006): the
mounted ``/mcp`` transport + the agent-run tool opt-in.

``/mcp`` is a NEW wire surface on the FastAPI app: the Skills extension, the
``skill://`` resources and every tool ride the same transport — including
the agent-run tools (ACI_MCP_AGENT_RUNS), which start runs that execute
workspace code on this host. Load-bearing claims, enforced here:

- With ``ACI_API_TOKEN`` set, every /mcp request needs ``Authorization:
  Bearer <token>`` — rejected BEFORE any MCP message is parsed, with the
  byte-identical REST 401 body (same ``rest/auth.py`` compare, constant-time).
- Token unset = unauthenticated mode (localhost-only by deployment
  assumption) — same rule as the REST routes today.
- The agent-run tools are OFF by default (ACI_MCP_AGENT_RUNS): a default
  deployment's tools/list never mentions them; opting in registers exactly
  the three, and they stay behind the /mcp gate.

Requests are shaped to need no DB rows: the gate answers before the transport
parses a message, and the tool-gating cases read the composed server only.
"""

import asyncio
import hmac
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver.server import MCPServer

from aci.adapters.inbound.mcp.server import create_mcp_server
from aci.adapters.inbound.rest import auth
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.main import create_app

DB_URL = "postgresql+psycopg://aci:aci@localhost:5432/aci"
TOKEN = "s3cret-mcp-token"

#: A well-formed initialize — the gate must pass it through to the transport.
INITIALIZE: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "security-suite", "version": "0"},
    },
}

STABLE_TOOLS = {"route_capabilities", "search_capabilities", "report_outcome"}
AGENT_TOOLS = {"run_agent_task", "get_agent_run", "cancel_agent_run"}


def _app(tmp_path: Path, **settings: Any) -> TestClient:
    """Full app (REST + /mcp mount) over a real Container — the gate runs
    before anything touches the DB, so a down database only turns the
    authenticated-case passes into 500s."""
    config = Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"), **settings)
    return TestClient(create_app(Container(config)))


def _post_mcp(client: TestClient, token: str | None = None) -> Any:
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    return client.post("/mcp", json=INITIALIZE, headers=headers)


def test_mcp_gate_requires_bearer_when_token_set(tmp_path: Path) -> None:
    client = _app(tmp_path, api_token=TOKEN)
    with client:
        missing = _post_mcp(client)
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.json() == {"detail": "invalid or missing token"}
        # wrong/malformed bearers — one POST each, all 401
        for header in (f"Bearer {TOKEN}x", TOKEN, f"Basic {TOKEN}", "Bearer "):
            response = client.post("/mcp", json=INITIALIZE, headers={"Authorization": header})
            assert response.status_code == 401, header
        ok = _post_mcp(client, TOKEN)
        assert ok.status_code == 200, ok.text
        assert ok.json()["result"]["serverInfo"]["name"] == "aci"


def test_mcp_gate_open_when_token_unset(tmp_path: Path) -> None:
    client = _app(tmp_path)
    with client:
        response = _post_mcp(client)
        assert response.status_code == 200, response.text
        assert response.json()["result"]["serverInfo"]["name"] == "aci"


def test_mcp_bearer_compare_is_constant_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[bytes, bytes]] = []
    real = hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    client = _app(tmp_path, api_token=TOKEN)
    with client:
        _post_mcp(client, "wrong")
    assert (b"Bearer wrong", f"Bearer {TOKEN}".encode()) in calls


# -- ACI_MCP_AGENT_RUNS: the agent-run tools are an OPT-IN execution surface --


def _container(tmp_path: Path, **settings: Any) -> Container:
    return Container(
        Settings(database_url=DB_URL, object_store_root=str(tmp_path / "objects"), **settings)
    )


def _tool_names(server: MCPServer[Any]) -> set[str]:
    return {t.name for t in asyncio.run(server.list_tools())}


def test_agent_run_tools_hidden_by_default(tmp_path: Path) -> None:
    server = create_mcp_server(_container(tmp_path))
    assert _tool_names(server) == STABLE_TOOLS


def test_agent_run_tools_opt_in_registers_exactly_three(tmp_path: Path) -> None:
    server = create_mcp_server(_container(tmp_path, mcp_agent_runs=True))
    assert _tool_names(server) == STABLE_TOOLS | AGENT_TOOLS


def test_agent_run_tools_stay_behind_the_mcp_gate(tmp_path: Path) -> None:
    """Opting in does NOT open a second auth path: with ACI_API_TOKEN set,
    an unauthenticated /mcp request is rejected before any tool — including
    run_agent_task — can be reached."""
    client = _app(tmp_path, api_token=TOKEN, mcp_agent_runs=True)
    with client:
        response = _post_mcp(client)
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid or missing token"}
        ok = _post_mcp(client, TOKEN)
        assert ok.status_code == 200
