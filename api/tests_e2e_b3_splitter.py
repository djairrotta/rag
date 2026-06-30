"""Teste e2e B3 — splitter do MBFT + ingestão em legal_documents/legal_document_chunks.

Roda contra o PDF real do MBFT (env MBFT_PDF, default /mnt/user-data/uploads/mbvt20222.pdf)
e contra o Postgres de teste já migrado até o head (B1).

Uso:
    MBFT_PDF=/mnt/user-data/uploads/mbvt20222.pdf \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433 POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=localtest POSTGRES_DB=seguramultas \
    /tmp/sm-venv/bin/python tests_e2e_b3_splitter.py
"""
import os
import re
import sys

from app.services.mbft_splitter import (
    fichas_to_chunks,
    parse_fichas,
    split_mbft_pdf,
    validate_fichas,
)

PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


PDF = os.environ.get("MBFT_PDF", "/mnt/user-data/uploads/mbvt20222.pdf")

if not os.path.exists(PDF):
    print(f"SKIP: PDF do MBFT não encontrado em {PDF} (defina MBFT_PDF). "
          "O splitter exige o documento; pulando.")
    sys.exit(0)

print("=== SPLITTER (parsing do PDF real) ===")
fichas = split_mbft_pdf(PDF)
rep = validate_fichas(fichas)

check("411 fichas extraídas", rep["fichas"] == 411, str(rep["fichas"]))
check("411 códigos únicos", rep["codigos_unicos"] == 411, str(rep["codigos_unicos"]))
check("0 fichas sem código", rep["sem_codigo"] == 0, str(rep["sem_codigo"]))
check("0 códigos em formato inválido", rep["codigo_formato_invalido"] == 0, str(rep["codigo_formato_invalido"]))
check("cobertura de amparo legal = 100%", rep["amparo_cobertura_pct"] == 100.0, str(rep["amparo_cobertura_pct"]))
check("1 duplicata do PDF descartada (542-81)", rep["duplicadas_no_pdf_descartadas"] == 1,
      str(rep["duplicadas_no_pdf_descartadas"]))
check("faixa de prefixos 501–778", rep["prefixo_min"] == 501 and rep["prefixo_max"] == 778,
      f"{rep['prefixo_min']}-{rep['prefixo_max']}")

codes = [f.codigo for f in fichas if f.codigo]
check("todos os códigos no formato NNN-NN", all(re.match(r"^\d{3}-\d{2}$", c) for c in codes))
check("códigos sem duplicata", len(codes) == len(set(codes)), f"{len(codes)} vs {len(set(codes))}")
check("nenhuma anomalia de extração (fora duplicata)",
      all(all(a == "duplicada_no_pdf" for a in f.anomalias) for f in fichas))

by = {f.codigo: f for f in fichas if f.codigo}

print("\n=== SPOT-CHECK DE FICHAS (código lido só do rótulo) ===")
f = by.get("751-01")
check("751-01 presente", f is not None)
if f:
    check("751-01 amparo = Art. 95.", f.amparo_legal == "Art. 95.", repr(f.amparo_legal))
    check("751-01 resumida correta",
          f.tipificacao_resumida == "Iniciar obra perturbe/interrompa circulação/segurança veíc/pedestre s/permissão.",
          repr(f.tipificacao_resumida))

f = by.get("504-50")  # rótulo variante "Código Enquadramento:" (sem preposição)
check("504-50 presente (rótulo variante)", f is not None)
if f:
    check("504-50 amparo = Art. 162, V.", f.amparo_legal == "Art. 162, V.", repr(f.amparo_legal))
    check("504-50 gravidade = Gravíssima", f.gravidade == "Gravíssima", repr(f.gravidade))

f = by.get("542-81")  # a deduplicada
check("542-81 presente (deduplicada)", f is not None)
if f:
    check("542-81 marcada como duplicada_no_pdf", "duplicada_no_pdf" in f.anomalias)
    check("542-81 amparo = Art. 181, V.", f.amparo_legal == "Art. 181, V.", repr(f.amparo_legal))
    check("542-81 pontuação = 7 (fallback por gravidade)", f.pontuacao == "7", repr(f.pontuacao))

