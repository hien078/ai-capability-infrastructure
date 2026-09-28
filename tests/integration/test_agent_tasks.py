"""Delegated-task persistence (V3 §56, §30.1) against the live DB.

Task rows project immutable domain states: transitions happen in the domain
(advance_task) and the resulting state is upserted; messages/artifacts are
append-only. Unique ids everywhere — the live DB is shared (§34 hygiene).
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.tasks import SqlAlchemyTaskRepository
from aci.domain.agent.models import AgentTask, TaskArtifact, TaskMessage, advance_task

NOW = datetime(2026, 9, 28, tzinfo=UTC)
LATER = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)


def uid(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex[:10]}"


def make_task() -> AgentTask:
    return AgentTask(
        task_id=uid("task"),
        profile_id="reviewer",
        capability_id=uid("agent-cap"),
        input_text="Review the attached diff for correctness.",
        created_at=NOW,
        updated_at=NOW,
    )


@pytest.fixture()
def tasks(sessions: sessionmaker[Session]) -> SqlAlchemyTaskRepository:
    return SqlAlchemyTaskRepository(sessions)


def test_task_roundtrip(tasks: SqlAlchemyTaskRepository) -> None:
    task = make_task()
    tasks.put_task(task)
    loaded = tasks.get_task(task.task_id)
    assert loaded == task


def test_lifecycle_upsert_projects_domain_states(
    tasks: SqlAlchemyTaskRepository,
) -> None:
    task = make_task()
    tasks.put_task(task)
    working = advance_task(task, "working", now=LATER)
    tasks.put_task(working)
    completed = advance_task(working, "completed", now=LATER)
    tasks.put_task(completed)

    assert tasks.get_task(task.task_id) == completed
    assert tasks.get_task(task.task_id).status == "completed"


def test_get_missing_task_is_none(tasks: SqlAlchemyTaskRepository) -> None:
    assert tasks.get_task(uid("nope")) is None


def test_messages_append_only_and_ordered(tasks: SqlAlchemyTaskRepository) -> None:
    task = make_task()
    tasks.put_task(task)
    first = TaskMessage(
        message_id=uid("msg"),
        task_id=task.task_id,
        author="caller",
        content="Please focus on the parser.",
        created_at=NOW,
    )
    second = TaskMessage(
        message_id=uid("msg"),
        task_id=task.task_id,
        author="agent",
        content="Found a boundary bug in the parser.",
        created_at=LATER,
    )
    tasks.put_message(first)
    tasks.put_message(second)

    listed = tasks.list_messages(task.task_id)
    assert [m.message_id for m in listed] == [first.message_id, second.message_id]
    assert listed[0].author == "caller" and listed[1].author == "agent"


def test_artifacts_digest_pinned_and_ordered(tasks: SqlAlchemyTaskRepository) -> None:
    task = make_task()
    tasks.put_task(task)
    artifact = TaskArtifact(
        artifact_id=uid("art"),
        task_id=task.task_id,
        name="review.md",
        digest="sha256:" + "cd" * 32,
        created_at=NOW,
    )
    tasks.put_artifact(artifact)

    listed = tasks.list_artifacts(task.task_id)
    assert listed == [artifact]
    assert listed[0].digest.startswith("sha256:")


def test_messages_isolated_per_task(tasks: SqlAlchemyTaskRepository) -> None:
    """No cross-task leakage: messages of one task never appear for another."""
    a, b = make_task(), make_task()
    tasks.put_task(a)
    tasks.put_task(b)
    tasks.put_message(
        TaskMessage(
            message_id=uid("msg"),
            task_id=a.task_id,
            author="agent",
            content="only for a",
            created_at=NOW,
        )
    )
    assert tasks.list_messages(b.task_id) == []
    assert len(tasks.list_messages(a.task_id)) == 1
