"""Canonical registry tables (Phase 2, plan §41).

facets stay JSONB+GIN on versions; normalized facet_nodes tables arrive in
Phase 5. A trigger rejects UPDATE on capability_versions: published versions
are immutable, promotion only moves release pointers.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("owner_scope", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "capability_versions",
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("content_digest", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("facets", postgresql.JSONB(), nullable=False),
        sa.Column("spec", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id"], ["capabilities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("capability_id", "version"),
    )
    op.create_index(
        "ix_capability_versions_facets",
        "capability_versions",
        ["facets"],
        postgresql_using="gin",
    )
    op.create_table(
        "capability_releases",
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.Text(), nullable=True),
        sa.Column("policy_snapshot_id", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("capability_id", "channel"),
    )
    op.create_table(
        "capability_bindings",
        sa.Column("binding_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("binding_type", sa.Text(), nullable=False),
        sa.Column("visibility_scope", sa.Text(), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("binding_id"),
    )
    op.create_table(
        "capability_artifacts",
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("package_digest", sa.Text(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("capability_id", "version"),
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION aci_reject_version_update()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION
                'capability_versions is immutable (capability_id=%, version=%)',
                OLD.capability_id, OLD.version;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_capability_versions_no_update
        BEFORE UPDATE ON capability_versions
        FOR EACH ROW EXECUTE FUNCTION aci_reject_version_update()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_capability_versions_no_update ON capability_versions")
    op.execute("DROP FUNCTION IF EXISTS aci_reject_version_update()")
    op.drop_table("capability_artifacts")
    op.drop_table("capability_bindings")
    op.drop_table("capability_releases")
    op.drop_index("ix_capability_versions_facets", table_name="capability_versions")
    op.drop_table("capability_versions")
    op.drop_table("capabilities")
