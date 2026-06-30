"""Recursos gerados pelo pipeline e seu versionamento (blueprint §6.9-6.10).

Roda em paralelo ao `recursos` legado (M7) até o cutover (M11): o caminho novo
escreve aqui; o síncrono antigo segue em `recursos` para não quebrar os testes.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class GeneratedResource(Base, PKMixin, TimestampMixin):
    """§6.9 — recurso gerado, com fontes/citações e argumentos persistidos (B5)."""

    __tablename__ = "generated_resources"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traffic_tickets.id"), nullable=True,
    )
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(
        String, default="draft", server_default=text("'draft'"), nullable=False, index=True,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    content_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    arguments_json: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    risk_flags: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_versions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ragflow_session_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class GeneratedResourceVersion(Base, PKMixin, TimestampMixin):
    """§6.10 — versão imutável do recurso (histórico de edições do revisor)."""

    __tablename__ = "generated_resource_versions"

    generated_resource_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("generated_resources.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_markdown: Mapped[str] = mapped_column(Text, nullable=False)
    change_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )

    __table_args__ = (
        UniqueConstraint("generated_resource_id", "version", name="uq_genres_version"),
    )
