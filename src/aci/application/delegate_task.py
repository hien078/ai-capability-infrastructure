"""Delegated-task orchestration (V3 §56.1; §30.1; §32).

ONE runtime, profiles are data: ``ProfileDrivenAgentRuntime`` orchestrates any
agent role — lifecycle, policy checks, persistence — while the pluggable
``AgentExecutor`` does the work under the profile's budget and tool grants.
The platform never gains write authority from a profile (§76); execution
stays with the executor's host.

Flow per delegation:

    validate profile match -> project the submitted state ->
    resolve SkillPolicy.required against ACTIVE production releases
    (eligibility is never bypassed, ADR-009 spirit; a missing release fails
    the task with a caller-visible message) -> working ->
    executor (budget/tool grants handed through) -> record messages/artifacts
    -> terminal state.

All state changes go through the domain's validated edges (``advance_task``);
the repository only projects the resulting immutable states. An executor that
crashes fails its task — never the platform.
"""

import logging
from datetime import datetime
from uuid import uuid4

from aci.application.protocols import (
    AgentExecutor,
    ReleaseRepository,
    TaskRepository,
)
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutorResult,
    SkillGrant,
    TaskMessage,
    advance_task,
)
from aci.domain.capability.errors import DomainError, ErrorCode

log = logging.getLogger(__name__)


class ProfileDrivenAgentRuntime:
    """Implements the V3 agent runtime over protocols (§56.1)."""

    def __init__(
        self,
        tasks: TaskRepository,
        releases: ReleaseRepository,
        executor: AgentExecutor,
    ) -> None:
        self._tasks = tasks
        self._releases = releases
        self._executor = executor

    def delegate(self, task: AgentTask, profile: AgentProfile, *, now: datetime) -> AgentTask:
        """Run one delegated task to a terminal state and persist every step."""
        if task.profile_id != profile.profile_id:
            raise DomainError(
                ErrorCode.POLICY_DENIED,
                f"task {task.task_id} targets profile '{task.profile_id}', "
                f"given '{profile.profile_id}'",
            )
        self._tasks.put_task(task)

        grants, missing = self._resolve_grants(profile)
        if missing:
            return self._fail(
                task,
                now,
                detail=f"required capabilities have no active production release: {missing}",
            )

        working = advance_task(task, "working", now=now)
        self._tasks.put_task(working)
        try:
            result = self._executor.execute(working, profile, grants, now=now)
        except Exception as exc:  # executor failure fails the task, never the platform
            # Task history is served to A2A callers: the type only on the wire,
            # the real text in the server log (§61).
            log.warning("executor failed for task %s: %s", task.task_id, exc)
            result = ExecutorResult(
                status="failed", detail=f"executor error ({type(exc).__name__})"
            )
        violation = self._validate_output(working, result)
        if violation is not None:
            failed = advance_task(working, "failed", now=now)
            self._tasks.put_task(failed)
            self._tasks.put_message(
                TaskMessage(
                    message_id=f"msg-{uuid4().hex[:12]}",
                    task_id=working.task_id,
                    author="agent",
                    content=violation,
                    created_at=now,
                )
            )
            raise DomainError(ErrorCode.EXECUTOR_CONTRACT_VIOLATION, violation)
        self._record(working, result)

        terminal_status = "completed" if result.status == "completed" else "failed"
        terminal = advance_task(working, terminal_status, now=now)  # type: ignore[arg-type]
        self._tasks.put_task(terminal)
        if result.status == "failed" and result.detail is not None and not result.messages:
            self._tasks.put_message(
                TaskMessage(
                    message_id=f"msg-{uuid4().hex[:12]}",
                    task_id=working.task_id,
                    author="agent",
                    content=result.detail,
                    created_at=now,
                )
            )
        return terminal

    def _resolve_grants(self, profile: AgentProfile) -> tuple[list[SkillGrant], list[str]]:
        """Required skills become grants only with an active production release."""
        grants: list[SkillGrant] = []
        missing: list[str] = []
        for capability_id in profile.skill_policy.required:
            release = self._releases.get_release(capability_id, "production")
            if release is None or release.status != "active":
                missing.append(capability_id)
                continue
            grants.append(SkillGrant(capability_id=capability_id, version=release.version))
        return grants, missing

    def _fail(self, task: AgentTask, now: datetime, *, detail: str) -> AgentTask:
        """Policy-unsatisfiable delegation: straight to failed, message recorded."""
        failed = advance_task(advance_task(task, "working", now=now), "failed", now=now)
        self._tasks.put_task(failed)
        self._tasks.put_message(
            TaskMessage(
                message_id=f"msg-{uuid4().hex[:12]}",
                task_id=task.task_id,
                author="agent",
                content=detail,
                created_at=now,
            )
        )
        return failed

    def _validate_output(self, task: AgentTask, result: ExecutorResult) -> str | None:
        """Executor output must belong to THIS task; returns the violation or None."""
        for message in result.messages:
            if message.task_id != task.task_id:
                return (
                    f"executor returned message {message.message_id} for task "
                    f"{message.task_id}, expected {task.task_id}"
                )
        for artifact in result.artifacts:
            if artifact.task_id != task.task_id:
                return (
                    f"executor returned artifact {artifact.artifact_id} for task "
                    f"{artifact.task_id}, expected {task.task_id}"
                )
        return None

    def _record(self, task: AgentTask, result: ExecutorResult) -> None:
        """Persist validated executor output (append-only)."""
        for message in result.messages:
            self._tasks.put_message(message)
        for artifact in result.artifacts:
            self._tasks.put_artifact(artifact)


class UnconfiguredExecutor:
    """Default executor when no deployment intelligence is plugged in (§50).

    Fails every task with a clear, caller-visible reason: the A2A wire surface
    stays real and testable, and plugging a model client (or human operator)
    into the runtime replaces exactly this object.
    """

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[SkillGrant],
        *,
        now: datetime,
    ) -> ExecutorResult:
        return ExecutorResult(
            status="failed",
            detail="no agent executor configured — plug one into the runtime wiring",
        )
