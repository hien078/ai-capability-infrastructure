"""Unit tests for ModelGateway (harness.md §20.1/§26/§47) — deterministic, no network."""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import (
    CapabilityRequest,
    ContinueAction,
    FinalCandidate,
    ToolCallBatchAction,
)
from aci.domain.runtime.tools import ToolCall, ToolSpec
from aci.runtime.cancellation import CancelToken, RunCancelled
from aci.runtime.model_gateway import (
    FakeModelGateway,
    FakeResponse,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    OpenAICompatGateway,
    normalize_model_action,
)


def _request(**overrides: Any) -> ModelRequest:
    kwargs: dict[str, Any] = {"messages": [ModelMessage(role="user", content="do the thing")]}
    kwargs.update(overrides)
    return ModelRequest(**kwargs)


def _tool() -> ToolSpec:
    return ToolSpec(
        tool_id="filesystem.read",
        version="1.0.0",
        description="read a file",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
    )


class TestModelContracts:
    def test_request_is_frozen(self) -> None:
        request = _request()
        with pytest.raises(ValidationError):
            request.token_limit = 99  # type: ignore[misc]

    def test_response_is_frozen(self) -> None:
        response = ModelResponse(
            action=ContinueAction(),
            raw_text="",
            usage=ModelUsage(input_tokens=1, output_tokens=1, latency_ms=1),
        )
        with pytest.raises(ValidationError):
            response.raw_text = "x"  # type: ignore[misc]


class TestCancelToken:
    def test_cancel_then_raise(self) -> None:
        token = CancelToken("run-1")
        assert not token.cancelled
        token.raise_if_cancelled()  # no-op before cancel
        token.cancel()
        assert token.cancelled
        with pytest.raises(RunCancelled) as exc_info:
            token.raise_if_cancelled()
        assert exc_info.value.run_id == "run-1"

    def test_run_cancelled_default_message(self) -> None:
        err = RunCancelled("run-2")
        assert err.run_id == "run-2"
        assert "run-2" in str(err)


class TestNormalizeModelAction:
    def test_final_candidate(self) -> None:
        action = normalize_model_action(
            {"type": "final_candidate", "summary": "done", "criteria_addressed": ["AC-1"]}
        )
        assert isinstance(action, FinalCandidate)
        assert action.summary == "done"
        assert action.criteria_addressed == ["AC-1"]

    def test_tool_calls(self) -> None:
        action = normalize_model_action(
            {
                "type": "tool_calls",
                "calls": [
                    {"call_id": "call_1", "tool_id": "filesystem.read", "arguments": {"path": "a"}}
                ],
            }
        )
        assert isinstance(action, ToolCallBatchAction)
        assert action.calls[0].tool_id == "filesystem.read"

    def test_capability_request(self) -> None:
        action = normalize_model_action(
            {"type": "capability_request", "objective": "diagnose query plan"}
        )
        assert isinstance(action, CapabilityRequest)

    def test_malformed_unknown_type(self) -> None:
        with pytest.raises(DomainError) as exc_info:
            normalize_model_action({"type": "explode"})
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT

    def test_malformed_missing_type(self) -> None:
        with pytest.raises(DomainError) as exc_info:
            normalize_model_action({"summary": "no type key"})
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT

    def test_malformed_invalid_payload(self) -> None:
        with pytest.raises(DomainError) as exc_info:
            normalize_model_action({"type": "tool_calls", "calls": []})
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT


class TestFakeModelGateway:
    def test_three_turn_script(self) -> None:
        gateway = FakeModelGateway(
            [
                ToolCallBatchAction(
                    calls=[ToolCall(call_id="c1", tool_id="filesystem.read", arguments={})]
                ),
                lambda request: FinalCandidate(summary=f"done for {request.messages[0].content}"),
                ContinueAction(),
            ]
        )
        first = gateway.invoke(_request())
        assert isinstance(first.action, ToolCallBatchAction)
        second = gateway.invoke(_request())
        assert isinstance(second.action, FinalCandidate)
        assert "done for" in second.action.summary
        third = gateway.invoke(_request())
        assert isinstance(third.action, ContinueAction)
        assert len(gateway.requests) == 3
        assert all(u.cost_usd == 0.0 for u in (first.usage, second.usage, third.usage))

    def test_script_exhausted_raises_model_failure(self) -> None:
        gateway = FakeModelGateway([ContinueAction()])
        gateway.invoke(_request())
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_FAILURE

    def test_cancellation_token_honoured(self) -> None:
        token = CancelToken("run-3")
        token.cancel()
        gateway = FakeModelGateway([ContinueAction()])
        with pytest.raises(RunCancelled):
            gateway.invoke(_request(cancellation_token=token))


def _sse_body(payload: dict[str, Any]) -> str:
    """The live gateway's quirk: one whole JSON completion + trailing DONE line."""
    return json.dumps(payload) + "\n\ndata: [DONE]\n\n"


