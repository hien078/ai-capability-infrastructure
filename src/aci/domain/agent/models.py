"""Agent platform domain contracts (V3, plan §56; A2A mapping §30.1; §32 semantics).

An agent is a capability (``kind=agent``, verb ``delegate`` — §8/§32); the
platform routes to it like any other capability. What V3 adds on top:

- ``AgentProfile`` — runtime configuration DATA, not a subsystem (§56.1:
  Planner/Coder/Reviewer/Verifier are profiles unless their runtime
  semantics genuinely differ). A profile pins the model class, allowed
  tools, required/optional skills (a routing integration point), budget,
  and an explicit execution policy (§32: no implicit side effects).
- ``AgentTask`` / ``TaskMessage`` / ``TaskArtifact`` — the delegated-task
  lifecycle (§30.1: A2A Task / Message / Artifact). Task state matters and
  crosses a system boundary, which is exactly when A2A-style delegation is
  warranted (§30); in-process method calls between roles never appear here.

Naming (§30.2): ``skill_policy`` refers to capability ids in the canonical
registry — never ambiguous nested ``skill`` fields.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from aci.domain.capability.errors import DomainError, ErrorCode

ExecutionMode = Literal["client_applied", "remote_call", "delegated_task", "read_only"]
SideEffectClass = Literal["none", "read_only", "local_write", "remote_write", "external_effect"]
TaskStatus = Literal["submitted", "working", "needs_input", "completed", "failed", "canceled"]
MessageAuthor = Literal["agent", "caller"]


class ProfileBudget(BaseModel):
    """Hard limits a runtime enforces per delegated task (§56.1)."""

    model_config = {"frozen": True}

    max_tokens: int = Field(ge=1)
    max_wall_time_seconds: int = Field(ge=1)


class SkillPolicy(BaseModel):
    """Which capabilities a profile's runtime must / may load (§56.1).

    ``required`` ids are routing integration points: the runtime delegates
    through the same canonical registry as every other consumer (§10 — no
    parallel agent registry). Ids refer to capabilities, never nested skills.
    """

    model_config = {"frozen": True}

    required: list[str] = Field(default_factory=list)
    optional: list[str] = Field(default_factory=list)


class ExecutionPolicy(BaseModel):
    """Explicit execution semantics (§32) — no implicit side effects."""

    model_config = {"frozen": True}

    execution_mode: ExecutionMode = "delegated_task"
    side_effect_class: SideEffectClass
    can_write_repository: bool = False


class AgentProfile(BaseModel):
    """One agent role as data (§56.1). Frozen: profiles are pinned by id+version."""

    model_config = {"frozen": True}

    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    version: str = Field(min_length=1)
    model_profile: str = Field(min_length=1)
    allowed_tools: list[str] = Field(default_factory=list)
    skill_policy: SkillPolicy = Field(default_factory=SkillPolicy)
    budget: ProfileBudget
    execution_policy: ExecutionPolicy
    created_at: datetime

    @model_validator(mode="after")
    def _delegation_only(self) -> "AgentProfile":
        """An agent profile delegates (§32); other modes belong to other kinds."""
        if self.execution_policy.execution_mode != "delegated_task":
            raise ValueError(
                f"agent profile execution_mode must be 'delegated_task', "
                f"got '{self.execution_policy.execution_mode}'"
            )
        return self


# Delegated-task lifecycle (§30.1). Terminal states are immutable.
_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    "submitted": frozenset({"working", "canceled"}),
    "working": frozenset({"needs_input", "completed", "failed", "canceled"}),
    "needs_input": frozenset({"working", "canceled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "canceled": frozenset(),
}


class AgentTask(BaseModel):
    """A delegated unit of work against a ``kind=agent`` capability (§30.1).

    Frozen: state changes go through :func:`advance_task`, which validates the
    lifecycle edges and returns a new instance — never mutate a task in place.
    """

    model_config = {"frozen": True}

    task_id: str = Field(min_length=1)
    profile_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    capability_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,63}$")
    input_text: str = Field(min_length=1)
    status: TaskStatus = "submitted"
    created_at: datetime
    updated_at: datetime


def advance_task(task: AgentTask, status: TaskStatus, *, now: datetime) -> AgentTask:
    """Validated lifecycle transition; returns the next immutable task state."""
    allowed = _TASK_TRANSITIONS[task.status]
    if status not in allowed:
        raise DomainError(
            ErrorCode.TASK_TRANSITION_INVALID,
            f"task {task.task_id} cannot transition {task.status} -> {status}",
        )
    return task.model_copy(update={"status": status, "updated_at": now})


class TaskMessage(BaseModel):
    """A message on a task's communication channel (§30.1 Message)."""

    model_config = {"frozen": True}

    message_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    author: MessageAuthor
    content: str = Field(min_length=1)
    created_at: datetime


class TaskArtifact(BaseModel):
    """A named output artifact of a task (§30.1 Artifact), digest-pinned."""

    model_config = {"frozen": True}

    artifact_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    created_at: datetime
