"""Agent run revision state (HarnessKernel §29A/§41.1; migration 0018).

A §29A revision builds FROM the previous attempt: its contract, its client
options and its working copy. 0016/0017 persisted only the terminal
projection, so after a restart ``GET`` returned the run while ``revise``
answered "unknown run". Three nullable columns close that gap:

- ``contract``    — ``SubtaskContract.model_dump(mode="json")``.
- ``run_options`` — the client-chosen RunOptions (workspace, verification
  command, write scopes, command prefixes, max turns). Requests, never
  grants: a revision re-derives every grant from the CURRENT server ceiling
  (INV-02).
- ``run_dir``     — the absolute path of the run's working copy. Server-side
  only; never returned to a client.

All three are NULL on pre-0018 rows (no backfill is possible — the data was
never recorded): such runs stay readable but are not revisable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_runs", sa.Column("contract", sa.dialects.postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "agent_runs", sa.Column("run_options", sa.dialects.postgresql.JSONB(), nullable=True)
    )
    op.add_column("agent_runs", sa.Column("run_dir", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "run_dir")
    op.drop_column("agent_runs", "run_options")
    op.drop_column("agent_runs", "contract")
