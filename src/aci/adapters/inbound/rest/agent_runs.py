"""Agent-as-a-Service surface (harness.md §0.4 B, §29A): POST /v1/agent-runs.

Thin edge: request → SubtaskContract + RuntimeSpec (from the AgentProfile) →
AgentRunService → RunResult projected as compact typed state (§29A: status,
verdict, checks, evidence refs, artifacts — never the transcript)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator

from aci.adapters.inbound.rest.auth import bearer_gate
from aci.adapters.inbound.rest.wiring import Container, get_container
from aci.application.run_agent_task import new_run_id
from aci.application.workspace_changes import WorkspaceChanges
from aci.config import Settings
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.runtime.spec import AgentProfileId
from aci.domain.runtime.state import BudgetLedger
from aci.domain.runtime.subtask import AcceptanceCriterion, RunResult, SubtaskContract
from aci.runtime.profiles import runtime_spec_for


def _agent_runs_token(settings: Settings) -> str:
    """A run executes under the server user (no sandbox, §16.4), so anyone
    who can reach the port can otherwise run code here. With
    ACI_AGENT_RUNS_TOKEN set, every route requires `Authorization: Bearer
    <token>`; empty = UNAUTHENTICATED mode (build_agent_run_service warns)."""
    return settings.agent_runs_token


router = APIRouter(
    prefix="/v1/agent-runs",
    tags=["agent-runs"],
    dependencies=[Depends(bearer_gate(_agent_runs_token))],
)


#: m5 review, ADV-6: request-field bounds. The objective was already capped
#: (8000); these close the remaining amplification channels — a multi-megabyte
#: field otherwise flows into every model request and the durable store.
#: Generous by design (they bound, they do not model the context budget):
#: 100k chars ≈ 25k tokens per string field, 100–200 items per list.
_MAX_CONTEXT_CHARS = 100_000
_MAX_ITEM_CHARS = 2_000
_MAX_COMMAND_ITEM_CHARS = 4_000
_MAX_LIST_ITEMS = 100


class AgentRunRequest(BaseModel):
    objective: str = Field(min_length=1, max_length=8000)
    global_context: str = Field(default="", max_length=_MAX_CONTEXT_CHARS)
    constraints: list[Annotated[str, Field(max_length=_MAX_ITEM_CHARS)]] = Field(
        default_factory=list, max_length=_MAX_LIST_ITEMS
    )
    acceptance_criteria: list[Annotated[str, Field(max_length=_MAX_ITEM_CHARS)]] = Field(
        default_factory=list, max_length=_MAX_LIST_ITEMS
    )
    requested_profile: str = "coder"
    max_turns: int | None = Field(default=None, ge=1, le=200)
    budget: BudgetLedger | None = None
    #: Name of a directory under the server's workspace root (§16); the run
    #: works in a per-run copy. None = no workspace tools.
    workspace: str | None = None
    #: argv the verifier runs after the model's final candidate; must be a
    #: workspace run and within the run's process prefixes (§19, INV-08).
    verification_command: list[Annotated[str, Field(max_length=_MAX_COMMAND_ITEM_CHARS)]] | None = (
        Field(default=None, min_length=1, max_length=_MAX_LIST_ITEMS)
    )
    #: Workspace-relative write scopes (default: whole workspace, "."); an
    #: explicit empty list is a read-only workspace.
    write_scopes: list[Annotated[str, Field(max_length=_MAX_ITEM_CHARS)]] | None = Field(
        default=None, max_length=_MAX_LIST_ITEMS
    )
    #: Command prefixes the model may run; must narrow the server ceiling
    #: (INV-02). None = the full ceiling.
    command_prefixes: list[Annotated[str, Field(max_length=_MAX_ITEM_CHARS)]] | None = Field(
        default=None, max_length=_MAX_LIST_ITEMS
    )
    #: Tool ids whose calls pause the run for this client's approval
    #: (§13.6) — ADDED to the server's own list, never replacing it.
    approval_required_tools: list[Annotated[str, Field(max_length=200)]] | None = Field(
        default=None, max_length=50
    )
    #: Load the routed registry skills before the first model turn (on/off
    #: for this run; None = the server default ACI_AGENT_CAPABILITY_PRELOAD).
    #: Grants nothing — it only adds skill TEXT to the model's context.
    preload_capabilities: bool | None = None


class AgentRunUsage(BaseModel):
    """The ``usage`` block of the shared read model (2026-10-03): the token
    counts ``RunUsage`` carries but the flat projection dropped (xl-harness
    FINDING 2). Exactly the contract's five fields — ``cost_usd`` exists on
    RunUsage but is not in the shared contract. The flat
    turns/tool_calls/wall_time_seconds above stay (backward compatible)."""

    model_input_tokens: int = Field(default=0, ge=0)
    model_output_tokens: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    wall_seconds: float = Field(default=0.0, ge=0.0)


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
    #: Set only while status == "interrupted_approval": name it in
    #: POST /v1/agent-runs/{run_id}/resume to approve or deny the call.
    approval_id: str | None = None
    #: §41.1 usage on the wire (the shared read model): tokens per run.
    usage: AgentRunUsage = Field(default_factory=AgentRunUsage)
    #: The run's file changes vs its start manifest (the shared read model,
    #: xl-harness FINDING 1): workspace-relative paths, unified diff text
    #: (text files only, 200 KB cap), None for a run without a workspace —
    #: or one whose changes are unavailable (vanished copy, no manifest).
    changes: WorkspaceChanges | None = None


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
        approval_required_tools=body.approval_required_tools,
        preload_capabilities=body.preload_capabilities,
    )
    return _to_response(result, container.agent_run_service.changes(result.run_id))


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
    return _to_response(result, container.agent_run_service.changes(run_id))


class ReviseRequest(BaseModel):
    """§29A delta revision: everything else (criteria, constraints, budget,
    profile, workspace options) comes from the previous attempt."""

    objective: str | None = Field(default=None, min_length=1, max_length=8000)
    failed_criteria: list[Annotated[str, Field(max_length=_MAX_ITEM_CHARS)]] = Field(
        default_factory=list, max_length=_MAX_LIST_ITEMS
    )
    feedback: str = Field(default="", max_length=_MAX_CONTEXT_CHARS)
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
    return _to_response(result, container.agent_run_service.changes(result.run_id))


class ResumeRequest(BaseModel):
    """§13.6/§17.4 resume of a PAUSED run — exactly one of:
    ``{approval_id, approve}`` for an interrupted_approval run (approve →
    the pending call executes once; deny → the model is told), or
    ``{answer}`` for a clarification pause (interrupted)."""

    approval_id: str | None = Field(default=None, min_length=1, max_length=200)
    approve: bool | None = None
    answer: str | None = Field(default=None, min_length=1, max_length=8000)

    @model_validator(mode="after")
    def _one_form(self) -> "ResumeRequest":
        decision = self.approval_id is not None or self.approve is not None
        if decision == (self.answer is not None):
            raise ValueError("send either {approval_id, approve} or {answer}")
        if decision and (self.approval_id is None or self.approve is None):
            raise ValueError("an approval decision needs both approval_id and approve")
        return self


@router.post("/{run_id}/resume")
def resume_agent_run(
    run_id: str,
    body: ResumeRequest,
    container: Annotated[Container, Depends(get_container)],
) -> AgentRunResponse:
    """Continue the SAME run from its pause checkpoint (at most once). The
    response is the RunResult projection — never the transcript, never a
    server path."""
    result = container.agent_run_service.resume(
        run_id, approval_id=body.approval_id, approve=body.approve, answer=body.answer
    )
    return _to_response(result, container.agent_run_service.changes(run_id))


def _to_response(result: RunResult, changes: WorkspaceChanges | None = None) -> AgentRunResponse:
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
        approval_id=result.approval_id,
        usage=AgentRunUsage(
            model_input_tokens=result.usage.model_input_tokens,
            model_output_tokens=result.usage.model_output_tokens,
            turns=result.usage.turns,
            tool_calls=result.usage.tool_calls,
            wall_seconds=result.usage.wall_time_seconds,
        ),
        changes=changes,
    )
