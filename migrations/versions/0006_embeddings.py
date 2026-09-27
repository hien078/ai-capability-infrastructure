"""Trusted routing documents + pgvector vectors (Phase 6, plan §§16, 40.2, 41)."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMS = 256


def upgrade() -> None:
    op.create_table(
        "embedding_documents",
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("capability_id", sa.Text(), nullable=False),
        sa.Column("version", sa.Text(), nullable=False),
        sa.Column("doc_type", sa.Text(), nullable=False),
        sa.Column("digest", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint("capability_id", "version", "doc_type"),
    )
    op.create_table(
        "embedding_vectors",
        sa.Column("document_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("dims", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(EMBEDDING_DIMS), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["embedding_documents.document_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("document_id", "model_id"),
    )


def downgrade() -> None:
    op.drop_table("embedding_vectors")
    op.drop_table("embedding_documents")
