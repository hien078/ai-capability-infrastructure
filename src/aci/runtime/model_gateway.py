"""ModelGateway (harness.md §20.1 + §26 + §47): the ONLY place provider wire
formats exist. Model output is untrusted input (INV-07) — every completion is
normalized into the domain ``ModelAction`` union before the kernel sees it;
usage is normalized per turn (§26); provider SDK objects never leak (§20.1).
"""

import json
import time
from collections.abc import Callable
from typing import Any, Literal

import httpx
from pydantic import BaseModel, Field

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.actions import (
    CapabilityRequest,
    ClarificationRequest,
    ContinueAction,
    DelegationRequest,
    FinalCandidate,
    ModelAction,
    PlanUpdateRequest,
    ToolCallBatchAction,
)
from aci.domain.runtime.tools import ToolCall, ToolSpec
from aci.runtime.cancellation import CancelToken


class ModelMessage(BaseModel):
    """One turn of conversation sent to the model."""

    model_config = {"frozen": True}

    role: Literal["system", "user", "assistant", "tool"]
    content: str
    #: OpenAI wire format: a role="tool" message binds to its call by id.
    tool_call_id: str | None = None


class ModelRequest(BaseModel):
    """§20.1 ModelRequest — one model invocation, provider-neutral."""

    model_config = {"frozen": True}

    messages: list[ModelMessage] = Field(min_length=1)
    tools: list[ToolSpec] = Field(default_factory=list)
    model_class: str = "reasoning"
    token_limit: int = Field(default=4_096, ge=1)
    temperature: float | None = None
    cancellation_token: CancelToken | None = None


class ModelUsage(BaseModel):
    """§26 — per-turn usage record, normalized across providers."""

    model_config = {"frozen": True}

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cached_tokens: int = Field(default=0, ge=0)
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    model_id: str = ""
    provider: str = ""
    reasoning_mode: str = ""


class ModelResponse(BaseModel):
    """Normalized completion: action + raw text + usage (§47 normalization)."""

    model_config = {"frozen": True}

    action: ModelAction
    raw_text: str = ""
    usage: ModelUsage


class FakeResponse:
    """Minimal response stand-in for the injected transport in tests."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.text = body


def _strip_code_fence(text: str) -> str:
    """Models wrap JSON actions in ``` fences despite the protocol prompt —
    unwrap before parsing (INV-07 still applies: the payload is validated)."""
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if len(lines) < 2:
        return text
    if lines[-1].strip().startswith("```"):
        return "\n".join(lines[1:-1]).strip()
    return "\n".join(lines[1:]).strip()


Transport = Callable[..., FakeResponse]


def _coerce_str_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """INV-07 tolerant normalization of COMMON shape slips before strict
    validation: models put a list into a string field (summary/claims mixups).
    Deterministic coercion only — anything else still fails validation."""
    for field in ("summary", "reason", "question", "objective"):
        value = payload.get(field)
        if isinstance(value, list):
            payload[field] = "; ".join(str(v) for v in value)
        elif isinstance(value, dict):
            payload[field] = json.dumps(value, ensure_ascii=False)
    return payload


def normalize_model_action(payload: dict[str, Any]) -> ModelAction:
    """Validate a parsed JSON dict into the ModelAction union (INV-07).

    Malformed output is a MODEL_MALFORMED_OUTPUT DomainError — the recovery
    manager maps it to a schema-focused repair turn, never a crash.
    """
    action_type = payload.get("type")
    payload = _coerce_str_fields(payload)
    try:
        if action_type == "final_candidate":
            return FinalCandidate.model_validate(payload)
        if action_type == "tool_calls":
            return ToolCallBatchAction.model_validate(payload)
        if action_type == "capability_request":
            return CapabilityRequest.model_validate(payload)
        if action_type == "delegation_request":
            return DelegationRequest.model_validate(payload)
        if action_type == "plan_update":
            return PlanUpdateRequest.model_validate(payload)
        if action_type == "clarification":
            return ClarificationRequest.model_validate(payload)
        if action_type == "continue":
            return ContinueAction.model_validate(payload)
    except ValueError as exc:  # pydantic ValidationError subclasses ValueError
        raise DomainError(
            ErrorCode.MODEL_MALFORMED_OUTPUT, f"invalid model action payload: {exc}"
        ) from exc
    raise DomainError(
        ErrorCode.MODEL_MALFORMED_OUTPUT,
        f"unknown model action type: {action_type!r}",
    )


class FakeModelGateway:
    """Deterministic scriptable ModelGateway for tests (§48.3).

    Scripted actions are consumed in order; a callable entry receives the
    ModelRequest and returns the next action. Exhausting the script is a
    MODEL_FAILURE — a test that drives more turns than it scripted is broken.
    """

    def __init__(
        self,
        script: list[ModelAction | Callable[[ModelRequest], ModelAction]],
    ) -> None:
        self._script = list(script)
        self._index = 0
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.cancellation_token is not None:
            request.cancellation_token.raise_if_cancelled()
        if self._index >= len(self._script):
            raise DomainError(
                ErrorCode.MODEL_FAILURE,
                f"FakeModelGateway script exhausted after {self._index} turns",
            )
        entry = self._script[self._index]
        self._index += 1
        action = entry(request) if callable(entry) else entry
        return ModelResponse(
            action=action,
            raw_text=action.model_dump_json(),
            usage=ModelUsage(
                input_tokens=sum(len(m.content) for m in request.messages) // 4,
                output_tokens=1,
                latency_ms=0,
                cost_usd=0.0,
                model_id="fake-model",
                provider="fake",
            ),
        )


