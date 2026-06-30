"""Pagamentos (B2C) e recursos gerados."""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Payment(Base, PKMixin, TimestampMixin):
    __tablename__ = "payments"
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    asaas_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    asaas_customer_id: Mapped[str | None] = mapped_column(String, nullable=True)
    method: Mapped[str] = mapped_column(String, nullable=False)  # pix|credit_card|boleto|UNDEFINED
    amount_brl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    due_date: Mapped[str | None] = mapped_column(String, nullable=True)         # ISO YYYY-MM-DD
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Recurso(Base, PKMixin, TimestampMixin):
    __tablename__ = "recursos"
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analyses.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    partner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partners.id", ondelete="SET NULL"), nullable=True, index=True,
    )
    md: Mapped[str | None] = mapped_column(Text, nullable=True)
    docx_url: Mapped[str | None] = mapped_column(String, nullable=True)
    pdf_url: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="generating", nullable=False)
    entrega: Mapped[str | None] = mapped_column(String, nullable=True)
    editado: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    onbook_id: Mapped[str | None] = mapped_column(String, nullable=True)
    custo_real_brl: Mapped[Decimal] = mapped_column(Numeric(12, 4), default=0, nullable=False)
