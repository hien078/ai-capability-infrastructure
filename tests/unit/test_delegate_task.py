"""ProfileDrivenAgentRuntime (V3 §56.1) — orchestration over fakes, no DB.

Pins the contract: lifecycle edges, policy-unsatisfiable failure, executor
crash containment, output validation, budget/tool grants handed through.
"""

from datetime import UTC, datetime
from typing import cast

import pytest

from aci.application.delegate_task import ProfileDrivenAgentRuntime
from aci.application.protocols import AgentExecutor, ReleaseRepository, TaskRepository
from aci.domain.agent.models import (
    AgentProfile,
    AgentTask,
    ExecutorResult,
    SkillGrant,
    TaskArtifact,
    TaskMessage,
)
from aci.domain.capability.errors import DomainError, ErrorCode
from aci.domain.capability.models import CapabilityRelease

NOW = datetime(2026, 9, 28, tzinfo=UTC)
DIGEST = "sha256:" + "ab" * 32


class FakeTasks:
    """In-memory TaskRepository double."""

    def __init__(self) -> None:
        self.tasks: dict[str, AgentTask] = {}
        self.messages: list[TaskMessage] = []
        self.artifacts: list[TaskArtifact] = []

    def put_task(self, task: AgentTask) -> AgentTask:
        self.tasks[task.task_id] = task
        return task

    def get_task(self, task_id: str) -> AgentTask | None:
        return self.tasks.get(task_id)

    def put_message(self, message: TaskMessage) -> TaskMessage:
        self.messages.append(message)
        return message

    def list_messages(self, task_id: str) -> list[TaskMessage]:
        return [m for m in self.messages if m.task_id == task_id]

    def put_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        self.artifacts.append(artifact)
        return artifact

    def list_artifacts(self, task_id: str) -> list[TaskArtifact]:
        return [a for a in self.artifacts if a.task_id == task_id]


class FakeReleases:
    """ReleaseRepository double: only 'code-review' has an active production release."""

    def __init__(self, active: set[str]) -> None:
        self.active = active
        self.set: list[CapabilityRelease] = []

    def set_release(self, release: CapabilityRelease) -> CapabilityRelease:
        self.set.append(release)
        return release

    def get_release(self, capability_id: str, channel: str) -> CapabilityRelease | None:
        if capability_id in self.active and channel == "production":
            return CapabilityRelease(
                capability_id=capability_id, version="1.0.0", channel="production"
            )
        return None

    def list_releases(self, capability_id: str) -> list[CapabilityRelease]:
        return []

    def list_channel(self, channel: str, *, status: str | None = None) -> list[CapabilityRelease]:
        return []


class FakeExecutor:
    """AgentExecutor double: records what it was handed, returns scripted results."""

    def __init__(self, result: ExecutorResult | Exception) -> None:
        self.result = result
        self.calls: list[tuple[AgentTask, AgentProfile, list[SkillGrant]]] = []

    def execute(
        self,
        task: AgentTask,
        profile: AgentProfile,
        skills: list[SkillGrant],
        *,
        now: datetime,
    ) -> ExecutorResult:
        self.calls.append((task, profile, list(skills)))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _profile(required: list[str] | None = None) -> AgentProfile:
    from aci.domain.agent.models import ExecutionPolicy, ProfileBudget, SkillPolicy

    return AgentProfile(
        profile_id="reviewer",
        version="1.0.0",
        model_profile="reasoning-medium",
        allowed_tools=["repository.read"],
        skill_policy=SkillPolicy(required=required or ["code-review"]),
        budget=ProfileBudget(max_tokens=1000, max_wall_time_seconds=60),
        execution_policy=ExecutionPolicy(side_effect_class="read_only"),
        created_at=NOW,
    )


def _task(profile_id: str = "reviewer") -> AgentTask:
    return AgentTask(
        task_id="task-1",
        profile_id=profile_id,
        capability_id="code-reviewer-agent",
        input_text="Review PR #42.",
        created_at=NOW,
        updated_at=NOW,
    )


