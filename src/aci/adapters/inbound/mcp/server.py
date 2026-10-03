"""MCP server composition (plan §29; ADR-006; Phase 12).

One composition over the same services REST uses: the Skills extension
(SEP-2640) for discovery/loading, three stable tools for routing/reporting,
and a ``skill://`` resource template for file bytes. Stateless by contract
(§29.4) — no handler keeps per-session state; durable state lives in the
registry under ``route_run_id``/``bundle_id``.
"""

import logging
from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError
from mcp.server.mcpserver.server import MCPServer

from aci.adapters.inbound.mcp.skills import SkillCatalog, SkillsExtension
from aci.adapters.inbound.mcp.tools import (
    make_cancel_agent_run_tool,
    make_get_agent_run_tool,
    make_report_outcome_tool,
    make_route_tool,
    make_run_agent_task_tool,
    make_search_tool,
)
from aci.adapters.inbound.rest.wiring import Container
from aci.domain.capability.errors import DomainError, ErrorCode


def create_mcp_server(container: Container) -> MCPServer[Any]:
    """Compose the MCP surface: skills extension + route/report/search tools."""
    catalog = SkillCatalog(
        container.releases,
        container.capabilities,
        container.artifacts,
        container.objects,
    )
    server = MCPServer[Any](
        name="aci",
        title="AI Capability Infrastructure",
        description=(
            "Capability routing, skill discovery/loading, and outcome reporting "
            "(plan §29, ADR-006)."
        ),
        version="1.0.0",
        extensions=[SkillsExtension(catalog)],
    )

    # §29.2: small, stable tool set — never one tool per skill.
    server.add_tool(
        make_route_tool(container.route_service),
        name="route_capabilities",
        description=(
            "Route a task to 0-5 production skills. Returns the route run id, a "
            "bundle pinning exact versions + digests, and stage traces. An empty "
            "bundle is a valid success."
        ),
    )
    server.add_tool(
        make_search_tool(container.search_service),
        name="search_capabilities",
        description=(
            "Discovery-only search over production-active capabilities by "
            "kind/domain facets. The full eligibility policy runs only in "
            "route_capabilities."
        ),
    )
    server.add_tool(
        make_report_outcome_tool(container.outcome_service),
        name="report_outcome",
        description=(
            "Attach multi-source outcome evidence (verdicts with per-source "
            "confidence) to a routed bundle by route_run_id/bundle_id."
        ),
    )

    # Agent-run tools (HarnessKernel surface, ADR-014): gated behind
    # ACI_MCP_AGENT_RUNS (default OFF) — a NEW execution surface. They call
    # the SAME AgentRunService the REST routes use (rest/agent_runs.start_run
    # — one translation, no second path). The /mcp transport itself is gated
    # by ACI_API_TOKEN (http.py); ACI_AGENT_RUNS_TOKEN guards only the REST
    # routes, so an unauthenticated /mcp deployment exposing these tools
    # gets one loud warning, not silence.
    if container.settings.mcp_agent_runs:
        if not container.settings.api_token:
            logging.getLogger("aci.mcp").warning(
                "ACI_MCP_AGENT_RUNS is ON while ACI_API_TOKEN is unset — the "
                "run_agent_task/get_agent_run/cancel_agent_run tools are exposed "
                "over /mcp UNAUTHENTICATED; keep the port on localhost or set "
                "ACI_API_TOKEN before exposing it"
            )
        server.add_tool(
            make_run_agent_task_tool(container.agent_run_service),
            name="run_agent_task",
            description=(
                "Run a delegated agent task on the server to its terminal state "
                "(HarnessKernel): objective + optional constraints, workspace, "
                "verification command, write scopes, turn budget. Returns the "
                "compact result with evidence — the same read model as "
                "GET /v1/agent-runs/{run_id}."
            ),
        )
        server.add_tool(
            make_get_agent_run_tool(container.agent_run_service),
            name="get_agent_run",
            description=(
                "Read one agent run: status, stop reason, summary, evidence "
                "verdict, checks, artifacts, usage. Unknown run → tool error "
                "ROUTE_RUN_NOT_FOUND."
            ),
        )
        server.add_tool(
            make_cancel_agent_run_tool(container.agent_run_service),
            name="cancel_agent_run",
            description="Request cancellation of an agent run (a terminal run answers false).",
        )

    # §29.1/§29.3: file bytes travel over resources/read on skill:// URIs —
    # the same URIs the Skills extension manifest advertises. The catalog
    # whitelists paths by the artifact manifest and re-verifies the sha256 on
    # every read; unknown skill/file → -32602 (SEP-2164), integrity failures
    # → -32603.
    @server.resource(
        "skill://{skill_id}/{+file_path}",
        name="skill_file",
        description="One file of a production skill (manifest-whitelisted, digest-verified).",
        mime_type="text/markdown",
    )
    def skill_file(skill_id: str, file_path: str) -> str:
        try:
            data, _ = catalog.read(skill_id, file_path)
        except DomainError as exc:
            if exc.code == ErrorCode.CAPABILITY_NOT_FOUND:
                raise ResourceNotFoundError(f"unknown skill file {skill_id}/{file_path}") from exc
            raise ResourceError(f"{exc.code.value}: {exc}") from exc
        return data.decode("utf-8")

    return server