def _completion(message: dict[str, Any], usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": usage,
    }


class TestOpenAICompatGateway:
    def _gateway(
        self, responses: list[FakeResponse]
    ) -> tuple[OpenAICompatGateway, list[dict[str, Any]]]:
        bodies: list[dict[str, Any]] = []

        def transport(url: str, headers: dict[str, Any], json: dict[str, Any]) -> FakeResponse:
            bodies.append(json)
            assert url.endswith("/chat/completions")
            assert headers["Authorization"].startswith("Bearer ")
            return responses.pop(0)

        return (
            OpenAICompatGateway(
                base_url="http://gateway.local/v1",
                api_key="test-key",
                model_id="test-model",
                transport=transport,
            ),
            bodies,
        )

    def test_sse_hybrid_body_text_continue(self) -> None:
        gateway, _ = self._gateway(
            [FakeResponse(200, _sse_body(_completion({"role": "assistant", "content": "hello"})))]
        )
        response = gateway.invoke(_request())
        assert isinstance(response.action, ContinueAction)
        assert response.raw_text == "hello"
        assert response.usage.model_id == "test-model"
        assert response.usage.provider == "openai-compat"

    def test_tool_calls_normalized(self) -> None:
        message = {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_9",
                    "type": "function",
                    "function": {
                        "name": "filesystem.read",
                        "arguments": json.dumps({"path": "src/app.py"}),
                    },
                }
            ],
        }
        gateway, _ = self._gateway([FakeResponse(200, _sse_body(_completion(message)))])
        response = gateway.invoke(_request(tools=[_tool()]))
        assert isinstance(response.action, ToolCallBatchAction)
        assert response.action.calls[0].call_id == "call_9"
        assert response.action.calls[0].arguments == {"path": "src/app.py"}

    def test_json_action_text_normalized(self) -> None:
        text = json.dumps({"type": "final_candidate", "summary": "all green"})
        gateway, _ = self._gateway(
            [FakeResponse(200, _sse_body(_completion({"role": "assistant", "content": text})))]
        )
        response = gateway.invoke(_request())
        assert isinstance(response.action, FinalCandidate)
        assert response.action.summary == "all green"

    def test_rate_limited_maps_to_domain_error(self) -> None:
        gateway, _ = self._gateway([FakeResponse(429, "too many requests")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.RATE_LIMITED

    def test_unauthorized_maps_to_domain_error(self) -> None:
        gateway, _ = self._gateway([FakeResponse(401, "bad key")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED

    def test_server_error_maps_to_model_failure(self) -> None:
        gateway, _ = self._gateway([FakeResponse(500, "boom")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_FAILURE

    def test_usage_from_response(self) -> None:
        payload = _completion(
            {"role": "assistant", "content": "ok"},
            usage={"prompt_tokens": 120, "completion_tokens": 30},
        )
        gateway, _ = self._gateway([FakeResponse(200, _sse_body(payload))])
        response = gateway.invoke(_request())
        assert response.usage.input_tokens == 120
        assert response.usage.output_tokens == 30

    def test_usage_estimated_when_absent(self) -> None:
        gateway, _ = self._gateway(
            [FakeResponse(200, _sse_body(_completion({"role": "assistant", "content": "ok"})))]
        )
        response = gateway.invoke(_request(messages=[ModelMessage(role="user", content="x" * 400)]))
        assert response.usage.input_tokens == 100  # len/4 estimate
        assert response.usage.output_tokens == 1

    def test_request_body_shape(self) -> None:
        gateway, bodies = self._gateway(
            [FakeResponse(200, _sse_body(_completion({"role": "assistant", "content": "ok"})))]
        )
        gateway.invoke(
            ModelRequest(
                messages=[
                    ModelMessage(role="system", content="be good"),
                    ModelMessage(role="system", content="and fast"),
                    ModelMessage(role="user", content="hi"),
                ],
                tools=[_tool()],
                token_limit=512,
                temperature=0.2,
            )
        )
        body = bodies[0]
        assert body["model"] == "test-model"
        assert body["messages"][0] == {"role": "system", "content": "be good\n\nand fast"}
        assert body["messages"][1] == {"role": "user", "content": "hi"}
        assert body["max_tokens"] == 512
        assert body["temperature"] == 0.2
        assert body["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "filesystem.read",
                    "description": "read a file",
                    "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                },
            }
        ]

    def test_malformed_body_raises(self) -> None:
        gateway, _ = self._gateway([FakeResponse(200, "not json at all")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT

    def test_cancellation_before_request(self) -> None:
        token = CancelToken("run-4")
        token.cancel()
        gateway, _ = self._gateway(
            [FakeResponse(200, _sse_body(_completion({"role": "assistant", "content": "ok"})))]
        )
        with pytest.raises(RunCancelled):
            gateway.invoke(_request(cancellation_token=token))
