"""Delegated-task persistence (V3 §56; §30.1 Task/Message/Artifact).

Task rows project immutable domain states: transitions are validated in the
domain (``advance_task``) and the resulting state is upserted here — this
repository never mutates a lifecycle on its own. Messages and artifacts are
append-only per task.
"""

from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.postgres.orm import AgentTaskRow, TaskArtifactRow, TaskMessageRow
from aci.domain.agent.models import AgentTask, TaskArtifact, TaskMessage


class SqlAlchemyTaskRepository:
    """Implements the TaskRepository protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_task(self, task: AgentTask) -> AgentTask:
        with self._sessions() as session, session.begin():
            session.merge(
                AgentTaskRow(
                    task_id=task.task_id,
                    profile_id=task.profile_id,
                    capability_id=task.capability_id,
                    input_text=task.input_text,
                    status=task.status,
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                    owner=task.owner,
                )
            )
        return task

    def get_task(self, task_id: str) -> AgentTask | None:
        with self._sessions() as session:
            row = session.get(AgentTaskRow, task_id)
            if row is None:
                return None
            return AgentTask.model_validate(
                {
                    "task_id": row.task_id,
                    "profile_id": row.profile_id,
                    "capability_id": row.capability_id,
                    "input_text": row.input_text,
                    "status": row.status,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                    "owner": row.owner,
                }
            )

    def put_message(self, message: TaskMessage) -> TaskMessage:
        with self._sessions() as session, session.begin():
            session.add(
                TaskMessageRow(
                    message_id=message.message_id,
                    task_id=message.task_id,
                    author=message.author,
                    content=message.content,
                    created_at=message.created_at,
                )
            )
        return message

    def list_messages(self, task_id: str) -> list[TaskMessage]:
        with self._sessions() as session:
            rows = (
                session.query(TaskMessageRow)
                .filter_by(task_id=task_id)
                .order_by(TaskMessageRow.created_at, TaskMessageRow.message_id)
                .all()
            )
            return [
                TaskMessage.model_validate(
                    {
                        "message_id": r.message_id,
                        "task_id": r.task_id,
                        "author": r.author,
                        "content": r.content,
                        "created_at": r.created_at,
                    }
                )
                for r in rows
            ]

    def put_artifact(self, artifact: TaskArtifact) -> TaskArtifact:
        with self._sessions() as session, session.begin():
            session.add(
                TaskArtifactRow(
                    artifact_id=artifact.artifact_id,
                    task_id=artifact.task_id,
                    name=artifact.name,
                    digest=artifact.digest,
                    created_at=artifact.created_at,
                )
            )
        return artifact

    def list_artifacts(self, task_id: str) -> list[TaskArtifact]:
        with self._sessions() as session:
            rows = (
                session.query(TaskArtifactRow)
                .filter_by(task_id=task_id)
                .order_by(TaskArtifactRow.created_at, TaskArtifactRow.artifact_id)
                .all()
            )
            return [
                TaskArtifact.model_validate(
                    {
                        "artifact_id": r.artifact_id,
                        "task_id": r.task_id,
                        "name": r.name,
                        "digest": r.digest,
                        "created_at": r.created_at,
                    }
                )
                for r in rows
            ]
