"""ModelGateway (harness.md §20.1 + §26 + §47): the ONLY place provider wire
formats exist. Model output is untrusted input (INV-07) — every completion is
normalized into the domain ``ModelAction`` union before the kernel sees it;
usage is normalized per turn (§26); provider SDK objects never leak (§20.1).
"""

import json
import re
import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any, Literal, NoReturn

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
    #: Assistant turns that requested tools; a following role="tool"
    #: message is only accepted by strict endpoints if these are replayed.
    tool_calls: list[ToolCall] = Field(default_factory=list)


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


def _json_fence_blocks(lines: list[str]) -> list[str]:
    """Bodies of ```json (or untagged) fenced blocks, in order; scanned by
    line so an unrelated fence cannot misalign the pairing."""
    blocks: list[str] = []
    tag: str | None = None
    body: list[str] = []
    for line in lines:
        marker = line.strip()
        if not marker.startswith("```"):
            if tag is not None:
                body.append(line)
            continue
        if tag is None:
            tag = marker[3:].strip().lower()
            body = []
        else:
            if tag in ("", "json"):
                blocks.append("\n".join(body).strip())
            tag = None
    return blocks


def _action_text_candidates(raw_text: str) -> Iterator[str]:
    """Where a JSON action may sit in model text, most specific first: the
    whole reply, the final line (the protocol asks for it there), the last
    fenced json block, then the tail from a line opening a ``{``."""
    text = raw_text.strip()
    yield _strip_code_fence(text)
    lines = text.splitlines()
    non_empty = [line.strip() for line in lines if line.strip()]
    if non_empty:
        yield non_empty[-1]
    fences = _json_fence_blocks(lines)
    if fences:
        yield fences[-1]
    for index in range(len(lines) - 1, -1, -1):
        if lines[index].lstrip().startswith("{"):
            yield "\n".join(lines[index:]).strip()


def _typed_json_object(candidate: str) -> dict[str, Any] | None:
    """Only a JSON object carrying a ``type`` key counts as an action."""
    if not (candidate.startswith("{") and candidate.endswith("}")):
        return None
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and "type" in payload:
        return payload
    return None


_WIRE_NAME_INVALID = re.compile(r"[^a-zA-Z0-9_-]")
_WIRE_NAME_MAX = 64


def _wire_name(tool_id: str) -> str:
    """OpenAI function names must match ``^[a-zA-Z0-9_-]{1,64}$``."""
    return _WIRE_NAME_INVALID.sub("_", tool_id)[:_WIRE_NAME_MAX]


