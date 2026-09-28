"""Ingestion status split from release channels (§22 lifecycle refactor).

The `raw` release channel conflated two state machines: ingestion trust
(quarantined/rejected/normalized/accepted) and release delivery
(staging/production). This migration:

1. adds `source_records.ingestion_status` (default 'quarantined') — the
   ingestion state machine lives on the provenance record, not on a
   release pointer;
2. migrates existing data: every source record that has a `production`
   release for its version becomes 'accepted' (it passed the gates when
   it was promoted); everything else stays 'quarantined';
3. deletes the `raw` release rows — raw is no longer a channel. Existing
   `raw` pointers were quarantine markers; the equivalent state is now
   `ingestion_status='quarantined'` on the source record.

`candidate`/`canonical` channels were never populated in this database
(the promotion service only ever wrote raw/production). The domain
ReleaseChannel type is now Literal['staging', 'production'].

Revision ID: 0013_ingestion_status
Revises: 0012_agent_tasks
"""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "source_records",
        sa.Column("ingestion_status", sa.Text(), nullable=False, server_default="quarantined"),
    )
    # Records whose version reached production passed the gates → accepted.
    op.execute(
        """
        UPDATE source_records sr
        SET ingestion_status = 'accepted'
        WHERE EXISTS (
            SELECT 1 FROM capability_releases r
            WHERE r.capability_id = sr.capability_id
              AND r.version = sr.version
              AND r.channel = 'production'
        )
        """
    )
    # Raw is an ingestion state, not a release channel: drop the pointers.
    op.execute("DELETE FROM capability_releases WHERE channel = 'raw'")


def downgrade() -> None:
    # Restore the legacy raw pointers for still-quarantined records.
    op.execute(
        """
        INSERT INTO capability_releases
            (capability_id, channel, version, status, promoted_at, approved_by,
             policy_snapshot_id)
        SELECT capability_id, 'raw', version, 'active', NULL, NULL, NULL
        FROM source_records WHERE ingestion_status = 'quarantined'
        """
    )
    op.drop_column("source_records", "ingestion_status")
