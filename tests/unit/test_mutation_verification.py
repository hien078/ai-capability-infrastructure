"""Mutation-audit killing tests for ``src/aci/runtime/verification.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants found by the mutmut run
(``verification`` before-run: 43 killed / 18 survived). The behaviors below had
NO test in the module's relevant subset (test_verification_recovery +
test_profiles + security/test_harness_invariants):

* §19.5 — read/``listed`` evidence stays OUT of the verification bundle
  (only writes/command outputs are client-facing evidence);
* ``_dedupe`` keeps DISTINCT evidence items and collapses identical ones;
* ``to_pack`` caps the summary at 3 ``"; "``-joined repair hints (§0.2);
* the repair-hint / crash-detail / contract-detail message formats.
"""

from __future__ import annotations

from datetime import UTC, datetime

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import (
    CandidateResult,
    CheckResult,
    EvidenceItem,
    EvidenceKind,
    EvidencePack,
    ResultContract,
    VerificationResult,
)
from aci.domain.runtime.state import (
    BudgetLedger,
    RunState,
    RuntimeStateSnapshot,
    TaskState,
)
from aci.runtime.verification import VerificationManager, VerifierCallable


def _snapshot(evidence: list[EvidenceItem]) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="run-mut", created_at=datetime.now(UTC)),
        task=TaskState(task_id="t", objective="verify"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
        observed_evidence=evidence,
    )


def _contract() -> ResultContract:
    return ResultContract(contract_id="c-mut", required_fields=["summary"])


def _item(kind: EvidenceKind, ref: str, summary: str) -> EvidenceItem:
    return EvidenceItem(kind=kind, ref=ref, summary=summary)


def _check(
    name: str, passed: bool, *, detail: str = "", evidence: list[EvidenceItem] | None = None
):
    def fn(_snapshot: RuntimeStateSnapshot, _candidate: CandidateResult) -> CheckResult:
        return CheckResult(name=name, passed=passed, detail=detail, evidence=evidence or [])

    return VerifierCallable(name, fn)


class TestEvidenceBundleContents:
    def test_reads_and_listings_stay_out_of_the_bundle(self) -> None:
        """§19.5 — the bundle carries writes/command outputs, never reads."""
        evidence = [
            _item(EvidenceKind.FILE_STATE, "file://src/a.py", "read"),
            _item(EvidenceKind.FILE_STATE, "file://src", "listed"),
            _item(EvidenceKind.FILE_STATE, "file://src/a.py", "written"),
            _item(EvidenceKind.COMMAND_OUTPUT, "cmd://1", "exit=0 pytest -q"),
        ]
        vm = VerificationManager([_check("ok", True)])
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot(evidence), contract=_contract()
        )
        summaries = [i.summary for i in result.evidence.items]
        assert "written" in summaries
        assert "exit=0 pytest -q" in summaries
        assert "read" not in summaries
        assert "listed" not in summaries

    def test_dedupe_keeps_distinct_items_and_collapses_identical(self) -> None:
        """Two checks' evidence merges; an identical item appears once."""
        first = _item(EvidenceKind.TEST_RESULT, "test://1", "1 passed")
        duplicate = _item(EvidenceKind.TEST_RESULT, "test://1", "1 passed")
        distinct = _item(EvidenceKind.TEST_RESULT, "test://2", "2 passed")
        vm = VerificationManager(
            [
                _check("a", True, evidence=[first]),
                _check("b", True, evidence=[duplicate, distinct]),
            ]
        )
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot([]), contract=_contract()
        )
        refs = [i.ref for i in result.evidence.items]
        assert refs.count("test://1") == 1
        assert refs.count("test://2") == 1


class TestPackProjection:
    def test_summary_joins_first_three_hints_with_semicolon(self) -> None:
        """§0.2 — the compact pack caps the summary at 3 hints, '; '-joined."""
        result = VerificationResult(
            verdict="FAIL",
            checks=[CheckResult(name=f"c{i}", passed=False, detail=f"d{i}") for i in range(4)],
            repair_hints=[f"check 'c{i}' failed: d{i}" for i in range(4)],
        )
        pack = VerificationManager.to_pack(result)
        assert isinstance(pack, EvidencePack)
        assert pack.summary == "check 'c0' failed: d0; check 'c1' failed: d1; check 'c2' failed: d2"


class TestMessageFormats:
    def test_repair_hint_names_the_failed_check(self) -> None:
        vm = VerificationManager([_check("tests", False, detail="1 failed")])
        result = vm.verify(
            CandidateResult(summary="done"), snapshot=_snapshot([]), contract=_contract()
        )
        assert result.repair_hints == ["check 'tests' failed: 1 failed"]

    def test_crashing_check_detail_is_error_prefixed(self) -> None:
        def _boom(_snapshot: RuntimeStateSnapshot, _candidate: CandidateResult) -> CheckResult:
            raise RuntimeError("verifier crashed")

        vm = VerificationManager([VerifierCallable("crashy", _boom)])
        result = vm.verify(
            CandidateResult(summary="x"), snapshot=_snapshot([]), contract=_contract()
        )
        assert result.checks[0].detail == "error: verifier crashed"

    def test_contract_check_details(self) -> None:
        vm = VerificationManager([])
        missing = vm.verify(
            CandidateResult(summary=""), snapshot=_snapshot([]), contract=_contract()
        )
        assert [c.detail for c in missing.checks if c.name == "contract:summary"] == [
            "missing required field"
        ]
        present = vm.verify(
            CandidateResult(summary="ok"), snapshot=_snapshot([]), contract=_contract()
        )
        assert [c.detail for c in present.checks if c.name == "contract:summary"] == [""]
