"""ToolRuntime unit acceptance (harness.md §12): pipeline order, fail-closed
envelope, bounded output, normalized errors — all with plain fakes."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    NetworkScope,
    ProcessScope,
)
from aci.domain.runtime.state import (
    BudgetLedger,
    GrantEnvelope,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.tools import (
    OutputPolicy,
    SideEffectReport,
    ToolCall,
    ToolObservation,
    ToolSpec,
)
from aci.runtime.guardrails import GuardrailResult
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.tool_runtime import OutputLimiter, ToolRegistry, ToolRuntime

NOW = datetime(2026, 9, 29, tzinfo=UTC)


def snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="run-1", created_at=NOW),
        task=TaskState(task_id="task-1", objective="do"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


def envelope(expires_at: datetime | None = None) -> ExecutionEnvelope:
    return ExecutionEnvelope(
        run_id="run-1",
        workspace_id="ws-1",
        filesystem=FilesystemScope(),
        network=NetworkScope(),
        process=ProcessScope(),
        expires_at=expires_at,
    )


def spec(
    tool_id: str = "grep",
    *,
    input_schema: dict[str, Any] | None = None,
    output_policy: OutputPolicy | None = None,
) -> ToolSpec:
    return ToolSpec(
        tool_id=tool_id,
        version="1.0.0",
        input_schema=input_schema or {},
        output_policy=output_policy or OutputPolicy(),
    )


class FakeDispatcher:
    def __init__(
        self,
        output: str = "ok",
        exc: Exception | None = None,
        side_effects: SideEffectReport | None = None,
    ) -> None:
        self.output = output
        self.exc = exc
        self.side_effects = side_effects or SideEffectReport()
        self.calls: list[tuple[ToolSpec, dict[str, Any]]] = []

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        self.calls.append((tool, args))
        if self.exc is not None:
            raise self.exc
        return ToolDispatchResult(output=self.output, side_effects=self.side_effects, duration_ms=5)


class FakeGuardrails:
    def __init__(
        self,
        pre_allowed: bool = True,
        post_allowed: bool = True,
        redacted: str | None = None,
    ) -> None:
        self.pre_allowed = pre_allowed
        self.post_allowed = post_allowed
        self.redacted = redacted
        self.pre_checked: list[ToolSpec] = []
        self.post_checked: list[str] = []

    def check_pre_tool(self, tool: ToolSpec, args: dict[str, Any]) -> GuardrailResult:
        self.pre_checked.append(tool)
        return GuardrailResult(
            status="BLOCK" if not self.pre_allowed else "PASS",
            guardrail="fake",
            reason="pre denied",
        )

    def check_post_tool(
        self, tool: ToolSpec, args: dict[str, Any], observation: ToolObservation
    ) -> GuardrailResult:
        self.post_checked.append(observation.inline_output)
        return GuardrailResult(
            status="BLOCK" if not self.post_allowed else "PASS",
            guardrail="fake",
            reason="post denied",
            transformed_value={"redacted_output": self.redacted} if self.redacted else None,
        )


class FakeSpill:
    def __init__(self) -> None:
        self.spilled: list[str] = []

    def __call__(self, content: str, *, tool_id: str, call_id: str) -> str:
        self.spilled.append(content)
        return f"artifact://{call_id}"


def runtime(
    dispatcher: FakeDispatcher,
    *,
    guardrails: Any | None = None,
    spill: FakeSpill | None = None,
    tools: list[ToolSpec] | None = None,
) -> ToolRuntime:
    registry = ToolRegistry()
    for t in tools or [spec()]:
        registry.register(t)
    return ToolRuntime(registry, dispatcher, guardrails=guardrails, spill=spill)


def call(tool_id: str = "grep", **args: Any) -> ToolCall:
    return ToolCall(call_id="call-1", tool_id=tool_id, arguments=dict(args))


def test_registry_lookup_miss_raises_domain_error() -> None:
    registry = ToolRegistry()
    with pytest.raises(DomainError) as exc_info:
        registry.require("nope")
    assert exc_info.value.code == ErrorCode.TOOL_NOT_FOUND


def test_unknown_tool_in_execute_raises_domain_error() -> None:
    rt = runtime(FakeDispatcher())
    with pytest.raises(DomainError) as exc_info:
        rt.execute(call("missing"), snapshot=snapshot(), envelope=envelope())
    assert exc_info.value.code == ErrorCode.TOOL_NOT_FOUND
    assert not rt._dispatcher.calls if hasattr(rt._dispatcher, "calls") else True


def test_argument_validation_wrong_type() -> None:
    tool = spec(
        input_schema={
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
    )
    rt = runtime(FakeDispatcher(), tools=[tool])
    obs = rt.execute(call(path=123), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "error"
    assert obs.error_class == "TOOL_INVALID_ARGUMENT"
    assert rt._dispatcher.calls == []  # type: ignore[attr-defined]


def test_argument_validation_missing_required() -> None:
    tool = spec(
        input_schema={
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }
    )
    rt = runtime(FakeDispatcher(), tools=[tool])
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "error"
    assert obs.error_class == "TOOL_INVALID_ARGUMENT"


def test_argument_validation_unknown_enum() -> None:
    tool = spec(
        input_schema={
            "properties": {"mode": {"type": "string", "enum": ["fast", "deep"]}},
        }
    )
    rt = runtime(FakeDispatcher(), tools=[tool])
    obs = rt.execute(call(mode="yolo"), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "error"
    assert obs.error_class == "TOOL_INVALID_ARGUMENT"


def test_argument_validation_any_of_accepts_branch() -> None:
    tool = spec(
        input_schema={
            "properties": {"target": {"anyOf": [{"type": "string"}, {"type": "integer"}]}},
        }
    )
    rt = runtime(FakeDispatcher(), tools=[tool])
    obs = rt.execute(call(target=42), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "success"


def test_guardrail_block_returns_blocked_no_dispatch() -> None:
    guardrails = FakeGuardrails(pre_allowed=False)
    dispatcher = FakeDispatcher()
    rt = runtime(dispatcher, guardrails=guardrails)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "blocked"
    assert obs.error_class == "GUARDRAIL_BLOCKED"
    assert dispatcher.calls == []


def test_expired_envelope_fails_closed_never_dispatches() -> None:
    dispatcher = FakeDispatcher()
    rt = runtime(dispatcher)
    expired = envelope(expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(DomainError) as exc_info:
        rt.execute(call(), snapshot=snapshot(), envelope=expired)
    assert exc_info.value.code == ErrorCode.AUTHORITY_EXPIRED
    assert dispatcher.calls == []


def test_valid_envelope_executes() -> None:
    dispatcher = FakeDispatcher()
    rt = runtime(dispatcher)
    obs = rt.execute(
        call(),
        snapshot=snapshot(),
        envelope=envelope(expires_at=datetime(2030, 1, 1, tzinfo=UTC)),
    )
    assert obs.status == "success"
    assert len(dispatcher.calls) == 1


def test_output_truncation_head_tail_and_spill() -> None:
    policy = OutputPolicy(max_inline_chars=100, truncation="head_tail", spill_to_artifact=True)
    tool = spec(output_policy=policy)
    spill = FakeSpill()
    raw = "A" * 60 + "B" * 60  # 120 chars, budget 100
    rt = runtime(FakeDispatcher(output=raw), spill=spill, tools=[tool])
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.artifact_ref == "artifact://call-1"
    assert spill.spilled == [raw]
    assert obs.inline_output.startswith("A" * 50)
    assert obs.inline_output.endswith("B" * 50)
    marker = "…[truncated 20 chars]…"
    assert marker in obs.inline_output
    assert len(obs.inline_output) == 50 + len(marker) + 50


def test_output_under_budget_no_spill() -> None:
    policy = OutputPolicy(max_inline_chars=100)
    tool = spec(output_policy=policy)
    spill = FakeSpill()
    rt = runtime(FakeDispatcher(output="short"), spill=spill, tools=[tool])
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.inline_output == "short"
    assert obs.artifact_ref is None
    assert spill.spilled == []


def test_dispatcher_exception_normalized_to_error_observation() -> None:
    dispatcher = FakeDispatcher(exc=RuntimeError("boom"))
    rt = runtime(dispatcher)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "error"
    assert obs.error_class == "TOOL_EXECUTION_FAILED"
    assert "boom" in obs.summary


def test_timeout_normalized_to_timeout_observation() -> None:
    dispatcher = FakeDispatcher(exc=TimeoutError("slow"))
    rt = runtime(dispatcher)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "timeout"
    assert obs.error_class == "TOOL_TIMEOUT"


def test_side_effect_report_passthrough() -> None:
    effects = SideEffectReport(state="confirmed", resources_changed=["/tmp/x"])
    rt = runtime(FakeDispatcher(side_effects=effects))
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.side_effects == effects
    assert obs.duration_ms == 5


def test_post_guardrail_redaction_applied() -> None:
    guardrails = FakeGuardrails(redacted="REDACTED")
    rt = runtime(FakeDispatcher(output="secret-token"), guardrails=guardrails)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "success"
    assert obs.inline_output == "REDACTED"


def test_post_guardrail_block_returns_blocked() -> None:
    guardrails = FakeGuardrails(post_allowed=False)
    rt = runtime(FakeDispatcher(), guardrails=guardrails)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "blocked"
    assert obs.error_class == "GUARDRAIL_BLOCKED"


def test_real_guardrail_manager_wired_through_tool_runtime() -> None:
    """ToolRuntime + the REAL GuardrailManager (SecretLeakGuard) — the two
    components must agree on the ToolObservation contract; a mismatch here
    once shipped because each side's unit tests used signature-matching fakes."""
    from aci.runtime.guardrails import GuardrailManager, SecretLeakGuard

    manager = GuardrailManager(post_guardrails=[SecretLeakGuard()])
    leak = "-----BEGIN RSA PRIVATE KEY-----\nabc\n-----END RSA PRIVATE KEY-----"
    rt = runtime(FakeDispatcher(output=leak), guardrails=manager)
    obs = rt.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs.status == "blocked"
    assert obs.error_class == "GUARDRAIL_BLOCKED"

    rt_clean = runtime(FakeDispatcher(output="all clear"), guardrails=manager)
    obs_clean = rt_clean.execute(call(), snapshot=snapshot(), envelope=envelope())
    assert obs_clean.status == "success"


