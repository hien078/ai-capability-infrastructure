"""Specialized AgentProfiles (harness.md §19–§21, §37, §38): behavior as data.

Profiles are DATA, not independent frameworks (§19A). All profiles share the
same HarnessKernel while specializing their loop family, policies, verifiers,
and risk boundaries (§38 R0–R4).
"""

import posixpath
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from aci.domain.runtime.evidence import CandidateResult, CheckResult, ResultContract
from aci.domain.runtime.spec import (
    AgentProfileId,
    DelegationPolicy,
    LoopFamily,
    LoopPolicy,
    ModelPolicy,
    PlanningPolicy,
    RuntimeSpec,
)
from aci.domain.runtime.state import BudgetLedger, RuntimeStateSnapshot
from aci.runtime.verification import VerifierCallable


class ProfileDefinition(BaseModel):
    """§21 profile schema: declarative runtime specialization."""

    model_config = {"frozen": True}

    profile_id: AgentProfileId
    version: str = "1.0.0"
    loop_family: LoopFamily
    model_policy: ModelPolicy
    loop_policy: LoopPolicy
    planning_policy: PlanningPolicy
    default_result_contract: ResultContract
    risk_level: int = Field(ge=0, le=4)
    instructions: str
    allowed_tools: list[str] = Field(default_factory=list)


PROFILES: dict[AgentProfileId, ProfileDefinition] = {
    AgentProfileId.CODER: ProfileDefinition(
        profile_id=AgentProfileId.CODER,
        loop_family=LoopFamily.ENGINEERING,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="adaptive"),
        default_result_contract=ResultContract(
            contract_id="coder-default-v1",
            required_fields=["summary", "changes"],
        ),
        risk_level=2,
        instructions=(
            "Implement or modify software while satisfying explicit acceptance criteria. "
            "Never claim completion without deterministic verification evidence."
        ),
    ),
    AgentProfileId.DEBUGGER: ProfileDefinition(
        profile_id=AgentProfileId.DEBUGGER,
        loop_family=LoopFamily.ENGINEERING,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="adaptive"),
        default_result_contract=ResultContract(
            contract_id="debugger-default-v1",
            required_fields=["summary", "claims"],
        ),
        risk_level=2,
        instructions=(
            "Find root cause, prove it, repair it, and demonstrate regression removal. "
            "Follow the hypothesis loop: reproduce -> localize -> hypothesize -> repair -> verify."
        ),
    ),
    AgentProfileId.RESEARCHER: ProfileDefinition(
        profile_id=AgentProfileId.RESEARCHER,
        loop_family=LoopFamily.RESEARCH,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="structured"),
        default_result_contract=ResultContract(
            contract_id="researcher-default-v1",
            required_fields=["summary", "claims"],
        ),
        risk_level=0,
        instructions=(
            "Produce evidence-grounded research with source traceability. "
            "Surface contradictions and provide citations. Read-only authority by default."
        ),
    ),
    AgentProfileId.REVIEWER: ProfileDefinition(
        profile_id=AgentProfileId.REVIEWER,
        loop_family=LoopFamily.EVALUATION,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="none"),
        default_result_contract=ResultContract(
            contract_id="reviewer-default-v1",
            required_fields=["summary", "claims"],
        ),
        risk_level=0,
        instructions=(
            "Assess changes and produce evidence-backed, deduplicated, risk-ranked findings. "
            "Read-only authority by default."
        ),
    ),
    AgentProfileId.TESTER: ProfileDefinition(
        profile_id=AgentProfileId.TESTER,
        loop_family=LoopFamily.ENGINEERING,
        model_policy=ModelPolicy(default_class="fast"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="adaptive"),
        default_result_contract=ResultContract(
            contract_id="tester-default-v1",
            required_fields=["summary", "artifacts"],
        ),
        risk_level=1,
        instructions=(
            "Validate behavior against acceptance criteria using assertion-driven testing "
            "and execution evidence."
        ),
    ),
    AgentProfileId.DEVOPS_SRE: ProfileDefinition(
        profile_id=AgentProfileId.DEVOPS_SRE,
        loop_family=LoopFamily.ENGINEERING,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="adaptive"),
        default_result_contract=ResultContract(
            contract_id="devops-default-v1",
            required_fields=["summary", "claims"],
        ),
        risk_level=3,
        instructions=(
            "Diagnose infrastructure/runtime incidents and safely propose or perform "
            "remediation with health checks."
        ),
    ),
    AgentProfileId.DATA_ANALYST: ProfileDefinition(
        profile_id=AgentProfileId.DATA_ANALYST,
        loop_family=LoopFamily.ANALYSIS,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="structured"),
        default_result_contract=ResultContract(
            contract_id="data-analyst-default-v1",
            required_fields=["summary", "artifacts"],
        ),
        risk_level=1,
        instructions=(
            "Perform reproducible data analysis with query execution, schema validation, "
            "and lineage verification."
        ),
    ),
    AgentProfileId.ARCHITECT: ProfileDefinition(
        profile_id=AgentProfileId.ARCHITECT,
        loop_family=LoopFamily.EVALUATION,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="structured"),
        default_result_contract=ResultContract(
            contract_id="architect-default-v1",
            required_fields=["summary", "artifacts"],
        ),
        risk_level=0,
        instructions=(
            "Produce architecture decisions and migration plans. Read-only default: "
            "generate ADRs, do not apply code changes."
        ),
    ),
    AgentProfileId.SECURITY_ANALYST: ProfileDefinition(
        profile_id=AgentProfileId.SECURITY_ANALYST,
        loop_family=LoopFamily.EVALUATION,
        model_policy=ModelPolicy(default_class="reasoning"),
        loop_policy=LoopPolicy(),
        planning_policy=PlanningPolicy(mode="structured"),
        default_result_contract=ResultContract(
            contract_id="security-default-v1",
            required_fields=["summary", "claims"],
        ),
        risk_level=4,
        instructions=(
            "Conduct defensive, scope-enforced security analysis with strict boundary checks. "
            "Rely on deterministic evidence."
        ),
    ),
}


