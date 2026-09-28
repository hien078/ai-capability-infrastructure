"""Agent task persistence (V3, plan §56; §30.1 Task/Message/Artifact lifecycle).

``agent_tasks`` — delegated units of work against ``kind=agent`` capabilities;
state advances only through the validated domain edges (``advance_task``),
so the row is a projection of immutable task states, latest wins.
``task_messages`` / ``task_artifacts`` — the task's communication channel and
digest-pinned outputs; both CASCADE from their task.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_tasks",
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("task_id"),
    )
    op.create_index("ix_agent_tasks_capability", "agent_tasks", ["capability_id"])
    op.create_table(
        "task_messages",
        sa.Column("message_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("author", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("message_id"),
    )
    op.create_index("ix_task_messages_task", "task_messages", ["task_id"])
    op.create_table(
        "task_artifacts",
        sa.Column("artifact_id", sa.Text(), nullable=False),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["agent_tasks.task_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("artifact_id"),
    )
    op.create_index("ix_task_artifacts_task", "task_artifacts", ["task_id"])


def downgrade() -> None:
    op.drop_table("task_artifacts")
    op.drop_table("task_messages")
    op.drop_table("agent_tasks")
