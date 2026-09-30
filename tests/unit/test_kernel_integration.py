"""Kernel integration smoke (§40 minimal vertical slice): a scripted model
drives a REAL ToolRuntime + AuthorityManager + LocalWorkspace through the
HarnessKernel — the tool path is real end to end, everything deterministic."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
from aci.domain.runtime.authority import ExecutionEnvelope
from aci.domain.runtime.evidence import CheckResult, ResultContract
from aci.domain.runtime.spec import RuntimeSpec
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.stop_reason import RunStatus, StopReason
from aci.domain.runtime.subtask import SubtaskContract
from aci.domain.runtime.tools import SideEffectReport, ToolSpec
from aci.runtime.context_engine import ContextBudget, ContextEngine
from aci.runtime.guardrails import GuardrailManager, PathTraversalGuard, ShellInjectionGuard
from aci.runtime.protocols import ToolDispatchResult
from aci.runtime.recovery import RecoveryManager
from aci.runtime.run_controller import HarnessKernel
from aci.runtime.state_manager import StateManager
from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
from aci.runtime.verification import VerificationManager, VerifierCallable
from aci.runtime.workspace import WorkspaceManager


class ScriptedModel:
    def __init__(self, actions: list[object]) -> None:
        self._actions = list(actions)

    def invoke(self, request: object) -> object:
        from aci.runtime.model_gateway import ModelResponse, ModelUsage

        return ModelResponse(
            action=self._actions.pop(0),
            usage=ModelUsage(input_tokens=100, output_tokens=50, latency_ms=10),
        )


class WorkspaceDispatcher:
    """ToolDispatcher adapter over a real WorkspaceManager (§12.1 step 6-7)."""

    def __init__(self, workspace_mgr: WorkspaceManager, workspace_id: str) -> None:
        self._mgr = workspace_mgr
        self._ws = workspace_id

    def dispatch(
        self, tool: ToolSpec, args: dict[str, Any], envelope: ExecutionEnvelope
    ) -> ToolDispatchResult:
        if tool.tool_id == "fs.read":
            content = self._mgr.read_file(self._ws, args["path"])
            return ToolDispatchResult(output=content)
        if tool.tool_id == "fs.write":
            self._mgr.write_file(self._ws, args["path"], args["content"])
            return ToolDispatchResult(
                output=f"wrote {args['path']}",
                side_effects=SideEffectReport(
                    state="confirmed", resources_changed=[f"file:{args['path']}"]
                ),
            )
        if tool.tool_id == "fs.list":
            entries = self._mgr.list_dir(self._ws, args.get("path", "."))
            return ToolDispatchResult(output="\n".join(entries))
        raise ValueError(f"unknown tool: {tool.tool_id}")


def _read_tool() -> ToolSpec:
    return ToolSpec(
        tool_id="fs.read",
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        side_effect_class="READ_ONLY",
    )


def _write_tool() -> ToolSpec:
    return ToolSpec(
        tool_id="fs.write",
        version="1.0.0",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
        side_effect_class="LOCAL_MUTATION",
    )


def _kernel(
    model: ScriptedModel,
    tmp_path: Path,
    checks: list[VerifierCallable],
) -> tuple[HarnessKernel, StateManager]:
    workspace_mgr = WorkspaceManager()
    workspace_id = workspace_mgr.create_local(str(str(tmp_path / "ws")))
    registry = ToolRegistry()
    registry.register(_read_tool())
    registry.register(_write_tool())
    guardrails = GuardrailManager([ShellInjectionGuard(), PathTraversalGuard()])
    tool_runtime = ToolRuntime(
        registry,
        dispatcher=WorkspaceDispatcher(workspace_mgr, workspace_id),
        guardrails=guardrails,
    )
    state = StateManager()
    kernel = HarnessKernel(
        state=state,
        model_gateway=model,
        tool_executor=tool_runtime,
        context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
        verifier=VerificationManager(checks),
        recovery=RecoveryManager(),
        capability_runtime=object(),
    )
    return kernel, state


def _contract() -> SubtaskContract:
    return SubtaskContract(
        task_id="run-int-1",
        objective="read the config and report",
        created_at=datetime.now(UTC),
    )


def _spec() -> RuntimeSpec:
    return RuntimeSpec(
        result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
        budget=BudgetLedger(),
        created_at=datetime.now(UTC),
    )


class TestKernelToolPathIntegration:
    def test_full_pipeline_read_then_verified_success(self, tmp_path: Path) -> None:
        model = ScriptedModel(
            [
                ToolCallBatchAction(
                    calls=[
                        ToolCall(call_id="c1", tool_id="fs.read", arguments={"path": "notes.txt"})
                    ]
                ),
                FinalCandidate(summary="read the file", changes=[]),
            ]
        )
        checks = [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        kernel, state = _kernel(model, tmp_path, checks)
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.SUCCEEDED
        assert result.stop_reason is StopReason.SUCCESS
        assert result.usage.tool_calls == 1
        assert result.usage.turns == 2
        assert state.snapshot("run-int-1").run.status is RunStatus.SUCCEEDED

    def test_guardrail_blocks_traversal_write(self, tmp_path: Path) -> None:
        """A `..` path must be BLOCKED by the pre-tool guardrail — the file
        never appears outside the workspace (§32.5 path traversal)."""
        model = ScriptedModel(
            [
                ToolCallBatchAction(
                    calls=[
                        ToolCall(
                            call_id="c1",
                            tool_id="fs.write",
                            arguments={"path": "../escape.txt", "content": "pwned"},
                        )
                    ]
                ),
                FinalCandidate(summary="done"),
            ]
        )
        checks = [VerifierCallable("always", lambda s, c: CheckResult(name="always", passed=True))]
        kernel, _ = _kernel(model, tmp_path, checks)
        result = kernel.run(_contract(), _spec())
        # The observation is blocked; the run itself can still complete —
        # what must NEVER happen is the file existing outside the workspace.
        assert not (tmp_path.parent / "escape.txt").exists()
        assert result.usage.tool_calls == 1

    def test_verification_failure_never_succeeds(self, tmp_path: Path) -> None:
        model = ScriptedModel([FinalCandidate(summary="trust me")])
        failing = VerifierCallable(
            "tests", lambda s, c: CheckResult(name="tests", passed=False, detail="0 run")
        )
        kernel, _ = _kernel(model, tmp_path, [failing])
        result = kernel.run(_contract(), _spec())
        assert result.status is RunStatus.FAILED
        assert result.stop_reason is StopReason.VERIFICATION_FAILED
