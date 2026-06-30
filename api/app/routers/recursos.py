"""Geração e entrega do recurso (M7) — porte de generate-resource.

POST /recursos              gera o recurso (gate: resource_available, ou interno+force).
GET  /recursos/{id}         metadados do recurso (escopo do dono).
GET  /recursos/{id}/download baixa o DOCX (local) ou redireciona p/ URL assinada (MinIO).

Auth: JWT do usuário OU header X-Internal-Secret (usado pelo webhook do Asaas).
"""
import uuid

from fastapi import APIRouter, Depends, Header
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_owner, get_db, get_optional_user
from app.core.errors import AppError, Codes
from app.models import Analysis, Recurso, User
from app.services import docx_render, recurso_gen, storage

router = APIRouter(tags=["recursos"])
TITULO = "RECURSO ADMINISTRATIVO DE MULTA DE TRÂNSITO"


class RecursoIn(BaseModel):
    analysis_id: uuid.UUID
    force: bool = False


def _download_ref(recurso: Recurso) -> str | None:
    """URL assinada (MinIO) ou caminho da API (local)."""
    signed = storage.presigned_url(recurso.docx_url) if recurso.docx_url else None
    return signed or (f"{settings.api_base_url}/recursos/{recurso.id}/download" if recurso.docx_url else None)


@router.post("/recursos")
def generate(
    body: RecursoIn,
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    is_internal = bool(settings.internal_secret) and x_internal_secret == settings.internal_secret
    if not is_internal and user is None:
        raise AppError(Codes.UNAUTHORIZED, "não autorizado", 401)

    analysis = db.get(Analysis, body.analysis_id)
    if analysis is None:
        raise AppError(Codes.NOT_FOUND, "análise não encontrada", 404)

    if not is_internal and analysis.user_id is not None:
        assert_owner(analysis, user)  # type: ignore[arg-type]

    # gate de pagamento (liberado por resource_available, ou internamente com force)
    if not analysis.resource_available and not (is_internal and body.force):
        return JSONResponse(status_code=402, content={
            "success": False, "error_code": "PAYMENT_REQUIRED",
            "message": "O recurso ainda não foi liberado. Conclua o pagamento para gerar o documento.",
        })

    # já gerado? devolve o existente
    existing = (db.query(Recurso)
                .filter(Recurso.analysis_id == analysis.id, Recurso.status == "ready")
                .order_by(Recurso.created_at.desc()).first())
    if existing and existing.docx_url:
        return {
            "success": True, "analysis_id": str(analysis.id), "recurso_id": str(existing.id),
            "status": existing.status, "generated": False,
            "download_url": _download_ref(existing), "engine": "cache",
        }

    veredito = analysis.veredito or {}
    text, engine = recurso_gen.generate_text(
        extracted=analysis.campos or {}, nullities=analysis.nulidades or [],
        answers=analysis.questionario or {}, summary=veredito.get("summary", ""),
    )
    docx_bytes = docx_render.render_docx(TITULO, text)
    owner = str(analysis.user_id or "anon")
    key = f"{owner}/{analysis.id}.docx"
    ref = storage.store_bytes(key, docx_bytes, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")

    rec = Recurso(
        analysis_id=analysis.id, user_id=analysis.user_id, partner_id=analysis.partner_id,
        md=text, docx_url=ref, status="ready",
    )
    db.add(rec); db.commit(); db.refresh(rec)

    return {
        "success": True, "analysis_id": str(analysis.id), "recurso_id": str(rec.id),
        "status": "ready", "generated": True,
        "download_url": _download_ref(rec), "engine": engine, "storage": storage.provider_label(),
    }


@router.get("/recursos/{recurso_id}")
def get_recurso(recurso_id: uuid.UUID, user: User = Depends(get_optional_user), db: Session = Depends(get_db)) -> dict:
    if user is None:
        raise AppError(Codes.UNAUTHORIZED, "não autorizado", 401)
    rec = db.get(Recurso, recurso_id)
    if rec is None:
        raise AppError(Codes.NOT_FOUND, "recurso não encontrado", 404)
    assert_owner(rec, user)
    return {
        "recurso_id": str(rec.id), "analysis_id": str(rec.analysis_id), "status": rec.status,
        "editado": rec.editado, "download_url": _download_ref(rec),
    }


@router.get("/recursos/{recurso_id}/download")
def download(recurso_id: uuid.UUID, user: User = Depends(get_optional_user), db: Session = Depends(get_db)):
    if user is None:
        raise AppError(Codes.UNAUTHORIZED, "não autorizado", 401)
    rec = db.get(Recurso, recurso_id)
    if rec is None or not rec.docx_url:
        raise AppError(Codes.NOT_FOUND, "recurso não encontrado", 404)
    assert_owner(rec, user)
    signed = storage.presigned_url(rec.docx_url)
    if signed:
        return RedirectResponse(url=signed)
    path = storage.local_path(rec.docx_url)
    if path is None or not path.exists():
        raise AppError(Codes.NOT_FOUND, "arquivo indisponível", 404)
    return FileResponse(
        path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=f"recurso-{rec.analysis_id}.docx",
    )
