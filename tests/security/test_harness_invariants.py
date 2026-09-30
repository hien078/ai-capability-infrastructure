"""HarnessKernel invariants on the INTEGRATED run path (ADR-014, harness.md §4).

Unit tests cover each manager in isolation; these drive the real
HarnessKernel + ToolRuntime end to end with a scripted model and assert the
review-blocking invariants hold for the RUN, not just for a component.

The tool dispatcher is deliberately naive (writes straight into the
workspace root, checks nothing): enforcement must come from the kernel
pipeline — validate → guardrail → authority → envelope (INV-06) — never
from a well-behaved adapter.

Known gaps are ``xfail(strict=True)``: CI stays green, and the moment a fix
lands the test XPASSes and fails, forcing the marker off. Positive controls
(no xfail) pin the behavior a fix must preserve, so "block everything" is
not a passing fix.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from aci.domain.capability.errors import DomainError
from aci.domain.runtime.actions import (
    ContinueAction,
    FinalCandidate,
    ToolCall,
    ToolCallBatchAction,
)
from aci.domain.runtime.authority import (
    ExecutionEnvelope,
    FilesystemScope,
    GrantEnvelope,
)
from aci.domain.runtime.spec import AgentProfileId, RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason, is_terminal
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolSpec
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.model_gateway import ModelRequest, ModelResponse, ModelUsage
from aci.runtime.profiles import runtime_spec_for, verifier_checks
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager

RUN_ID = "run-inv"
WRITE_GRANT = GrantEnvelope(filesystem=FilesystemScope(read=["out"], write=["out"]))


class ScriptedModel:
    """Replays actions in order and records every request it received."""

    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)
        self.requests: list[ModelRequest] = []

    def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._actions:
            raise AssertionError("scripted model exhausted — the kernel should have stopped")
        return ModelResponse(
            action=self._actions.pop(0),  # type: ignore[arg-type]
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=10),
        )


class NaiveDispatcher:
    """Executes fs.write/fs.read with NO authority checks of its own."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        target = self._root / args["path"]
        if tool.tool_id == "fs.write":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(args["content"], encoding="utf-8")
            return ToolDispatchResult(
                output=f"wrote {args['path']}",
                side_effects=SideEffectReport(
                    state="confirmed", resources_changed=[f"file:{args['path']}"]
                ),
            )
        if tool.tool_id == "fs.read":
            return ToolDispatchResult(output=target.read_text(encoding="utf-8"))
        raise ValueError(f"unexpected tool: {tool.tool_id}")


def _path_tool(tool_id: str, side_effect: str, *, content: bool) -> ToolSpec:
    properties: dict[str, Any] = {"path": {"type": "string"}}
    if content:
        properties["content"] = {"type": "string"}
    return ToolSpec(
        tool_id=tool_id,
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": list(properties),
        },
        side_effect_class=side_effect,  # type: ignore[arg-type]
    )


def _kernel(model: ScriptedModel, root: Path) -> tuple[HarnessKernel, StateManager]:
    registry = ToolRegistry()
    registry.register(_path_tool("fs.read", "READ_ONLY", content=False))
    registry.register(_path_tool("fs.write", "LOCAL_MUTATION", content=True))
    state = StateManager()
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=ToolRuntime(registry, dispatcher=NaiveDispatcher(root)),
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(verifier_checks(AgentProfileId.CODER)),
        recovery=RecoveryManager(),
        capability_runtime=object(),
    )
    return kernel, state


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id=RUN_ID, objective="fix the bug in out/app.py", created_at=datetime.now(UTC)
    )


def _coder_spec(
    *, grants: GrantEnvelope = WRITE_GRANT, budget: BudgetLedger | None = None
) -> RuntimeSpec:
    return runtime_spec_for(AgentProfileId.CODER, budget=budget).model_copy(
        update={"initial_grants": grants}
    )


def _write(call_id: str, path: str) -> ToolCallBatchAction:
    return ToolCallBatchAction(
        calls=[
            ToolCall(call_id=call_id, tool_id="fs.write", arguments={"path": path, "content": "x"})
        ]
    )


# -- positive controls: must pass today AND after every fix -----------------


class TestPositiveControls:
    def test_granted_write_with_honest_claim_succeeds(self, tmp_path: Path) -> None:
        model = ScriptedModel(
            [_write("c1", "out/app.py"), FinalCandidate(summary="fixed", changes=["out/app.py"])]
        )
        kernel, _ = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec())
        assert result.status is RunStatus.SUCCEEDED
        assert (tmp_path / "out/app.py").exists()

    def test_budget_within_limits_does_not_stop_the_run(self, tmp_path: Path) -> None:
        budget = BudgetLedger(max_turns=5, max_tool_calls=3)
        model = ScriptedModel(
            [_write("c1", "out/app.py"), FinalCandidate(summary="fixed", changes=["out/app.py"])]
        )
        kernel, _ = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec(budget=budget))
        assert result.status is RunStatus.SUCCEEDED


# -- INV-08: completion is verifier-gated, never model-gated ----------------


