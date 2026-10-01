"""Agent run pause checkpoints (HarnessKernel §13.6/§17; migration 0019).

A run that pauses — INTERRUPTED (clarification) or INTERRUPTED_APPROVAL (a
tool call awaiting a human approval) — can be RESUMED from its checkpoint,
in the same process or after a restart. ``agent_run_checkpoints`` holds one
row per pause:

- ``payload``     — the serialized runtime ``Checkpoint``: the full
  StateManager snapshot (version included — CAS continuity, INV-01), the
  pending interrupt (unexecuted tool batch bound to an operation hash, or
  the open question) and the run context (contract, spec, turn ceiling,
  usage so far — budgets are never reset by a resume).
- ``kind`` / ``approval_id`` — what the pause waits for.
- ``consumed_at`` — set atomically by the ONE resume that claims the row
  (``UPDATE … WHERE consumed_at IS NULL``): a checkpoint resumes at most once.

Server-side only — no column is ever returned to a client. CASCADE from the
run row (written at the pause, before the checkpoint).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_run_checkpoints",
        sa.Column("checkpoint_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("approval_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("checkpoint_id"),
    )
    op.create_index(
        "ix_agent_run_checkpoints_run", "agent_run_checkpoints", ["run_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_agent_run_checkpoints_run", table_name="agent_run_checkpoints")
    op.drop_table("agent_run_checkpoints")
