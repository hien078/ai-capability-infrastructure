"""Per-principal ownership of delegated A2A tasks.

Adds ``agent_tasks.owner`` (TEXT NOT NULL, server default 'anonymous') —
the authenticated A2A principal that created the task. The gateway treats
a task owned by another principal exactly like an unknown id
(TASK_NOT_FOUND), closing the any-caller-who-knows-the-id read/cancel gap.

Existing rows predate ownership and were created in whatever auth mode was
running; they become 'anonymous' — the principal of the unauthenticated
(no tokens configured) mode. No authenticated caller resolves to
'anonymous', so once tokens are configured legacy tasks are unreachable
over the wire rather than readable by everyone (fail-closed).

The server default stays on the column (the ORM declares the same) so
in-process writers that predate the field keep working.

Revision ID: 0015
Revises: 0014
"""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_tasks",
        sa.Column("owner", sa.Text(), nullable=False, server_default="anonymous"),
    )


def downgrade() -> None:
    op.drop_column("agent_tasks", "owner")
