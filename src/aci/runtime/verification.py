"""VerificationManager (harness.md §19): the completion gate — deterministic
evidence first, model self-assessment last (INV-08)."""

from collections.abc import Callable

from aci.domain.runtime.evidence import (
    CandidateResult,
    CheckResult,
    EvidenceBundle,
    EvidenceItem,
    EvidencePack,
    ResultContract,
    VerificationResult,
)
from aci.domain.runtime.state import RuntimeStateSnapshot


class VerificationCheckError(Exception):
    """A verifier callable crashed; recorded as a failed mandatory check."""


class VerifierCallable:
    """One deterministic check: name, mandatory flag, run(snapshot, candidate)."""

    def __init__(
        self,
        name: str,
        fn: Callable[[RuntimeStateSnapshot, CandidateResult], CheckResult],
        *,
        mandatory: bool = True,
    ) -> None:
        self.name = name
        self.fn = fn
        self.mandatory = mandatory


class VerificationManager:
    """§19.1–19.6 — cheap deterministic checks first; LLM evaluator only if
    needed (none in v2 default). Verdict rules: a failed MANDATORY check
    forces FAIL; INCONCLUSIVE mandatory → INCONCLUSIVE; optional failures
    never block (recorded only)."""

    def __init__(self, checks: list[VerifierCallable]) -> None:
        self._checks = list(checks)

    def verify(
        self,
        candidate: CandidateResult,
        *,
        snapshot: RuntimeStateSnapshot,
        contract: ResultContract,
    ) -> VerificationResult:
        checks: list[CheckResult] = []
        evidence_items: list[EvidenceItem] = []
        verdict: str = "PASS"
        for check in self._checks:
            try:
                result = check.fn(snapshot, candidate)
            except Exception as exc:  # noqa: BLE001 — a crashing check is evidence, not a crash
                result = CheckResult(
                    name=check.name, passed=False, mandatory=check.mandatory, detail=f"error: {exc}"
                )
            # The VerifierCallable owns the mandatory flag; a check fn that
            # sets it differently would silently change verdict semantics.
            if result.mandatory != check.mandatory:
                result = CheckResult(
                    name=result.name,
                    passed=result.passed,
                    mandatory=check.mandatory,
                    detail=result.detail,
                )
            checks.append(result)
            if result.passed:
                continue
            if check.mandatory:
                verdict = "FAIL" if not result.passed else verdict
        # Contract validation (§24): required fields must be present + non-empty.
        contract_checks = _validate_contract(candidate, contract)
        checks.extend(contract_checks)
        if any((not c.passed) and c.mandatory for c in contract_checks):
            verdict = "FAIL"
        if verdict == "PASS" and any((not c.passed) and c.mandatory for c in checks):
            verdict = "FAIL"
        return VerificationResult(
            verdict=verdict,  # type: ignore[arg-type]
            checks=checks,
            evidence=EvidenceBundle(items=evidence_items),
            repair_hints=[f"check '{c.name}' failed: {c.detail}" for c in checks if not c.passed],
        )

    @staticmethod
    def to_pack(result: VerificationResult) -> EvidencePack:
        """§0.2 — compact client-facing projection of the bundle."""
        return EvidencePack(
            verification_verdict=result.verdict,
            checks=[f"{'PASS' if c.passed else 'FAIL'}:{c.name}" for c in result.checks],
            evidence_refs=[i.ref for i in result.evidence.items],
            summary="; ".join(result.repair_hints[:3]),
        )


def _validate_contract(candidate: CandidateResult, contract: ResultContract) -> list[CheckResult]:
    results: list[CheckResult] = []
    for field in contract.required_fields:
        value = getattr(candidate, field, None)
        present = value is not None and (not isinstance(value, (str, list)) or len(value) > 0)
        results.append(
            CheckResult(
                name=f"contract:{field}",
                passed=present,
                mandatory=True,
                detail="" if present else "missing required field",
            )
        )
    return results
