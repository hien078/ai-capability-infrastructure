"""pgvector table models (plan §§40.2, 41).

Documents are immutable per (capability_id, version, doc_type); vectors are
per (document_id, model_id) so multiple embedder models can coexist. Uses the
same DeclarativeBase as the Postgres registry so alembic keeps one metadata.
"""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from aci.adapters.outbound.postgres.base import Base

EMBEDDING_DIMS = 384


class EmbeddingDocumentRow(Base):
    __tablename__ = "embedding_documents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["capability_id", "version"],
            ["capability_versions.capability_id", "capability_versions.version"],
            ondelete="CASCADE",
        ),
        UniqueConstraint("capability_id", "version", "doc_type"),
    )

    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    capability_id: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str] = mapped_column(Text, nullable=False)
    doc_type: Mapped[str] = mapped_column(Text, nullable=False, default="routing")
    digest: Mapped[str] = mapped_column(Text, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class EmbeddingVectorRow(Base):
    __tablename__ = "embedding_vectors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id"], ["embedding_documents.document_id"], ondelete="CASCADE"
        ),
    )

    document_id: Mapped[str] = mapped_column(Text, primary_key=True)
    model_id: Mapped[str] = mapped_column(Text, primary_key=True)
    dims: Mapped[int] = mapped_column(nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIMS), nullable=False)
