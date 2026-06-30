#!/usr/bin/env python3
"""CLI de ingestão do MBFT (blueprint B3).

Exemplos:
    # só validar + exportar chunks pra JSONL (não toca o banco):
    python scripts/ingest_mbft.py /caminho/mbvt20222.pdf --jsonl /tmp/mbft_chunks.jsonl

    # validar e gravar em legal_documents/legal_document_chunks:
    POSTGRES_HOST=... python scripts/ingest_mbft.py /caminho/mbvt20222.pdf --write-db

O caminho do PDF pode vir do argumento posicional ou da env MBFT_PDF.
"""
import argparse
import json
import os
import sys

# garante import de `app.*` quando rodado de dentro de api/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.mbft_splitter import (  # noqa: E402
    fichas_to_chunks,
    split_mbft_pdf,
    validate_fichas,
)


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingestão das Fichas de Fiscalização do MBFT.")
    ap.add_argument("pdf", nargs="?", default=os.environ.get("MBFT_PDF"),
                    help="Caminho do PDF do MBFT (ou env MBFT_PDF).")
    ap.add_argument("--jsonl", help="Escreve os chunks (1 por linha) neste arquivo.")
    ap.add_argument("--write-db", action="store_true",
                    help="Grava em legal_documents/legal_document_chunks (usa POSTGRES_*).")
    ap.add_argument("--push-ragflow", action="store_true",
                    help="Empurra os chunks pro RAGFlow e grava os ids (requer --write-db + RAGFLOW_*).")
    ap.add_argument("--version", default="2022", help="version_label do documento (default 2022).")
    args = ap.parse_args()

    if not args.pdf:
        ap.error("informe o caminho do PDF (argumento ou env MBFT_PDF).")
    if not os.path.exists(args.pdf):
        ap.error(f"PDF não encontrado: {args.pdf}")

    fichas = split_mbft_pdf(args.pdf)
    report = validate_fichas(fichas)
    print("=== RELATÓRIO DO SPLITTER ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))

    anomalas = [{"codigo": f.codigo, "anomalias": f.anomalias}
                for f in fichas if any(a != "duplicada_no_pdf" for a in f.anomalias)]
    if anomalas:
        print(f"\nATENÇÃO: {len(anomalas)} ficha(s) com anomalia de extração:")
        print(json.dumps(anomalas, ensure_ascii=False, indent=2))

    if args.jsonl:
        chunks = fichas_to_chunks(fichas, document_label=f"MBFT {args.version}")
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(json.dumps(c, ensure_ascii=False) + "\n")
        print(f"\nJSONL escrito: {args.jsonl} ({len(chunks)} chunks)")

    if args.write_db:
        from app.db.session import SessionLocal
        from app.services.legal_ingest import ingest_mbft
        db = SessionLocal()
        try:
            db_report = ingest_mbft(db, args.pdf, version_label=args.version,
                                    push_ragflow=args.push_ragflow)
        finally:
            db.close()
        print("\n=== INGESTÃO NO BANCO ===")
        print(json.dumps(db_report, ensure_ascii=False, indent=2))
    elif args.push_ragflow:
        ap.error("--push-ragflow exige --write-db (o push lê os chunks do Postgres).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
