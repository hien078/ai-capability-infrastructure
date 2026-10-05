"""MCP client self-identification over HTTP (telemetry attribution).

V1 MCP records every call as principal ``anonymous`` (§29.4), so two real
clients on the same endpoint (Goose, Antigravity) were indistinguishable in
``route_runs``. An HTTP client may name itself with the ``X-ACI-Client``
header, or ``?client=<id>`` on the endpoint URL for clients whose config has
no header field; the id becomes the request envelope's ``principal_id``.

Self-asserted, exactly like the REST ``principal_id`` body field — this is
attribution for telemetry, NOT authentication (the perimeter stays the
``ACI_API_TOKEN`` gate + network position). Ids are a strict lowercase slug;
anything else is ignored (the call stays ``anonymous``), never echoed.

The id travels in a ContextVar set for the duration of one ASGI request:
the SDK's stateless transport handles the request in tasks spawned from it,
which copy the context. stdio has no requests → always ``None``.
"""

import re
from contextvars import ContextVar
from urllib.parse import parse_qs

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

CLIENT_HEADER = "x-aci-client"
CLIENT_QUERY_PARAM = "client"
_CLIENT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")

_current: ContextVar[str | None] = ContextVar("aci_mcp_client_id", default=None)


def parse_client_id(raw: str | None) -> str | None:
    """A valid client id, or ``None`` (missing / malformed — ignored)."""
    if raw is None or _CLIENT_ID.fullmatch(raw) is None:
        return None
    return raw


def current_client_id() -> str | None:
    """The calling HTTP client's self-declared id, if any."""
    return _current.get()


class ClientIdentityASGI:
    """Sets :func:`current_client_id` for one HTTP request, then restores it."""

    def __init__(self, inner: ASGIApp) -> None:
        self.inner = inner

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.inner(scope, receive, send)
            return
        raw = Headers(scope=scope).get(CLIENT_HEADER)
        if raw is None:
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            values = query.get(CLIENT_QUERY_PARAM)
            raw = values[0] if values else None
        token = _current.set(parse_client_id(raw))
        try:
            await self.inner(scope, receive, send)
        finally:
            _current.reset(token)