def test_batch_executes_sequentially_in_order() -> None:
    dispatcher = FakeDispatcher()
    rt = runtime(dispatcher)
    calls = [
        ToolCall(call_id="c1", tool_id="grep", arguments={}),
        ToolCall(call_id="c2", tool_id="grep", arguments={}),
        ToolCall(call_id="c3", tool_id="grep", arguments={}),
    ]
    observations = rt.execute_batch(calls, snapshot=snapshot(), envelope=envelope())
    assert [o.tool_call_id for o in observations] == ["c1", "c2", "c3"]
    assert all(o.status == "success" for o in observations)
    assert len(dispatcher.calls) == 3


def test_output_limiter_unit_head_tail_marker() -> None:
    limiter = OutputLimiter()
    policy = OutputPolicy(max_inline_chars=100, truncation="head_tail")
    raw = "0" * 60 + "F" * 60  # 120 chars, budget 100
    inline, ref = limiter.limit(raw, policy, None, tool_id="t", call_id="c")
    assert inline == "0" * 50 + "…[truncated 20 chars]…" + "F" * 50
    assert ref is None  # no spill callable provided


def test_output_limiter_tail_mode() -> None:
    limiter = OutputLimiter()
    policy = OutputPolicy(max_inline_chars=100, truncation="tail", spill_to_artifact=False)
    raw = "A" * 40 + "Z" * 80  # 120 chars, budget 100
    inline, ref = limiter.limit(raw, policy, None, tool_id="t", call_id="c")
    assert inline == raw[-100:]
    assert ref is None
