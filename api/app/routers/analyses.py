"""Análises (veredito) — M4: POST /analyses (análise grátis) + claim (M2).

POST /analyses devolve o contrato AnalysisResult que o frontend "O Veredito" espera
(top-level success/status/extracted_data/nullities/claim_token), por isso NÃO usa o
envelope de erro padrão — espelha o analyze-fine do Lovable.
"""
import base64
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_current_user, get_db, get_optional_user
from app.core.errors import AppError, Codes
from app.models import Analysis, User
from app.services import analyzer

router = APIRouter(prefix="/analyses", tags=["analyses"])


def _verdict_payload(verdict: dict) -> dict:
    return {"summary": verdict.get("summary"), "recommendation": verdict.get("recommendation")}


@router.post("")
async def create_analysis(
    file: UploadFile = File(...),
    questionnaire_answers: str = Form("{}"),
    user: User | None = Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    data = await file.read()
    if not data:
        return JSONResponse(status_code=400, content={"success": False, "error": "Arquivo é obrigatório"})

    try:
        answers = json.loads(questionnaire_answers or "{}")
        if not isinstance(answers, dict):
            answers = {}
    except Exception:
        answers = {}

    mime = file.content_type or "image/jpeg"
    image_b64 = base64.b64encode(data).decode("ascii")

    result = analyzer.analyze(image_b64=image_b64, mime=mime, file_size=len(data), answers=answers)

    if result["rejected"]:
        v = result["validation"]
        return JSONResponse(status_code=400, content={
            "success": False,
            "error": "Documento não reconhecido como infração de trânsito",
            "error_code": "NOT_TRAFFIC_FINE",
            "details": {
                "document_type": v["document_type"],
                "reason": v["reason"],
                "message": ("O documento enviado não parece ser uma notificação de infração "
                            "de trânsito. Envie a imagem ou PDF de uma multa válida."),
            },
        })

    extracted = result["extracted"]
    verdict = result["verdict"]
    status = verdict.get("status", "valid")
    has_findings = status in ("null", "weak")

    analysis_id: str | None = None
    claim_token: str | None = None

    if user is not None:
        # autenticado: persiste (M7 sobe o documento no MinIO; aqui guarda metadados)
        an = Analysis(
            user_id=user.id, partner_id=user.partner_id, document_type=mime,
            campos=extracted, questionario=answers, veredito=_verdict_payload(verdict),
            nulidades=verdict.get("nullities", []), status=status,
            resource_available=(False if settings.require_payment else has_findings),
        )
        db.add(an); db.commit(); db.refresh(an)
        analysis_id = str(an.id)
    elif settings.require_payment:
        # anônimo + paywall: persiste SEM documento, com claim_token p/ vincular após cadastro
        ct = uuid.uuid4()
        an = Analysis(
            user_id=None, claim_token=ct, document_type=mime,
            campos=extracted, questionario=answers, veredito=_verdict_payload(verdict),
            nulidades=verdict.get("nullities", []), status=status, resource_available=False,
        )
        db.add(an); db.commit(); db.refresh(an)
        analysis_id, claim_token = str(an.id), str(ct)
    # anônimo sem paywall: não persiste (analysis_id None)

    return {
        "success": True,
        "analysis_id": analysis_id,
        "claim_token": claim_token,
        "is_authenticated": user is not None,
        "status": status,
        "extracted_data": extracted,
        "nullities": verdict.get("nullities", []),
        "summary": verdict.get("summary"),
        "recommendation": verdict.get("recommendation"),
        "engine": analyzer.provider_label(),
    }


class ClaimIn(BaseModel):
    claim_token: str


@router.post("/{analysis_id}/claim")
def claim(analysis_id: uuid.UUID, body: ClaimIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    an = db.get(Analysis, analysis_id)
    if an is None:
        raise AppError(Codes.NOT_FOUND, "análise não encontrada", 404)
    if an.claim_token is None or str(an.claim_token) != body.claim_token:
        raise AppError(Codes.CLAIM_INVALID, "claim_token inválido ou já usado", 400)
    an.user_id = user.id
    an.claim_token = None  # uso único
    db.commit()
    return {"id": str(an.id), "user_id": str(user.id), "claimed": True}
