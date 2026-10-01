"""Facet/compatibility columns + policy snapshots (Phase 5, plan §§41, 46)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("capabilities", sa.Column("owner_scope_id", sa.Text(), nullable=True))
    op.add_column(
        "capability_versions", sa.Column("compatibility", postgresql.JSONB(), nullable=True)
    )
    op.create_table(
        "policy_snapshots",
        sa.Column("snapshot_id", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )


def downgrade() -> None:
    op.drop_table("policy_snapshots")
    op.drop_column("capability_versions", "compatibility")
    op.drop_column("capabilities", "owner_scope_id")
