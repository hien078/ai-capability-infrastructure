"""H-bench runner structural tests — the A/B fairness invariants (§42).

The runner measures HARNESS value (kernel arm K vs naive loop arm N); these
pins keep the comparison honest: same fixtures, same ceiling, same model
action protocol, and a verification command that can actually run.
"""

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))

import run_hbench  # noqa: E402

from aci.evaluation.harness_cases import HARNESS_CASE_IDS  # noqa: E402
from aci.runtime.workspace import command_within_prefixes  # noqa: E402
from tests.sandbox_support import available_sandbox  # noqa: E402


class TestHbenchRunner:
    def test_fixture_pack_is_the_verified_eight(self) -> None:
        """The 8 §80-verified fixtures (verify_multi_fixtures.py: each fails
        as shipped, passes after the root-cause fix) — pinned so proof_loop
        drift breaks loudly HERE, not silently inside a report."""
        assert {f["name"] for f in run_hbench._all_fixtures()} == {
            "multi-config-precedence",
            "multi-cache-invalidation",
            "multi-pipeline-ordering",
            "multi-error-translation",
            "multi-event-aliasing",
            "long-order-pipeline",
            "long-auth-session",
            "long-notify-fanout",
        }

    def test_every_fixture_is_labeled_with_a_real_h_case(self) -> None:
        for fixture in run_hbench._all_fixtures():
            ref = run_hbench.H_REFS.get(fixture["name"])
            assert ref in HARNESS_CASE_IDS, fixture["name"]

    def test_pending_h_cases_are_recorded_not_hidden(self) -> None:
        """H005/H006/H007/H011 need dedicated fixtures (context flood,
        approval, injected transient failure, crash/resume) — the report
        must SAY they are pending instead of implying 20/20 coverage."""
        assert "H005" in run_hbench.PENDING_H_CASES
        assert "H011" in run_hbench.PENDING_H_CASES

    def test_verification_command_is_within_the_process_ceiling(self) -> None:
        """If this breaks, both arms refuse to verify (INV-06 fail-closed)
        and every `accepted` in every report is a lie."""
        assert command_within_prefixes(run_hbench.VERIFICATION, run_hbench.PROCESS_PREFIXES)

    def test_naive_arm_uses_the_kernel_system_prompt(self) -> None:
        """§42: same model interface in both arms — N gets the kernel's own
        _system_prompt over the same contract/spec/grants (identity +
        objective + authority summary + action protocol), so the A/B
        measures harness mechanisms (verification gate, recovery, context),
        not prompts."""
        import inspect

        source = inspect.getsource(run_hbench.run_naive_arm)
        assert "_system_prompt" in source
        assert "task_state_from(contract)" in source


