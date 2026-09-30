"""Unit tests for ModelGateway (harness.md §20.1/§26/§47) — deterministic, no network."""

import json
import re
from typing import Any

import httpx
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

    def test_accepts_assistant_turn_with_tool_calls(self) -> None:
        call = ToolCall(call_id="c1", tool_id="filesystem.read", arguments={"path": "a"})
        gateway = FakeModelGateway([FinalCandidate(summary="ok")])
        response = gateway.invoke(
            _request(
                messages=[
                    ModelMessage(role="user", content="go"),
                    ModelMessage(role="assistant", content="", tool_calls=[call]),
                    ModelMessage(role="tool", content="data", tool_call_id="c1"),
                ]
            )
        )
        assert isinstance(response.action, FinalCandidate)
        assert gateway.requests[0].messages[1].tool_calls == [call]


def _sse_body(payload: dict[str, Any]) -> str:
    """The live gateway's quirk: one whole JSON completion + trailing DONE line."""
    return json.dumps(payload) + "\n\ndata: [DONE]\n\n"


def _completion(message: dict[str, Any], usage: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": usage,
    }


API_KEY = "test-key"


def _scripted_gateway(
    responses: list[FakeResponse | BaseException],
) -> tuple[OpenAICompatGateway, list[dict[str, Any]]]:
    """Injected-transport gateway; an exception entry is raised by the transport."""
    bodies: list[dict[str, Any]] = []

    def transport(url: str, headers: dict[str, Any], json: dict[str, Any]) -> FakeResponse:
        bodies.append(json)
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        entry = responses.pop(0)
        if isinstance(entry, BaseException):
            raise entry
        return entry

    return (
        OpenAICompatGateway(
            base_url="http://gateway.local/v1",
            api_key=API_KEY,
            model_id="test-model",
            transport=transport,
        ),
        bodies,
    )


