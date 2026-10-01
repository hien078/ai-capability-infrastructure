"""Agent run persistence (HarnessKernel §41.1 — the §36-equivalent for the
agent-run plane; migration 0016).

``agent_runs`` — one row per run, written ONCE at terminal state from the
frozen RunResult (a projection, never a source of truth for a live run —
StateManager is the only mutable authority, INV-01). ``agent_run_events`` —
the EventBus history flattened into ordered rows (INV-15 made real).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("parent_run_id", sa.Text(), nullable=True),
        sa.Column("profile_id", sa.Text(), nullable=False),
        sa.Column("objective", sa.Text(), nullable=False),
        sa.Column("workspace", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("stop_reason", sa.Text(), nullable=True),
        sa.Column("detail_code", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("evidence", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("usage", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("spec", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("verification_command", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("run_id"),
    )
    op.create_index("ix_agent_runs_created", "agent_runs", ["created_at"])
    op.create_table(
        "agent_run_events",
        sa.Column("event_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("turn_id", sa.Text(), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["agent_runs.run_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_index("ix_agent_run_events_run", "agent_run_events", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_run_events_run", table_name="agent_run_events")
    op.drop_table("agent_run_events")
    op.drop_index("ix_agent_runs_created", table_name="agent_runs")
    op.drop_table("agent_runs")
