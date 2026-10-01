"""Unit tests for AgentProfiles (§19–§21, §37, §38) and their grounded verifiers (INV-08)."""

from datetime import UTC, datetime

import pytest

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import CandidateResult, CheckResult, EvidenceItem, EvidenceKind
from aci.domain.runtime.spec import AgentProfileId, LoopFamily
from aci.domain.runtime.state import BudgetLedger, RunState, RuntimeStateSnapshot, TaskState
from aci.domain.runtime.stop_reason import RunStatus
from aci.runtime.profiles import (
    PROFILES,
    acceptance_prefixes,
    artifacts_observed,
    claimed_changes_observed,
    claims_grounded,
    command_passed_after_last_change,
    command_run_after_last_change,
    risk_level,
    runtime_spec_for,
    summary_present,
    verifier_checks,
)
from aci.runtime.verification import VerificationManager, VerifierCallable


def _dummy_snapshot() -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="r1", status=RunStatus.RUNNING, created_at=datetime.now(UTC)),
        task=TaskState(task_id="t1", objective="do something"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
    )


def _file(path: str, verb: str) -> EvidenceItem:
    return EvidenceItem(kind=EvidenceKind.FILE_STATE, ref=f"file://{path}", summary=verb)


def _read(path: str) -> EvidenceItem:
    return _file(path, "read")


def _listed(path: str) -> EvidenceItem:
    return _file(path, "listed")


def _written(path: str) -> EvidenceItem:
    return _file(path, "written")


def _cmd(n: int, status: int | str, argv: str = "pytest -q") -> EvidenceItem:
    return EvidenceItem(
        kind=EvidenceKind.COMMAND_OUTPUT, ref=f"cmd://{n}", summary=f"exit={status} {argv}"
    )


def _observed(
    evidence: list[EvidenceItem], changed: list[str] | None = None
) -> RuntimeStateSnapshot:
    return _dummy_snapshot().model_copy(
        update={
            "observed_evidence": evidence,
            "changed_resources": [f"file:{p}" for p in changed or []],
        }
    )


#: the run's acceptance-tied command (the client's verification_command)
ACCEPT_ARGV = ["pytest", "-q"]
ACCEPT = ["pytest -q"]


def _checks(profile: AgentProfileId) -> list[VerifierCallable]:
    return verifier_checks(profile, acceptance_commands=[ACCEPT_ARGV])


def _run(
    profile: AgentProfileId, snap: RuntimeStateSnapshot, cand: CandidateResult
) -> list[CheckResult]:
    return [check.fn(snap, cand) for check in _checks(profile)]


#: profile -> (self-reported candidate, the evidence that grounds it, confirmed changes)
GROUNDED: dict[AgentProfileId, tuple[CandidateResult, list[EvidenceItem], list[str]]] = {
    AgentProfileId.CODER: (
        CandidateResult(summary="fixed", changes=["src/app.py: fix bound"]),
        [_read("src/app.py"), _written("src/app.py")],
        ["src/app.py"],
    ),
    AgentProfileId.DEBUGGER: (
        CandidateResult(
            summary="fixed",
            claims=["src/app.py:42: loop bound is off by one"],
            changes=["src/app.py: fix bound"],
        ),
        [_read("src/app.py"), _written("src/app.py"), _cmd(1, 0)],
        ["src/app.py"],
    ),
    AgentProfileId.TESTER: (
        CandidateResult(summary="tests added", artifacts=["tests/test_app.py: bound cases"]),
        [_written("tests/test_app.py"), _cmd(1, 1)],
        ["tests/test_app.py"],
    ),
    AgentProfileId.RESEARCHER: (
        CandidateResult(summary="findings", claims=["docs/spec.md: requires idempotent retries"]),
        [_read("docs/spec.md")],
        [],
    ),
    AgentProfileId.REVIEWER: (
        CandidateResult(summary="reviewed", claims=["src/app.py:10-12: None is not handled"]),
        [_read("src/app.py")],
        [],
    ),
    AgentProfileId.SECURITY_ANALYST: (
        CandidateResult(summary="audit", claims=["src/auth.py:7: token compared with =="]),
        [_read("src/auth.py")],
        [],
    ),
    AgentProfileId.ARCHITECT: (
        CandidateResult(summary="adr", artifacts=["docs/adr/0015-queue.md"]),
        [_written("docs/adr/0015-queue.md")],
        ["docs/adr/0015-queue.md"],
    ),
    AgentProfileId.DATA_ANALYST: (
        CandidateResult(summary="analysis", artifacts=["out/report.csv: weekly totals"]),
        [_read("data/sales.csv"), _written("out/report.csv")],
        ["out/report.csv"],
    ),
    AgentProfileId.DEVOPS_SRE: (
        CandidateResult(
            summary="diagnosed", claims=["deploy/compose.yml:3: db has no healthcheck"]
        ),
        [_read("deploy/compose.yml")],
        [],
    ),
}


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

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError):
            verifier_checks("unknown_role")
        with pytest.raises(KeyError):
            runtime_spec_for("unknown_role")


