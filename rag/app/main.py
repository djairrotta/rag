"""Serviço RAG do SEGURA MULTAS — contrato FIXO (blueprint v3 / api-contracts §10).

M3: ingestão (parse->chunk->embed->upsert), busca semântica com filtro por metadado
e visibilidade por parceiro nas 3 coleções, status real e reindexação rastreável.
Embeddings plugáveis (OpenAI 3072 com chave; fallback determinístico p/ teste).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.core.config import settings
from app.core.embeddings import get_embedder
from app.core import store
from app.core import ingest
from app.core import ragflow

app = FastAPI(title="SEGURA MULTAS · RAG", version="0.3.0")

_jobs: dict[str, dict] = {}
_state: dict[str, object] = {"last_ingested_at": None, "collections_ready": False}


@app.on_event("startup")
def _startup() -> None:
    try:
        store.ensure_collections()
        _state["collections_ready"] = True
    except Exception:
        _state["collections_ready"] = False  # Qdrant indisponível no boot — /status reflete


def verify_bearer(authorization: str | None = Header(default=None)) -> None:
    """Exige Authorization: Bearer <RAG_API_KEY>. Vazio em dev => libera."""
    expected = settings.rag_api_key
    if not expected:
        return
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="RAG_API_KEY inválida")


class Filtros(BaseModel):
    codigo: str | None = None
    tema: str | None = None
    tipo: str | None = None


class SearchRequest(BaseModel):
    consulta: str
    filtros: Filtros | None = None
    partner_id: str | None = None
    top_k: int = 8


class ReindexRequest(BaseModel):
    type: str | None = None          # mbft | jurisprudencia | modelo_recurso
    partner_id: str | None = None


@app.get("/health", tags=["rag"])
def health() -> dict:
    return {"ok": True}


@app.get("/status", tags=["rag"])
def status(_: None = Depends(verify_bearer)) -> dict:
    try:
        c = store.counts()
        total_chunks = sum(c.values())
        healthy = True
    except Exception:
        c, total_chunks, healthy = {}, 0, False
    return {
        "fichas_indexed": c.get(settings.qdrant_collection_mbft, 0),
        "chunks": total_chunks,
        "by_collection": c,
        "collection": settings.qdrant_collection_mbft,
        "embed_provider": get_embedder().provider,
        "embed_dim": settings.embed_dim,
        "last_ingested_at": _state["last_ingested_at"],
        "healthy": healthy,
    }


@app.post("/search", tags=["rag"])
def search(req: SearchRequest, _: None = Depends(verify_bearer)) -> dict:
    if not req.consulta or not req.consulta.strip():
        return {"results": []}
    filtros = req.filtros.model_dump() if req.filtros else None

    # Backend RAGFlow (se configurado); senão, Qdrant. Contrato idêntico.
    if ragflow.enabled():
        try:
            return {"results": ragflow.search(req.consulta, filtros, req.top_k)}
        except Exception:
            pass  # falha do RAGFlow → cai no Qdrant abaixo

    vector = get_embedder().embed_one(req.consulta)
    merged: list[dict] = []
    for collection in store.ALL_COLLECTIONS:
        try:
            merged.extend(store.search(collection, vector, req.top_k, req.partner_id, filtros))
        except Exception:
            continue
    merged.sort(key=lambda r: r.get("score", 0.0), reverse=True)
    return {"results": merged[: req.top_k]}


def _run_ingest(job_id: str, req: ReindexRequest) -> None:
    _jobs[job_id]["status"] = "running"
    try:
        # Operação real: ler do MinIO (bucket conhecimento). Enquanto não há insumos,
        # usa o seed sintético p/ deixar o pipeline exercitável.
        docs = ingest.demo_docs()
        if req.type:
            docs = [d for d in docs if d.get("type") == req.type]
        result = ingest.ingest_documents(docs, default_partner_id=req.partner_id)
        _state["last_ingested_at"] = result["at"]
        _jobs[job_id].update(status="completed", result=result)
    except Exception as exc:  # noqa: BLE001
        _jobs[job_id].update(status="failed", error=str(exc))


@app.post("/reindex", tags=["rag"])
def reindex(req: ReindexRequest | None = Body(default=None), _: None = Depends(verify_bearer)) -> dict:
    req = req or ReindexRequest()
    job_id = uuid4().hex
    _jobs[job_id] = {"status": "queued", "requested_at": datetime.now(timezone.utc).isoformat()}
    _run_ingest(job_id, req)  # síncrono: simples e testável; status fica "completed"
    return {"job_id": job_id, "status": _jobs[job_id]["status"]}


@app.get("/reindex/{job_id}", tags=["rag"])
def reindex_status(job_id: str, _: None = Depends(verify_bearer)) -> dict:
    job = _jobs.get(job_id)
    if job is None:
        return {"job_id": job_id, "status": "unknown"}
    return {"job_id": job_id, **job}
