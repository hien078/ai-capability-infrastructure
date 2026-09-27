"""MCP server composition (plan §29; ADR-006; Phase 12).

One composition over the same services REST uses: the Skills extension
(SEP-2640) for discovery/loading, three stable tools for routing/reporting,
and a ``skill://`` resource template for file bytes. Stateless by contract
(§29.4) — no handler keeps per-session state; durable state lives in the
registry under ``route_run_id``/``bundle_id``.
"""

from typing import Any

from mcp.server.mcpserver.exceptions import ResourceError, ResourceNotFoundError
from mcp.server.mcpserver.server import MCPServer

from aci.adapters.inbound.mcp.skills import SkillCatalog, SkillsExtension
from aci.adapters.inbound.mcp.tools import (
    make_report_outcome_tool,
    make_route_tool,
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
