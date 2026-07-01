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


# =========================================================================== #
# Ingestão do CTB consolidado (Planalto + Celso + 360 + Resolução 432)
# =========================================================================== #
def ingest_ctb(
    db: Session,
    *,
    planalto_html_path: str | None = None,
    celso_pdf: str | None = None,
    leg360_pdf: str | None = None,
    version_label: str = "2024",
    name: str = "Código de Trânsito Brasileiro — consolidado (lei + doutrina + resoluções)",
    dataset_name: str = "seguramultas_ctb",
    push_ragflow: bool = False,
) -> dict:
    """Pipeline de ingestão do CTB consolidado no Postgres (+ RAGFlow opcional).

    Casa Planalto (lei oficial) + Celso (doutrina) + 360 (extras) por artigo, e inclui
    a Resolução 432/2013. Grava em legal_documents/legal_document_chunks de forma
    idempotente (document_type='ctb').
    """
    from app.services.ctb_consolidate import (
        consolidar_ctb,
        parse_resolucao_432_embutida,
        relatorio as ctb_relatorio,
    )

    planalto_html = None
    if planalto_html_path:
        planalto_html = open(planalto_html_path, "rb").read().decode("latin-1", errors="ignore")

    consolidados = consolidar_ctb(
        planalto_html=planalto_html, celso_pdf=celso_pdf, leg360_pdf=leg360_pdf,
    )
    res432 = parse_resolucao_432_embutida()

    # monta chunk_dicts no formato esperado por replace_chunks
    chunk_dicts: list[dict] = []
    idx = 0
    for c in consolidados + res432:
        chunk_dicts.append({
            "chunk_index": idx,
            "title": c["article"],
            "section": c["metadata"].get("source", "ctb"),
            "article": c["article"],
            "content": c["content"],
            "metadata": c["metadata"],
        })
        idx += 1

    doc = upsert_legal_document(
        db, name=name, document_type="ctb", version_label=version_label,
        source_url="https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm",
    )
    n = replace_chunks(db, doc, chunk_dicts)
    db.commit()

    report = ctb_relatorio(consolidados, res432)
    report["legal_document_id"] = str(doc.id)
    report["chunks_gravados"] = n

    if push_ragflow:
        report["ragflow"] = push_ctb_to_ragflow(db, version_label=version_label, dataset_name=dataset_name)
    else:
        report["ragflow_push"] = "desabilitado (use --push-ragflow)"
    return report


def push_ctb_to_ragflow(
    db: Session, *, version_label: str = "2024",
    dataset_name: str = "seguramultas_ctb", client=None,
) -> dict:
    """Empurra os chunks do CTB (já no Postgres) para o RAGFlow dataset `seguramultas_ctb`.

    Mesmo padrão do MBFT: acha/cria o dataset, recria um documento limpo, insere cada
    artigo como chunk (com art_numero, resoluções e fonte como keywords) e grava os ids.
    """
    from app.services.ragflow_client import RagflowClient

    doc = db.execute(
        select(LegalDocument).where(
            LegalDocument.document_type == "ctb",
            LegalDocument.version_label == version_label,
        )
    ).scalars().first()
    if doc is None:
        raise RuntimeError("CTB ainda não está no Postgres — rode ingest_ctb primeiro.")

    rows = db.execute(
        select(LegalDocumentChunk)
        .where(LegalDocumentChunk.legal_document_id == doc.id)
        .order_by(LegalDocumentChunk.chunk_index)
    ).scalars().all()
    if not rows:
        raise RuntimeError("nenhum chunk do CTB no Postgres para empurrar.")

    owns_client = client is None
    client = client or RagflowClient()
    try:
        dataset_id = client.find_or_create_dataset(
            dataset_name,
            embedding_model=settings.ragflow_embed_model,
            chunk_method="naive",
        )
        doc_name = f"CTB {version_label}.txt"
        provenance = (
            f"Código de Trânsito Brasileiro consolidado {version_label} — lei oficial "
            f"(Planalto) + doutrina (Celso Luiz Martins, 2012) + Resolução CONTRAN 432/2013. "
            f"{len(rows)} chunks, ingestão via pipeline do SEGURA MULTAS."
        ).encode("utf-8")
        rf_doc_id = client.ensure_clean_document(dataset_id, doc_name, provenance)

        pushed = 0
        for i, row in enumerate(rows, start=1):
            meta = row.chunk_metadata or {}
            # keywords: artigo + números das resoluções citadas + fonte
            kws = [meta.get("art_numero"), row.article]
            for r in (meta.get("resolucoes_citadas") or []):
                kws.append(f"Res{r.get('numero')}")
            if meta.get("resolucao"):
                kws.append(f"Res{meta['resolucao']}")
            keywords = [k for k in kws if k]
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
        "dataset_name": dataset_name,
        "dataset_id": dataset_id,
        "document_id": rf_doc_id,
        "chunks_pushed": pushed,
    }
