"""Capability relations (Phase 8, plan §18; V1: requires/conflicts_with/checks)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capability_relations",
        sa.Column("relation_id", sa.Text(), nullable=False),
        sa.Column("source_capability_id", sa.Text(), nullable=False),
        sa.Column("source_version_constraint", sa.Text(), nullable=True),
        sa.Column("target_capability_id", sa.Text(), nullable=False),
        sa.Column("target_version_constraint", sa.Text(), nullable=True),
        sa.Column("relation", sa.Text(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(["source_capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("relation_id"),
    )
    op.create_index(
        "ix_capability_relations_source", "capability_relations", ["source_capability_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_capability_relations_source", table_name="capability_relations")
    op.drop_table("capability_relations")
