"""Backend de retrieval via RAGFlow para o /search (blueprint B3).

Mantém o contrato /search intacto: recebe {consulta, filtros, top_k} e devolve
results no mesmo shape do Qdrant ({...payload, score}). Quando RAGFLOW_BASE_URL
está setado, o /search usa este módulo; senão, cai no Qdrant.
"""
from __future__ import annotations

import re

import httpx

from app.core.config import settings

_CODE_RE = re.compile(r"\b\d{3}-\d{2}\b")
_dataset_id_cache: str | None = None


def enabled() -> bool:
    return bool(settings.ragflow_base_url)


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=f"{settings.ragflow_base_url.rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {settings.ragflow_api_key}"},
        timeout=30.0,
    )


def _unwrap(resp: httpx.Response) -> object:
    body = resp.json()
    if isinstance(body, dict) and body.get("code") not in (0, None):
        raise RuntimeError(f"RAGFlow code={body.get('code')}: {body.get('message')}")
    return body.get("data") if isinstance(body, dict) else body


def _resolve_dataset_id(client: httpx.Client) -> str | None:
    global _dataset_id_cache
    if settings.ragflow_dataset_id:
        return settings.ragflow_dataset_id
    if _dataset_id_cache:
        return _dataset_id_cache
    data = _unwrap(client.get("/datasets", params={"name": settings.ragflow_dataset_name})) or []
    items = data if isinstance(data, list) else data.get("datasets") or []
    for d in items:
        if d.get("name") == settings.ragflow_dataset_name:
            _dataset_id_cache = d["id"]
            return _dataset_id_cache
    return None


def _codigo_of(chunk: dict) -> str | None:
    for kw in chunk.get("important_keywords") or []:
        if _CODE_RE.fullmatch(kw or ""):
            return kw
    m = _CODE_RE.search(chunk.get("content") or "")
    return m.group(0) if m else None


def search(consulta: str, filtros: dict | None, top_k: int) -> list[dict]:
    """Executa o retrieval no RAGFlow e devolve no shape do contrato /search."""
    filtros = filtros or {}
    codigo = filtros.get("codigo")
    question = f"{consulta} {codigo}".strip() if codigo else consulta

    with _client() as client:
        dataset_id = _resolve_dataset_id(client)
        if not dataset_id:
            return []
        payload = {
            "question": question,
            "dataset_ids": [dataset_id],
            "top_k": top_k,
            "page": 1,
            "page_size": top_k,
            "similarity_threshold": 0.0 if codigo else 0.2,
            "keyword": bool(codigo),
        }
        data = _unwrap(client.post("/retrieval", json=payload)) or {}
    raw = data if isinstance(data, list) else data.get("chunks") or []

    results: list[dict] = []
    for ch in raw:
        results.append({
            "content": ch.get("content"),
            "codigo": _codigo_of(ch),
            "score": float(ch.get("similarity", 0.0)),
            "ragflow_chunk_id": ch.get("id"),
            "ragflow_document_id": ch.get("document_id"),
            "source": "ragflow",
        })

    # filtro exato por código: se houver match, restringe; senão devolve o semântico
    if codigo:
        exact = [r for r in results if r.get("codigo") == codigo]
        if exact:
            return exact[:top_k]
    return results[:top_k]
