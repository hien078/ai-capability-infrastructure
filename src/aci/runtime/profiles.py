"""Specialized AgentProfiles (harness.md §19–§21, §37, §38): behavior as data.

Profiles are DATA, not independent frameworks (§19A). All profiles share the
same HarnessKernel while specializing their loop family, policies, verifiers,
and risk boundaries (§38 R0–R4). Every verifier check is grounded in evidence
the tool path OBSERVED, never in the model's own report (INV-08, §19.4).
"""

import posixpath
import re
import shlex
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from functools import partial

from pydantic import BaseModel, Field

from aci.domain.runtime.evidence import (
    CandidateResult,
    CheckResult,
    EvidenceItem,
    EvidenceKind,
    ResultContract,
)
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
from aci.runtime.workspace import command_within_prefixes

Check = Callable[[RuntimeStateSnapshot, CandidateResult], CheckResult]

_CLAIMS_FORMAT = (
    'Start every "claims" entry with the workspace-relative file it rests on, as '
    '"path[:line[-line]]: text" (e.g. "src/app.py:42: loop bound is off by one"); '
    "claims are checked against the files you actually read or changed."
)
_ARTIFACTS_FORMAT = (
    'List every file you produced in "artifacts" as "path" or "path: note"; '
    "artifacts are checked against the files you actually wrote."
)


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
            contract_id="debugger-default-v2",
            required_fields=["summary", "claims", "changes"],
        ),
        risk_level=2,
        instructions=" ".join(
            (
                "Find root cause, prove it, repair it, and demonstrate regression removal.",
                "Follow the hypothesis loop: reproduce -> localize -> hypothesize -> repair "
                "-> verify.",
                _CLAIMS_FORMAT,
                'List the fix in "changes". After your last edit, run the regression check: '
                "a command must exit 0 after your final write.",
            )
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
        instructions=" ".join(
            (
                "Produce evidence-grounded research with source traceability. "
                "Surface contradictions. Read-only authority by default.",
                _CLAIMS_FORMAT,
            )
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
        instructions=" ".join(
            (
                "Assess changes and produce evidence-backed, deduplicated, risk-ranked "
                "findings, one claim per finding. Read-only authority by default.",
                _CLAIMS_FORMAT,
            )
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
        instructions=" ".join(
            (
                "Validate behavior against acceptance criteria using assertion-driven testing "
                "and execution evidence.",
                _ARTIFACTS_FORMAT,
                "Run the tests after your last edit; a failing run is evidence, a timeout is not.",
            )
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
        instructions=" ".join(
            (
                "Diagnose infrastructure/runtime incidents and safely propose or perform "
                "remediation with health checks.",
                _CLAIMS_FORMAT,
            )
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
        instructions=" ".join(
            (
                "Perform reproducible data analysis with query execution, schema validation, "
                "and lineage verification. Write results to files.",
                _ARTIFACTS_FORMAT,
            )
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
        instructions=" ".join(
            (
                "Produce architecture decisions and migration plans. Write each ADR or plan "
                "as a file; do not apply code changes.",
                _ARTIFACTS_FORMAT,
            )
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
        instructions=" ".join(
            (
                "Conduct defensive, scope-enforced security analysis with strict boundary "
                "checks. Rely on deterministic evidence.",
                _CLAIMS_FORMAT,
            )
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


_MAX_LISTED = 5
_MAX_QUOTED = 100
_LINE_SUFFIX = re.compile(r"(?P<path>.+?)(?::\d+(?:-\d+)?)?")
_EXIT_CODE = re.compile(r"-?[0-9]+")
_CITE_HINT = 'cite a file you read or changed as "path[:line]: text"'


def _norm(path: str) -> str:
    return posixpath.normpath(path.strip())


def _claimed_path(entry: str) -> str:
    """``"src/app.py: fix off-by-one"`` → ``"src/app.py"`` (changes/artifacts format)."""
    return _norm(entry.split(":", 1)[0])


def _cited_path(claim: str) -> str | None:
    """``"src/app.py:42: text"`` → ``"src/app.py"``; None when the claim cites nothing."""
    head, sep, _ = claim.strip().partition(": ")
    match = _LINE_SUFFIX.fullmatch(head.strip())
    if not sep or match is None:
        return None
    return _norm(match.group("path"))


def _quote(text: str) -> str:
    text = text.strip()
    return f'"{text[:_MAX_QUOTED]}..."' if len(text) > _MAX_QUOTED else f'"{text}"'


def _bounded(entries: Sequence[str]) -> str:
    shown = "; ".join(entries[:_MAX_LISTED])
    extra = len(entries) - _MAX_LISTED
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _first_word(text: str) -> str:
    words = text.split(maxsplit=1)
    return words[0] if words else ""


def _is_file_event(item: EvidenceItem, verb: str) -> bool:
    """FILE_STATE evidence ``file://<path>`` whose summary verb is read/listed/written."""
    return (
        item.kind == EvidenceKind.FILE_STATE
        and item.ref.startswith("file://")
        and _first_word(item.summary) == verb
    )


def _file_path(item: EvidenceItem) -> str:
    return _norm(item.ref.removeprefix("file://"))


def _files(snapshot: RuntimeStateSnapshot, verb: str) -> set[str]:
    return {_file_path(i) for i in snapshot.observed_evidence if _is_file_event(i, verb)}


def _changed_files(snapshot: RuntimeStateSnapshot) -> set[str]:
    """§41.2 confirmed file side effects (``file:<path>``)."""
    return {
        _norm(r.removeprefix("file:")) for r in snapshot.changed_resources if r.startswith("file:")
    }


def _exit_status(item: EvidenceItem) -> str:
    """``"exit=0 pytest -q"`` → ``"0"``; ``"exit=timeout …"`` → ``"timeout"``."""
    word = _first_word(item.summary)
    return word.removeprefix("exit=") if word.startswith("exit=") else ""


def summary_present(snapshot: RuntimeStateSnapshot, candidate: CandidateResult) -> CheckResult:
    """§24 — the candidate carries a non-empty summary."""
    return CheckResult(
        name="summary_present",
        passed=bool(candidate.summary),
        detail="" if candidate.summary else "summary is missing",
    )


def claimed_changes_observed(
    snapshot: RuntimeStateSnapshot, candidate: CandidateResult
) -> CheckResult:
    """INV-08 / §19.4 "changed files exist": every claimed change must match a
    file effect the tool path CONFIRMED — the model's report alone is never
    evidence."""
    name = "claimed_changes_observed"
    observed = _changed_files(snapshot)
    if not observed:
        return CheckResult(name=name, passed=False, detail="no file change was observed")
    if not candidate.changes:
        return CheckResult(name=name, passed=False, detail="candidate claims no changes")
    unobserved = [c for c in candidate.changes if _claimed_path(c) not in observed]
    if unobserved:
        return CheckResult(
            name=name, passed=False, detail=f"claimed but not observed: {_bounded(unobserved)}"
        )
    return CheckResult(name=name, passed=True)


def claims_grounded(snapshot: RuntimeStateSnapshot, candidate: CandidateResult) -> CheckResult:
    """INV-08 / §19.4: every claim cites a file this run actually READ or CHANGED;
    a listed directory entry is not a source."""
    name = "claims_grounded"
    if not candidate.claims:
        return CheckResult(name=name, passed=False, detail=f"no claims; {_CITE_HINT}")
    sources = _files(snapshot, "read") | _changed_files(snapshot)
    listed = _files(snapshot, "listed")
    ungrounded: list[str] = []
    for claim in candidate.claims:
        path = _cited_path(claim)
        if path is None:
            ungrounded.append(f"{_quote(claim)} cites no source")
        elif path in listed and path not in sources:
            ungrounded.append(f"{_quote(claim)} cites {path}, which was only listed, never read")
        elif path not in sources:
            ungrounded.append(f"{_quote(claim)} cites {path}, which was never read or changed")
    if ungrounded:
        return CheckResult(
            name=name,
            passed=False,
            detail=(
                f"{len(ungrounded)}/{len(candidate.claims)} claims ungrounded: "
                f"{_bounded(ungrounded)}; {_CITE_HINT}"
            ),
        )
    return CheckResult(name=name, passed=True)


def artifacts_observed(snapshot: RuntimeStateSnapshot, candidate: CandidateResult) -> CheckResult:
    """§19.4: every listed artifact is a file this run was observed to produce."""
    name = "artifacts_observed"
    if not candidate.artifacts:
        return CheckResult(
            name=name, passed=False, detail='no artifacts; list each file you wrote as "path"'
        )
    changed = _changed_files(snapshot)
    missing = [a for a in candidate.artifacts if _claimed_path(a) not in changed]
    if missing:
        return CheckResult(
            name=name,
            passed=False,
            detail=f"artifacts not written by this run: {_bounded(missing)}",
        )
    return CheckResult(name=name, passed=True)


def acceptance_prefixes(commands: Sequence[Sequence[str]]) -> list[str]:
    """Acceptance argv → `command_within_prefixes` prefix strings.

    Prefixes are whitespace-split, so an argv token that is empty or contains
    whitespace (``python -c "import sys; ..."``) has no faithful prefix form;
    such a command is DROPPED rather than truncated — truncating would turn
    ``python -c <script>`` into ``python -c``, which admits ``python -c pass``.
    Dropping fails closed."""
    prefixes: list[str] = []
    for argv in commands:
        if isinstance(argv, str):  # a bare string is a Sequence[str]: refuse, never char-split
            raise TypeError("acceptance commands are argv lists, not strings")
        tokens = list(argv)
        if tokens and all(t and not any(c.isspace() for c in t) for t in tokens):
            prefixes.append(" ".join(tokens))
    return list(dict.fromkeys(prefixes))


def _observed_argv(item: EvidenceItem) -> list[str]:
    """``"exit=0 python -m pytest -q"`` → argv (the summary is ``shlex.join(argv)``)."""
    _, _, rendered = item.summary.strip().partition(" ")
    try:
        return shlex.split(rendered)
    except ValueError:
        return []


def _command_check(
    name: str,
    snapshot: RuntimeStateSnapshot,
    *,
    accept: Callable[[str], bool],
    wanted: str,
    acceptance: Sequence[str],
) -> CheckResult:
    """Pass iff an ACCEPTANCE-TIED COMMAND_OUTPUT after the last observed write has
    an accepted exit status.

    A command is acceptance-tied iff its argv starts with one of the run's
    acceptance prefixes (token-aware, `command_within_prefixes`). Any other
    command — ``true``, ``python -c pass``, ``pytest --version`` under a
    ``pytest -q`` acceptance — is not evidence. With NO acceptance prefixes the
    check FAILS CLOSED: nothing distinguishes a test run from a no-op."""
    if not acceptance:
        return CheckResult(
            name=name,
            passed=False,
            detail=(
                "no acceptance command is configured for this run, so no command can stand "
                "as test evidence; the delegating client must supply verification_command"
            ),
        )
    evidence = snapshot.observed_evidence
    writes = [i for i, item in enumerate(evidence) if _is_file_event(item, "written")]
    start = writes[-1] + 1 if writes else 0
    commands = [e for e in evidence[start:] if e.kind == EvidenceKind.COMMAND_OUTPUT]
    scope = (
        f"after the last write to {_file_path(evidence[writes[-1]])}" if writes else "in this run"
    )
    tied = [c for c in commands if command_within_prefixes(_observed_argv(c), acceptance)]
    run_one = f"run {_bounded([_quote(p) for p in acceptance])}"
    if not tied:
        if not commands:
            return CheckResult(name=name, passed=False, detail=f"no command ran {scope}; {run_one}")
        last = _quote(commands[-1].summary)
        return CheckResult(
            name=name,
            passed=False,
            detail=f"no acceptance command ran {scope} (last: {last} is not one); {run_one}",
        )
    if any(accept(_exit_status(c)) for c in tied):
        return CheckResult(name=name, passed=True)
    last = _quote(tied[-1].summary)
    return CheckResult(
        name=name,
        passed=False,
        detail=f"no acceptance command {wanted} {scope}; last: {last}",
    )


def command_passed_after_last_change(
    snapshot: RuntimeStateSnapshot,
    candidate: CandidateResult,
    *,
    acceptance: Sequence[str] = (),
) -> CheckResult:
    """§19.4 regression evidence: an acceptance-tied command exited 0 after the final
    observed write. `acceptance` holds `command_within_prefixes` prefix strings;
    empty (the default) fails closed."""
    return _command_check(
        "command_passed_after_last_change",
        snapshot,
        accept=lambda status: status == "0",
        wanted="exited 0",
        acceptance=acceptance,
    )


def command_run_after_last_change(
    snapshot: RuntimeStateSnapshot,
    candidate: CandidateResult,
    *,
    acceptance: Sequence[str] = (),
) -> CheckResult:
    """§19.4 execution evidence: an acceptance-tied command ran to an exit code (pass
    or fail, not a timeout) after the final observed write. Empty `acceptance`
    (the default) fails closed."""
    return _command_check(
        "command_run_after_last_change",
        snapshot,
        accept=lambda status: _EXIT_CODE.fullmatch(status) is not None,
        wanted="finished with an exit code",
        acceptance=acceptance,
    )


_CHECKS: dict[str, Check] = {
    "summary_present": summary_present,
    "claimed_changes_observed": claimed_changes_observed,
    "claims_grounded": claims_grounded,
    "artifacts_observed": artifacts_observed,
    "command_passed_after_last_change": command_passed_after_last_change,
    "command_run_after_last_change": command_run_after_last_change,
}

#: Checks whose evidence must come from an ACCEPTANCE-TIED command; bound per
#: run by `verifier_checks(..., acceptance_commands=...)`.
_ACCEPTANCE_CHECKS = {
    "command_passed_after_last_change": command_passed_after_last_change,
    "command_run_after_last_change": command_run_after_last_change,
}

_PROFILE_CHECKS: dict[AgentProfileId, tuple[str, ...]] = {
    AgentProfileId.CODER: ("claimed_changes_observed", "summary_present"),
    AgentProfileId.DEBUGGER: (
        "claims_grounded",
        "claimed_changes_observed",
        "command_passed_after_last_change",
    ),
    AgentProfileId.TESTER: ("artifacts_observed", "command_run_after_last_change"),
    AgentProfileId.RESEARCHER: ("claims_grounded", "summary_present"),
    AgentProfileId.REVIEWER: ("claims_grounded", "summary_present"),
    AgentProfileId.SECURITY_ANALYST: ("claims_grounded",),
    AgentProfileId.ARCHITECT: ("artifacts_observed",),
    AgentProfileId.DATA_ANALYST: ("artifacts_observed",),
    AgentProfileId.DEVOPS_SRE: ("claims_grounded",),
}


def verifier_checks(
    profile: AgentProfileId | str,
    *,
    acceptance_commands: Sequence[Sequence[str]] = (),
) -> list[VerifierCallable]:
    """§37 verification profiles: deterministic checks grounded in observed evidence.

    `acceptance_commands` are the run's acceptance-tied argv (the client's
    `verification_command`): the command-evidence checks count ONLY commands
    whose argv starts with one of them. Default empty → those checks fail
    closed (DEBUGGER/TESTER require a verification_command to ever pass)."""
    pid = _as_profile_id(profile)
    acceptance = acceptance_prefixes(acceptance_commands)
    checks: list[VerifierCallable] = []
    for name in _PROFILE_CHECKS[pid]:
        fn = (
            partial(_ACCEPTANCE_CHECKS[name], acceptance=acceptance)
            if name in _ACCEPTANCE_CHECKS
            else _CHECKS[name]
        )
        checks.append(VerifierCallable(name, fn))
    return checks


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
