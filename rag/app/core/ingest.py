"""Ingestão: parse -> chunk -> embed -> upsert no Qdrant.

`parse_document` lida com texto/markdown direto; PDF tem hook (pypdf se presente).
`ingest_documents` recebe uma lista de docs já estruturados (texto + metadados) e
indexa na coleção certa. `seed_demo` injeta conteúdo sintético p/ validar o fluxo
ponta a ponta sem depender dos insumos reais do MBFT.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone

from qdrant_client.models import PointStruct

from app.core.embeddings import get_embedder
from app.core.store import TYPE_TO_COLLECTION, ensure_collections, upsert_points

_PARA = re.compile(r"\n\s*\n")


def chunk_text(text: str, max_chars: int = 1200, overlap: int = 150) -> list[str]:
    """Quebra por parágrafos, agrupando até max_chars, com sobreposição leve."""
    text = (text or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in _PARA.split(text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for p in paras:
        if len(buf) + len(p) + 2 <= max_chars:
            buf = f"{buf}\n\n{p}".strip()
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= max_chars:
                buf = p
            else:
                # parágrafo gigante: corta em janelas
                start = 0
                while start < len(p):
                    chunks.append(p[start:start + max_chars])
                    start += max_chars - overlap
                buf = ""
    if buf:
        chunks.append(buf)
    # aplica overlap textual entre chunks consecutivos
    if overlap > 0 and len(chunks) > 1:
        overlapped = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = chunks[i - 1][-overlap:]
            overlapped.append((tail + "\n" + chunks[i]).strip())
        return overlapped
    return chunks


def parse_document(*, content: bytes | str, content_type: str = "text/plain") -> str:
    """Retorna texto. Markdown/texto direto. PDF via pypdf se disponível."""
    if isinstance(content, str):
        return content
    if content_type in ("text/plain", "text/markdown") or content_type.startswith("text/"):
        return content.decode("utf-8", errors="replace")
    if content_type == "application/pdf":
        try:
            import io

            from pypdf import PdfReader  # type: ignore

            reader = PdfReader(io.BytesIO(content))
            return "\n\n".join((page.extract_text() or "") for page in reader.pages)
        except Exception:
            return ""
    return content.decode("utf-8", errors="replace")


def _point_id(collection: str, fonte: str, idx: int) -> str:
    """ID determinístico (idempotência): reingestar a mesma fonte sobrescreve."""
    raw = f"{collection}|{fonte}|{idx}"
    return str(uuid.UUID(hashlib.sha256(raw.encode()).hexdigest()[:32]))


def ingest_documents(docs: list[dict], default_partner_id: str | None = None) -> dict:
    """Cada doc: {type, texto|content, content_type?, fonte, codigo?, tema?,
    artigo_ctb?, pagina?, ficha?, partner_id?}. Indexa na coleção do `type`."""
    ensure_collections()
    embedder = get_embedder()

    per_collection: dict[str, list[PointStruct]] = {}
    total_chunks = 0
    fontes: set[str] = set()

    for doc in docs:
        dtype = doc.get("type", "mbft")
        collection = TYPE_TO_COLLECTION.get(dtype)
        if collection is None:
            continue
        text = doc.get("texto")
        if text is None and "content" in doc:
            text = parse_document(content=doc["content"], content_type=doc.get("content_type", "text/plain"))
        chunks = chunk_text(text or "")
        if not chunks:
            continue
        fonte = doc.get("fonte", "desconhecida")
        fontes.add(fonte)
        partner_id = doc.get("partner_id", default_partner_id)
        payload_base = {
            "tipo": doc.get("tipo", dtype),
            "fonte": fonte,
            "codigo": doc.get("codigo"),
            "tema": doc.get("tema"),
            "artigo_ctb": doc.get("artigo_ctb"),
            "pagina": doc.get("pagina"),
            "ficha": doc.get("ficha"),
            "partner_id": partner_id or "__global__",
        }
        vectors = embedder.embed(chunks)
        pts = per_collection.setdefault(collection, [])
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            payload = dict(payload_base)
            payload["texto"] = chunk
            payload["chunk_index"] = i
            pts.append(PointStruct(id=_point_id(collection, fonte, i), vector=vec, payload=payload))
        total_chunks += len(chunks)

    upserted = 0
    for collection, pts in per_collection.items():
        upserted += upsert_points(collection, pts)

    return {
        "fontes": len(fontes),
        "chunks": total_chunks,
        "upserted": upserted,
        "collections": sorted(per_collection.keys()),
        "embed_provider": embedder.provider,
        "at": datetime.now(timezone.utc).isoformat(),
    }


# --------------------------------------------------------------------------
# Seed sintético — valida o pipeline sem os insumos reais do MBFT.
# Substituível pela ingestão real (MinIO bucket `conhecimento`) na operação.
# --------------------------------------------------------------------------
def demo_docs() -> list[dict]:
    return [
        {
            "type": "mbft",
            "tipo": "ficha",
            "ficha": "Ficha 5.1 — Ausência de sinalização",
            "fonte": "MBFT/ficha-5.1",
            "codigo": "501-00",
            "artigo_ctb": "181, XVII",
            "tema": "estacionamento",
            "pagina": 142,
            "texto": (
                "Estacionar o veículo em desacordo com a regulamentação. A autuação por "
                "estacionamento irregular exige sinalização vertical e/ou horizontal "
                "regulamentando a proibição no local. Ausente a sinalização que delimite "
                "claramente a restrição, a penalidade é insubsistente. A presunção de "
                "legitimidade do ato administrativo é relativa e cede diante da ausência "
                "de comprovação da regular sinalização do local autuado."
            ),
        },
        {
            "type": "mbft",
            "tipo": "ficha",
            "ficha": "Ficha 7.3 — Erro na identificação do veículo",
            "fonte": "MBFT/ficha-7.3",
            "codigo": "703-21",
            "artigo_ctb": "230, V",
            "tema": "identificacao",
            "pagina": 318,
            "texto": (
                "Conduzir o veículo sem registro adequado. Divergência entre a placa "
                "constante do auto de infração e a placa do veículo do autuado caracteriza "
                "vício insanável de identificação. O equívoco na descrição do veículo "
                "compromete a certeza e a liquidez do auto, impondo o cancelamento da "
                "penalidade por afronta ao devido processo legal."
            ),
        },
        {
            "type": "jurisprudencia",
            "tipo": "acordao",
            "fonte": "TJSP-Ap-1001234-00",
            "tema": "sinalizacao",
            "codigo": "501-00",
            "texto": (
                "RECURSO — Infração de trânsito — Estacionamento — Ausência de sinalização "
                "regulamentar no local — Presunção de legitimidade afastada — É ônus da "
                "Administração demonstrar a regular sinalização da via — Auto de infração "
                "anulado — Recurso provido."
            ),
        },
        {
            "type": "modelo_recurso",
            "tipo": "modelo",
            "fonte": "modelo/defesa-previa-sinalizacao",
            "tema": "sinalizacao",
            "texto": (
                "EXCELENTÍSSIMO SENHOR PRESIDENTE DA JARI. O autuado, já qualificado, vem "
                "apresentar DEFESA PRÉVIA contra o auto de infração em epígrafe, pelos "
                "fundamentos a seguir. DOS FATOS. DA AUSÊNCIA DE SINALIZAÇÃO. Nos termos do "
                "CTB e da regulamentação do CONTRAN, a validade da autuação por "
                "estacionamento depende de sinalização regulamentar visível. DO PEDIDO. "
                "Requer o cancelamento da penalidade."
            ),
        },
    ]


def seed_demo(partner_id: str | None = None) -> dict:
    return ingest_documents(demo_docs(), default_partner_id=partner_id)
