"""Embedding column 256 -> 384 dims (V2 semantic embedder, plan §55).

The V1 hashing baseline used 256 dims; the V2 semantic embedder
(BAAI/bge-small-en-v1.5) produces 384. pgvector columns carry one fixed
dimension, so the column moves to 384 and every embedder emits 384
(HashingEmbedder's default bumps with it; its model_id becomes
``hashing-384-v2``).

Existing vectors are dropped, not converted: they are re-derivable cache
(§46 — cache only immutable content keyed by version/digest; the
retriever re-indexes lazily per (capability_id, version, model_id)).
The trusted routing documents themselves are model-independent and stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMBEDDING_DIMS = 384


def upgrade() -> None:
    # 256-dim vectors are incompatible with the new column; they are
    # re-derivable cache, so drop rather than convert.
    op.execute("DELETE FROM embedding_vectors")
    op.alter_column("embedding_vectors", "embedding", type_=Vector(EMBEDDING_DIMS))


def downgrade() -> None:
    op.execute("DELETE FROM embedding_vectors")
    op.alter_column("embedding_vectors", "embedding", type_=Vector(256))