class TestOpenAICompatGateway:
    def _gateway(
        self, responses: list[FakeResponse]
    ) -> tuple[OpenAICompatGateway, list[dict[str, Any]]]:
        return _scripted_gateway(list(responses))

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

    @pytest.mark.parametrize("status", [500, 502, 503, 504])
    def test_server_error_maps_to_model_unavailable(self, status: int) -> None:
        gateway, _ = self._gateway([FakeResponse(status, "boom")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_UNAVAILABLE
        assert str(status) in str(exc_info.value)

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
                    "name": "filesystem_read",
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


WIRE_NAME = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _ok(message: dict[str, Any]) -> FakeResponse:
    return FakeResponse(200, _sse_body(_completion(message)))


def _tool_call_message(*calls: dict[str, Any]) -> dict[str, Any]:
    return {"role": "assistant", "content": "", "tool_calls": list(calls)}


def _spec(tool_id: str, input_schema: dict[str, Any] | None = None) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="1.0.0",
        description=f"{tool_id} tool",
        input_schema=input_schema if input_schema is not None else {"type": "object"},
    )


class TestToolCallWireFormat:
    def test_assistant_history_serialized_as_openai_tool_calls(self) -> None:
        gateway, bodies = _scripted_gateway([_ok({"role": "assistant", "content": "ok"})])
        gateway.invoke(
            ModelRequest(
                messages=[
                    ModelMessage(role="system", content="sys"),
                    ModelMessage(role="user", content="fix it"),
                    ModelMessage(
                        role="assistant",
                        content="reading first",
                        tool_calls=[
                            ToolCall(
                                call_id="call_1",
                                tool_id="filesystem.read",
                                arguments={"path": "a.py"},
                            )
                        ],
                    ),
                    ModelMessage(role="tool", content="print(1)", tool_call_id="call_1"),
                    ModelMessage(
                        role="assistant",
                        content="",
                        tool_calls=[
                            ToolCall(call_id="call_2", tool_id="filesystem.read"),
                            ToolCall(
                                call_id="call_3",
                                tool_id="shell.run",
                                arguments={"cmd": "pytest -q", "timeout": 30},
                            ),
                        ],
                    ),
                    ModelMessage(role="tool", content="x", tool_call_id="call_2"),
                    ModelMessage(role="tool", content="1 passed", tool_call_id="call_3"),
                    ModelMessage(role="assistant", content="plain turn"),
                ],
                tools=[_tool()],
            )
        )
        assert bodies[0]["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "fix it"},
            {
                "role": "assistant",
                "content": "reading first",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "filesystem_read",
                            "arguments": json.dumps({"path": "a.py"}),
                        },
                    }
                ],
            },
            {"role": "tool", "content": "print(1)", "tool_call_id": "call_1"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {"name": "filesystem_read", "arguments": "{}"},
                    },
                    {
                        "id": "call_3",
                        "type": "function",
                        "function": {
                            "name": "shell_run",
                            "arguments": json.dumps({"cmd": "pytest -q", "timeout": 30}),
                        },
                    },
                ],
            },
            {"role": "tool", "content": "x", "tool_call_id": "call_2"},
            {"role": "tool", "content": "1 passed", "tool_call_id": "call_3"},
            {"role": "assistant", "content": "plain turn"},
        ]

    def test_tool_names_sanitized_and_mapped_back(self) -> None:
        tools = [_spec("filesystem.read"), _spec("mcp/github: create issue")]
        response_message = _tool_call_message(
            {
                "id": "a",
                "type": "function",
                "function": {"name": "filesystem_read", "arguments": '{"path": "x"}'},
            },
            {
                "id": "b",
                "type": "function",
                "function": {"name": "mcp_github__create_issue", "arguments": "{}"},
            },
        )
        gateway, bodies = _scripted_gateway([_ok(response_message)])
        response = gateway.invoke(_request(tools=tools))
        wire_names = [t["function"]["name"] for t in bodies[0]["tools"]]
        assert wire_names == ["filesystem_read", "mcp_github__create_issue"]
        assert all(WIRE_NAME.match(name) for name in wire_names)
        assert isinstance(response.action, ToolCallBatchAction)
        assert [c.tool_id for c in response.action.calls] == [
            "filesystem.read",
            "mcp/github: create issue",
        ]
        assert response.action.calls[0].arguments == {"path": "x"}

    def test_long_tool_id_truncated_to_64_and_round_trips(self) -> None:
        long_id = "capability." + "x" * 80
        wire = "capability_" + "x" * 53
        gateway, bodies = _scripted_gateway(
            [_ok(_tool_call_message({"id": "c", "type": "function", "function": {"name": wire}}))]
        )
        response = gateway.invoke(_request(tools=[_spec(long_id)]))
        assert bodies[0]["tools"][0]["function"]["name"] == wire
        assert len(wire) == 64 and WIRE_NAME.match(wire)
        assert isinstance(response.action, ToolCallBatchAction)
        assert response.action.calls[0].tool_id == long_id

    def test_wire_name_collision_is_model_failure_before_any_request(self) -> None:
        gateway, bodies = _scripted_gateway([_ok({"role": "assistant", "content": "ok"})])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request(tools=[_spec("fs.read"), _spec("fs_read")]))
        assert exc_info.value.code == ErrorCode.MODEL_FAILURE
        assert bodies == []

    def test_unknown_response_name_passes_through(self) -> None:
        gateway, _ = _scripted_gateway(
            [
                _ok(
                    _tool_call_message(
                        {"id": "c", "type": "function", "function": {"name": "no_such.tool"}}
                    )
                )
            ]
        )
        response = gateway.invoke(_request(tools=[_tool()]))
        assert isinstance(response.action, ToolCallBatchAction)
        assert response.action.calls[0].tool_id == "no_such.tool"
        assert response.action.calls[0].arguments == {}

    def test_empty_input_schema_becomes_empty_object_schema(self) -> None:
        gateway, bodies = _scripted_gateway([_ok({"role": "assistant", "content": "ok"})])
        gateway.invoke(_request(tools=[_spec("git.status", input_schema={})]))
        assert bodies[0]["tools"][0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }


class TestResponseToolCallIds:
    def test_missing_or_empty_ids_get_index_fallback(self) -> None:
        message = _tool_call_message(
            {"type": "function", "function": {"name": "filesystem_read", "arguments": "{}"}},
            {"id": "", "type": "function", "function": {"name": "filesystem_read"}},
            {"id": None, "type": "function", "function": {"name": "filesystem_read"}},
            {"id": "call_real", "type": "function", "function": {"name": "filesystem_read"}},
        )
        gateway, _ = _scripted_gateway([_ok(message)])
        response = gateway.invoke(_request(tools=[_tool()]))
        assert isinstance(response.action, ToolCallBatchAction)
        assert [c.call_id for c in response.action.calls] == [
            "call_0",
            "call_1",
            "call_2",
            "call_real",
        ]

    def test_missing_name_is_malformed_output(self) -> None:
        message = _tool_call_message({"id": "c", "type": "function", "function": {}})
        gateway, _ = _scripted_gateway([_ok(message)])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request(tools=[_tool()]))
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT


def _invoke_text(text: str) -> ModelResponse:
    gateway, _ = _scripted_gateway([_ok({"role": "assistant", "content": text})])
    return gateway.invoke(_request())


FINAL = '{"type": "final_candidate", "summary": "all green", "criteria_addressed": ["AC-1"]}'


