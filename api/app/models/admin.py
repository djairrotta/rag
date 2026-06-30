"""Tabelas administrativas (blueprint §6.6, §6.12 + observabilidade/config).

Mudanças do B1:
- `Prompt` (órfão, tabela `prompts`) -> substituído por `PromptTemplate` (§6.6, `prompt_templates`).
- `KnowledgeDocument` (órfão, `knowledge_documents`) -> movido/expandido p/ `legal.py` (§6.7-6.8).
- `AuditLog` evoluído: tabela `audit_log` -> `audit_logs` (§6.12), com case_id/ip/user_agent
  e renomes entity->entity_type, data->metadata.
Mantidos: LLMConfig, TokenUsage, SystemError, EmailConfig.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, PKMixin, TimestampMixin


class PromptTemplate(Base, PKMixin, TimestampMixin):
    """§6.6 — prompts editáveis pelo admin, versionados (semeados no B6)."""

    __tablename__ = "prompt_templates"

    name: Mapped[str] = mapped_column(String, nullable=False)
    template_type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False,
    )
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    temperature: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), default=Decimal("0.2"), server_default=text("0.2"), nullable=True,
    )
    top_p: Mapped[Decimal | None] = mapped_column(
        Numeric(3, 2), default=Decimal("1.0"), server_default=text("1.0"), nullable=True,
    )
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False,
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("template_type", "version", name="uq_prompt_templates_type_version"),
    )


class LLMConfig(Base, PKMixin, TimestampMixin):
    __tablename__ = "llm_configs"
    task: Mapped[str] = mapped_column(String, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    price_in_per_1k: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    price_out_per_1k: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TokenUsage(Base, PKMixin, TimestampMixin):
    __tablename__ = "token_usage"
    ref_type: Mapped[str] = mapped_column(String, nullable=False)  # analise|recurso|case
    ref_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    custo_brl: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=0, nullable=False)


class SystemError(Base, PKMixin, TimestampMixin):
    __tablename__ = "system_errors"
    origem: Mapped[str | None] = mapped_column(String, nullable=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class EmailConfig(Base, PKMixin, TimestampMixin):
    __tablename__ = "email_config"
    smtp_host: Mapped[str | None] = mapped_column(String, nullable=True)
    smtp_port: Mapped[int | None] = mapped_column(Integer, nullable=True)
    smtp_user: Mapped[str | None] = mapped_column(String, nullable=True)
    smtp_pass: Mapped[str | None] = mapped_column(String, nullable=True)
    smtp_from: Mapped[str | None] = mapped_column(String, nullable=True)


class AuditLog(Base, PKMixin, TimestampMixin):
    """§6.12 — auditoria (evoluída do órfão `audit_log`).

    Atributo Python `event_metadata` mapeia a coluna SQL `metadata` (reservada).
    """

    __tablename__ = "audit_logs"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("cases.id"), nullable=True, index=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String, nullable=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    event_metadata: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, server_default=text("'{}'::jsonb"),
    )
    ip_address: Mapped[str | None] = mapped_column(String, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
