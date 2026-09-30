"""Agent-as-a-Service surface (harness.md §0.4 B, §29): POST /v1/agent-runs.

Thin edge: request → SubtaskContract + RuntimeSpec (from the AgentProfile) →
AgentRunService → RunResult. The edge never touches kernel internals."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from aci.adapters.inbound.rest.wiring import Container, get_container
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


class AgentRunResponse(BaseModel):
    run_id: str
    status: str
    stop_reason: str | None = None
    summary: str = ""
    evidence_verdict: str | None = None
    turns: int = 0
    tool_calls: int = 0
    wall_time_seconds: float = 0.0


@router.post("", status_code=201)
def start_agent_run(
    body: AgentRunRequest,
    request: Request,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    try:
        profile = AgentProfileId(body.requested_profile)
    except ValueError as exc:
        raise DomainError(
            ErrorCode.CLIENT_INCOMPATIBLE,
            f"unknown profile: {body.requested_profile}",
        ) from exc
    contract = SubtaskContract(
        task_id=f"run_{uuid4().hex[:12]}",
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
    result = container.agent_run_service.run(contract, spec, max_turns=body.max_turns)
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
    objective: str = Field(min_length=1, max_length=8000)
    failed_criteria: list[str] = Field(default_factory=list)
    feedback: str = ""
    requested_profile: str = "coder"
    max_turns: int | None = Field(default=None, ge=1, le=200)


@router.post("/{run_id}/revise", status_code=201)
def revise_agent_run(
    run_id: str,
    body: ReviseRequest,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    """§29.6 — a revision attempt linked to the previous run, carrying the
    failed criteria + feedback (never a blind restart)."""
    try:
        profile = AgentProfileId(body.requested_profile)
    except ValueError as exc:
        raise DomainError(
            ErrorCode.CLIENT_INCOMPATIBLE,
            f"unknown profile: {body.requested_profile}",
        ) from exc
    contract = SubtaskContract(
        task_id=f"run_{uuid4().hex[:12]}",
        objective=body.objective,
        acceptance_criteria=[],
        requested_profile=profile.value,
        created_at=datetime.now(UTC),
    )
    spec = runtime_spec_for(profile)
    result = container.agent_run_service.revise(
        run_id,
        contract,
        spec,
        failed_criteria=body.failed_criteria,
        feedback=body.feedback,
        max_turns=body.max_turns,
    )
    return _to_response(result)


def _to_response(result: RunResult) -> AgentRunResponse:
    return AgentRunResponse(
        run_id=result.run_id,
        status=result.status.value,
        stop_reason=result.stop_reason.value if result.stop_reason else None,
        summary=result.summary,
        evidence_verdict=(
            result.evidence.verification_verdict if result.evidence is not None else None
        ),
        turns=result.usage.turns,
        tool_calls=result.usage.tool_calls,
        wall_time_seconds=result.usage.wall_time_seconds,
    )
