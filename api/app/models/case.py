"""Pipeline assíncrono (blueprint §6): caso, arquivos, multa extraída, análise, jobs.

Tradução Supabase→self-hosted: FKs que no blueprint apontam para `auth.users(id)`
apontam aqui para a nossa tabela `users`. PK por uuid4 client-side (PKMixin),
no lugar de `gen_random_uuid()`.
"""
import uuid
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Time,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Case(Base, PKMixin, TimestampMixin):
    """§6.2 — caso principal do cliente (entidade-topo do pipeline)."""

    __tablename__ = "cases"

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    case_type: Mapped[str] = mapped_column(
        String, default="traffic_fine", server_default=text("'traffic_fine'"), nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String, default="uploaded", server_default=text("'uploaded'"), nullable=False, index=True,
    )
    current_step: Mapped[str | None] = mapped_column(String, nullable=True)
    payment_status: Mapped[str | None] = mapped_column(
        String, default="pending", server_default=text("'pending'"), nullable=True,
    )
    assigned_reviewer: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class CaseFile(Base, PKMixin, TimestampMixin):
    """§6.3 — arquivos enviados ou gerados, com ponteiro pro bucket MinIO."""

    __tablename__ = "case_files"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    file_type: Mapped[str] = mapped_column(String, nullable=False)
    bucket: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sha256: Mapped[str | None] = mapped_column(String, nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String, nullable=True)
    processing_status: Mapped[str | None] = mapped_column(
        String, default="pending", server_default=text("'pending'"), nullable=True,
    )


class TrafficTicket(Base, PKMixin, TimestampMixin):
    """§6.4 — dados estruturados extraídos da multa. `infraction_code` indexado
    porque é o filtro exato que casa a multa com a ficha do MBFT no RAG (item 9)."""

    __tablename__ = "traffic_tickets"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("case_files.id"), nullable=True,
    )

    ait_number: Mapped[str | None] = mapped_column(String, nullable=True)
    issuing_authority: Mapped[str | None] = mapped_column(String, nullable=True)
    plate: Mapped[str | None] = mapped_column(String, nullable=True)
    renavam: Mapped[str | None] = mapped_column(String, nullable=True)
    infraction_code: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    infraction_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    ctb_article: Mapped[str | None] = mapped_column(String, nullable=True)
    infraction_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    infraction_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    infraction_location: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    state: Mapped[str | None] = mapped_column(String, nullable=True)
    fine_amount: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notification_issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notification_received_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    defense_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)
    officer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    equipment: Mapped[str | None] = mapped_column(String, nullable=True)
    observations: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    extracted_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confidence_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    missing_fields: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    illegible_fields: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )

    extraction_model: Mapped[str | None] = mapped_column(String, nullable=True)
    extraction_prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )


class CaseAnalysis(Base, PKMixin, TimestampMixin):
    """§6.5 — análise determinística/preliminar (sucessora self-hosted da `analyses`
    do M4 dentro do fluxo de caso; a `analyses` legada segue para o caminho anônimo)."""

    __tablename__ = "case_analyses"

    case_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("traffic_tickets.id"), nullable=True,
    )
    analysis_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    possible_arguments: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    risk_flags: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    required_client_inputs: Mapped[list | None] = mapped_column(
        JSONB, nullable=True, server_default=text("'[]'::jsonb"),
    )
    confidence_score: Mapped[Decimal | None] = mapped_column(Numeric(4, 3), nullable=True)


class ProcessingJob(Base, PKMixin, TimestampMixin):
    """§6.11 — fila de jobs assíncronos com lock, tentativas e idempotência.
    Em produção a fila é Redis; esta tabela é o estado durável (item 11)."""

    __tablename__ = "processing_jobs"

    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    job_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String, default="queued", server_default=text("'queued'"), nullable=False, index=True,
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=5, server_default=text("5"), nullable=False,
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb"),
    )
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False,
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=3, server_default=text("3"), nullable=False,
    )
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    locked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