class TestVerificationGate:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="INV-08 gap: coder verifier checks bool(candidate.changes), not observed effects",
    )
    def test_claimed_changes_without_any_tool_call_never_succeed(self, tmp_path: Path) -> None:
        model = ScriptedModel([FinalCandidate(summary="fixed", changes=["out/app.py: fixed"])])
        kernel, _ = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec())
        assert result.status is not RunStatus.SUCCEEDED

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="INV-08 gap: claimed changes are never reconciled with confirmed side effects",
    )
    def test_claimed_change_must_match_an_observed_side_effect(self, tmp_path: Path) -> None:
        model = ScriptedModel(
            [_write("c1", "out/other.py"), FinalCandidate(summary="fixed", changes=["out/app.py"])]
        )
        kernel, _ = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec())
        assert result.status is not RunStatus.SUCCEEDED


# -- INV-04/INV-06: no side effect without authority ------------------------


class TestAuthorityOnRunPath:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="INV-06 gap: no authority preflight on the kernel tool path",
    )
    def test_write_under_read_only_grant_has_no_effect(self, tmp_path: Path) -> None:
        read_only = GrantEnvelope(filesystem=FilesystemScope(read=["out"], write=[]))
        model = ScriptedModel(
            [_write("c1", "out/pwned.txt"), FinalCandidate(summary="ok", changes=["out/pwned.txt"])]
        )
        kernel, _ = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec(grants=read_only))
        assert not (tmp_path / "out/pwned.txt").exists()
        assert result.status is not RunStatus.SUCCEEDED

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="INV-06 gap: write scope is never compared to the target path",
    )
    def test_write_outside_granted_scope_has_no_effect(self, tmp_path: Path) -> None:
        model = ScriptedModel(
            [_write("c1", "etc/pwned.txt"), FinalCandidate(summary="ok", changes=["etc/pwned.txt"])]
        )
        kernel, _ = _kernel(model, tmp_path)
        kernel.run(_contract(), _coder_spec())
        assert not (tmp_path / "etc/pwned.txt").exists()

    @pytest.mark.xfail(
        strict=True,
        raises=DomainError,
        reason="INV-07 gap: expired envelope raises AUTHORITY_EXPIRED out of run()",
    )
    def test_expired_grant_fails_the_run_without_raising(self, tmp_path: Path) -> None:
        expired = WRITE_GRANT.model_copy(
            update={"expires_at": datetime.now(UTC) - timedelta(minutes=1)}
        )
        model = ScriptedModel(
            [_write("c1", "out/app.py"), FinalCandidate(summary="ok", changes=["out/app.py"])]
        )
        kernel, state = _kernel(model, tmp_path)
        kernel.run(_contract(), _coder_spec(grants=expired))
        assert not (tmp_path / "out/app.py").exists()
        assert is_terminal(state.snapshot(RUN_ID).run.status)


# -- INV-07: model output is untrusted input --------------------------------


class TestUntrustedModelOutput:
    @pytest.mark.xfail(
        strict=True,
        raises=DomainError,
        reason="INV-07 gap: TOOL_NOT_FOUND raises out of run(), run stuck in RUNNING",
    )
    def test_unknown_tool_is_an_observation_not_a_crash(self, tmp_path: Path) -> None:
        model = ScriptedModel(
            [
                ToolCallBatchAction(
                    calls=[ToolCall(call_id="c1", tool_id="shell.exec", arguments={})]
                ),
                FinalCandidate(summary="gave up", changes=[]),
            ]
        )
        kernel, state = _kernel(model, tmp_path)
        kernel.run(_contract(), _coder_spec())
        assert is_terminal(state.snapshot(RUN_ID).run.status)
        # The model must be told the tool does not exist on its next turn.
        assert len(model.requests) == 2
        tool_messages = [m for m in model.requests[1].messages if m.role == "tool"]
        assert any("shell.exec" in m.content for m in tool_messages)


# -- §7.6: budgets are enforced from the authoritative ledger ---------------


class TestBudgetEnforcement:
    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="§7.6 gap: consumed_tool_calls is never incremented",
    )
    def test_tool_call_budget_stops_the_run(self, tmp_path: Path) -> None:
        budget = BudgetLedger(max_tool_calls=1)
        model = ScriptedModel(
            [
                _write("c0", "out/f0"),
                _write("c1", "out/f1"),
                _write("c2", "out/f2"),
                FinalCandidate(summary="ok", changes=["out/f0"]),
            ]
        )
        kernel, state = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec(budget=budget))
        assert result.stop_reason is StopReason.LIMIT_TOOL_CALLS
        assert not (tmp_path / "out/f1").exists()
        assert not (tmp_path / "out/f2").exists()
        assert state.snapshot(RUN_ID).budget.consumed_tool_calls == 1

    @pytest.mark.xfail(
        strict=True,
        raises=AssertionError,
        reason="§7.6 gap: consumed_turns is never incremented; only run(max_turns=40) applies",
    )
    def test_turn_budget_stops_the_run(self, tmp_path: Path) -> None:
        budget = BudgetLedger(max_turns=3)
        model = ScriptedModel(
            [ContinueAction()] * 10 + [FinalCandidate(summary="ok", changes=["out/app.py"])]
        )
        kernel, state = _kernel(model, tmp_path)
        result = kernel.run(_contract(), _coder_spec(budget=budget))
        assert result.stop_reason is StopReason.LIMIT_TURNS
        assert result.usage.turns == 3
        assert state.snapshot(RUN_ID).budget.consumed_turns == 3
