"""Sync client: route/search/outcome wire shapes, §45 errors, §31.1 retries."""

from __future__ import annotations

import json

import httpx
import pytest
from aci_client import (
    ACIClient,
    ACIConnectionError,
    ACIError,
    ACIValidationError,
    OutcomeVerdict,
    TaskContext,
)
from aci_wire import BASE, WIRE, make_client


def test_route_happy_path_and_request_shape() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["route_result"])

    client = make_client(handler, token="sekrit")
    result = client.route(
        "fix the flaky login test",
        context=TaskContext(language="python", phase="debugging"),
        max_items=3,
        principal_id="p-1",
        organization_id="org-1",
        workspace_id="ws-1",
    )

    # Request shape (§11.3): envelope + command, auth header, client identity.
    request = seen[0]
    assert request.url.path == "/v1/routes"
    assert request.headers["Authorization"] == "Bearer sekrit"
    payload = json.loads(request.read())
    assert payload["task"]["text"] == "fix the flaky login test"
    assert payload["context"]["language"] == "python"
    assert payload["context"]["phase"] == "debugging"
    assert payload["constraints"] == {
        "max_items": 3,
        "max_context_tokens": 8000,
        "allowed_kinds": ["skill"],
    }
    assert payload["principal_id"] == "p-1"
    assert payload["organization_id"] == "org-1"
    assert payload["workspace_id"] == "ws-1"
    assert payload["client_type"] == "aci-client-python"
    assert payload["client_version"]

    # Response models: typed, frozen, faithful.
    assert result.route_run_id == "rr_1"
    assert result.bundle.bundle_id == "bun_1"
    item = result.bundle.items[0]
    assert (item.capability_id, item.version, item.kind) == ("debugging", "1.0.0", "skill")
    assert result.bundle.budget is not None
    assert result.bundle.budget.max_context_tokens == 8000
    with pytest.raises(Exception, match="[Ff]rozen"):
        result.bundle.bundle_id = "nope"  # type: ignore[misc]


def test_route_empty_bundle_is_valid() -> None:
    """ADR-008: 0 items is a valid success — the SDK must not reject it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "route_run_id": "rr_0",
                "bundle": {
                    "bundle_id": "bun_0",
                    "route_run_id": "rr_0",
                    "created_at": "2026-10-03T00:00:00Z",
                    "items": [],
                    "execution_order": [],
                    "budget": None,
                    "policy_snapshot_id": None,
                },
            },
        )

    result = make_client(handler).route("anything")
    assert result.bundle.items == []


def test_search_request_and_models() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "capability_id": "debugging",
                    "version": "1.0.0",
                    "digest": "sha256:" + "a" * 64,
                    "kind": "skill",
                    "display_name": "Debugging",
                    "description": "sys debugging",
                    "facets": {"domain": ["debugging"]},
                    "score": 0.42,
                }
            ],
        )

    hits = make_client(handler).search("debugging", kinds=("skill",), domains=("x",), limit=5)
    payload = json.loads(seen[0].read())
    assert payload == {
        "query": "debugging",
        "filters": {"kinds": ["skill"], "domains": ["x"]},
        "limit": 5,
    }
    assert len(hits) == 1
    assert hits[0].capability_id == "debugging"
    assert hits[0].score == 0.42
    assert hits[0].facets == {"domain": ["debugging"]}


def test_report_outcome_request_and_model() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["outcome"])

    evidence = make_client(handler).report_outcome(
        "rr_1",
        "bun_1",
        [OutcomeVerdict(source="test_harness", status="success", confidence="high")],
        tests_before={"failed": 1},
        tests_after={"failed": 0},
        latency_ms=900,
        client_status="completed",
        lint_passed=True,
        build_passed=True,
        changed_files=2,
        tool_calls=3,
        input_tokens=1000,
        output_tokens=200,
        estimated_usd=0.01,
    )
    payload = json.loads(seen[0].read())
    assert payload["route_run_id"] == "rr_1"
    assert payload["bundle_id"] == "bun_1"
    assert payload["verdicts"] == [
        {"source": "test_harness", "status": "success", "confidence": "high"}
    ]
    assert payload["tests_before"] == {"failed": 1}
    assert payload["human_corrected"] is None
    assert evidence.outcome_id == "out_1"
    assert evidence.verdicts[0].source == "test_harness"
    assert evidence.estimated_usd == 0.01


def test_error_45_body_maps_to_typed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"error": {"code": "ROUTE_RUN_NOT_FOUND", "message": "unknown route run x"}},
        )

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).route("t")
    assert exc_info.value.code == "ROUTE_RUN_NOT_FOUND"
    assert exc_info.value.status_code == 404
    assert "unknown route run x" in str(exc_info.value)


def test_error_validation_422_is_validation_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            json={
                "detail": [
                    {"loc": ["body", "task", "text"], "msg": "field required", "type": "missing"}
                ]
            },
        )

    with pytest.raises(ACIValidationError) as exc_info:
        make_client(handler).route("")
    assert exc_info.value.code == "REQUEST_INVALID"
    assert exc_info.value.status_code == 422
    assert "task.text" in exc_info.value.message


def test_error_bearer_gate_401_maps_to_authentication_required() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "invalid or missing token"})

    with pytest.raises(ACIError) as exc_info:
        make_client(handler, token="wrong").route("t")
    assert exc_info.value.code == "AUTHENTICATION_REQUIRED"
    assert exc_info.value.status_code == 401


def test_error_non_json_body_is_generic() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500, content=b"<html>boom</html>", headers={"content-type": "text/html"}
        )

    with pytest.raises(ACIError) as exc_info:
        make_client(handler).route("t")
    assert exc_info.value.code == "HTTP_ERROR"
    assert exc_info.value.status_code == 500


def test_get_retries_connection_failures() -> None:
    """§31.1: idempotent GETs retry connection failures."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) < 3:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=WIRE["index"])

    client = make_client(handler)
    response = client._request("GET", "/opencode/skills/index.json", retry_get=True)
    assert response.status_code == 200
    assert len(calls) == 3


def test_post_is_never_retried() -> None:
    """§31.1: POSTs have side effects (telemetry, appends) — no auto-retry."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ACIConnectionError):
        make_client(handler).route("t")
    assert len(calls) == 1


def test_connection_error_after_retries_is_typed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(ACIConnectionError) as exc_info:
        make_client(handler).search("x")
    assert "testserver" in str(exc_info.value)


def test_client_type_and_version_override() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(201, json=WIRE["route_result"])

    client = make_client(handler, client_type="my-custom-agent", client_version="9.9")
    client.route("t")
    payload = json.loads(seen[0].read())
    assert payload["client_type"] == "my-custom-agent"
    assert payload["client_version"] == "9.9"


def test_context_manager_closes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=WIRE["route_result"])

    with make_client(handler) as client:
        client.route("t")


def test_invalid_base_url_rejected() -> None:
    from aci_client import ACIClient

    with pytest.raises(ValueError, match="http"):
        ACIClient("localhost:8000")


def test_base_url_trailing_slash_normalized() -> None:
    """A base_url with or without a trailing slash must produce the same paths."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/routes"
        return httpx.Response(201, json=WIRE["route_result"])

    make_client(handler).route("t")
    ACIClient(BASE + "/", transport=httpx.MockTransport(handler)).route("t")
