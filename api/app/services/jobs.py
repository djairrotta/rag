"""Execução de jobs assíncronos (blueprint B2 / §10, §6.11).

`enqueue_job` cria a linha durável em `processing_jobs` e empurra o id pra fila.
`process_one`/`drain` consomem a fila, aplicam o ciclo de vida
(queued → running → done|failed, com tentativas) e despacham para o handler
registrado por `job_type`.

O handler `process_case` é o ponto de entrada do pipeline: aqui ele apenas conclui
a *ingestão* (marca o arquivo como recebido). A extração de dados da multa (OCR +
LLM de visão) é o B4 — que registra/expande este handler. Nada de análise falsa.
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.orm import Session

from app.models import Case, CaseFile, ProcessingJob
from app.services.queue import Queue, get_queue

# tipos de job
JOB_PROCESS_CASE = "process_case"

Handler = Callable[[Session, ProcessingJob], dict]
HANDLERS: dict[str, Handler] = {}


def register(job_type: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[job_type] = fn
        return fn
    return deco


def _now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_job(
    db: Session,
    job_type: str,
    payload: dict,
    *,
    case_id: uuid.UUID | None = None,
    priority: int = 5,
    queue: Queue | None = None,
) -> ProcessingJob:
    """Cria o job durável (status queued) e o empurra pra fila."""
    job = ProcessingJob(
        case_id=case_id, job_type=job_type, status="queued",
        priority=priority, payload=payload,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    (queue or get_queue()).enqueue(str(job.id))
    return job


def process_one(db: Session, *, worker_id: str = "worker", queue: Queue | None = None) -> ProcessingJob | None:
    """Consome um job da fila e o executa. Devolve o job processado (ou None se fila vazia)."""
    q = queue or get_queue()
    job_id = q.dequeue()
    if not job_id:
        return None

    job = db.get(ProcessingJob, uuid.UUID(job_id))
    if job is None or job.status not in ("queued", "retry"):
        return job  # já processado / desconhecido — ignora

    handler = HANDLERS.get(job.job_type)
    job.status = "running"
    job.attempts = (job.attempts or 0) + 1
    job.locked_at = _now()
    job.locked_by = worker_id
    job.started_at = job.started_at or _now()
    db.commit()

    if handler is None:
        job.status = "failed"
        job.error_message = f"sem handler para job_type={job.job_type}"
        job.finished_at = _now()
        db.commit()
        return job

    try:
        result = handler(db, job)
        job.status = "done"
        job.result = result or {}
        job.error_message = None
        job.finished_at = _now()
        db.commit()
    except Exception as exc:  # noqa: BLE001 — registra a falha no job
        db.rollback()
        job = db.get(ProcessingJob, uuid.UUID(job_id))
        job.error_message = f"{type(exc).__name__}: {exc}"[:2000]
        if (job.attempts or 0) < (job.max_attempts or 3):
            job.status = "queued"  # re-tenta
            db.commit()
            q.enqueue(str(job.id))
        else:
            job.status = "failed"
            job.finished_at = _now()
            db.commit()
    return job


def drain(db: Session, *, queue: Queue | None = None, max_iter: int = 1000) -> int:
    """Processa a fila até esvaziar (ou max_iter). Devolve quantos jobs rodou."""
    q = queue or get_queue()
    n = 0
    while n < max_iter:
        if q.size() == 0:
            break
        job = process_one(db, queue=q)
        if job is None:
            break
        n += 1
    return n


def run_worker(*, worker_id: str = "worker-1", poll_interval: float = 1.0) -> None:  # pragma: no cover
    """Loop de worker para produção."""
    from app.db.session import SessionLocal

    q = get_queue()
    while True:
        db = SessionLocal()
        try:
            job = process_one(db, worker_id=worker_id, queue=q)
        finally:
            db.close()
        if job is None:
            time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@register(JOB_PROCESS_CASE)
def _handle_process_case(db: Session, job: ProcessingJob) -> dict:
    """Conclui a ingestão do arquivo do caso.

    B2: marca o CaseFile como recebido e registra o passo. A extração dos dados da
    multa (OCR + visão) é o B4 — que estende este handler enfileirando/realizando
    `extract_ticket_data`. Aqui NÃO se faz análise.
    """
    cf_id = (job.payload or {}).get("case_file_id")
    case_file = db.get(CaseFile, uuid.UUID(cf_id)) if cf_id else None
    if case_file is None:
        raise ValueError("case_file_id ausente ou inexistente no payload")

    case_file.processing_status = "received"
    case = db.get(Case, case_file.case_id)
    if case is not None:
        case.current_step = "intake_done"
    db.flush()
    return {
        "case_file_id": str(case_file.id),
        "case_id": str(case_file.case_id),
        "note": "ingestão concluída; extração de dados da multa entra no B4 (extract_ticket_data).",
    }