class TestProfileVerifiers:
    def test_profile_check_mapping(self) -> None:
        expected = {
            AgentProfileId.CODER: ["claimed_changes_observed", "summary_present"],
            AgentProfileId.DEBUGGER: [
                "claims_grounded",
                "claimed_changes_observed",
                "command_passed_after_last_change",
            ],
            AgentProfileId.TESTER: ["artifacts_observed", "command_run_after_last_change"],
            AgentProfileId.RESEARCHER: ["claims_grounded", "summary_present"],
            AgentProfileId.REVIEWER: ["claims_grounded", "summary_present"],
            AgentProfileId.SECURITY_ANALYST: ["claims_grounded"],
            AgentProfileId.ARCHITECT: ["artifacts_observed"],
            AgentProfileId.DATA_ANALYST: ["artifacts_observed"],
            AgentProfileId.DEVOPS_SRE: ["claims_grounded"],
        }
        assert set(expected) == set(AgentProfileId)
        for pid, names in expected.items():
            checks = verifier_checks(pid)
            assert [c.name for c in checks] == names
            assert all(c.mandatory for c in checks)
        assert [c.name for c in verifier_checks("debugger")] == expected[AgentProfileId.DEBUGGER]

    @pytest.mark.parametrize("pid", list(AgentProfileId))
    def test_contract_requires_what_the_checks_ground(self, pid: AgentProfileId) -> None:
        required = set(PROFILES[pid].default_result_contract.required_fields)
        needs = {
            "claims_grounded": "claims",
            "artifacts_observed": "artifacts",
            "claimed_changes_observed": "changes",
            "summary_present": "summary",
        }
        for check in verifier_checks(pid):
            if check.name in needs:
                assert needs[check.name] in required, (pid, check.name)

    @pytest.mark.parametrize("pid", list(AgentProfileId))
    def test_self_report_alone_fails(self, pid: AgentProfileId) -> None:
        """INV-08: the model's own fields, with nothing observed, never pass."""
        candidate, _, _ = GROUNDED[pid]
        results = _run(pid, _dummy_snapshot(), candidate)
        assert not all(r.passed for r in results)
        assert all(r.detail for r in results if not r.passed)
        verdict = VerificationManager(verifier_checks(pid)).verify(
            candidate, snapshot=_dummy_snapshot(), contract=PROFILES[pid].default_result_contract
        )
        assert verdict.verdict == "FAIL"

    @pytest.mark.parametrize("pid", list(AgentProfileId))
    def test_same_report_with_observed_evidence_passes(self, pid: AgentProfileId) -> None:
        candidate, evidence, changed = GROUNDED[pid]
        snap = _observed(evidence, changed)
        results = _run(pid, snap, candidate)
        assert all(r.passed for r in results), [r.detail for r in results if not r.passed]
        assert [r.name for r in results] == [c.name for c in verifier_checks(pid)]
        verdict = VerificationManager(_checks(pid)).verify(
            candidate, snapshot=snap, contract=PROFILES[pid].default_result_contract
        )
        assert verdict.verdict == "PASS", verdict.repair_hints

    def test_debugger_passing_command_before_the_fix_is_not_regression_evidence(self) -> None:
        candidate, _, changed = GROUNDED[AgentProfileId.DEBUGGER]
        snap = _observed([_read("src/app.py"), _cmd(1, 0), _written("src/app.py")], changed)
        failed = [r for r in _run(AgentProfileId.DEBUGGER, snap, candidate) if not r.passed]
        assert [r.name for r in failed] == ["command_passed_after_last_change"]


