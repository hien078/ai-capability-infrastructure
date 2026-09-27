"""Stdio entry point: ``python -m aci.adapters.inbound.mcp`` (plan §29; Phase 12).

Stdio is the universal MCP transport (local clients spawn the server as a
child process). Configuration comes from the environment (``ACI_*``), the
same source as the REST app — no MCP-specific settings exist.
"""

from aci.adapters.inbound.mcp.server import create_mcp_server
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings


def main() -> None:
    """Run the MCP server over stdio against the configured registry."""
    server = create_mcp_server(Container(Settings()))
    server.run("stdio")


if __name__ == "__main__":
    main()
