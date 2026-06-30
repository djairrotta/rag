"""Busca na base legal — endpoint híbrido (estruturada + semântica).

Este é o ponto de entrada do NÚCLEO DE CONHECIMENTO LEGAL, consumido por:
- App de RECURSO (defesa): acha a ficha exata da infração + teses relacionadas.
- App de GUIA POLICIAL (futuro): mesma base, ângulo da acusação.
- Dashboard Admin (busca de teste) e o gerador de recurso.

Por que a orquestração mora na API (e não no serviço `rag`)
-----------------------------------------------------------
A busca ESTRUTURADA (código/artigo) é SQL determinístico sobre o Postgres, onde as
fichas do MBFT estão com metadados estruturados (infraction_code, ctb_article, ...).
Só a API tem acesso a esse Postgres. A busca SEMÂNTICA vive no serviço `rag` (RAGFlow).
Portanto a API é quem combina os dois: chama `legal_search` localmente e o `rag` por HTTP.
O `rag` permanece um motor semântico puro e desacoplado.

Contrato (alinhado ao proxy `mbft` do front)
---------------------------------------------
POST /legal/search
  body: { consulta?, codigos?[], artigo?, inciso?, top_k? }
  resposta: { results: [ { texto, codigo, artigo_ctb, gravidade, score, origem, ... } ],
              encontrou_exato: bool }

Fallback híbrido SEM prejuízo
-----------------------------
- Tem código/artigo → busca exata no Postgres (precisão: a ficha certa).
- Tem consulta textual → busca semântica no `rag` (amplitude: teses relacionadas).
- Os dois são COMBINADOS (estruturada primeiro, semântica complementa), com dedupe.
  Nunca "ou": o exato dá a ficha, o semântico enriquece. Nunca retorna vazio se houver
  qualquer sinal de entrada.
"""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.deps import get_db
from app.services import legal_search

router = APIRouter(prefix="/legal", tags=["legal"])


class LegalSearchIn(BaseModel):
    consulta: str | None = None          # busca semântica (descrição livre)
    codigos: list[str] | None = None     # busca exata por código(s) de enquadramento
    artigo: str | None = None            # busca exata por artigo do CTB
    inciso: str | None = None            # inciso separado (frontend controlado)
    top_k: int = 8


def _semantic_search(consulta: str, codigo: str | None, top_k: int) -> list[dict]:
    """Chama o serviço `rag` (RAGFlow) — busca semântica. Best-effort: lista vazia se cair."""
    if not consulta or not consulta.strip():
        return []
    filtros = {"codigo": codigo} if codigo else None
    headers = {"Authorization": f"Bearer {settings.rag_api_key}"} if settings.rag_api_key else {}
    try:
        r = httpx.post(
            f"{settings.rag_api_url}/search",
            json={"consulta": consulta, "filtros": filtros, "top_k": top_k},
            headers=headers,
            timeout=8,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
        # marca a procedência para o consumidor distinguir das fichas exatas
        for x in results:
            x.setdefault("origem", "semantica")
        return results
    except Exception:
        return []


def _dedupe(items: list[dict]) -> list[dict]:
    """Remove repetições por (codigo, artigo, início do texto). Preserva ordem de inserção
    (estruturada entra antes → tem prioridade)."""
    seen: set[str] = set()
    out: list[dict] = []
    for it in items:
        key = (
            (it.get("codigo") or "")
            + "|" + (it.get("artigo") or it.get("artigo_ctb") or "")
            + "|" + (it.get("texto") or "")[:40]
        )
        if key not in seen:
            seen.add(key)
            out.append(it)
    return out


@router.post("/search")
def legal_search_endpoint(body: LegalSearchIn, db: Session = Depends(get_db)) -> dict:
    """Busca híbrida na base legal. Aberto a qualquer chamador autenticado pelo gateway
    (o proxy `mbft`/front cuida do auth de usuário; aqui é o núcleo de conhecimento)."""
    resultados: list[dict] = []

    # 1) ESTRUTURADA (Postgres) — precisão da ficha exata
    estruturada = legal_search.buscar_contexto_legal(
        db,
        codigos=body.codigos,
        artigo=body.artigo,
        inciso=body.inciso,
    )
    resultados.extend(estruturada["fichas"])

    # 2) SEMÂNTICA (rag/RAGFlow) — amplitude de teses relacionadas
    #    Se não há consulta textual mas há ficha exata, usamos a tipificação da 1ª ficha
    #    como consulta — assim trazemos fichas semanticamente próximas mesmo quando o
    #    cliente só informou o código.
    consulta = body.consulta
    if not consulta and resultados:
        consulta = resultados[0].get("tipificacao") or resultados[0].get("texto", "")[:120]

    codigo_filtro = (body.codigos or [None])[0]
    semantica = _semantic_search(consulta or "", codigo_filtro, body.top_k)
    resultados.extend(semantica)

    # 3) combina + dedupe (estruturada já está na frente → prioridade no dedupe)
    final = _dedupe(resultados)[: max(body.top_k, len(estruturada["fichas"]))]

    return {
        "results": final,
        "encontrou_exato": estruturada["encontrou_exato"],
    }
