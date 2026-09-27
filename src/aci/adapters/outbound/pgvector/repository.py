"""pgvector-backed embedding store (plan §§16, 40.2, 46)."""

from sqlalchemy import desc, tuple_
from sqlalchemy.orm import Session, sessionmaker

from aci.adapters.outbound.pgvector.orm import EmbeddingDocumentRow, EmbeddingVectorRow
from aci.domain.routing.models import RetrievedDocument, TrustedRoutingDocument


def _document_of(row: EmbeddingDocumentRow, model_id: str) -> TrustedRoutingDocument:
    return TrustedRoutingDocument(
        document_id=row.document_id,
        capability_id=row.capability_id,
        version=row.version,
        digest=row.digest,
        doc_type=row.doc_type,  # type: ignore[arg-type]
        model_id=model_id,
        text=row.text,
        created_at=row.created_at,
    )


class SqlAlchemyEmbeddingRepository:
    """Implements the EmbeddingRepository protocol."""

    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    def put_document(self, document: TrustedRoutingDocument, vector: list[float]) -> None:
        with self._sessions() as session, session.begin():
            row = (
                session.query(EmbeddingDocumentRow)
                .filter_by(
                    capability_id=document.capability_id,
                    version=document.version,
                    doc_type=document.doc_type,
                )
                .one_or_none()
            )
            if row is None:
                row = EmbeddingDocumentRow(
                    document_id=document.document_id,
                    capability_id=document.capability_id,
                    version=document.version,
                    doc_type=document.doc_type,
                    digest=document.digest,
                    text=document.text,
                    created_at=document.created_at,
                )
                session.add(row)
            else:
                # Same natural key: refresh text/digest (idempotent re-index).
                row.digest = document.digest
                row.text = document.text
                row.created_at = document.created_at
            document_id = row.document_id
            vector_row = (
                session.query(EmbeddingVectorRow)
                .filter_by(document_id=document_id, model_id=document.model_id)
                .one_or_none()
            )
            if vector_row is None:
                session.add(
                    EmbeddingVectorRow(
                        document_id=document_id,
                        model_id=document.model_id,
                        dims=len(vector),
                        embedding=vector,
                    )
                )
            else:
                vector_row.dims = len(vector)
                vector_row.embedding = vector

    def get_indexed_document(
        self, capability_id: str, version: str, model_id: str
    ) -> TrustedRoutingDocument | None:
        with self._sessions() as session:
            row = (
                session.query(EmbeddingDocumentRow)
                .join(
                    EmbeddingVectorRow,
                    EmbeddingVectorRow.document_id == EmbeddingDocumentRow.document_id,
                )
                .filter(
                    EmbeddingDocumentRow.capability_id == capability_id,
                    EmbeddingDocumentRow.version == version,
                    EmbeddingVectorRow.model_id == model_id,
                )
                .one_or_none()
            )
            if row is None:
                return None
            return _document_of(row, model_id)

    def search(
        self,
        query_vector: list[float],
        pairs: list[tuple[str, str]],
        *,
        model_id: str,
        limit: int,
    ) -> list[RetrievedDocument]:
        if not pairs or limit <= 0:
            return []
        with self._sessions() as session:
            similarity = (1.0 - EmbeddingVectorRow.embedding.cosine_distance(query_vector)).label(
                "similarity"
            )
            rows = (
                session.query(
                    EmbeddingDocumentRow.capability_id,
                    EmbeddingDocumentRow.version,
                    similarity,
                )
                .join(
                    EmbeddingVectorRow,
                    EmbeddingVectorRow.document_id == EmbeddingDocumentRow.document_id,
                )
                .filter(
                    tuple_(EmbeddingDocumentRow.capability_id, EmbeddingDocumentRow.version).in_(
                        pairs
                    ),
                    EmbeddingVectorRow.model_id == model_id,
                )
                .order_by(desc("similarity"))
                .limit(limit)
                .all()
            )
            return [
                RetrievedDocument(
                    capability_id=capability_id,
                    version=version,
                    score=float(score),
                )
                for capability_id, version, score in rows
            ]