def _as_profile_id(profile: AgentProfileId | str) -> AgentProfileId:
    if isinstance(profile, AgentProfileId):
        return profile
    try:
        return AgentProfileId(profile)
    except ValueError as exc:
        raise KeyError(f"unknown profile id: {profile}") from exc


def _claimed_path(claim: str) -> str:
    """``"src/app.py: fix off-by-one"`` → ``"src/app.py"`` (the action protocol format)."""
    return posixpath.normpath(claim.split(":", 1)[0].strip())


def _claimed_changes_observed(
    snapshot: RuntimeStateSnapshot, candidate: CandidateResult
) -> CheckResult:
    """INV-08 / §19.4 "changed files exist": every claimed change must match a
    file effect the tool path CONFIRMED — the model's report alone is never
    evidence."""
    name = "claimed_changes_observed"
    observed = {
        r.removeprefix("file:") for r in snapshot.changed_resources if r.startswith("file:")
    }
    if not observed:
        return CheckResult(name=name, passed=False, detail="no file change was observed")
    if not candidate.changes:
        return CheckResult(name=name, passed=False, detail="candidate claims no changes")
    unobserved = [c for c in candidate.changes if _claimed_path(c) not in observed]
    if unobserved:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"claimed but not observed: {', '.join(unobserved[:5])}",
        )
    return CheckResult(name=name, passed=True)