print("\n=== CÓDIGO NÃO É LIDO DO CORPO DA FICHA ===")
# a ficha 542-81 cita no corpo enquadramentos específicos (536-30, 542-82...); o
# código extraído deve ser 542-81, jamais um desses.
if by.get("542-81"):
    body_codes = set(re.findall(r"\b\d{3}-\d{2}\b", by["542-81"].raw_text))
    check("542-81: corpo cita outros códigos", len(body_codes) > 1, str(body_codes))
    check("542-81: código da ficha é o do rótulo, não do corpo", by["542-81"].codigo == "542-81")

print("\n=== CHUNKS (mapeamento p/ legal_document_chunks) ===")
chunks = fichas_to_chunks(fichas)
check("um chunk por ficha", len(chunks) == len(fichas), f"{len(chunks)} vs {len(fichas)}")
check("todo chunk tem conteúdo não vazio", all(c["content"].strip() for c in chunks))
check("todo chunk tem content_hash", all(len(c["content_hash"]) == 64 for c in chunks))
check("metadata carrega infraction_code", all(c["metadata"]["infraction_code"] for c in chunks))
check("page-chrome removido do conteúdo",
      all("CONSELHO NACIONAL DE TRÂNSITO" not in c["content"] for c in chunks))

print("\n=== PARSE DETERMINÍSTICO (idempotência do parser) ===")
# reparse a partir do texto extraído deve dar o mesmo resultado
from app.services.mbft_splitter import extract_text_layout
txt = extract_text_layout(PDF)
again = validate_fichas(parse_fichas(txt))
check("reparse dá mesma contagem de fichas", again["fichas"] == rep["fichas"])
check("reparse dá mesmos códigos únicos", again["codigos_unicos"] == rep["codigos_unicos"])

# ---------------------------------------------------------------------------
# Round-trip no banco (valida as tabelas novas do B1 com dado real)
# ---------------------------------------------------------------------------
if os.environ.get("POSTGRES_HOST"):
    print("\n=== INGESTÃO NO BANCO (legal_documents / legal_document_chunks) ===")
    from app.db.session import SessionLocal
    from app.models import LegalDocument, LegalDocumentChunk
    from app.services.legal_ingest import ingest_mbft

    db = SessionLocal()
    try:
        r1 = ingest_mbft(db, PDF, version_label="2022")
        check("ingestão gravou 411 chunks", r1["chunks_gravados"] == 411, str(r1["chunks_gravados"]))

        ndoc = db.query(LegalDocument).filter(LegalDocument.document_type == "mbft").count()
        nchunk = db.query(LegalDocumentChunk).count()
        check("1 legal_document (mbft) no banco", ndoc == 1, str(ndoc))
        check("411 legal_document_chunks no banco", nchunk == 411, str(nchunk))

        # re-ingestão é idempotente (substitui, não duplica)
        r2 = ingest_mbft(db, PDF, version_label="2022")
        ndoc2 = db.query(LegalDocument).filter(LegalDocument.document_type == "mbft").count()
        nchunk2 = db.query(LegalDocumentChunk).count()
        check("re-ingestão mantém 1 documento", ndoc2 == 1, str(ndoc2))
        check("re-ingestão mantém 411 chunks (idempotente)", nchunk2 == 411, str(nchunk2))

        # a coluna SQL 'metadata' (attr chunk_metadata) persiste o código
        sample = db.query(LegalDocumentChunk).filter(
            LegalDocumentChunk.article.isnot(None)).first()
        check("chunk persistido tem metadata.infraction_code",
              bool(sample and sample.chunk_metadata and sample.chunk_metadata.get("infraction_code")))
    finally:
        db.close()
else:
    print("\n(SKIP ingestão no banco — POSTGRES_HOST não definido)")

print(f"\n=== RESULTADO B3-SPLITTER: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