class TestMechanismCounts:
    """The §44 cost columns are counted from the events the kernel ACTUALLY
    emits — driven through a real HarnessKernel, so an event-name drift
    (the original `repairs` counter listened for RECOVERY_STARTED, which the
    kernel never emits, and silently reported 0) breaks here."""

    def _run(
        self,
        script: list[object],
        check_results: list[bool],
        *,
        tools: list[object] | None = None,
        dispatcher: object | None = None,
    ) -> dict[str, int]:
        from datetime import UTC, datetime

        from aci.domain.runtime.evidence import CheckResult, ResultContract
        from aci.domain.runtime.spec import RuntimeSpec
        from aci.domain.runtime.subtask import SubtaskContract
        from aci.runtime.context_engine import ContextBudget, ContextEngine
        from aci.runtime.event_bus import EventBus
        from aci.runtime.model_gateway import FakeModelGateway
        from aci.runtime.recovery import RecoveryManager
        from aci.runtime.run_controller import HarnessKernel
        from aci.runtime.state_manager import StateManager
        from aci.runtime.tool_runtime import ToolRegistry, ToolRuntime
        from aci.runtime.verification import VerificationManager, VerifierCallable

        verdicts = iter(check_results)
        check = VerifierCallable(
            "gate", lambda s, c: CheckResult(name="gate", passed=next(verdicts))
        )
        bus = EventBus()
        registry = ToolRegistry()
        for tool in tools or []:
            registry.register(tool)  # type: ignore[arg-type]
        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=FakeModelGateway(script),  # type: ignore[arg-type]
            tool_executor=ToolRuntime(registry, dispatcher=dispatcher or object()),  # type: ignore[arg-type]
            context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
            verifier=VerificationManager([check]),
            recovery=RecoveryManager(),
            capability_runtime=object(),  # type: ignore[arg-type]
            event_bus=bus,
            sleep=lambda _s: None,
        )
        contract = SubtaskContract(task_id="hb-1", objective="fix", created_at=datetime.now(UTC))
        from aci.domain.runtime.authority import FilesystemScope, GrantEnvelope

        spec = RuntimeSpec(
            result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
            initial_grants=GrantEnvelope(filesystem=FilesystemScope(read=["out"])),
            created_at=datetime.now(UTC),
        )
        kernel.run(contract, spec)
        return run_hbench.mechanism_counts(bus.history("hb-1"))

    def test_repairs_and_model_recoveries_are_counted_separately(self) -> None:
        from aci.domain.capability.errors import DomainError, ErrorCode
        from aci.domain.runtime.actions import FinalCandidate

        def unavailable(_request: object) -> object:
            raise DomainError(ErrorCode.MODEL_UNAVAILABLE, "503")

        counts = self._run(
            [unavailable, FinalCandidate(summary="try 1"), FinalCandidate(summary="try 2")],
            check_results=[False, True],
        )
        assert counts == {
            "verification_rounds": 2,
            "verification_fails": 1,
            "repairs": 1,
            "model_recoveries": 1,
            "tool_recoveries": 0,
        }

    def test_terminal_escalation_is_not_a_repair(self) -> None:
        from aci.domain.runtime.actions import FinalCandidate

        counts = self._run(
            [FinalCandidate(summary=f"try {i}") for i in range(3)],
            check_results=[False, False, False],
        )
        # 3 failed rounds: the first two became repair turns, the third
        # escalated (terminal) — it ended the run, it was not a repair.
        assert counts["verification_fails"] == 3
        assert counts["repairs"] == 2
        assert counts["model_recoveries"] == 0

    def test_tool_recoveries_are_not_counted_as_model_recoveries(self) -> None:
        """A transient tool failure emits RECOVERY_ACTION with
        component="tool_runtime" — it is a TOOL recovery, not a model one
        (the old split counted every non-verification recovery as model)."""
        from aci.domain.capability.errors import DomainError, ErrorCode
        from aci.domain.runtime.actions import FinalCandidate, ToolCall, ToolCallBatchAction
        from aci.domain.runtime.tools import ToolAuthority, ToolSpec
        from aci.runtime.protocols import ToolDispatchResult

        read = ToolSpec(
            tool_id="fs.read",
            version="1.0.0",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            side_effect_class="READ_ONLY",
            idempotency_class="IDEMPOTENT",
            authority_requirements=ToolAuthority(read_path_args=["path"]),
        )

        class Flaky:
            def __init__(self) -> None:
                self.failures = [ConnectionResetError("reset"), TimeoutError("slow")]

            def dispatch(self, tool: object, args: object, envelope: object) -> object:
                if self.failures:
                    raise self.failures.pop(0)
                return ToolDispatchResult(output="ok")

        def unavailable(_request: object) -> object:
            raise DomainError(ErrorCode.MODEL_UNAVAILABLE, "503")

        call = ToolCallBatchAction(
            calls=[ToolCall(call_id="c1", tool_id="fs.read", arguments={"path": "out/a.py"})]
        )
        counts = self._run(
            [call, unavailable, FinalCandidate(summary="done")],
            check_results=[True],
            tools=[read],
            dispatcher=Flaky(),
        )
        assert counts == {
            "verification_rounds": 1,
            "verification_fails": 0,
            "repairs": 0,
            "model_recoveries": 1,
            "tool_recoveries": 2,
        }

    def test_split_on_component_from_payload(self) -> None:
        from types import SimpleNamespace

        from aci.runtime.event_bus import RECOVERY_ACTION

        def ev(**payload: object) -> object:
            return SimpleNamespace(event_type=RECOVERY_ACTION, payload=payload)

        counts = run_hbench.mechanism_counts(
            [
                ev(failure_class="TRANSIENT_TOOL", action="RETRY_SAME", component="tool_runtime"),
                ev(failure_class="TOOL_TIMEOUT", action="FAIL", component="tool_runtime"),
                ev(failure_class="TRANSIENT_MODEL", action="RETRY_SAME", component="model_gateway"),
                ev(failure_class="VERIFICATION_FAILED", action="REPAIR", component="verifier"),
            ]
        )
        assert counts["tool_recoveries"] == 1  # the terminal FAIL is not a recovery taken
        assert counts["model_recoveries"] == 1
        assert counts["repairs"] == 1

    def test_no_mechanism_row_has_every_counted_key(self) -> None:
        """Arm N / crashed rows must carry the same columns the printer and
        aggregator read."""
        assert set(run_hbench._NO_MECHANISM) == set(run_hbench.mechanism_counts([]))


