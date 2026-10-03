"""MCP server entry point: ``python -m aci.adapters.inbound.mcp`` (plan §29; Phase 12).

Stdio is the default and universal transport (local clients spawn the
server as a child process). ``--transport streamable-http`` serves the same
composition over HTTP (official SDK transport, stateless §29.4) for clients
that cannot spawn processes — remote/MCP-over-HTTP deployments. Configuration
comes from the environment (``ACI_*``), the same source as the REST app;
``ACI_API_TOKEN`` gates the HTTP transport exactly like the REST routes.
"""

import argparse

from aci.adapters.inbound.mcp.http import run_streamable_http
from aci.adapters.inbound.mcp.server import create_mcp_server
from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m aci.adapters.inbound.mcp",
        description="Serve the ACI MCP surface (skills extension + stable tools).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default="stdio",
        help="stdio (default, for local clients) or streamable-http (HTTP endpoint)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host (streamable-http only)")
    parser.add_argument("--port", type=int, default=8000, help="bind port (streamable-http only)")
    args = parser.parse_args()

    container = Container(Settings())
    if args.transport == "stdio":
        server = create_mcp_server(container)
        server.run("stdio")
    else:
        run_streamable_http(container, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