def _wire_name_map(tools: list[ToolSpec]) -> dict[str, str]:
    """Per-request wire name -> tool_id; a sanitization collision is a
    configuration error (the model could not address both tools)."""
    mapping: dict[str, str] = {}
    for tool in tools:
        wire = _wire_name(tool.tool_id)
        existing = mapping.setdefault(wire, tool.tool_id)
        if existing != tool.tool_id:
            raise DomainError(
                ErrorCode.MODEL_FAILURE,
                f"tool ids {existing!r} and {tool.tool_id!r} collide on wire name {wire!r}",
            )
    return mapping


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
        wire_to_tool = _wire_name_map(request.tools)
        body = self._build_body(request)
        started = time.monotonic()
        status_code, text = self._post(body)
        latency_ms = int((time.monotonic() - started) * 1000)
        if status_code != 200:
            self._raise_for_status(status_code, text)
        data = self._parse_body(text)
        message, usage = self._extract_message(data)
        content = message.get("content")
        raw_text = content if isinstance(content, str) else ""
        tool_calls = message.get("tool_calls") or []
        if not isinstance(tool_calls, list):
            raise DomainError(ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_calls is not a list")
        if tool_calls:
            action = self._action_from_tool_calls(tool_calls, wire_to_tool)
        else:
            action = self._action_from_text(raw_text)
        return ModelResponse(
            action=action,
            raw_text=raw_text,
            usage=self._usage(request, latency_ms, usage),
        )

    def _post(self, body: dict[str, Any]) -> tuple[int, str]:
        """One HTTP round trip; transport failures are transient (retryable)
        and never leak provider/httpx exceptions (§20.1)."""
        headers = {"Authorization": f"Bearer {self._api_key}"}
        url = f"{self._base_url}/chat/completions"
        try:
            if self._transport is not None:
                response = self._transport(url, headers=headers, json=body)
                return response.status_code, response.text
            with httpx.Client() as client:
                http_response = client.post(
                    url, json=body, headers=headers, timeout=self._request_timeout_seconds
                )
            return http_response.status_code, http_response.text
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as exc:
            raise DomainError(ErrorCode.MODEL_FAILURE, "model endpoint URL is invalid") from exc
        except (httpx.HTTPError, TimeoutError, OSError) as exc:
            raise DomainError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"model endpoint unreachable ({type(exc).__name__})",
            ) from exc

    def _raise_for_status(self, status_code: int, text: str) -> NoReturn:
        """429 rate limit, 401/403 credentials, 5xx transient (retryable),
        anything else a non-retryable model failure."""
        if status_code == 429:
            raise DomainError(ErrorCode.RATE_LIMITED, "model endpoint rate limited (429)")
        if status_code in (401, 403):
            raise DomainError(
                ErrorCode.AUTHENTICATION_REQUIRED,
                f"model endpoint rejected credentials ({status_code})",
            )
        excerpt = text[:200]
        if self._api_key:
            excerpt = excerpt.replace(self._api_key, "***")
        if 500 <= status_code < 600:
            raise DomainError(
                ErrorCode.MODEL_UNAVAILABLE, f"model endpoint returned {status_code}: {excerpt}"
            )
        raise DomainError(
            ErrorCode.MODEL_FAILURE, f"model endpoint returned {status_code}: {excerpt}"
        )

    def _build_body(self, request: ModelRequest) -> dict[str, Any]:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        messages: list[dict[str, Any]] = []
        for m in request.messages:
            if m.role == "system":
                continue
            entry: dict[str, Any] = {"role": m.role, "content": m.content}
            if m.role == "tool" and m.tool_call_id:
                entry["tool_call_id"] = m.tool_call_id
            if m.role == "assistant" and m.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": call.call_id,
                        "type": "function",
                        "function": {
                            "name": _wire_name(call.tool_id),
                            "arguments": json.dumps(call.arguments),
                        },
                    }
                    for call in m.tool_calls
                ]
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
                        "name": _wire_name(t.tool_id),
                        "description": t.description,
                        "parameters": t.input_schema or {"type": "object", "properties": {}},
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
    def _action_from_tool_calls(
        tool_calls: list[Any], wire_to_tool: Mapping[str, str]
    ) -> ModelAction:
        """Wire names map back to real tool_ids; an unknown name passes
        through so ToolRuntime reports TOOL_NOT_FOUND to the model."""
        calls: list[ToolCall] = []
        for index, tc in enumerate(tool_calls):
            if not isinstance(tc, dict):
                raise DomainError(
                    ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call entry is not an object"
                )
            function = tc.get("function") or {}
            if not isinstance(function, dict):
                raise DomainError(
                    ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call function is not an object"
                )
            arguments = function.get("arguments")
            if arguments is None:
                arguments = {}
            elif isinstance(arguments, str):
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
            name = function.get("name")
            if not isinstance(name, str) or not name.strip():
                raise DomainError(ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_call has no name")
            raw_id = tc.get("id")
            call_id = str(raw_id).strip() if raw_id is not None else ""
            calls.append(
                ToolCall(
                    call_id=call_id or f"call_{index}",
                    tool_id=wire_to_tool.get(name, name),
                    arguments=arguments,
                )
            )
        if not calls:
            raise DomainError(ErrorCode.MODEL_MALFORMED_OUTPUT, "tool_calls list is empty")
        return ToolCallBatchAction(calls=calls)

    @staticmethod
    def _action_from_text(raw_text: str) -> ModelAction:
        """The first typed JSON object found (see ``_action_text_candidates``)
        is normalized and validated (INV-07); prose alone is a ContinueAction."""
        for candidate in _action_text_candidates(raw_text):
            payload = _typed_json_object(candidate)
            if payload is not None:
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
