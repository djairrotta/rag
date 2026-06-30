"""Modelos ORM — importa tudo para registrar no metadata (usado pelo Alembic).

B1: adicionado o pipeline do blueprint §6 (cases, case_files, traffic_tickets,
case_analyses, processing_jobs, generated_resources(+versions), legal_documents
(+chunks), prompt_templates, audit_logs). Órfãos sem uso (Prompt/KnowledgeDocument/
audit_log) foram superseditados — ver migração aa0d737fabc3 e os docstrings dos modelos.
"""
from app.models.user import RefreshToken, User
from app.models.partner import Partner, PartnerWallet, Subscription, WalletTransaction
from app.models.analysis import Analysis, Question
from app.models.recurso import Payment, Recurso
from app.models.case import (
    Case,
    CaseAnalysis,
    CaseFile,
    ProcessingJob,
    TrafficTicket,
)
from app.models.generated import GeneratedResource, GeneratedResourceVersion
from app.models.legal import LegalDocument, LegalDocumentChunk
from app.models.admin import (
    AuditLog,
    EmailConfig,
    LLMConfig,
    PromptTemplate,
    SystemError,
    TokenUsage,
)

__all__ = [
    # identidade / parceiros
    "User", "RefreshToken", "Partner", "PartnerWallet", "WalletTransaction", "Subscription",
    # fluxo legado (M4/M5/M7) — segue ativo em paralelo até o cutover (M11)
    "Analysis", "Question", "Payment", "Recurso",
    # pipeline novo (blueprint §6)
    "Case", "CaseFile", "TrafficTicket", "CaseAnalysis", "ProcessingJob",
    "GeneratedResource", "GeneratedResourceVersion",
    "LegalDocument", "LegalDocumentChunk",
    # admin / observabilidade / config
    "PromptTemplate", "LLMConfig", "TokenUsage", "SystemError", "EmailConfig", "AuditLog",
]