class TestClaimsGrounded:
    def test_claim_citing_line_and_line_range(self) -> None:
        snap = _observed([_read("src/app.py")])
        for claim in (
            "src/app.py:42: off by one",
            "src/app.py:10-20: dead branch",
            "src/app.py: no validation",
            "./src/app.py: normalized path",
        ):
            assert claims_grounded(snap, CandidateResult(claims=[claim])).passed, claim

    def test_changed_file_is_a_source(self) -> None:
        snap = _observed([_written("src/new.py")], ["src/new.py"])
        assert claims_grounded(snap, CandidateResult(claims=["src/new.py:1: adds X"])).passed

    def test_file_only_listed_fails(self) -> None:
        snap = _observed([_listed("src"), _listed("src/app.py")])
        result = claims_grounded(snap, CandidateResult(claims=["src/app.py:3: bug"]))
        assert not result.passed
        assert "only listed" in result.detail
        dir_claim = claims_grounded(snap, CandidateResult(claims=["src: layout is flat"]))
        assert not dir_claim.passed

    def test_claim_without_citation_fails(self) -> None:
        snap = _observed([_read("src/app.py")])
        for claim in ("cause found", "src/app.py:42 off by one", "src/app.py:"):
            result = claims_grounded(snap, CandidateResult(claims=[claim]))
            assert not result.passed, claim
            assert "cites no source" in result.detail

    def test_unread_file_fails_and_detail_names_only_ungrounded_claims(self) -> None:
        snap = _observed([_read("src/app.py")])
        cand = CandidateResult(claims=["src/app.py:1: fine", "src/other.py:2: invented"])
        result = claims_grounded(snap, cand)
        assert not result.passed
        assert result.detail.startswith("1/2 claims ungrounded")
        assert "src/other.py" in result.detail
        assert "fine" not in result.detail
        assert "path[:line]: text" in result.detail

    def test_no_claims_fails(self) -> None:
        result = claims_grounded(_observed([_read("a.py")]), CandidateResult(summary="x"))
        assert not result.passed
        assert "no claims" in result.detail

    def test_detail_is_bounded(self) -> None:
        cand = CandidateResult(claims=[f"x{i}.py: " + "y" * 300 for i in range(8)])
        result = claims_grounded(_dummy_snapshot(), cand)
        assert "(+3 more)" in result.detail
        assert len(result.detail) < 1000

    def test_command_evidence_does_not_ground_file_claims(self) -> None:
        snap = _observed([_cmd(1, 0, "cat src/app.py")])
        assert not claims_grounded(snap, CandidateResult(claims=["src/app.py:1: x"])).passed


class TestArtifactsObserved:
    def test_written_artifact_passes_with_or_without_note(self) -> None:
        snap = _observed([_written("out/a.md")], ["out/a.md"])
        assert artifacts_observed(snap, CandidateResult(artifacts=["out/a.md"])).passed
        assert artifacts_observed(snap, CandidateResult(artifacts=["out/a.md: the plan"])).passed

    def test_read_only_artifact_fails(self) -> None:
        snap = _observed([_read("out/a.md")])
        result = artifacts_observed(snap, CandidateResult(artifacts=["out/a.md"]))
        assert not result.passed
        assert "out/a.md" in result.detail

    def test_partially_produced_fails(self) -> None:
        snap = _observed([_written("out/a.md")], ["out/a.md"])
        result = artifacts_observed(snap, CandidateResult(artifacts=["out/a.md", "out/b.md"]))
        assert not result.passed
        assert "out/b.md" in result.detail
        assert "out/a.md" not in result.detail

    def test_no_artifacts_fails(self) -> None:
        snap = _observed([_written("out/a.md")], ["out/a.md"])
        assert not artifacts_observed(snap, CandidateResult(summary="x")).passed


