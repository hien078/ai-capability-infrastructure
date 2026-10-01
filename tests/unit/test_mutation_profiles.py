"""Mutation-audit killing tests for ``src/aci/runtime/profiles.py`` (job m6-mutation-audit).

Each test kills a class of surviving mutants from the mutmut before-run
(profiles: 297 mutants, 200 killed + 11 suspicious, 86 survived). The
behaviors below had NO test in the module's relevant subset:

* ``ProfileDefinition`` is frozen (§21 declarative config, not runtime data)
  and the §38 risk scale is bounded to 0..4;
* ``runtime_spec_for`` pins the profile version and the §0.3 default:
  delegation OFF;
* a claim citing a file that was LISTED *and* READ is grounded (listed-only
  is not — but read beats listed);
* command evidence counts even when the run wrote nothing (scope "in this
  run").

The bulk of the surviving mutants are the PROFILES instruction/contract-id
strings and CheckResult detail prose — prompt-engineering data and
model-facing hints, not deterministic contracts; they are deliberately not
pinned.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from aci.domain.runtime.authority import GrantEnvelope
from aci.domain.runtime.evidence import (
    CandidateResult,
    CheckResult,
    EvidenceItem,
    EvidenceKind,
    ResultContract,
)
from aci.domain.runtime.spec import (
    AgentProfileId,
    LoopFamily,
    LoopPolicy,
    ModelPolicy,
    PlanningPolicy,
)
from aci.domain.runtime.state import BudgetLedger, RunState, RuntimeStateSnapshot, TaskState
from aci.runtime.profiles import (
    PROFILES,
    ProfileDefinition,
    claims_grounded,
    command_passed_after_last_change,
    runtime_spec_for,
)


def _snapshot(evidence: list[EvidenceItem]) -> RuntimeStateSnapshot:
    return RuntimeStateSnapshot(
        run=RunState(run_id="r-mut", created_at=datetime.now(UTC)),
        task=TaskState(task_id="t", objective="verify"),
        budget=BudgetLedger(),
        grants=GrantEnvelope(),
        observed_evidence=evidence,
    )


def _file(path: str, verb: str) -> EvidenceItem:
    return EvidenceItem(kind=EvidenceKind.FILE_STATE, ref=f"file://{path}", summary=verb)


def _cmd(status: int | str, argv: str) -> EvidenceItem:
    return EvidenceItem(
        kind=EvidenceKind.COMMAND_OUTPUT, ref="cmd://1", summary=f"exit={status} {argv}"
    )


class TestProfileDefinitionModel:
    def _definition(self, risk_level: int) -> ProfileDefinition:
        return ProfileDefinition(
            profile_id=AgentProfileId.CODER,
            loop_family=LoopFamily.ENGINEERING,
            model_policy=ModelPolicy(default_class="reasoning"),
            loop_policy=LoopPolicy(),
            planning_policy=PlanningPolicy(mode="adaptive"),
            default_result_contract=ResultContract(
                contract_id="mut-v1", required_fields=["summary"]
            ),
            risk_level=risk_level,
            instructions="do the thing",
        )

    def test_risk_scale_is_bounded_to_four(self) -> None:
        """§38 R0–R4: risk_level 5 is out of the scale."""
        assert self._definition(4).risk_level == 4
        with pytest.raises(ValidationError):
            self._definition(5)

    def test_definitions_are_frozen(self) -> None:
        """§21: profiles are declarative CONFIG — nothing may rewrite a live
        profile (a mutable profile would let a run widen its own risk level)."""
        definition = PROFILES[AgentProfileId.CODER]
        with pytest.raises(ValidationError):
            definition.risk_level = 0  # type: ignore[misc]


class TestRuntimeSpec:
    def test_spec_pins_version_and_delegation_off(self) -> None:
        """§0.3: delegation is OFF by default; the spec carries the profile
        version (telemetry/contract identity)."""
        spec = runtime_spec_for(AgentProfileId.CODER)
        assert spec.profile_version == "1.0.0"
        assert spec.delegation_policy.enabled is False
        assert spec.budget.max_turns > 0


class TestClaimsGrounded:
    def test_listed_and_read_file_is_a_valid_source(self) -> None:
        """A directory listing alone is not a source, but a file that was
        listed AND read was read — citing it is grounded."""
        snapshot = _snapshot([_file("docs/x.md", "listed"), _file("docs/x.md", "read")])
        candidate = CandidateResult(
            summary="found it", claims=["docs/x.md: requires idempotent retries"]
        )
        result = claims_grounded(snapshot, candidate)
        assert isinstance(result, CheckResult)
        assert result.passed is True

    def test_listed_only_file_is_not_a_source(self) -> None:
        snapshot = _snapshot([_file("docs/x.md", "listed")])
        candidate = CandidateResult(
            summary="found it", claims=["docs/x.md: requires idempotent retries"]
        )
        result = claims_grounded(snapshot, candidate)
        assert result.passed is False


class TestCommandEvidence:
    def test_passing_command_counts_with_no_writes(self) -> None:
        """A run that wrote nothing still has command evidence — the scope is
        "in this run", and the FIRST command must not be skipped."""
        snapshot = _snapshot([_cmd(0, "pytest -q")])
        candidate = CandidateResult(summary="done")
        result = command_passed_after_last_change(snapshot, candidate, acceptance=["pytest -q"])
        assert result.passed is True