class TestTurnBudgetFairness:
    """§42: arm N gets the kernel's turn-budget note — identical text,
    identical threshold — so the A/B does not hand the signal to K only."""

    def test_naive_arm_uses_the_kernel_turn_budget_note(self) -> None:
        import inspect

        from aci.runtime import run_controller

        assert run_hbench.turn_budget_note is run_controller.turn_budget_note
        source = inspect.getsource(run_hbench.run_naive_arm)
        assert "turn_budget_note(max_turns - turns + 1)" in source

    def test_naive_arm_sends_the_note_on_its_last_two_turns(self, tmp_path: Path) -> None:
        """Drive run_naive_arm with a recording gateway: the note text the
        naive loop sends equals the kernel's, at the same turns."""
        from aci.domain.runtime.actions import ContinueAction
        from aci.runtime.model_gateway import ModelResponse, ModelUsage
        from aci.runtime.run_controller import turn_budget_note

        class Recorder:
            def __init__(self) -> None:
                self.requests: list[object] = []

            def invoke(self, request: object) -> ModelResponse:
                self.requests.append(request)
                return ModelResponse(
                    action=ContinueAction(),
                    raw_text="thinking",
                    usage=ModelUsage(input_tokens=1, output_tokens=1, latency_ms=1),
                )

        fixture = {"name": "fx", "prompt": "fix the bug"}
        (tmp_path / "src" / "fx").mkdir(parents=True)
        (tmp_path / "src" / "fx" / "a.py").write_text("x = 1\n", encoding="utf-8")
        contract, spec = run_hbench._contract_spec(fixture)
        gateway = Recorder()
        record = run_hbench.run_naive_arm(
            fixture,
            contract,
            spec,
            gateway,  # type: ignore[arg-type]
            tmp_path / "src",
            tmp_path / "runs",
            max_turns=4,
            sandbox=available_sandbox(),
        )
        assert record["turns"] == 4
        notes = [
            [m.content for m in r.messages if "Turn budget:" in m.content]  # type: ignore[attr-defined]
            for r in gateway.requests
        ]
        assert notes == [[], [], [turn_budget_note(2)], [turn_budget_note(1)]]
        # A system message right after the system prompt — the kernel's slot.
        last = gateway.requests[-1].messages  # type: ignore[attr-defined]
        assert last[1].role == "system" and last[1].content == turn_budget_note(1)
        # Per-request only: the note never accumulates in the history.
        assert sum("Turn budget:" in m.content for m in last) == 1