class TestClaimedChangesObserved:
    def test_coder_semantics_unchanged(self) -> None:
        snap = _dummy_snapshot()
        coder_checks = verifier_checks(AgentProfileId.CODER)
        empty = CandidateResult(summary="")
        assert all(not check.fn(snap, empty).passed for check in coder_checks)

        valid = CandidateResult(summary="done", changes=["file.py: fixed"])
        assert not claimed_changes_observed(snap, valid).passed
        observed = snap.model_copy(update={"changed_resources": ["file:file.py"]})
        assert all(check.fn(observed, valid).passed for check in coder_checks)
        unobserved = CandidateResult(summary="done", changes=["file.py", "other.py"])
        result = claimed_changes_observed(observed, unobserved)
        assert not result.passed
        assert result.detail == "claimed but not observed: other.py"
        assert not claimed_changes_observed(observed, CandidateResult(summary="x")).passed

    def test_summary_present(self) -> None:
        assert summary_present(_dummy_snapshot(), CandidateResult(summary="s")).passed
        missing = summary_present(_dummy_snapshot(), CandidateResult())
        assert not missing.passed
        assert missing.detail == "summary is missing"


class TestCommandAfterLastChange:
    def test_command_before_the_last_write_fails(self) -> None:
        snap = _observed([_cmd(1, 0), _written("src/app.py")], ["src/app.py"])
        for check in (command_passed_after_last_change, command_run_after_last_change):
            result = check(snap, CandidateResult(), acceptance=ACCEPT)
            assert not result.passed
            assert result.detail == (
                'no command ran after the last write to src/app.py; run "pytest -q"'
            )

    def test_command_after_the_last_write_passes(self) -> None:
        snap = _observed(
            [_written("a.py"), _cmd(1, 1), _written("b.py"), _cmd(2, 0)], ["a.py", "b.py"]
        )
        assert command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed
        assert command_run_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed

    def test_only_commands_after_the_final_write_count(self) -> None:
        snap = _observed([_written("a.py"), _cmd(1, 0), _written("b.py"), _cmd(2, 2)])
        result = command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT)
        assert not result.passed
        assert result.detail == (
            'no acceptance command exited 0 after the last write to b.py; last: "exit=2 pytest -q"'
        )

    def test_failing_run_counts_as_run_not_as_passed(self) -> None:
        snap = _observed([_written("tests/t.py"), _cmd(1, 1)])
        assert not command_passed_after_last_change(
            snap, CandidateResult(), acceptance=ACCEPT
        ).passed
        assert command_run_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed

    def test_timeout_is_neither_passed_nor_run(self) -> None:
        snap = _observed([_written("tests/t.py"), _cmd(1, "timeout")])
        passed = command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT)
        ran = command_run_after_last_change(snap, CandidateResult(), acceptance=ACCEPT)
        assert not passed.passed
        assert not ran.passed
        assert "exit=timeout" in ran.detail
        with_code = _observed([_written("tests/t.py"), _cmd(1, "timeout"), _cmd(2, 0)])
        assert command_passed_after_last_change(
            with_code, CandidateResult(), acceptance=ACCEPT
        ).passed

    def test_without_writes_any_acceptance_command_counts(self) -> None:
        snap = _observed([_read("a.py"), _cmd(1, 0, "pytest -q tests/test_a.py")])
        assert command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed

    def test_no_command_at_all_fails(self) -> None:
        result = command_run_after_last_change(
            _observed([_read("a.py")]), CandidateResult(), acceptance=ACCEPT
        )
        assert not result.passed
        assert result.detail == 'no command ran in this run; run "pytest -q"'

    def test_read_and_listed_do_not_reset_the_window(self) -> None:
        snap = _observed([_written("a.py"), _cmd(1, 0), _read("a.py"), _listed("src")])
        assert command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed


