"""License + security assessment tables (Phase 4, plan §§24-25, 41)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "license_assessments",
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("license_identifier", sa.Text(), nullable=False),
        sa.Column("permissions", postgresql.JSONB(), nullable=False),
        sa.Column("assessed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("assessed_by", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assessment_id", "capability_id", "version"),
    )
    op.create_table(
        "security_assessments",
        sa.Column("assessment_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("scan_status", sa.Text(), nullable=False),
        sa.Column("findings", postgresql.JSONB(), nullable=False),
        sa.Column("scanned_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("scanner_version", sa.Text(), nullable=False),
        sa.Column("reviewed_by", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("assessment_id", "capability_id", "version"),
    )


def downgrade() -> None:
    op.drop_table("security_assessments")
    op.drop_table("license_assessments")
