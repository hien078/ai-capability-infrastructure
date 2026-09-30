"""VerificationManager + RecoveryManager + CheckpointCoordinator unit tests."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import (
    CandidateResult,
    CheckResult,
    ResultContract,
)
from aci.domain.runtime.failures import FailureClass, FailureEnvelope
from aci.domain.runtime.state import (
    BudgetLedger,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.domain.runtime.stop_reason import StopReason
from aci.runtime.checkpoints import (
    CheckpointCoordinator,
    CheckpointError,
    CheckpointStore,
)
from aci.runtime.recovery import (
    RECOVERY_ACTIONS,
    RecoveryAction,
    RecoveryManager,
    idempotency_allows_retry,
    stop_reason_for_failure,
)
from aci.runtime.verification import VerificationCheckError, VerificationManager, VerifierCallable


def _snapshot() -> RuntimeStateSnapshot:
    now = datetime.now(UTC)
    return RuntimeStateSnapshot(
        run=RunState(run_id="run-1", created_at=now),
        task=TaskState(task_id="t", objective="fix the bug"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
        workspace_id="ws-1",
    )


def _contract() -> ResultContract:
    return ResultContract(contract_id="c-v1", required_fields=["summary"])


class TestVerificationManager:
    def test_all_pass_verdict_pass(self) -> None:
        vm = VerificationManager(
            [
                VerifierCallable(
                    "files-exist", lambda s, c: CheckResult(name="files-exist", passed=True)
                )
            ]
        )
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot(), contract=_contract()
        )
        assert result.verdict == "PASS"
        assert all(c.passed for c in result.checks)

    def test_failed_mandatory_check_forces_fail(self) -> None:
        vm = VerificationManager(
            [
                VerifierCallable("ok", lambda s, c: CheckResult(name="ok", passed=True)),
                VerifierCallable(
                    "tests", lambda s, c: CheckResult(name="tests", passed=False, detail="1 failed")
                ),
            ]
        )
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot(), contract=_contract()
        )
        assert result.verdict == "FAIL"
        assert "tests" in result.repair_hints[0]

    def test_optional_failure_never_blocks(self) -> None:
        vm = VerificationManager(
            [
                VerifierCallable(
                    "lint", lambda s, c: CheckResult(name="lint", passed=False), mandatory=False
                )
            ]
        )
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot(), contract=_contract()
        )
        assert result.verdict == "PASS"
        assert any(not c.passed for c in result.checks)

    def test_crashing_check_is_failed_mandatory_not_exception(self) -> None:
        def _boom(s: object, c: object) -> CheckResult:
            raise VerificationCheckError("verifier crashed")

        vm = VerificationManager([VerifierCallable("crashy", _boom)])
        result = vm.verify(CandidateResult(summary="x"), snapshot=_snapshot(), contract=_contract())
        assert result.verdict == "FAIL"
        assert "error: verifier crashed" in result.checks[0].detail

    def test_missing_contract_field_fails(self) -> None:
        vm = VerificationManager([])
        result = vm.verify(CandidateResult(summary=""), snapshot=_snapshot(), contract=_contract())
        assert result.verdict == "FAIL"
        assert any(c.name == "contract:summary" and not c.passed for c in result.checks)

    def test_to_pack_projection(self) -> None:
        vm = VerificationManager(
            [VerifierCallable("ok", lambda s, c: CheckResult(name="ok", passed=True))]
        )
        result = vm.verify(CandidateResult(summary="d"), snapshot=_snapshot(), contract=_contract())
        pack = VerificationManager.to_pack(result)
        assert pack.verification_verdict == "PASS"
        assert "PASS:ok" in pack.checks
        assert any("contract:summary" in c for c in pack.checks)


class TestRecoveryManager:
    def _failure(self, cls: FailureClass, side_effect: str = "none") -> FailureEnvelope:
        return FailureEnvelope(
            failure_id="f1",
            failure_class=cls,
            component="test",
            message="boom",
            side_effect_state=side_effect,  # type: ignore[arg-type]
        )

    def test_transient_model_gets_backoff(self) -> None:
        rm = RecoveryManager()
        assert rm.decide(self._failure(FailureClass.TRANSIENT_MODEL)).action == "RETRY_BACKOFF"

    def test_uncertain_side_effect_never_blind_retried(self) -> None:
        rm = RecoveryManager()
        action = rm.decide(self._failure(FailureClass.TRANSIENT_TOOL, side_effect="possible"))
        assert action.action == "REPLAN"

    def test_budget_exhausted_after_max_attempts(self) -> None:
        rm = RecoveryManager(max_attempts_total=2)
        assert rm.decide(self._failure(FailureClass.TRANSIENT_MODEL)).action == "RETRY_BACKOFF"
        assert rm.decide(self._failure(FailureClass.TRANSIENT_TOOL)).action == "RETRY_SAME"
        assert rm.decide(self._failure(FailureClass.TRANSIENT_MODEL)).action == "FAIL"

    def test_repeated_same_class_escalates(self) -> None:
        rm = RecoveryManager(max_same_failure_retries=1)
        rm.decide(self._failure(FailureClass.MODEL_MALFORMED_OUTPUT))
        action = rm.decide(self._failure(FailureClass.MODEL_MALFORMED_OUTPUT))
        assert action.action == "ESCALATE"

    def test_stop_reason_mapping(self) -> None:
        assert (
            stop_reason_for_failure(self._failure(FailureClass.VERIFICATION_FAILED))
            == StopReason.VERIFICATION_FAILED
        )
        assert (
            stop_reason_for_failure(self._failure(FailureClass.AUTHORITY_BLOCKED))
            == StopReason.AUTHORITY_DENIED
        )

    def test_idempotency_gate(self) -> None:
        assert idempotency_allows_retry("IDEMPOTENT")
        assert idempotency_allows_retry("IDEMPOTENT_WITH_KEY")
        assert not idempotency_allows_retry("NON_IDEMPOTENT")
        assert not idempotency_allows_retry("UNKNOWN")

    def test_retry_same_never_issued_for_non_idempotent_tool(self) -> None:
        """§12.6 wired into the matrix: a RETRY_SAME default becomes REPLAN
        when the failed tool is not declared idempotent."""
        rm = RecoveryManager()
        timeout = self._failure(FailureClass.TOOL_TIMEOUT)
        assert rm.decide(timeout, idempotency="IDEMPOTENT").action == "RETRY_SAME"
        assert rm.decide(timeout, idempotency="IDEMPOTENT_WITH_KEY").action == "RETRY_SAME"
        assert RecoveryManager().decide(timeout, idempotency="NON_IDEMPOTENT").action == "REPLAN"
        assert RecoveryManager().decide(timeout, idempotency="UNKNOWN").action == "REPLAN"
        # A non-retry default is unaffected by the idempotency class.
        malformed = self._failure(FailureClass.MODEL_MALFORMED_OUTPUT)
        assert RecoveryManager().decide(malformed, idempotency="UNKNOWN").action == "REPAIR_OUTPUT"

    def test_every_failure_class_has_a_default_action_and_stop_reason(self) -> None:
        for cls in FailureClass:
            action = RecoveryManager().decide(self._failure(cls))
            assert action.action in RECOVERY_ACTIONS, cls
            assert isinstance(stop_reason_for_failure(self._failure(cls)), StopReason), cls
        assert (
            stop_reason_for_failure(self._failure(FailureClass.CANCELLED)) is StopReason.CANCELLED
        )
        assert stop_reason_for_failure(self._failure(FailureClass.FATAL)) is StopReason.FATAL_ERROR

    def test_recovery_actions_are_frozen_and_validated(self) -> None:
        """The default matrix is shared by every manager: a decision handed
        out must not be a mutable alias into it."""
        action = RecoveryManager().decide(self._failure(FailureClass.TRANSIENT_TOOL))
        with pytest.raises(AttributeError):
            action.action = "FAIL"  # type: ignore[misc]
        assert RecoveryManager().decide(self._failure(FailureClass.TRANSIENT_TOOL)).action == (
            "RETRY_SAME"
        )
        with pytest.raises(ValueError, match="unknown recovery action"):
            RecoveryAction("SHRUG", "nope")
        assert RecoveryAction("ESCALATE", "x").is_terminal
        assert not RecoveryAction("REPLAN", "x").is_terminal

    def test_escalation_and_total_budget_interplay(self) -> None:
        rm = RecoveryManager(max_attempts_total=3, max_same_failure_retries=1)
        first = rm.decide(self._failure(FailureClass.TRANSIENT_MODEL))
        assert first.action == "RETRY_BACKOFF"
        assert rm.decide(self._failure(FailureClass.TRANSIENT_MODEL)).action == "ESCALATE"
        assert rm.decide(self._failure(FailureClass.TRANSIENT_TOOL)).action == "RETRY_SAME"
        assert rm.decide(self._failure(FailureClass.TOOL_TIMEOUT)).action == "FAIL"
        assert rm.attempts == 4

    def test_custom_matrix_override(self) -> None:
        rm = RecoveryManager({FailureClass.TRANSIENT_TOOL: RecoveryAction("FAIL", "strict")})
        assert rm.decide(self._failure(FailureClass.TRANSIENT_TOOL)).action == "FAIL"


class TestCheckpoints:
    def test_save_and_restore_roundtrip(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        cp = coord.save(_snapshot(), checkpoint_id="cp-1")
        restored = coord.restore("cp-1")
        assert restored is not None
        assert restored.run_id == "run-1"
        assert restored.snapshot.run.run_id == "run-1"
        assert cp.state_version == 1

    def test_restore_missing_raises(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        with pytest.raises(CheckpointError):
            coord.restore("nope")

    def test_latest_for_run(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(), checkpoint_id="cp-1")
        snap2 = _snapshot()
        coord.save(snap2, checkpoint_id="cp-2")
        latest = coord.latest("run-1")
        assert latest is not None and latest.checkpoint_id == "cp-2"

    def test_pending_approval_persisted(self) -> None:
        coord = CheckpointCoordinator(CheckpointStore())
        coord.save(_snapshot(), pending_approval_id="apr-1", checkpoint_id="cp-a")
        cp = coord.restore("cp-a")
        assert cp is not None and cp.pending_approval_id == "apr-1"
