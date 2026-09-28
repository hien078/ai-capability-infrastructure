"""Canary releases (crawl.md §26-27): status + percentage routing.

Adds capability_releases.canary_percent (nullable int) — the share of
eligible traffic a status='canary' release may route (§27: shadow →
limited canary → progressive rollout → production). The eligibility
engine splits deterministically by hash(request_id, capability_id).

status='canary' is a new ReleaseStatus value — TEXT column, no enum
constraint to alter. Rollback flips the pointer to 'disabled';
graduation (human) flips to 'active'. Artifacts never change (§30).

Revision ID: 0014
Revises: 0013
"""

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("capability_releases", sa.Column("canary_percent", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("capability_releases", "canary_percent")
