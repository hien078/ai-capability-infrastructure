"""Provenance source records (Phase 3, plan §§23, 41)."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_records",
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("source_type", sa.Text(), nullable=False),
        sa.Column("source_repository", sa.Text(), nullable=True),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("source_url_reference", sa.Text(), nullable=True),
        sa.Column("commit_sha", sa.Text(), nullable=True),
        sa.Column("source_version", sa.Text(), nullable=True),
        sa.Column("raw_snapshot_digest", sa.Text(), nullable=False),
        sa.Column("license_identifier", sa.Text(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingestion_tool_version", sa.Text(), nullable=False),
        sa.Column("local_transformations", postgresql.JSONB(), nullable=False),
        sa.Column("derived_from", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["capability_id"], ["capabilities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("record_id"),
    )
    op.create_index("ix_source_records_capability", "source_records", ["capability_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_source_records_capability", table_name="source_records")
    op.drop_table("source_records")
