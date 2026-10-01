"""Agent run artifacts + trace ref (HarnessKernel §41.1; migration 0017).

``agent_runs.artifacts`` / ``agent_runs.trace_ref`` — RunResult fields that
0016 did not persist, so a read-back after a restart (§29 GET, the
read-through rebuild) returned ``artifacts=[]`` and lost the trace link. The
row is the projection of the WHOLE frozen terminal state (INV-01): both
fields persist with it.

``artifacts`` is JSONB NOT NULL with server default ``'[]'`` — existing rows
(runs recorded before this migration) backfill to the empty list, which is
exactly what they meant: no artifacts were recorded. The ORM declares the
same server default (env.py compares server defaults).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("artifacts", sa.dialects.postgresql.JSONB(), nullable=False, server_default="[]"),
    )
    op.add_column("agent_runs", sa.Column("trace_ref", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "trace_ref")
    op.drop_column("agent_runs", "artifacts")
