"""MCP client self-identification over HTTP (telemetry attribution).

Every MCP route used to be recorded as principal ``anonymous`` — Goose and
Antigravity were indistinguishable in route_runs. An HTTP client may now name
itself with ``X-ACI-Client: <id>`` (or ``?client=<id>`` for clients that
cannot set headers); the id becomes the envelope's principal. Self-asserted,
exactly like REST ``principal_id`` — attribution, not authentication.
"""

import asyncio
from typing import Any

import pytest

from aci.adapters.inbound.mcp.identity import (
    ClientIdentityASGI,
    current_client_id,
    parse_client_id,
)
from aci.adapters.inbound.mcp.tools import _envelope


@pytest.mark.parametrize("raw", ["goose-arch", "antigravity", "a", "x1-2-3"])
def test_valid_ids_pass(raw: str) -> None:
    assert parse_client_id(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [None, "", "Goose", "goose arch", "-goose", "a" * 65, "goose/../x", "gö", "opencode\n"],
)
def test_invalid_ids_are_ignored(raw: str | None) -> None:
    assert parse_client_id(raw) is None


def _scope(headers: list[tuple[bytes, bytes]], query: bytes = b"") -> dict[str, Any]:
    return {"type": "http", "headers": headers, "query_string": query}


def _seen(scope: dict[str, Any]) -> str | None:
    captured: list[str | None] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        captured.append(current_client_id())

    asyncio.run(ClientIdentityASGI(inner)(scope, None, None))  # type: ignore[arg-type]
    return captured[0]


def test_header_sets_client_for_the_request() -> None:
    assert _seen(_scope([(b"x-aci-client", b"goose-arch")])) == "goose-arch"


def test_query_param_is_the_fallback() -> None:
    assert _seen(_scope([], b"client=antigravity-arch")) == "antigravity-arch"


def test_header_wins_over_query() -> None:
    assert _seen(_scope([(b"x-aci-client", b"goose")], b"client=other")) == "goose"


def test_invalid_or_missing_leaves_anonymous() -> None:
    assert _seen(_scope([(b"x-aci-client", b"BAD ID")])) is None
    assert _seen(_scope([])) is None


def test_not_leaked_outside_the_request() -> None:
    _seen(_scope([(b"x-aci-client", b"goose")]))
    assert current_client_id() is None


def test_envelope_uses_client_id_as_principal() -> None:
    assert _envelope().principal_id == "anonymous"

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        env = _envelope()
        assert env.principal_id == "goose-arch"
        assert env.client.type == "mcp-client"

    asyncio.run(
        ClientIdentityASGI(inner)(_scope([(b"x-aci-client", b"goose-arch")]), None, None)  # type: ignore[arg-type]
    )
