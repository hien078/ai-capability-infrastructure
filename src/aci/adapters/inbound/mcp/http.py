"""MCP over streamable HTTP (official SDK transport; plan §29, ADR-006).

Two ways to serve it, one composition:

- **Mounted** — ``create_app`` serves it at ``/mcp`` inside the same process
  as REST/catalog/A2A (``mcp_streamable_http_route``); the session manager's
  ``run()`` is entered by the FastAPI lifespan.
- **Standalone** — ``python -m aci.adapters.inbound.mcp --transport
  streamable-http [--host --port]`` serves just the MCP surface
  (``run_streamable_http``); stdio stays the default transport.

Stateless by contract (§29.4): ``stateless_http=True`` + ``json_response=True``
— every POST is a fresh transport, no session tracking, no GET SSE stream;
durable state lives in the registry under ``route_run_id``/``bundle_id``.

Auth: the same ``ACI_API_TOKEN`` gate as the REST routes (``rest/auth.py`` —
constant-time compare, empty token = unauthenticated localhost mode). The
gate wraps the whole ASGI app, so it covers the Skills extension, the
``skill://`` resources and every tool including the agent-run tools.

DNS-rebinding protection: the standalone server binds a known ``--host`` and
keeps the SDK default (protection ON for localhost binds). The in-app mount
cannot know uvicorn's bind host at build time, so it disables the
Host/Origin validation explicitly — parity with every other ACI HTTP route
(none of them validate Host); the perimeter is the token gate plus the
deployment's network position.
"""

import logging
from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Receive, Scope, Send

from aci.adapters.inbound.mcp.server import create_mcp_server
from aci.adapters.inbound.rest.auth import verify_bearer
from aci.adapters.inbound.rest.wiring import Container

if TYPE_CHECKING:  # pragma: no cover — import shape pinned by the unit tests
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

log = logging.getLogger(__name__)

#: The streamable-HTTP endpoint inside the FastAPI app (spec default path).
MCP_PATH = "/mcp"


class BearerGateASGI:
    """``ACI_API_TOKEN`` gate in front of an ASGI app (rest/auth.py reuse).

    Non-HTTP scopes (lifespan, websocket) pass straight through — the gate
    never breaks the host app's lifecycle. Rejections are byte-identical to
    the REST 401 (``{"detail": "invalid or missing token"}``,
    ``WWW-Authenticate: Bearer``) because they come from the same
    ``verify_bearer``.
    """

    def __init__(self, inner: ASGIApp, token: str) -> None:
        self.inner = inner
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        authorization = Headers(scope=scope).get("authorization")
        try:
            verify_bearer(authorization, self.token)
        except HTTPException as exc:
            response = JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )
            await response(scope, receive, send)
            return
        await self.inner(scope, receive, send)


def _streamable_http_components(
    container: Container, *, streamable_http_path: str, host: str | None
) -> tuple[ASGIApp, "StreamableHTTPSessionManager"]:
    """The gated SDK streamable-HTTP app + its session manager.

    ``host``: the bind host when known (standalone) → the SDK auto-enables
    DNS-rebinding protection for localhost binds; ``None`` (in-app mount)
    → protection explicitly off (see module docstring).
    """
    from mcp.server.transport_security import TransportSecuritySettings

    server = create_mcp_server(container)
    security = (
        None
        if host is not None
        else TransportSecuritySettings(enable_dns_rebinding_protection=False)
    )
    app = server.streamable_http_app(
        streamable_http_path=streamable_http_path,
        stateless_http=True,
        json_response=True,
        transport_security=security,
        host=host or "127.0.0.1",
    )
    manager = server.session_manager  # created by streamable_http_app()
    gated = BearerGateASGI(app, container.settings.api_token)
    return gated, manager


class MCPMount:
    """The in-app ``/mcp`` endpoint, rebuildable per app lifespan.

    The SDK's ``StreamableHTTPSessionManager.run()`` may be entered ONCE per
    instance, but one app can see several lifespans (each TestClient /
    ``lifespan_context``; a server restart in-process). ``start()`` builds a
    FRESH gated SDK app + session manager and swaps it in; the route always
    delegates to the current one. Requests before the first ``start()`` are
    answered 503 (the session manager is not running yet)."""

    def __init__(self, container: Container) -> None:
        self._container = container
        self._current: ASGIApp | None = None
        self.route = Route(
            MCP_PATH, endpoint=self, name="mcp-streamable-http", include_in_schema=False
        )

    def start(self) -> "StreamableHTTPSessionManager":
        gated, manager = _streamable_http_components(
            self._container, streamable_http_path=MCP_PATH, host=None
        )
        self._current = gated
        return manager

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self._current is None:
            await send({"type": "http.response.start", "status": 503, "headers": []})
            await send({"type": "http.response.body", "body": b"MCP transport not started"})
            return
        await self._current(scope, receive, send)


def mcp_streamable_http_route(container: Container) -> MCPMount:
    """The ``/mcp`` mount for ``create_app`` (one process with
    REST/catalog/A2A). The host MUST call ``mount.start()`` in every lifespan
    and enter the returned manager (``async with manager.run():`` — a raw
    ASGI route has no lifespan of its own; ``aci.main.lifespan`` does it)."""
    return MCPMount(container)


def run_streamable_http(container: Container, *, host: str, port: int) -> None:
    """Serve the MCP streamable-HTTP transport standalone (uvicorn).

    The SDK app's own lifespan (``session_manager.run()``) runs because
    uvicorn drives the lifespan of the app it serves — the gate passes
    non-HTTP scopes through untouched.
    """
    import uvicorn

    gated, _manager = _streamable_http_components(
        container, streamable_http_path=MCP_PATH, host=host
    )
    log.info("MCP streamable-HTTP listening on http://%s:%d%s", host, port, MCP_PATH)
    uvicorn.run(gated, host=host, port=port)