def verifier_checks(profile: AgentProfileId | str) -> list[VerifierCallable]:
    """§37 verification profiles: return deterministic checks mapped to profile goals."""
    pid = _as_profile_id(profile)
    if pid is AgentProfileId.CODER:
        return [
            VerifierCallable("claimed_changes_observed", _claimed_changes_observed),
            VerifierCallable(
                "summary_present",
                lambda s, c: CheckResult(
                    name="summary_present",
                    passed=bool(c.summary),
                    mandatory=True,
                    detail="" if c.summary else "summary is missing",
                ),
            ),
        ]
    if pid is AgentProfileId.DEBUGGER:
        return [
            VerifierCallable(
                "root_cause_stated",
                lambda s, c: CheckResult(
                    name="root_cause_stated",
                    passed=bool(c.claims),
                    mandatory=True,
                    detail="" if c.claims else "root cause claim is missing",
                ),
            ),
            VerifierCallable(
                "regression_evidence",
                lambda s, c: CheckResult(
                    name="regression_evidence",
                    passed=bool(c.artifacts),
                    mandatory=True,
                    detail="" if c.artifacts else "regression evidence artifact missing",
                ),
            ),
        ]
    if pid is AgentProfileId.RESEARCHER:
        return [
            VerifierCallable(
                "evidence_present",
                lambda s, c: CheckResult(
                    name="evidence_present",
                    passed=bool(c.artifacts or c.claims),
                    mandatory=True,
                    detail="" if (c.artifacts or c.claims) else "research evidence/claims missing",
                ),
            ),
            VerifierCallable(
                "summary_present",
                lambda s, c: CheckResult(
                    name="summary_present",
                    passed=bool(c.summary),
                    mandatory=True,
                    detail="" if c.summary else "summary is missing",
                ),
            ),
        ]
    if pid is AgentProfileId.REVIEWER:
        return [
            VerifierCallable(
                "findings_present",
                lambda s, c: CheckResult(
                    name="findings_present",
                    passed=bool(c.claims),
                    mandatory=True,
                    detail="" if c.claims else "review findings claims missing",
                ),
            ),
            VerifierCallable(
                "summary_present",
                lambda s, c: CheckResult(
                    name="summary_present",
                    passed=bool(c.summary),
                    mandatory=True,
                    detail="" if c.summary else "summary is missing",
                ),
            ),
        ]
    if pid is AgentProfileId.TESTER:
        return [
            VerifierCallable(
                "test_evidence_present",
                lambda s, c: CheckResult(
                    name="test_evidence_present",
                    passed=bool(c.artifacts),
                    mandatory=True,
                    detail="" if c.artifacts else "test report artifacts missing",
                ),
            )
        ]
    if pid is AgentProfileId.DEVOPS_SRE:
        return [
            VerifierCallable(
                "remediation_stated",
                lambda s, c: CheckResult(
                    name="remediation_stated",
                    passed=bool(c.claims),
                    mandatory=True,
                    detail="" if c.claims else "remediation action claim missing",
                ),
            )
        ]
    if pid is AgentProfileId.DATA_ANALYST:
        return [
            VerifierCallable(
                "analysis_artifacts_present",
                lambda s, c: CheckResult(
                    name="analysis_artifacts_present",
                    passed=bool(c.artifacts),
                    mandatory=True,
                    detail="" if c.artifacts else "data artifacts missing",
                ),
            )
        ]
    if pid is AgentProfileId.ARCHITECT:
        return [
            VerifierCallable(
                "design_artifacts_present",
                lambda s, c: CheckResult(
                    name="design_artifacts_present",
                    passed=bool(c.artifacts),
                    mandatory=True,
                    detail="" if c.artifacts else "architecture design artifacts missing",
                ),
            )
        ]
    if pid is AgentProfileId.SECURITY_ANALYST:
        return [
            VerifierCallable(
                "security_findings_present",
                lambda s, c: CheckResult(
                    name="security_findings_present",
                    passed=bool(c.claims),
                    mandatory=True,
                    detail="" if c.claims else "security finding claims missing",
                ),
            )
        ]
    raise KeyError(f"unknown profile: {profile}")


def risk_level(profile: AgentProfileId | str) -> int:
    """§38 — runtime risk level 0..4 for the given profile."""
    pid = _as_profile_id(profile)
    return PROFILES[pid].risk_level


def runtime_spec_for(
    profile: AgentProfileId | str,
    *,
    budget: BudgetLedger | None = None,
) -> RuntimeSpec:
    """Compose the immutable RuntimeSpec for one run from its AgentProfile."""
    pid = _as_profile_id(profile)
    defn = PROFILES[pid]
    loop_policy = defn.loop_policy
    if budget is not None:
        loop_policy = LoopPolicy(
            max_turns=budget.max_turns,
            max_total_tokens=budget.max_total_tokens,
            max_output_tokens=budget.max_output_tokens,
            max_tool_calls=budget.max_tool_calls,
            max_wall_time_seconds=budget.max_wall_time_seconds,
            max_recoveries=budget.max_recoveries,
        )
    else:
        budget = BudgetLedger(
            max_turns=loop_policy.max_turns,
            max_total_tokens=loop_policy.max_total_tokens,
            max_output_tokens=loop_policy.max_output_tokens,
            max_tool_calls=loop_policy.max_tool_calls,
            max_wall_time_seconds=loop_policy.max_wall_time_seconds,
            max_recoveries=loop_policy.max_recoveries,
        )
    return RuntimeSpec(
        profile_id=defn.profile_id,
        profile_version=defn.version,
        loop_family=defn.loop_family,
        model_policy=defn.model_policy,
        loop_policy=loop_policy,
        planning_policy=defn.planning_policy,
        delegation_policy=DelegationPolicy(enabled=False),
        result_contract=defn.default_result_contract,
        budget=budget,
        created_at=datetime.now(UTC),
    )
