"""Agent-run surface: run_agent/get_run/cancel_run + the shared read model.

``usage``/``changes`` are the p-agentrun-api additive fields: the SDK must
parse responses BOTH without them (older server) and with them.
"""

from __future__ import annotations

import json

import httpx
import pytest
from aci_client import ACIError, Budget
from aci_wire import WIRE, make_client


def test_run_agent_request_shape_and_read_model() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["agent_run"])

    client = make_client(handler, token="rest-token", agent_runs_token="runs-token")
    run = client.run_agent(
        "add a retry to the uploader",
        global_context="repo: demo",
        constraints=["no new deps"],
        acceptance_criteria=["tests pass"],
        requested_profile="coder",
        max_turns=12,
        budget=Budget(max_turns=10),
        workspace="demo",
        verification_command=["pytest", "-q"],
        write_scopes=["src/"],
        command_prefixes=["pytest"],
        approval_required_tools=["shell"],
        preload_capabilities=True,
    )

    request = seen[0]
    assert request.url.path == "/v1/agent-runs"
    # The agent-runs surface is gated by its OWN token setting.
    assert request.headers["Authorization"] == "Bearer runs-token"
    payload = json.loads(request.read())
    assert payload["objective"] == "add a retry to the uploader"
    assert payload["global_context"] == "repo: demo"
    assert payload["constraints"] == ["no new deps"]
    assert payload["acceptance_criteria"] == ["tests pass"]
    assert payload["requested_profile"] == "coder"
    assert payload["max_turns"] == 12
    assert payload["budget"]["max_turns"] == 10
    assert payload["workspace"] == "demo"
    assert payload["verification_command"] == ["pytest", "-q"]
    assert payload["write_scopes"] == ["src/"]
    assert payload["command_prefixes"] == ["pytest"]
    assert payload["approval_required_tools"] == ["shell"]
    assert payload["preload_capabilities"] is True

    assert run.run_id == "run_1"
    assert run.status == "succeeded"
    assert run.stop_reason == "SUCCESS"
    assert run.evidence_verdict == "PASS"
    assert run.turns == 3
    assert run.tool_calls == 2
    assert run.wall_time_seconds == 12.5


def test_run_agent_omits_unset_optionals() -> None:
    """Optional fields default server-side: the SDK must not send nulls
    (an explicit null is a DIFFERENT request than an absent field)."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["agent_run"])

    make_client(handler).run_agent("do the thing")
    payload = json.loads(seen[0].read())
    assert payload == {
        "objective": "do the thing",
        "global_context": "",
        "constraints": [],
        "acceptance_criteria": [],
        "requested_profile": "coder",
    }


def test_run_agent_agent_runs_token_defaults_to_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["agent_run"])

    client = make_client(handler, token="one-token")
    client.run_agent("t")
    assert seen[0].headers["Authorization"] == "Bearer one-token"


def test_get_run_read_model_tolerates_missing_usage_and_changes() -> None:
    """The p-agentrun-api read model is ADDITIVE: an older server's response
    (no usage/changes) must parse with both defaulting to None."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/agent-runs/run_1"
        return httpx.Response(200, json=WIRE["agent_run"])

    run = make_client(handler).get_run("run_1")
    assert run.usage is None
    assert run.changes is None
    assert run.run_id == "run_1"


def test_get_run_parses_usage_and_changes_when_present() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=WIRE["agent_run_rich"])

    run = make_client(handler).get_run("run_1")
    assert run.usage is not None
    assert run.usage.model_input_tokens == 5000
    assert run.usage.wall_seconds == 12.5
    assert run.changes is not None
    change = run.changes.files[0]
    assert change.path == "src/app.py"  # workspace-relative POSIX, never absolute
    assert change.status == "modified"
    assert change.sha256_after == "e" * 64
    assert run.changes.diff.startswith("--- a/src/app.py")
    assert run.changes.truncated is False


def test_get_run_unknown_maps_to_typed_404() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "ROUTE_RUN_NOT_FOUND", "message": "unknown agent run: x"}},
        )

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).get_run("run_x")
    assert exc_info.value.code == "ROUTE_RUN_NOT_FOUND"


def test_cancel_run_returns_bool() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"cancelled": True})

    assert make_client(handler).cancel_run("run_1") is True
    assert seen[0].url.path == "/v1/agent-runs/run_1/cancel"
    assert seen[0].method == "POST"


def test_cancel_run_denied_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"error": {"code": "TASK_TRANSITION_INVALID", "message": "terminal"}},
        )

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).cancel_run("run_1")
    assert exc_info.value.code == "TASK_TRANSITION_INVALID"
    assert exc_info.value.status_code == 409
