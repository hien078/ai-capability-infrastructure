"""ToolRuntime (harness.md §12, §60): the only path for tool side effects (INV-06).

12-step pipeline as explicit methods: registry lookup → argument validation →
pre-tool guardrails → envelope check (authority preflight happens OUTSIDE —
ToolRuntime refuses expired envelopes, fail closed) → dispatch → output
limiting → post-tool guardrails → observation. Dispatcher exceptions are
normalized into observations; never raised raw out of execute().
"""

import time
from collections.abc import Callable
from typing import Any

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.state import RuntimeStateSnapshot
from aci.domain.runtime.tools import (
    OutputPolicy,
    ToolCall,
    ToolObservation,
    ToolSpec,
)
from aci.runtime.guardrails import GuardrailResult
from aci.runtime.protocols import (
    ArtifactSpill,
    GuardrailManager,
    ToolDispatcher,
    ToolDispatchResult,
)

TRUNCATION_MARKER = "…[truncated {n} chars]…"


class ToolRegistry:
    """§12.2 — frozen ToolSpec lookup; unknown tool_id is a DomainError."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        self._tools[spec.tool_id] = spec

    def get(self, tool_id: str) -> ToolSpec | None:
        return self._tools.get(tool_id)

    def all(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def require(self, tool_id: str) -> ToolSpec:
        tool = self._tools.get(tool_id)
        if tool is None:
            raise DomainError(ErrorCode.TOOL_NOT_FOUND, f"tool not registered: {tool_id}")
        return tool


class ArgumentValidator:
    """§12.1 step 2 — validates model-provided args against ToolSpec.input_schema.

    Supports the JSON-schema subset tools actually declare: type, required,
    enum, const, anyOf, additionalProperties. No jsonschema dependency.
    """

    def validate(self, tool: ToolSpec, arguments: dict[str, Any]) -> dict[str, Any]:
        schema = tool.input_schema
        if not schema:
            return arguments
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in arguments:
                raise DomainError(
                    ErrorCode.TOOL_ARGUMENT_INVALID,
                    f"{tool.tool_id}: missing required argument: {name}",
                )
        for name, value in arguments.items():
            if name in properties:
                self._check_property(tool, name, properties[name], value)
            elif schema.get("additionalProperties") is False:
                raise DomainError(
                    ErrorCode.TOOL_ARGUMENT_INVALID,
                    f"{tool.tool_id}: unknown argument: {name}",
                )
        return arguments

    def _check_property(self, tool: ToolSpec, name: str, prop: dict[str, Any], value: Any) -> None:
        if "const" in prop and value != prop["const"]:
            self._reject(tool, name, f"expected const {prop['const']!r}")
        if "enum" in prop and value not in prop["enum"]:
            self._reject(tool, name, f"value not in enum {prop['enum']!r}")
        if "anyOf" in prop:
            if not any(self._matches(branch, value) for branch in prop["anyOf"]):
                self._reject(tool, name, "value matches no anyOf branch")
            return
        if not self._matches(prop, value):
            self._reject(tool, name, f"expected type {prop.get('type')!r}")

    def _matches(self, prop: dict[str, Any], value: Any) -> bool:
        expected = prop.get("type")
        if expected is None:
            return True
        checks: dict[str, Callable[[Any], bool]] = {
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
            "array": lambda v: isinstance(v, list),
            "object": lambda v: isinstance(v, dict),
        }
        check = checks.get(expected)
        return check(value) if check else True

    def _reject(self, tool: ToolSpec, name: str, detail: str) -> None:
        raise DomainError(
            ErrorCode.TOOL_ARGUMENT_INVALID,
            f"{tool.tool_id}: invalid argument {name!r}: {detail}",
        )


class OutputLimiter:
    """§12.1 step 9 — bounds tool output (INV-10): inline budget + artifact spill."""

    def limit(
        self,
        raw_output: str,
        policy: OutputPolicy,
        spill: ArtifactSpill | None,
        *,
        tool_id: str,
        call_id: str,
    ) -> tuple[str, str | None]:
        if len(raw_output) <= policy.max_inline_chars:
            return raw_output, None
        if policy.truncation == "head":
            inline = raw_output[: policy.max_inline_chars]
        elif policy.truncation == "tail":
            inline = raw_output[-policy.max_inline_chars :]
        else:
            budget = policy.max_inline_chars
            head = raw_output[: budget // 2]
            tail = raw_output[-(budget - budget // 2) :]
            marker = TRUNCATION_MARKER.format(n=len(raw_output) - budget)
            inline = f"{head}{marker}{tail}"
        artifact_ref = None
        if policy.spill_to_artifact and spill is not None:
            artifact_ref = spill(raw_output, tool_id=tool_id, call_id=call_id)
        return inline, artifact_ref


class ToolRuntime:
    """§12.1 pipeline executor. Sync; unit-testable with fakes (no FastAPI here)."""

    def __init__(
        self,
        registry: ToolRegistry,
        dispatcher: ToolDispatcher,
        *,
        guardrails: GuardrailManager | None = None,
        spill: ArtifactSpill | None = None,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._guardrails = guardrails
        self._spill = spill
        self._validator = ArgumentValidator()
        self._limiter = OutputLimiter()

    def execute(
        self,
        call: ToolCall,
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
    ) -> ToolObservation:
        tool = self._lookup(call)
        try:
            args = self._validate_arguments(tool, call)
        except DomainError as exc:
            return self._error_observation(call, tool, "TOOL_INVALID_ARGUMENT", str(exc))
        if self._guardrails is not None:
            verdict = self._guardrails.check_pre_tool(tool, args)
            if verdict.status == "BLOCK":
                return self._blocked_observation(call, tool, verdict)
        self._check_envelope(envelope)
        try:
            dispatch = self._dispatch(tool, args, envelope)
        except TimeoutError:
            return self._timeout_observation(call, tool)
        except Exception as exc:  # noqa: BLE003 — normalize, never leak (§12.1)
            return self._error_observation(call, tool, "TOOL_EXECUTION_FAILED", str(exc))
        inline, artifact_ref = self._limit_output(tool, call, dispatch)
        observation = ToolObservation(
            tool_call_id=call.call_id,
            tool_id=tool.tool_id,
            status="success",
            summary=f"executed {tool.tool_id}",
            inline_output=inline,
            artifact_ref=artifact_ref,
            side_effects=dispatch.side_effects,
            duration_ms=dispatch.duration_ms,
        )
        redacted, blocked = self._post_guard(tool, args, observation)
        if blocked is not None:
            return self._blocked_observation(call, tool, blocked)
        if redacted is not None and redacted != observation.inline_output:
            observation = observation.model_copy(update={"inline_output": redacted})
        return observation

    def execute_batch(
        self,
        calls: list[ToolCall],
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
    ) -> list[ToolObservation]:
        # v2: always sequential. Parallel requires concurrency_safe tools +
        # disjoint mutable resources + authority permitting (§12.5, deferred).
        return [self.execute(call, snapshot=snapshot, envelope=envelope) for call in calls]

    def available_tools(self) -> list[ToolSpec]:
        """§12.2 — the specs the model gateway advertises to the model."""
        return list(self._registry.all())

    def _lookup(self, call: ToolCall) -> ToolSpec:
        return self._registry.require(call.tool_id)

    def _validate_arguments(self, tool: ToolSpec, call: ToolCall) -> dict[str, Any]:
        return self._validator.validate(tool, call.arguments)

    def _check_envelope(self, envelope: ExecutionEnvelope) -> None:
        if envelope.expires_at is not None and envelope.expires_at.timestamp() <= time.time():
            raise DomainError(
                ErrorCode.AUTHORITY_EXPIRED,
                f"execution envelope expired at {envelope.expires_at.isoformat()}",
            )

    def _dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        return self._dispatcher.dispatch(tool, args, envelope)

    def _limit_output(
        self, tool: ToolSpec, call: ToolCall, dispatch: ToolDispatchResult
    ) -> tuple[str, str | None]:
        return self._limiter.limit(
            dispatch.output,
            tool.output_policy,
            self._spill,
            tool_id=tool.tool_id,
            call_id=call.call_id,
        )

    def _post_guard(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> tuple[str | None, GuardrailResult | None]:
        """§14.2 post-tool phase — the guardrail sees the full observation
        (inline output + summary); BLOCK fails the call, TRANSFORM redacts."""
        if self._guardrails is None:
            return None, None
        verdict = self._guardrails.check_post_tool(tool, args, observation)
        if verdict.status == "BLOCK":
            return None, verdict
        if verdict.transformed_value is not None and "redacted_output" in verdict.transformed_value:
            return str(verdict.transformed_value["redacted_output"]), None
        return None, None

    def _error_observation(
        self, call: ToolCall, tool: ToolSpec, error_class: str, detail: str
    ) -> ToolObservation:
        return ToolObservation(
            tool_call_id=call.call_id,
            tool_id=tool.tool_id,
            status="error",
            summary=detail,
            error_class=error_class,
        )

    def _timeout_observation(self, call: ToolCall, tool: ToolSpec) -> ToolObservation:
        return ToolObservation(
            tool_call_id=call.call_id,
            tool_id=tool.tool_id,
            status="timeout",
            summary=f"{tool.tool_id} exceeded timeout {tool.timeout_ms}ms",
            error_class="TOOL_TIMEOUT",
            duration_ms=tool.timeout_ms,
        )

    def _blocked_observation(
        self, call: ToolCall, tool: ToolSpec, verdict: GuardrailResult
    ) -> ToolObservation:
        return ToolObservation(
            tool_call_id=call.call_id,
            tool_id=tool.tool_id,
            status="blocked",
            summary=verdict.reason or f"blocked by {verdict.guardrail or 'guardrail'}",
            error_class="GUARDRAIL_BLOCKED",
        )
