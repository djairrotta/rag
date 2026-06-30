"""Análises (veredito) e perguntas condicionais."""
import uuid

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Analysis(Base, PKMixin, TimestampMixin):
    __tablename__ = "analyses"
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    foto_key: Mapped[str | None] = mapped_column(String, nullable=True)
    document_type: Mapped[str | None] = mapped_column(String, nullable=True)
    campos: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    questionario: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    veredito: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    nulidades: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, default="created", nullable=False)
    resource_available: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    contact_required: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    claim_token: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), unique=True, index=True, nullable=True,
    )


class Question(Base, PKMixin, TimestampMixin):
    __tablename__ = "questions"
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    pergunta: Mapped[str] = mapped_column(String, nullable=False)
    tipo: Mapped[str] = mapped_column(String, default="boolean", nullable=False)
    answer: Mapped[str | None] = mapped_column(String, nullable=True)