class TestAcceptanceTiedCommands:
    """The verifier gap: an arbitrary exit-0 command after the last edit is NOT
    regression evidence — only the run's acceptance command (or argv extending
    it) counts, and with no acceptance command the checks fail closed."""

    NOOPS = ("true", "python -c pass", "make check", "pytest --version", "pytest-evil -q")

    @pytest.mark.parametrize("noop", NOOPS)
    def test_unrelated_exit_zero_after_the_last_change_does_not_pass(self, noop: str) -> None:
        snap = _observed([_written("src/app.py"), _cmd(1, 0, noop)], ["src/app.py"])
        for check in (command_passed_after_last_change, command_run_after_last_change):
            result = check(snap, CandidateResult(), acceptance=ACCEPT)
            assert not result.passed, noop
            assert "is not one" in result.detail
            assert '"pytest -q"' in result.detail  # the repair hint names the real command

    @pytest.mark.parametrize("noop", NOOPS)
    def test_debugger_gate_refuses_a_noop_regression_check(self, noop: str) -> None:
        candidate, _, changed = GROUNDED[AgentProfileId.DEBUGGER]
        snap = _observed([_read("src/app.py"), _written("src/app.py"), _cmd(1, 0, noop)], changed)
        failed = [r.name for r in _run(AgentProfileId.DEBUGGER, snap, candidate) if not r.passed]
        assert failed == ["command_passed_after_last_change"]
        contract = PROFILES[AgentProfileId.DEBUGGER].default_result_contract
        verdict = VerificationManager(_checks(AgentProfileId.DEBUGGER)).verify(
            candidate, snapshot=snap, contract=contract
        )
        assert verdict.verdict == "FAIL"

    def test_tester_gate_refuses_a_noop_run(self) -> None:
        candidate, _, changed = GROUNDED[AgentProfileId.TESTER]
        snap = _observed([_written("tests/test_app.py"), _cmd(1, 1, "false")], changed)
        failed = [r.name for r in _run(AgentProfileId.TESTER, snap, candidate) if not r.passed]
        assert failed == ["command_run_after_last_change"]

    @pytest.mark.parametrize("argv", ["pytest -q", "pytest -q tests/test_app.py -x"])
    def test_the_acceptance_command_or_an_extension_of_it_passes(self, argv: str) -> None:
        snap = _observed(
            [_written("src/app.py"), _cmd(1, 0, "true"), _cmd(2, 0, argv)], ["src/app.py"]
        )
        assert command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed
        assert command_run_after_last_change(snap, CandidateResult(), acceptance=ACCEPT).passed

    def test_noop_does_not_mask_a_failing_acceptance_run(self) -> None:
        snap = _observed([_written("src/app.py"), _cmd(1, 1, "pytest -q"), _cmd(2, 0, "true")])
        result = command_passed_after_last_change(snap, CandidateResult(), acceptance=ACCEPT)
        assert not result.passed
        assert result.detail.endswith('last: "exit=1 pytest -q"')

    def test_no_acceptance_command_fails_closed(self) -> None:
        snap = _observed([_written("src/app.py"), _cmd(1, 0, "pytest -q")], ["src/app.py"])
        for check in (command_passed_after_last_change, command_run_after_last_change):
            result = check(snap, CandidateResult())
            assert not result.passed
            assert "verification_command" in result.detail
        candidate, _, changed = GROUNDED[AgentProfileId.DEBUGGER]
        snap = _observed([_read("src/app.py"), _written("src/app.py"), _cmd(1, 0)], changed)
        results = [c.fn(snap, candidate) for c in verifier_checks(AgentProfileId.DEBUGGER)]
        assert [r.name for r in results if not r.passed] == ["command_passed_after_last_change"]

    def test_acceptance_prefixes_from_argv(self) -> None:
        assert acceptance_prefixes([["python", "-m", "pytest", "-q"]]) == ["python -m pytest -q"]
        assert acceptance_prefixes([["pytest"], ["pytest"]]) == ["pytest"]
        # Not faithfully expressible as a whitespace-split prefix: dropped (fail
        # closed), never truncated to `python -c`, which would admit `python -c pass`.
        assert acceptance_prefixes([["python", "-c", "import sys; sys.exit(0)"]]) == []
        assert acceptance_prefixes([["pytest", ""]]) == []
        assert acceptance_prefixes([[]]) == []
        with pytest.raises(TypeError):
            acceptance_prefixes(["pytest -q"])

    def test_verifier_checks_binds_the_run_acceptance_command(self) -> None:
        checks = verifier_checks(
            AgentProfileId.DEBUGGER, acceptance_commands=[("python", "-m", "pytest", "-q")]
        )
        gate = next(c for c in checks if c.name == "command_passed_after_last_change")
        ran = _observed([_written("a.py"), _cmd(1, 0, "python -m pytest -q -x")])
        assert gate.fn(ran, CandidateResult()).passed
        noop = _observed([_written("a.py"), _cmd(1, 0, "python -c pass")])
        assert not gate.fn(noop, CandidateResult()).passed
