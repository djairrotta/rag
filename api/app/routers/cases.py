"""Casos e upload de arquivos (blueprint B2 / §6.2-6.3, §6.11).

Fluxo de intake autenticado:
- POST /cases               cria o caso (user_id obrigatório — fluxo anônimo fica na
                            /analyses legada do M4).
- POST /cases/{id}/files    sobe a foto/PDF da multa, grava no storage (MinIO/local),
                            registra o CaseFile e ENFILEIRA o job de processamento.
- GET  /cases/{id}          devolve o caso + arquivos + jobs (p/ polling no frontend).

A extração dos dados da multa roda no worker (handler `process_case`); a lógica de
OCR/visão entra no B4. Aqui o pipeline já fica ligado ponta a ponta.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import assert_owner, get_current_user, get_db
from app.core.errors import AppError, Codes
from app.models import Case, CaseFile, ProcessingJob, User
from app.services import jobs, storage

router = APIRouter(prefix="/cases", tags=["cases"])

ACCEPTED_MIME_PREFIXES = ("image/",)
ACCEPTED_MIME_EXACT = ("application/pdf",)
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str | None) -> str:
    base = _SAFE.sub("_", (name or "arquivo").strip()) or "arquivo"
    return base[-120:]


def _case_public(case: Case) -> dict:
    return {
        "id": str(case.id),
        "title": case.title,
        "case_type": case.case_type,
        "status": case.status,
        "current_step": case.current_step,
        "payment_status": case.payment_status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


# ---------------------------------------------------------------------------
class CaseCreate(BaseModel):
    title: str | None = None


@router.post("", status_code=201)
def create_case(body: CaseCreate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    case = Case(user_id=user.id, title=body.title, case_type="traffic_fine", status="uploaded")
    db.add(case)
    db.commit()
    db.refresh(case)
    return _case_public(case)


def _load_owned_case(db: Session, case_id: uuid.UUID, user: User) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise AppError(Codes.NOT_FOUND, "caso não encontrado", 404)
    assert_owner(case, user)
    return case


@router.post("/{case_id}/files", status_code=201)
async def upload_case_file(
    case_id: uuid.UUID,
    file: UploadFile = File(...),
    file_type: str = Form("ticket"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    case = _load_owned_case(db, case_id, user)

    data = await file.read()
    if not data:
        raise AppError(Codes.VALIDATION, "arquivo vazio", 400)

    mime = file.content_type or "application/octet-stream"
    if not (mime.startswith(ACCEPTED_MIME_PREFIXES) or mime in ACCEPTED_MIME_EXACT):
        raise AppError(
            Codes.VALIDATION, "tipo de arquivo não suportado (envie imagem ou PDF)", 400,
            {"received_mime": mime},
        )

    sha = hashlib.sha256(data).hexdigest()
    bucket = settings.bucket_fotos
    key = f"cases/{case_id}/{uuid.uuid4().hex}-{_safe_name(file.filename)}"
    storage.store_bytes(key, data, mime, bucket=bucket)

    cf = CaseFile(
        case_id=case.id, user_id=user.id, file_type=file_type,
        bucket=bucket, path=key, mime_type=mime, size_bytes=len(data),
        sha256=sha, original_filename=file.filename, processing_status="pending",
    )
    db.add(cf)
    # caso passa a 'processing' assim que recebe arquivo
    case.status = "processing"
    db.commit()
    db.refresh(cf)

    job = jobs.enqueue_job(
        db, jobs.JOB_PROCESS_CASE,
        {"case_file_id": str(cf.id), "case_id": str(case.id)},
        case_id=case.id,
    )

    return {
        "file_id": str(cf.id),
        "case_id": str(case.id),
        "job_id": str(job.id),
        "status": "queued",
        "storage": storage.provider_label(),
        "sha256": sha,
        "size_bytes": len(data),
    }


@router.get("/{case_id}")
def get_case(case_id: uuid.UUID, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    case = _load_owned_case(db, case_id, user)

    files = db.execute(
        select(CaseFile).where(CaseFile.case_id == case.id).order_by(CaseFile.created_at)
    ).scalars().all()
    job_rows = db.execute(
        select(ProcessingJob).where(ProcessingJob.case_id == case.id).order_by(ProcessingJob.created_at)
    ).scalars().all()

    out = _case_public(case)
    out["files"] = [{
        "id": str(f.id), "file_type": f.file_type, "mime_type": f.mime_type,
        "size_bytes": f.size_bytes, "original_filename": f.original_filename,
        "processing_status": f.processing_status,
    } for f in files]
    out["jobs"] = [{
        "id": str(j.id), "job_type": j.job_type, "status": j.status,
        "attempts": j.attempts, "result": j.result, "error_message": j.error_message,
    } for j in job_rows]
    return out
