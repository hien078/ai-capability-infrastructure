"""A2A gateway mounted through create_app (V3 §56) — wiring smoke, live DB.

Verifies the router is mounted with the real container: the well-known Agent
Card answers, and delegation without a configured profile fails with the
documented JSON-RPC error (the UnconfiguredExecutor path needs a profile
first, so the honest default surface is: card + clear errors).
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from aci.adapters.inbound.rest.wiring import Container
from aci.config import Settings
from aci.main import create_app

pytestmark = pytest.mark.integration


def test_agent_card_mounted_with_real_container(engine: Any) -> None:  # noqa: ARG001
    app = create_app(Container(Settings()))
    client = TestClient(app)

    card = client.get("/.well-known/agent-card.json")
    assert card.status_code == 200
    body = card.json()
    assert body["name"] == "aci"
    assert body["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card.headers["A2A-Version"] == "1.0"


def test_send_message_without_configured_profile_is_invalid_params(
    engine: Any,  # noqa: ARG001
) -> None:
    app = create_app(Container(Settings()))
    client = TestClient(app)

    response = client.post(
        "/a2a",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "SendMessage",
            "params": {
                "message": {
                    "role": "ROLE_USER",
                    "parts": [{"text": "hi"}],
                    "metadata": {"aci": {"profileId": "none", "capabilityId": "x"}},
                }
            },
        },
    )
    assert response.json()["error"]["code"] == -32602


def test_a2a_token_from_container_settings_gates_rpc(engine: Any) -> None:  # noqa: ARG001
    """ACI_A2A_TOKEN reaches the mounted router through the app container."""
    client = TestClient(create_app(Container(Settings(a2a_token="wired-token"))))
    rpc = {"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "task-none"}}

    assert client.post("/a2a", json=rpc).status_code == 401
    authed = client.post("/a2a", json=rpc, headers={"Authorization": "Bearer wired-token"})
    assert authed.status_code == 200
    assert authed.json()["error"]["code"] == -32001
    assert client.get("/.well-known/agent-card.json").status_code == 200
