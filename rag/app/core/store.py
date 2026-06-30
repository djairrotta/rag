"""Camada de armazenamento vetorial (Qdrant).

3 coleções fixas (mbft, jurisprudencia, modelos_recurso), vetor `embed_dim`/cosine.
Cada ponto carrega `partner_id` no payload (None = global, visível a todos).

Em produção: QdrantClient(url=...). Em teste: settings.qdrant_location ":memory:".
"""
from __future__ import annotations

from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)

from app.core.config import settings

# type lógico -> coleção
TYPE_TO_COLLECTION = {
    "mbft": settings.qdrant_collection_mbft,
    "jurisprudencia": settings.qdrant_collection_juris,
    "modelo_recurso": settings.qdrant_collection_modelos,
}
ALL_COLLECTIONS = list(TYPE_TO_COLLECTION.values())


@lru_cache
def get_client() -> QdrantClient:
    if settings.qdrant_location:
        # modo local (testes): ":memory:" ou um diretório
        return QdrantClient(location=settings.qdrant_location)
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None)


def ensure_collections() -> None:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    for name in ALL_COLLECTIONS:
        if name not in existing:
            client.create_collection(
                collection_name=name,
                vectors_config=VectorParams(size=settings.embed_dim, distance=Distance.COSINE),
            )


def upsert_points(collection: str, points: list[PointStruct]) -> int:
    if not points:
        return 0
    get_client().upsert(collection_name=collection, points=points)
    return len(points)


def _build_filter(partner_id: str | None, filtros: dict | None) -> Filter:
    """Visibilidade: global (partner_id ausente) OU do próprio parceiro.
    Mais os filtros opcionais do contrato (codigo/tema/tipo)."""
    must: list[FieldCondition] = []
    if filtros:
        for key in ("codigo", "tema", "tipo"):
            val = filtros.get(key)
            if val:
                must.append(FieldCondition(key=key, match=MatchValue(value=val)))

    # partner visibility: documentos globais sempre; do parceiro quando houver partner_id
    if partner_id:
        should = [
            FieldCondition(key="partner_id", match=MatchValue(value=partner_id)),
            FieldCondition(key="partner_id", match=MatchValue(value="__global__")),
        ]
    else:
        should = [FieldCondition(key="partner_id", match=MatchValue(value="__global__"))]

    return Filter(must=must or None, should=should)


def search(
    collection: str,
    vector: list[float],
    top_k: int,
    partner_id: str | None,
    filtros: dict | None,
) -> list[dict]:
    client = get_client()
    flt = _build_filter(partner_id, filtros)
    resp = client.query_points(
        collection_name=collection,
        query=vector,
        query_filter=flt,
        limit=top_k,
        with_payload=True,
    )
    out: list[dict] = []
    for h in resp.points:
        payload = dict(h.payload or {})
        payload["score"] = float(h.score)
        # normaliza partner_id de volta p/ None quando global
        if payload.get("partner_id") == "__global__":
            payload["partner_id"] = None
        out.append(payload)
    return out


def counts() -> dict[str, int]:
    client = get_client()
    existing = {c.name for c in client.get_collections().collections}
    result: dict[str, int] = {}
    for name in ALL_COLLECTIONS:
        result[name] = client.count(name).count if name in existing else 0
    return result