def _runtime(
    executor: FakeExecutor, active: set[str] | None = None
) -> tuple[ProfileDrivenAgentRuntime, FakeTasks]:
    tasks = FakeTasks()
    releases = FakeReleases({"code-review"} if active is None else active)
    runtime = ProfileDrivenAgentRuntime(
        tasks=cast(TaskRepository, tasks),
        releases=cast(ReleaseRepository, releases),
        executor=cast(AgentExecutor, executor),
    )
    return runtime, tasks


def test_happy_path_completes_with_messages_and_artifacts() -> None:
    artifact = TaskArtifact(
        artifact_id="art-1",
        task_id="task-1",
        name="review.md",
        digest=DIGEST,
        created_at=NOW,
    )
    message = TaskMessage(
        message_id="msg-1",
        task_id="task-1",
        author="agent",
        content="Found a boundary bug.",
        created_at=NOW,
    )
    executor = FakeExecutor(
        ExecutorResult(status="completed", messages=[message], artifacts=[artifact])
    )
    runtime, tasks = _runtime(executor)

    final = runtime.delegate(_task(), _profile(), now=NOW)

    assert final.status == "completed"
    assert tasks.get_task("task-1") == final
    assert tasks.list_messages("task-1") == [message]
    assert tasks.list_artifacts("task-1") == [artifact]


def test_required_skills_arrive_as_grants_with_pinned_versions() -> None:
    executor = FakeExecutor(ExecutorResult(status="completed"))
    runtime, _ = _runtime(executor)

    runtime.delegate(_task(), _profile(required=["code-review"]), now=NOW)

    task, profile, skills = executor.calls[0]
    assert task.status == "working"  # executor sees the working state
    assert profile.profile_id == "reviewer"
    assert skills == [SkillGrant(capability_id="code-review", version="1.0.0")]


def test_missing_required_release_fails_task_with_visible_reason() -> None:
    executor = FakeExecutor(ExecutorResult(status="completed"))
    runtime, tasks = _runtime(executor, active=set())  # nothing active

    final = runtime.delegate(_task(), _profile(), now=NOW)

    assert final.status == "failed"
    assert executor.calls == []  # never started work
    reasons = [m.content for m in tasks.list_messages("task-1")]
    assert any("no active production release" in r for r in reasons)


def test_executor_crash_fails_task_not_the_platform() -> None:
    executor = FakeExecutor(RuntimeError("model provider down at /srv/secret"))
    runtime, tasks = _runtime(executor)

    final = runtime.delegate(_task(), _profile(), now=NOW)

    assert final.status == "failed"
    history = [m.content for m in tasks.list_messages("task-1")]
    assert any("executor error (RuntimeError)" in m for m in history)
    # Task history is served over A2A GetTask: the exception text stays server-side.
    assert not any("/srv/secret" in m for m in history)


def test_executor_failed_result_records_detail() -> None:
    executor = FakeExecutor(ExecutorResult(status="failed", detail="tests failed"))
    runtime, tasks = _runtime(executor)

    final = runtime.delegate(_task(), _profile(), now=NOW)

    assert final.status == "failed"
    assert any("tests failed" in m.content for m in tasks.list_messages("task-1"))


def test_profile_mismatch_is_policy_denied() -> None:
    executor = FakeExecutor(ExecutorResult(status="completed"))
    runtime, _ = _runtime(executor)

    with pytest.raises(DomainError) as exc:
        runtime.delegate(_task(profile_id="coder"), _profile(), now=NOW)
    assert exc.value.code == ErrorCode.POLICY_DENIED


def test_cross_task_output_is_contract_violation_and_task_fails() -> None:
    foreign = TaskMessage(
        message_id="msg-x",
        task_id="OTHER-task",
        author="agent",
        content="not yours",
        created_at=NOW,
    )
    executor = FakeExecutor(ExecutorResult(status="completed", messages=[foreign]))
    runtime, tasks = _runtime(executor)

    with pytest.raises(DomainError) as exc:
        runtime.delegate(_task(), _profile(), now=NOW)
    assert exc.value.code == ErrorCode.EXECUTOR_CONTRACT_VIOLATION
    # the task still reached a terminal state — never stuck in working
    assert tasks.get_task("task-1").status == "failed"
    # the caller sees WHY; the foreign output itself was NOT persisted
    violation_messages = tasks.list_messages("task-1")
    assert len(violation_messages) == 1
    assert "expected task-1" in violation_messages[0].content