class TestActionFromText:
    def test_whole_fenced_reply(self) -> None:
        action = _invoke_text(f"```json\n{FINAL}\n```").action
        assert isinstance(action, FinalCandidate)

    def test_prose_then_final_line_json(self) -> None:
        text = f"I ran the tests and they pass.\nHere is my result:\n\n{FINAL}\n"
        action = _invoke_text(text).action
        assert isinstance(action, FinalCandidate)
        assert action.summary == "all green"
        assert action.criteria_addressed == ["AC-1"]

    def test_final_line_wins_over_earlier_example(self) -> None:
        text = (
            'Example:\n```json\n{"type": "clarification", "question": "which file?"}\n```\n'
            f"{FINAL}"
        )
        assert isinstance(_invoke_text(text).action, FinalCandidate)

    def test_prose_then_fenced_json_block(self) -> None:
        text = f"Done. Result below.\n```json\n{FINAL}\n```\nThanks!"
        assert isinstance(_invoke_text(text).action, FinalCandidate)

    def test_prose_then_untagged_fence(self) -> None:
        text = f"Result:\n```\n{FINAL}\n```"
        assert isinstance(_invoke_text(text).action, FinalCandidate)

    def test_json_fence_not_misaligned_by_other_fences(self) -> None:
        text = (
            "Patch:\n```python\nprint('hi')\n```\nnotes\n"
            f"```json\n{FINAL}\n```\n```bash\npytest -q\n```\nend"
        )
        assert isinstance(_invoke_text(text).action, FinalCandidate)

    def test_prose_then_pretty_printed_nested_json(self) -> None:
        payload = {
            "type": "tool_calls",
            "calls": [{"call_id": "c1", "tool_id": "filesystem.read", "arguments": {"p": 1}}],
        }
        text = "Let me read the file.\n" + json.dumps(payload, indent=2)
        action = _invoke_text(text).action
        assert isinstance(action, ToolCallBatchAction)
        assert action.calls[0].tool_id == "filesystem.read"

    def test_prose_only_is_continue(self) -> None:
        text = "Still investigating; next I will look at {the config}."
        assert isinstance(_invoke_text(text).action, ContinueAction)

    def test_untyped_json_is_continue(self) -> None:
        text = 'Summary so far:\n{"summary": "no type key"}'
        assert isinstance(_invoke_text(text).action, ContinueAction)

    def test_typed_but_invalid_final_line_is_malformed(self) -> None:
        with pytest.raises(DomainError) as exc_info:
            _invoke_text('prose\n{"type": "explode"}')
        assert exc_info.value.code == ErrorCode.MODEL_MALFORMED_OUTPUT


class TestErrorClassification:
    @pytest.mark.parametrize(
        "exc",
        [
            httpx.ConnectError("connection refused"),
            httpx.ReadTimeout("read timed out"),
            httpx.RemoteProtocolError("peer closed"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            OSError("network down"),
        ],
    )
    def test_transport_failure_is_model_unavailable(self, exc: BaseException) -> None:
        gateway, _ = _scripted_gateway([exc])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_UNAVAILABLE
        assert isinstance(exc_info.value.__cause__, type(exc))
        message = str(exc_info.value)
        assert API_KEY not in message and "Bearer" not in message

    @pytest.mark.parametrize("status", [401, 403])
    def test_credentials_rejected(self, status: int) -> None:
        gateway, _ = _scripted_gateway([FakeResponse(status, f"bad key {API_KEY}")])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.AUTHENTICATION_REQUIRED
        assert API_KEY not in str(exc_info.value)

    @pytest.mark.parametrize("status", [400, 404, 422])
    def test_other_client_errors_are_model_failure(self, status: int) -> None:
        body = '{"error": {"message": "tool_call_id not found"}}'
        gateway, _ = _scripted_gateway([FakeResponse(status, body)])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_FAILURE
        assert "tool_call_id not found" in str(exc_info.value)

    @pytest.mark.parametrize("status", [400, 500])
    def test_error_excerpt_is_bounded_and_redacts_api_key(self, status: int) -> None:
        body = f"echo: Bearer {API_KEY} " + "z" * 1000
        gateway, _ = _scripted_gateway([FakeResponse(status, body)])
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        message = str(exc_info.value)
        assert API_KEY not in message
        assert len(message) < 300

    @pytest.mark.parametrize("base_url", ["http://[::1/v1", "ftp://gateway.invalid/v1"])
    def test_invalid_endpoint_url_is_model_failure(self, base_url: str) -> None:
        gateway = OpenAICompatGateway(base_url=base_url, api_key=API_KEY, model_id="m")
        with pytest.raises(DomainError) as exc_info:
            gateway.invoke(_request())
        assert exc_info.value.code == ErrorCode.MODEL_FAILURE
