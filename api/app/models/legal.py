"""Documentos jurídicos e seus chunks (blueprint §6.7-6.8).

Supersede o órfão `knowledge_documents` (que era só um contador de chunks).
`legal_document_chunks` é o espelho Postgres dos ids do RAGFlow: os vetores vivem
no RAGFlow/Qdrant, mas mantemos aqui o registro (dataset/document/chunk id) para
auditoria, reindex idempotente e filtro por `article`/`section` (item 8/9).

Atributo Python `chunk_metadata` mapeia a coluna SQL `metadata` (o nome `metadata`
é reservado pela Base declarativa do SQLAlchemy e não pode ser atributo de modelo).
"""
import uuid
from datetime import date

from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class LegalDocument(Base, PKMixin, TimestampMixin):
    """§6.7 — documento jurídico importado (CTB, MBFT, resolução, jurisprudência, modelo)."""

    __tablename__ = "legal_documents"

    name: Mapped[str] = mapped_column(String, nullable=False)
    document_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    storage_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_files.id"), nullable=True,
    )
    jurisdiction: Mapped[str | None] = mapped_column(
        String, default="BR", server_default=text("'BR'"), nullable=True,
    )
    version_label: Mapped[str | None] = mapped_column(String, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False,
    )
    imported_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )


class LegalDocumentChunk(Base, PKMixin, TimestampMixin):
    """§6.8 — chunk preparado pelo Docling e enviado ao RAGFlow."""

    __tablename__ = "legal_document_chunks"

    legal_document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("legal_documents.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ragflow_dataset_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ragflow_document_id: Mapped[str | None] = mapped_column(String, nullable=True)
    ragflow_chunk_id: Mapped[str | None] = mapped_column(String, nullable=True)
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    section: Mapped[str | None] = mapped_column(String, nullable=True)
    article: Mapped[str | None] = mapped_column(String, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    chunk_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, server_default=text("'{}'::jsonb"),
    )
    content_hash: Mapped[str | None] = mapped_column(String, nullable=True)
