"""Delegated-task persistence (V3 §56, §30.1) against the live DB.

Task rows project immutable domain states: transitions happen in the domain
(advance_task) and the resulting state is upserted; messages/artifacts are
append-only. Unique ids everywhere — the live DB is shared (§34 hygiene).

Migration 0015 (task owner) is exercised on a throwaway ``aci_mig_<hex>``
database — never the shared ``aci`` and never ``aci_bench``.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.tasks import SqlAlchemyTaskRepository
from aci.domain.agent.models import AgentTask, TaskArtifact, TaskMessage, advance_task

pytestmark = pytest.mark.integration

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


def test_owner_roundtrips_and_survives_upsert(tasks: SqlAlchemyTaskRepository) -> None:
    task = make_task().model_copy(update={"owner": "alice"})
    tasks.put_task(task)
    assert tasks.get_task(task.task_id) == task
    canceled = advance_task(task, "canceled", now=LATER)
    tasks.put_task(canceled)
    loaded = tasks.get_task(task.task_id)
    assert loaded is not None and loaded.owner == "alice" and loaded.status == "canceled"


def test_default_owner_is_anonymous(tasks: SqlAlchemyTaskRepository) -> None:
    task = make_task()
    tasks.put_task(task)
    loaded = tasks.get_task(task.task_id)
    assert loaded is not None and loaded.owner == "anonymous"


# -- migration 0015 on a scratch database ------------------------------------

DB_URL = os.environ.get("ACI_DATABASE_URL", "postgresql+psycopg://aci:aci@localhost:5432/aci")
REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture()
def scratch_db_url(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """A fresh empty ``aci_mig_<hex>`` database; ACI_DATABASE_URL points at it."""
    name = f"aci_mig_{uuid4().hex[:12]}"
    base = make_url(DB_URL)
    admin = create_engine(
        base.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        connect_args={"connect_timeout": 2},
    )
    try:
        with admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    except Exception as exc:  # no CREATEDB privilege etc.
        admin.dispose()
        pytest.skip(f"cannot create scratch database: {exc}")
    url = base.set(database=name).render_as_string(hide_password=False)
    monkeypatch.setenv("ACI_DATABASE_URL", url)
    try:
        yield url
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def _alembic_config() -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return cfg


def test_migration_0015_backfills_existing_tasks_as_anonymous(scratch_db_url: str) -> None:
    cfg = _alembic_config()
    command.upgrade(cfg, "0014")
    eng = create_engine(scratch_db_url)
    try:
        with eng.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO agent_tasks (task_id, profile_id, capability_id, input_text, "
                    "status, created_at, updated_at) VALUES "
                    "('task-legacy', 'reviewer', 'agent-cap', 'x', 'completed', now(), now())"
                )
            )

        command.upgrade(cfg, "head")
        with eng.connect() as conn:
            owner = conn.execute(
                text("SELECT owner FROM agent_tasks WHERE task_id = 'task-legacy'")
            ).scalar_one()
            nullable = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'agent_tasks' AND column_name = 'owner'"
                )
            ).scalar_one()
        assert owner == "anonymous"
        assert nullable == "NO"
        command.check(cfg)  # ORM declares the same server_default: no drift

        command.downgrade(cfg, "0014")
        with eng.connect() as conn:
            columns = conn.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'agent_tasks'"
                )
            ).scalars()
            assert "owner" not in set(columns)
            assert conn.execute(text("SELECT count(*) FROM agent_tasks")).scalar_one() == 1
    finally:
        eng.dispose()
