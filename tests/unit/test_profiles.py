"""Unit tests for AgentProfiles (§19–§21, §37, §38)."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import CandidateResult
from aci.domain.runtime.spec import AgentProfileId, LoopFamily
from aci.domain.runtime.state import BudgetLedger, RunState, RuntimeStateSnapshot, TaskState
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.profiles import (
    PROFILES,
    risk_level,
    runtime_spec_for,
    verifier_checks,
)


def _dummy_snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="r1", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(task_id="t1", objective="do something"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


class TestProfiles:
    def test_all_nine_profiles_present(self) -> None:
        assert len(PROFILES) == 9
        for pid in AgentProfileId:
            assert pid in PROFILES
            defn = PROFILES[pid]
            assert defn.profile_id == pid
            assert len(defn.instructions) > 10

    def test_risk_levels(self) -> None:
        assert risk_level(AgentProfileId.CODER) == 2
        assert risk_level(AgentProfileId.DEBUGGER) == 2
        assert risk_level(AgentProfileId.RESEARCHER) == 0
        assert risk_level(AgentProfileId.REVIEWER) == 0
        assert risk_level(AgentProfileId.TESTER) == 1
        assert risk_level(AgentProfileId.DEVOPS_SRE) == 3
        assert risk_level(AgentProfileId.DATA_ANALYST) == 1
        assert risk_level(AgentProfileId.ARCHITECT) == 0
        assert risk_level(AgentProfileId.SECURITY_ANALYST) == 4
        # String lookup works
        assert risk_level("coder") == 2
        with pytest.raises(KeyError):
            risk_level("nonexistent")

    def test_runtime_spec_for_default_and_custom_budget(self) -> None:
        spec = runtime_spec_for(AgentProfileId.CODER)
        assert spec.profile_id == AgentProfileId.CODER
        assert spec.loop_family == LoopFamily.ENGINEERING
        assert spec.budget.max_turns == spec.loop_policy.max_turns

        # Custom budget synchronizes loop_policy
        custom_b = BudgetLedger(
            max_turns=10,
            max_total_tokens=50000,
            max_output_tokens=10000,
            max_tool_calls=25,
            max_wall_time_seconds=600,
            max_recoveries=3,
        )
        spec_custom = runtime_spec_for("coder", budget=custom_b)
        assert spec_custom.budget.max_turns == 10
        assert spec_custom.loop_policy.max_turns == 10
        assert spec_custom.loop_policy.max_total_tokens == 50000

    def test_verifier_checks_pass_and_fail(self) -> None:
        snap = _dummy_snapshot()
        # Coder
        coder_checks = verifier_checks(AgentProfileId.CODER)
        assert len(coder_checks) == 2
        empty_candidate = CandidateResult(summary="")
        results = [check.fn(snap, empty_candidate) for check in coder_checks]
        assert all(not r.passed for r in results)

        valid_coder = CandidateResult(summary="done", changes=["file.py"])
        results = [check.fn(snap, valid_coder) for check in coder_checks]
        assert all(r.passed for r in results)

        # Debugger
        dbg_checks = verifier_checks("debugger")
        assert len(dbg_checks) == 2
        valid_dbg = CandidateResult(claims=["cause found"], artifacts=["trace.log"])
        assert all(c.fn(snap, valid_dbg).passed for c in dbg_checks)

        # Researcher
        res_checks = verifier_checks(AgentProfileId.RESEARCHER)
        valid_res = CandidateResult(summary="findings", claims=["citation A"])
        assert all(c.fn(snap, valid_res).passed for c in res_checks)

        # Reviewer
        rev_checks = verifier_checks(AgentProfileId.REVIEWER)
        valid_rev = CandidateResult(summary="diff reviewed", claims=["finding 1"])
        assert all(c.fn(snap, valid_rev).passed for c in rev_checks)

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError):
            verifier_checks("unknown_role")
        with pytest.raises(KeyError):
            runtime_spec_for("unknown_role")
