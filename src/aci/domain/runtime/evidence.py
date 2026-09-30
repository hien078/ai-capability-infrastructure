"""Evidence + result contracts (harness.md §0.2, §19, §24, §34).

``EvidenceBundle`` is the full internal verification/evidence object;
``EvidencePack`` is its compact client-facing projection (§0.2). The model may
propose completion; only verification evidence gates SUCCEEDED (INV-08).
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class EvidenceKind(StrEnum):
    TEST_RESULT = "test_result"
    DIFF = "diff"
    LINT_RESULT = "lint_result"
    TYPECHECK_RESULT = "typecheck_result"
    COMMAND_OUTPUT = "command_output"
    FILE_STATE = "file_state"
    VERIFIER_NOTE = "verifier_note"
    CAPABILITY_TRACE = "capability_trace"


class EvidenceItem(BaseModel):
    model_config = {"frozen": True}

    kind: EvidenceKind
    ref: str = Field(min_length=1)  # artifact://… or file://…
    sha256: str | None = None
    summary: str = ""


class EvidenceBundle(BaseModel):
    """Full internal evidence object (§19.5)."""

    model_config = {"frozen": True}

    items: list[EvidenceItem] = Field(default_factory=list)


class EvidencePack(BaseModel):
    """Compact client-facing projection (§0.2, §58) — the client accepts on
    ResultContract + EvidencePack + artifact refs, never the full transcript."""

    model_config = {"frozen": True}

    verification_verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    checks: list[str] = Field(default_factory=list)  # check names + outcomes
    evidence_refs: list[str] = Field(default_factory=list)
    summary: str = ""


class CheckResult(BaseModel):
    model_config = {"frozen": True}

    name: str = Field(min_length=1)
    passed: bool
    mandatory: bool = True
    detail: str = ""


class VerificationResult(BaseModel):
    """§19.3 — deterministic-first verdict with repair hints."""

    model_config = {"frozen": True}

    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    checks: list[CheckResult] = Field(default_factory=list)
    evidence: EvidenceBundle = Field(default_factory=EvidenceBundle)
    repair_hints: list[str] = Field(default_factory=list)


class ResultContract(BaseModel):
    """§24 — what a valid result must contain; the verifier checks it before
    SUCCESS. ``required_fields`` are names; ``acceptance`` maps check names to
    expected outcomes."""

    model_config = {"frozen": True}

    contract_id: str = Field(min_length=1)
    required_fields: list[str] = Field(min_length=1)
    acceptance: dict[str, str] = Field(default_factory=dict)  # check -> PASS


class CandidateResult(BaseModel):
    """§34.3 — the model's proposed result BEFORE verification (INV-07)."""

    model_config = {"frozen": True}

    summary: str = ""
    changes: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    claims: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    remaining_work: list[str] = Field(default_factory=list)
    criteria_addressed: list[str] = Field(default_factory=list)
