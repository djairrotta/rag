"""Ingestão de documentos jurídicos no Postgres (blueprint B3 / §6.7-6.8, §10.5).

Por enquanto cobre o MBFT (fichas via `mbft_splitter`). Grava o documento em
`legal_documents` e os chunks (uma ficha = um chunk) em `legal_document_chunks`,
de forma idempotente (re-ingestão substitui os chunks do mesmo documento).

O push dos chunks para o RAGFlow (preenchendo ragflow_dataset_id/document_id/
chunk_id) é o passo seguinte do B3 e exige uma instância RAGFlow — fica marcado
como TODO; o registro Postgres já serve de fonte de verdade e índice.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import LegalDocument, LegalDocumentChunk
from app.services.mbft_splitter import (
    fichas_to_chunks,
    split_mbft_pdf,
    validate_fichas,
)


def upsert_legal_document(
    db: Session,
    *,
    name: str,
    document_type: str,
    version_label: str,
    jurisdiction: str = "BR",
    source_url: str | None = None,
) -> LegalDocument:
    """Acha o documento por (document_type, version_label) ou cria um novo."""
    existing = db.execute(
        select(LegalDocument).where(
            LegalDocument.document_type == document_type,
            LegalDocument.version_label == version_label,
        )
    ).scalars().first()
    if existing:
        existing.name = name
        existing.jurisdiction = jurisdiction
        existing.source_url = source_url
        existing.is_active = True
        return existing
    doc = LegalDocument(
        name=name,
        document_type=document_type,
        version_label=version_label,
        jurisdiction=jurisdiction,
        source_url=source_url,
        is_active=True,
    )
    db.add(doc)
    db.flush()  # garante doc.id
    return doc


def replace_chunks(db: Session, doc: LegalDocument, chunk_dicts: list[dict]) -> int:
    """Substitui todos os chunks do documento (re-ingestão idempotente)."""
    db.query(LegalDocumentChunk).filter(
        LegalDocumentChunk.legal_document_id == doc.id
    ).delete(synchronize_session=False)
    for cd in chunk_dicts:
        db.add(LegalDocumentChunk(
            legal_document_id=doc.id,
            chunk_index=cd.get("chunk_index"),
            title=cd.get("title"),
            section=cd.get("section"),
            article=cd.get("article"),
            content=cd["content"],
            content_hash=cd.get("content_hash"),
            chunk_metadata=cd.get("metadata"),
        ))
    return len(chunk_dicts)


def ingest_mbft(
    db: Session,
    pdf_path: str,
    *,
    version_label: str = "2022",
    name: str = "Manual Brasileiro de Fiscalização de Trânsito (MBFT)",
    push_ragflow: bool = False,
) -> dict:
    """Pipeline completo de ingestão do MBFT no Postgres. Devolve o relatório.

    Se `push_ragflow=True` (e RAGFLOW_BASE_URL estiver configurado), empurra os
    chunks para o RAGFlow e grava os ids de volta em legal_document_chunks.
    """
    fichas = split_mbft_pdf(pdf_path)
    report = validate_fichas(fichas)
    chunks = fichas_to_chunks(fichas, document_label=f"{name.split('(')[0].strip()} {version_label}")

    doc = upsert_legal_document(
        db, name=name, document_type="mbft", version_label=version_label,
    )
    n = replace_chunks(db, doc, chunks)
    db.commit()

    report["legal_document_id"] = str(doc.id)
    report["chunks_gravados"] = n

    if push_ragflow:
        report["ragflow"] = push_mbft_to_ragflow(db, version_label=version_label)
    else:
        report["ragflow_push"] = "desabilitado (use --push-ragflow / push_ragflow=True)"
    return report


def push_mbft_to_ragflow(db: Session, *, version_label: str = "2022", client=None) -> dict:
    """Empurra os chunks do MBFT (já no Postgres) para o RAGFlow e grava os ids.

    - acha/cria o dataset (nome em settings.ragflow_dataset_name);
    - (re)cria um documento limpo 'MBFT <versão>' (idempotência);
    - insere cada ficha como chunk manual, com o código e o artigo como keywords;
    - grava ragflow_dataset_id/document_id/chunk_id em cada linha.
    """
    from app.services.ragflow_client import RagflowClient  # import tardio (httpx)

    doc = db.execute(
        select(LegalDocument).where(
            LegalDocument.document_type == "mbft",
            LegalDocument.version_label == version_label,
        )
    ).scalars().first()
    if doc is None:
        raise RuntimeError("MBFT ainda não está no Postgres — rode ingest_mbft primeiro.")

    rows = db.execute(
        select(LegalDocumentChunk)
        .where(LegalDocumentChunk.legal_document_id == doc.id)
        .order_by(LegalDocumentChunk.chunk_index)
    ).scalars().all()
    if not rows:
        raise RuntimeError("nenhum chunk do MBFT no Postgres para empurrar.")

    owns_client = client is None
    client = client or RagflowClient()
    try:
        dataset_id = client.find_or_create_dataset(
            settings.ragflow_dataset_name,
            embedding_model=settings.ragflow_embed_model,
            chunk_method="naive",
        )
        doc_name = f"MBFT {version_label}.txt"
        provenance = (
            f"Manual Brasileiro de Fiscalização de Trânsito (MBFT) {version_label} — "
            f"{len(rows)} fichas de fiscalização, ingestão via splitter do SEGURA MULTAS."
        ).encode("utf-8")
        rf_doc_id = client.ensure_clean_document(dataset_id, doc_name, provenance)

        pushed = 0
        for i, row in enumerate(rows, start=1):
            meta = row.chunk_metadata or {}
            keywords = [meta.get("infraction_code"), row.article, meta.get("gravidade")]
            chunk_id = client.add_chunk(dataset_id, rf_doc_id, row.content, keywords)
            row.ragflow_dataset_id = dataset_id
            row.ragflow_document_id = rf_doc_id
            row.ragflow_chunk_id = chunk_id
            pushed += 1
            if i % 50 == 0:
                db.commit()
        db.commit()
    finally:
        if owns_client:
            client.close()

    return {
        "dataset_name": settings.ragflow_dataset_name,
        "dataset_id": dataset_id,
        "document_id": rf_doc_id,
        "chunks_pushed": pushed,
    }
