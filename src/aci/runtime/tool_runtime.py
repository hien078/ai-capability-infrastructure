"""ToolRuntime (harness.md §12, §60): the only path for tool side effects (INV-06).

12-step pipeline as explicit methods: registry lookup → argument validation →
pre-tool guardrails → envelope expiry → authority preflight (§58: the call's
requirement, derived from the tool's declared argument roles, evaluated
against the envelope's grants) → dispatch → output limiting → post-tool
guardrails → observation. Model output is untrusted (INV-07): every failure,
including an unknown tool id, is normalized into an observation — nothing is
raised raw out of execute().
"""

import time
from collections.abc import Callable
from typing import Any

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import AuthorityDecision, AuthorityDecisionKind, ExecutionEnvelope
from aci.domain.runtime.state import RuntimeStateSnapshot
from aci.domain.runtime.tools import (
    MUTATING_CLASSES,
    OutputPolicy,
    SideEffectReport,
    ToolCall,
    ToolObservation,
    ToolSpec,
)
from aci.runtime.authority import (
    AuthorityPolicy,
    PolicyEvaluator,
    derive_requirement,
    grants_from_envelope,
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
        authority_policy: AuthorityPolicy | None = None,
    ) -> None:
        self._registry = registry
        self._dispatcher = dispatcher
        self._guardrails = guardrails
        self._spill = spill
        # Default policy: empty ceiling — anything beyond the run's grants is
        # DENY. A REQUIRE_APPROVAL decision (approval classes) is surfaced as
        # an APPROVAL_REQUIRED observation; the kernel pauses the run on it
        # (§13.6) and a one-shot approval re-executes exactly that call.
        self._policy = authority_policy or AuthorityPolicy()
        self._evaluator = PolicyEvaluator()
        self._validator = ArgumentValidator()
        self._limiter = OutputLimiter()

    def execute(
        self,
        call: ToolCall,
        *,
        snapshot: RuntimeStateSnapshot,
        envelope: ExecutionEnvelope,
        approved: bool = False,
    ) -> ToolObservation:
        """``approved`` is a ONE-SHOT human approval for THIS call (§13.6):
        it satisfies a REQUIRE_APPROVAL decision and nothing else — a DENY
        (outside grants/ceiling, INV-02), an expired envelope or a guardrail
        BLOCK still stop the call, and the envelope is never widened."""
        tool = self._registry.get(call.tool_id)
        if tool is None:
            available = ", ".join(sorted(t.tool_id for t in self._registry.all())) or "none"
            return self._error_observation(
                call,
                "TOOL_NOT_FOUND",
                f"unknown tool {call.tool_id!r}; available tools: {available}",
            )
        try:
            args = self._validate_arguments(tool, call)
        except DomainError as exc:
            return self._error_observation(call, "TOOL_INVALID_ARGUMENT", str(exc))
        if self._guardrails is not None:
            verdict = self._guardrails.check_pre_tool(tool, args)
            if verdict.status == "BLOCK":
                return self._blocked_observation(call, tool, verdict)
        if envelope_expired(envelope):
            return self._denied_observation(
                call, "AUTHORITY_EXPIRED", "execution envelope expired — no further effects"
            )
        try:
            decision = self._authorize(tool, args, envelope)
        except DomainError as exc:
            return self._error_observation(call, "TOOL_INVALID_ARGUMENT", str(exc))
        if decision.kind is AuthorityDecisionKind.DENY:
            return self._denied_observation(call, "AUTHORITY_DENIED", decision.reason)
        if decision.kind is AuthorityDecisionKind.REQUIRE_APPROVAL and not approved:
            return self._denied_observation(
                call, "APPROVAL_REQUIRED", f"{decision.reason} (approval required)"
            )
        try:
            dispatch = self._dispatch(tool, args, envelope)
        except Exception as exc:  # noqa: BLE003 — normalize, never leak (§12.1)
            return self._dispatch_failure_observation(call, tool, exc)
        inline, artifact_ref = self._limit_output(tool, call, dispatch)
        observation = ToolObservation(
            tool_call_id=call.call_id,
            tool_id=tool.tool_id,
            status="success",
            summary=f"executed {tool.tool_id}",
            inline_output=inline,
            artifact_ref=artifact_ref,
            side_effects=dispatch.side_effects,
            evidence=dispatch.evidence,
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
        approved_call_ids: frozenset[str] = frozenset(),
    ) -> list[ToolObservation]:
        # v2: always sequential. Parallel requires concurrency_safe tools +
        # disjoint mutable resources + authority permitting (§12.5, deferred).
        return [
            self.execute(
                call,
                snapshot=snapshot,
                envelope=envelope,
                approved=call.call_id in approved_call_ids,
            )
            for call in calls
        ]

    def preflight(self, call: ToolCall, *, envelope: ExecutionEnvelope) -> AuthorityDecision | None:
        """The authority decision ``execute`` WOULD reach for ``call``, with
        no dispatch and no side effect — or None when the call would fail
        before authority (unknown tool, invalid arguments, guardrail BLOCK,
        expired envelope). The kernel asks before pausing a run for an
        approval, so a call that authority would DENY anyway never bothers
        a human (and an approval can never turn a DENY into an effect)."""
        tool = self._registry.get(call.tool_id)
        if tool is None:
            return None
        try:
            args = self._validate_arguments(tool, call)
        except DomainError:
            return None
        if self._guardrails is not None:
            if self._guardrails.check_pre_tool(tool, args).status == "BLOCK":
                return None
        if envelope_expired(envelope):
            return None
        try:
            return self._authorize(tool, args, envelope)
        except DomainError:
            return None

    def available_tools(self) -> list[ToolSpec]:
        """§12.2 — the specs the model gateway advertises to the model."""
        return list(self._registry.all())

    def _validate_arguments(self, tool: ToolSpec, call: ToolCall) -> dict[str, Any]:
        return self._validator.validate(tool, call.arguments)

    def _authorize(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> AuthorityDecision:
        """§12.1 step 4 / §58 — INV-06: no side effect without authority."""
        if tool.side_effect_class in MUTATING_CLASSES and tool.authority_requirements.is_empty():
            return AuthorityDecision(
                kind=AuthorityDecisionKind.DENY,
                reason=f"{tool.tool_id} mutates but declares no authority requirements",
            )
        requirement = derive_requirement(tool, args)
        return self._evaluator.evaluate(
            requirement, grants_from_envelope(envelope), self._policy, tool.side_effect_class
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

    def _dispatch_failure_observation(
        self, call: ToolCall, tool: ToolSpec, exc: Exception
    ) -> ToolObservation:
        """§12.1 ErrorNormalizer / §18.1: classify a dispatch failure into a
        stable code (the RecoveryManager keys off ``error_class``, never off
        exception text, §28). Only a DomainError's message is shown — it is
        authored by the tool adapter for the model; any other exception is
        reported by TYPE only, so raw text (absolute paths, internals) never
        reaches the model or the client. A mutating tool that failed after
        dispatch started may have had an effect: side-effect state
        ``possible`` (the RecoveryManager never blind-retries it)."""
        error_class, detail, effect_possible = classify_dispatch_error(tool, exc)
        side_effects = SideEffectReport(
            state="possible"
            if effect_possible and tool.side_effect_class in MUTATING_CLASSES
            else "none"
        )
        if error_class == "TOOL_TIMEOUT":
            return self._timeout_observation(call, tool).model_copy(
                update={"side_effects": side_effects}
            )
        return self._error_observation(call, error_class, detail).model_copy(
            update={"side_effects": side_effects}
        )

    def _error_observation(self, call: ToolCall, error_class: str, detail: str) -> ToolObservation:
        return ToolObservation(
            tool_call_id=call.call_id,
            tool_id=call.tool_id,
            status="error",
            summary=detail,
            error_class=error_class,
        )

    def _denied_observation(self, call: ToolCall, error_class: str, detail: str) -> ToolObservation:
        return ToolObservation(
            tool_call_id=call.call_id,
            tool_id=call.tool_id,
            status="denied",
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


#: Exceptions an execution adapter raises for an infrastructure hiccup that a
#: re-run may not hit again (§18.1 TRANSIENT_TOOL) — connection resets,
#: interrupted / would-block syscalls. Everything else is deterministic.
TRANSIENT_DISPATCH_ERRORS: tuple[type[BaseException], ...] = (
    ConnectionError,
    InterruptedError,
    BlockingIOError,
)

#: DomainError codes a tool adapter raises → observation error_class.
_DOMAIN_ERROR_CLASSES: dict[ErrorCode, str] = {
    ErrorCode.TOOL_ARGUMENT_INVALID: "TOOL_INVALID_ARGUMENT",
    ErrorCode.TOOL_NOT_FOUND: "TOOL_NOT_FOUND",
}


def classify_dispatch_error(tool: ToolSpec, exc: Exception) -> tuple[str, str, bool]:
    """§18.1 classification of an exception raised by dispatch →
    ``(error_class, model-safe detail, effect_possible)``.

    ``effect_possible`` is False only when the adapter rejected the call
    before acting (invalid argument, unknown tool)."""
    if isinstance(exc, TimeoutError):
        return "TOOL_TIMEOUT", f"{tool.tool_id} exceeded timeout {tool.timeout_ms}ms", True
    if isinstance(exc, TRANSIENT_DISPATCH_ERRORS):
        return (
            "TRANSIENT_TOOL",
            f"{tool.tool_id}: transient execution failure ({type(exc).__name__})",
            True,
        )
    if isinstance(exc, DomainError):
        error_class = _DOMAIN_ERROR_CLASSES.get(exc.code, "TOOL_EXECUTION_FAILED")
        return error_class, str(exc), error_class == "TOOL_EXECUTION_FAILED"
    return "TOOL_EXECUTION_FAILED", f"{tool.tool_id} failed ({type(exc).__name__})", True


def envelope_expired(envelope: ExecutionEnvelope) -> bool:
    return envelope.expires_at is not None and envelope.expires_at.timestamp() <= time.time()