class OpenAICompatGateway:
    """ModelGateway over an OpenAI-compatible /chat/completions endpoint.

    The endpoint is deployment infrastructure (§26: never assume one
    provider); config comes from the constructor, never hardcoded. Handles
    the live gateway's SSE quirk (whole-JSON body + trailing ``data:
    [DONE]``, no per-event prefix) via raw_decode, like the delegated-task
    executor; real SSE chunk fallback included.
    """

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model_id: str,
        provider: str = "openai-compat",
        transport: Transport | None = None,
        request_timeout_seconds: float = 120.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model_id = model_id
        self._provider = provider
        self._transport = transport
        self._request_timeout_seconds = request_timeout_seconds

    def invoke(self, request: ModelRequest) -> ModelResponse:
        if request.cancellation_token is not None:
            request.cancellation_token.raise_if_cancelled()
        body = self._build_body(request)
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/chat/completions"
        started = time.monotonic()
        if self._transport is not None:
            status_code, text = self._status_and_text(
                self._transport(url, headers=headers, json=body)
            )
        else:
            with httpx.Client() as client:
                http_response = client.post(
                    url, json=body, headers=headers, timeout=self._request_timeout_seconds
                )
            status_code, text = http_response.status_code, http_response.text
        latency_ms = int((time.monotonic() - started) * 1000)
        if status_code == 429:
            raise DomainError(ErrorCode.RATE_LIMITED, "model endpoint rate limited (429)")
        if status_code == 401:
            raise DomainError(
                ErrorCode.AUTHENTICATION_REQUIRED, "model endpoint rejected credentials (401)"
            )
        if status_code != 200:
            raise DomainError(
                ErrorCode.MODEL_FAILURE,
                f"model endpoint returned {status_code}: {text[:300]}",
            )
        data = self._parse_body(text)
        message, usage = self._extract_message(data)
        content = message.get("content")
        raw_text = content if isinstance(content, str) else ""
        tool_calls: list[Any] = message.get("tool_calls") or []
        if tool_calls:
            action = self._action_from_tool_calls(tool_calls)
        else:
            action = self._action_from_text(raw_text)
        return ModelResponse(
            action=action,
            raw_text=raw_text,
            usage=self._usage(request, latency_ms, usage),
        )

    @staticmethod
    def _status_and_text(response: FakeResponse) -> tuple[int, str]:
        return response.status_code, response.text

    def _build_body(self, request: ModelRequest) -> dict[str, Any]:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                continue
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.role == "tool" and m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            messages.append(entry)
        if system_parts:
            messages.insert(0, {"role": "system", "content": "\n\n".join(system_parts)})
        body: dict[str, Any] = {
            "model": self._model_id,
            "messages": messages,
            "max_tokens": request.token_limit,
        }
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.tool_id,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in request.tools
            ]
        return body

    @staticmethod
    def _parse_body(raw: str) -> dict[str, Any]:
        """JSON fast path (raw_decode ignores the trailing ``data: [DONE]``),
        then real SSE fallback."""
        try:
            data, _ = json.JSONDecoder().raw_decode(raw.lstrip())
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            return data
        for line in raw.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:") :].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                return event
        raise DomainError(
            ErrorCode.MODEL_MALFORMED_OUTPUT, "model response body is not JSON or SSE events"
        )

    @staticmethod
    def _extract_message(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
        try:
            message = data["choices"][0]["message"]
            if not isinstance(message, dict):
                raise TypeError("message is not an object")
        except (KeyError, IndexError, TypeError) as exc:
            raise DomainError(
                ErrorCode.MODEL_MALFORMED_OUTPUT, f"completion has no message: {exc}"
            ) from exc
        usage = data.get("usage")
        return message, usage if isinstance(usage, dict) else None

    @staticmethod
    def _action_from_tool_calls(tool_calls: list[Any]) -> ModelAction:
        calls: list[ToolCall] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                raise DomainError(
                    ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call entry is not an object"
                )
            function = tc.get("function") or {}
            if not isinstance(function, dict):
                raise DomainError(
                    ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call function is not an object"
                )
            arguments = function.get("arguments", "{}")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments) if arguments.strip() else {}
                except json.JSONDecodeError as exc:
                    raise DomainError(
                        ErrorCode.MODEL_MALFORMED_OUTPUT,
                        f"tool_call arguments are not valid JSON: {exc}",
                    ) from exc
            if not isinstance(arguments, dict):
                raise DomainError(
                    ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call arguments are not an object"
                )
            calls.append(
                ToolCall(
                    call_id=str(tc.get("id", "")),
                    tool_id=str(function.get("name", "")),
                    arguments=arguments,
                )
            )
        if not calls:
            raise DomainError(ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_calls list is empty")
        return ToolCallBatchAction(calls=calls)

    @staticmethod
    def _action_from_text(raw_text: str) -> ModelAction:
        """Text that is a typed JSON action object normalizes (INV-07);
        anything else is a ContinueAction carrying the raw text."""
        stripped = _strip_code_fence(raw_text.strip())
        if stripped.startswith("{") and stripped.endswith("}"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                return ContinueAction()
            if isinstance(payload, dict) and "type" in payload:
                return normalize_model_action(payload)
        return ContinueAction()

    def _usage(
        self,
        request: ModelRequest,
        latency_ms: int,
        usage: dict[str, Any] | None,
    ) -> ModelUsage:
        if usage is not None:
            input_tokens = int(usage.get("prompt_tokens", 0))
            output_tokens = int(usage.get("completion_tokens", 0))
        else:
            input_tokens = sum(len(m.content) for m in request.messages) // 4
            output_tokens = 1
        return ModelUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=0.0,
            model_id=self._model_id,
            provider=self._provider,
        )
