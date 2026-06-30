"""Parceiros: branding, carteira, transações, assinatura."""
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class Partner(Base, PKMixin, TimestampMixin):
    __tablename__ = "partners"
    name: Mapped[str] = mapped_column(String, nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String, nullable=True)
    timbrado_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    cores: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, default="active", nullable=False)


class PartnerWallet(Base, PKMixin, TimestampMixin):
    __tablename__ = "partner_wallet"
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partners.id", ondelete="CASCADE"),
        unique=True, nullable=False, index=True,
    )
    saldo_brl: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=0, nullable=False)


class WalletTransaction(Base, PKMixin, TimestampMixin):
    __tablename__ = "wallet_transactions"
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    tipo: Mapped[str] = mapped_column(String, nullable=False)  # recarga|debito
    valor_brl: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    recurso_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("recursos.id", ondelete="SET NULL"), nullable=True,
    )


class Subscription(Base, PKMixin, TimestampMixin):
    __tablename__ = "subscriptions"
    partner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("partners.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    asaas_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", nullable=False)
    next_due: Mapped[date | None] = mapped_column(Date, nullable=True)
