"""Agent-as-a-Service surface (harness.md §0.4 B, §29A): POST /v1/agent-runs.

Thin edge: request → SubtaskContract + RuntimeSpec (from the AgentProfile) →
AgentRunService → RunResult projected as compact typed state (§29A: status,
verdict, checks, evidence refs, artifacts — never the transcript)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.application.run_agent_task import new_run_id
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.spec import AgentProfileId
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.subtask import AcceptanceCriterion, RunResult, SubtaskContract
from aci.runtime.profiles import runtime_spec_for

router = APIRouter(prefix="/v1/agent-runs", tags=["agent-runs"])


class AgentRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=8000)
    global_context: str = ""
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    requested_profile: str = "coder"
    max_turns: int | None = Field(default=None, ge=1, le=200)
    budget: BudgetLedger | None = None
    #: Name of a directory under the server's workspace root (§16); the run
    #: works in a per-run copy. None = no workspace tools.
    workspace: str | None = None
    #: argv the verifier runs after the model's final candidate; must be a
    #: workspace run and within the run's process prefixes (§19, INV-08).
    verification_command: list[str] | None = Field(default=None, min_length=1)
    #: Workspace-relative write scopes (default: whole workspace, "."); an
    #: explicit empty list is a read-only workspace.
    write_scopes: list[str] | None = None
    #: Command prefixes the model may run; must narrow the server ceiling
    #: (INV-02). None = the full ceiling.
    command_prefixes: list[str] | None = None


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    stop_reason: str | None = None
    detail_code: str | None = None
    summary: str = ""
    evidence_verdict: str | None = None
    checks: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    wall_time_seconds: float = 0.0


def _profile(requested: str) -> AgentProfileId:
    try:
        return AgentProfileId(requested)
    except ValueError as exc:
        raise DomainError(ErrorCode.CLIENT_INCOMPATIBLE, f"unknown profile: {requested}") from exc


@router.post("", status_code=201)
def start_agent_run(
    body: AgentRunRequest,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    profile = _profile(body.requested_profile)
    contract = SubtaskContract(
        task_id=new_run_id(),
        objective=body.objective,
        global_context=body.global_context,
        constraints=body.constraints,
        acceptance_criteria=[
            AcceptanceCriterion(criterion_id=f"ac-{i + 1}", description=c)
            for i, c in enumerate(body.acceptance_criteria)
        ],
        requested_profile=profile.value,
        budget=body.budget,
        created_at=datetime.now(UTC),
    )
    spec = runtime_spec_for(profile, budget=body.budget)
    result = container.agent_run_service.run(
        contract,
        spec,
        max_turns=body.max_turns,
        workspace=body.workspace,
        verification_command=body.verification_command,
        write_scopes=body.write_scopes,
        command_prefixes=body.command_prefixes,
    )
    return _to_response(result)


@router.post("/{run_id}/cancel")
def cancel_agent_run(
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> dict[str, bool]:
    return {"cancelled": container.agent_run_service.cancel(run_id)}


@router.get("/{run_id}")
def get_agent_run(
    run_id: str,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    result = container.agent_run_service.get(run_id)
    if result is None:
        raise DomainError(ErrorCode.ROUTE_RUN_NOT_FOUND, f"unknown agent run: {run_id}")
    return _to_response(result)


class ReviseRequest(BaseModel):
    """§29A delta revision: everything else (criteria, constraints, budget,
    profile, workspace options) comes from the previous attempt."""

    objective: str | None = Field(default=None, min_length=1, max_length=8000)
    failed_criteria: list[str] = Field(default_factory=list)
    feedback: str = ""
    max_turns: int | None = Field(default=None, ge=1, le=200)


@router.post("/{run_id}/revise", status_code=201)
def revise_agent_run(
    run_id: str,
    body: ReviseRequest,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    result = container.agent_run_service.revise(
        run_id,
        objective=body.objective,
        failed_criteria=body.failed_criteria,
        feedback=body.feedback,
        max_turns=body.max_turns,
    )
    return _to_response(result)


def _to_response(result: RunResult) -> AgentRunResponse:
    evidence = result.evidence
    return AgentRunResponse(
        run_id=result.run_id,
        status=result.status.value,
        stop_reason=result.stop_reason.value if result.stop_reason else None,
        detail_code=result.detail_code,
        summary=result.summary,
        evidence_verdict=evidence.verification_verdict if evidence is not None else None,
        checks=list(evidence.checks) if evidence is not None else [],
        evidence_refs=list(evidence.evidence_refs) if evidence is not None else [],
        artifacts=list(result.artifacts),
        turns=result.usage.turns,
        tool_calls=result.usage.tool_calls,
        wall_time_seconds=result.usage.wall_time_seconds,
    )
