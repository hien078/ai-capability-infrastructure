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

    def _run(self, script: list[object], check_results: list[bool]) -> dict[str, int]:
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
        kernel = HarnessKernel(
            state=StateManager(),
            model_gateway=FakeModelGateway(script),  # type: ignore[arg-type]
            tool_executor=ToolRuntime(ToolRegistry(), dispatcher=object()),  # type: ignore[arg-type]
            context_engine=ContextEngine(ContextBudget(total_tokens=60_000)),
            verifier=VerificationManager([check]),
            recovery=RecoveryManager(),
            capability_runtime=object(),  # type: ignore[arg-type]
            event_bus=bus,
            sleep=lambda _s: None,
        )
        contract = SubtaskContract(task_id="hb-1", objective="fix", created_at=datetime.now(UTC))
        spec = RuntimeSpec(
            result_contract=ResultContract(contract_id="c", required_fields=["summary"]),
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
