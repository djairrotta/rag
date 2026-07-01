#!/usr/bin/env python3
"""CLI de ingestão do CTB consolidado (Planalto + Celso + 360 + Resolução 432).

Casa a lei oficial (Planalto) com a doutrina (Celso) e os extras (360), por número de
artigo, e inclui a Resolução CONTRAN 432/2013 (embutida no código). Baixa as fontes do
Planalto (oficial) e do GitHub (api/data). Mesmo fluxo do MBFT.

    # validar (não toca o banco):
    python scripts/ingest_ctb.py
    # gravar no Postgres + indexar no RAGFlow (dataset seguramultas_ctb):
    python scripts/ingest_ctb.py --write-db --push-ragflow
"""
import argparse
import glob
import json
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pasta de dados local (fallback dev)
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_PLANALTO_URL = "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"

# Os arquivos-fonte estão versionados no repositório GitHub em api/data/.
# O container os baixa via raw (comprovado acessível). Base do raw:
_GH_RAW = "https://raw.githubusercontent.com/djairrotta/rag/main/api/data"
_GH_CELSO = f"{_GH_RAW}/celso_comentado.pdf"
_GH_360 = f"{_GH_RAW}/legislacao_360.pdf"
_GH_PLANALTO = f"{_GH_RAW}/ctb_planalto.html"


def _ctx():
    c = ssl.create_default_context()
    c.check_hostname = False
    c.verify_mode = ssl.CERT_NONE
    return c


def _baixar_url(url: str, destino: str, rotulo: str, minimo: int = 1000) -> str | None:
    """Baixa uma URL para um arquivo local e devolve o caminho (ou None se falhar)."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data = urllib.request.urlopen(req, timeout=40, context=_ctx()).read()
        if len(data) < minimo:
            print(f"[{rotulo}] download muito curto ({len(data)} bytes); ignorando.")
            return None
        open(destino, "wb").write(data)
        print(f"[{rotulo}] baixado ({len(data)} bytes) de {url.split('/')[-1]}")
        return destino
    except Exception as e:
        print(f"[{rotulo}] falha ao baixar: {type(e).__name__}: {str(e)[:100]}")
        return None


def _achar_local(*padroes: str) -> str | None:
    for pad in padroes:
        p = os.path.join(_DATA, pad)
        if os.path.exists(p):
            return p
        for achado in glob.glob(os.path.join(_DATA, "**", pad), recursive=True):
            return achado
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingestão do CTB consolidado (lei + doutrina + resoluções).")
    ap.add_argument("--planalto", default=os.environ.get("CTB_PLANALTO_HTML"),
                    help="HTML do CTB. Se omitido: fonte oficial do Planalto; fallback GitHub.")
    ap.add_argument("--celso", default=os.environ.get("CTB_CELSO_PDF"),
                    help="PDF comentado (Celso). Se omitido: baixa do GitHub (api/data).")
    ap.add_argument("--leg360", default=os.environ.get("CTB_360_PDF"),
                    help="PDF Legislação 360. Se omitido: baixa do GitHub (api/data).")
    ap.add_argument("--version", default="2024", help="version_label do documento (default 2024).")
    ap.add_argument("--dataset", default="seguramultas_ctb", help="nome do dataset no RAGFlow.")
    ap.add_argument("--write-db", action="store_true",
                    help="Grava em legal_documents/legal_document_chunks (usa POSTGRES_*).")
    ap.add_argument("--push-ragflow", action="store_true",
                    help="Empurra os chunks pro RAGFlow e grava os ids (requer --write-db + RAGFLOW_*).")
    args = ap.parse_args()

    # 1) PLANALTO — fonte oficial (mais atual); se falhar, versão do GitHub; depois local
    if not args.planalto:
        args.planalto = (
            _baixar_url(_PLANALTO_URL, "/tmp/ctb_planalto.html", "planalto", minimo=200_000)
            or _baixar_url(_GH_PLANALTO, "/tmp/ctb_planalto.html", "planalto-gh", minimo=200_000)
            or _achar_local("ctb_planalto.html", "*9503*.htm*")
        )
    # 2) CELSO — do GitHub; fallback local
    if not args.celso:
        args.celso = (
            _baixar_url(_GH_CELSO, "/tmp/celso_comentado.pdf", "celso", minimo=100_000)
            or _achar_local("celso_comentado.pdf", "*Celso*.pdf", "*comentado*.pdf")
        )
    # 3) 360 — do GitHub; fallback local
    if not args.leg360:
        args.leg360 = (
            _baixar_url(_GH_360, "/tmp/legislacao_360.pdf", "leg360", minimo=100_000)
            or _achar_local("legislacao_360.pdf", "CTB_*.pdf", "*360*.pdf")
        )

    print("\n=== FONTES LOCALIZADAS ===")
    for rot, cam in [("planalto", args.planalto), ("celso", args.celso), ("leg360", args.leg360)]:
        print(f"  {rot}: {cam or '(ausente)'}")
    print("  res432: embutida no código (ctb_consolidate)")

    if not args.planalto and not args.celso and not args.leg360:
        ap.error("nenhuma fonte de lei/doutrina disponível (Planalto, Celso ou 360).")
    for rotulo, caminho in [("planalto", args.planalto), ("celso", args.celso), ("leg360", args.leg360)]:
        if caminho and not os.path.exists(caminho):
            ap.error(f"arquivo não encontrado ({rotulo}): {caminho}")

    # relatório de consolidação (sem tocar o banco)
    from app.services.ctb_consolidate import (  # noqa: E402
        consolidar_ctb, parse_resolucao_432_embutida, relatorio,
    )
    planalto_html = None
    if args.planalto:
        planalto_html = open(args.planalto, "rb").read().decode("latin-1", errors="ignore")
    consolidados = consolidar_ctb(
        planalto_html=planalto_html, celso_pdf=args.celso, leg360_pdf=args.leg360,
    )
    res432 = parse_resolucao_432_embutida()   # texto embutido no código
    print("\n=== RELATÓRIO DA CONSOLIDAÇÃO ===")
    print(json.dumps(relatorio(consolidados, res432), ensure_ascii=False, indent=2))

    if args.write_db:
        from app.db.session import SessionLocal
        from app.services.legal_ingest import ingest_ctb
        db = SessionLocal()
        try:
            db_report = ingest_ctb(
                db,
                planalto_html_path=args.planalto,
                celso_pdf=args.celso,
                leg360_pdf=args.leg360,
                version_label=args.version,
                dataset_name=args.dataset,
                push_ragflow=args.push_ragflow,
            )
        finally:
            db.close()
        print("\n=== INGESTÃO NO BANCO ===")
        print(json.dumps(db_report, ensure_ascii=False, indent=2))
    elif args.push_ragflow:
        ap.error("--push-ragflow exige --write-db (o push lê os chunks do Postgres).")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
