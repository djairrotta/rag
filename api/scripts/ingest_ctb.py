#!/usr/bin/env python3
"""CLI de ingestão do CTB consolidado (Planalto + Celso + 360 + Resolução 432).

Casa a lei oficial (Planalto) com a doutrina (Celso) e os extras (360), por número de
artigo, e inclui a Resolução CONTRAN 432/2013. Mesmo fluxo do MBFT.

Exemplos:
    # validar + relatório (não toca o banco):
    python scripts/ingest_ctb.py \
        --planalto /caminho/ctb_planalto.html \
        --celso /caminho/Codigo-...-Celso.pdf \
        --leg360 /caminho/CTB_-_2024.pdf \
        --res432 /caminho/resolucao_432_2013.txt

    # gravar no Postgres:
    POSTGRES_HOST=... python scripts/ingest_ctb.py ... --write-db

    # gravar e empurrar pro RAGFlow (dataset seguramultas_ctb):
    POSTGRES_HOST=... RAGFLOW_BASE_URL=... RAGFLOW_API_KEY=... \
        python scripts/ingest_ctb.py ... --write-db --push-ragflow
"""
import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# pasta de dados versionada no repo (Forma A). Os arquivos foram enviados em api/data/.
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_PLANALTO_URL = "https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm"


def _achar(*padroes: str) -> str | None:
    """Acha o primeiro arquivo que casa com um dos padrões, procurando em api/data/
    e subpastas. Robusto a variações de nome do upload."""
    for pad in padroes:
        # match direto
        p = os.path.join(_DATA, pad)
        if os.path.exists(p):
            return p
        # match por glob (inclui subpastas como data/ctb/)
        for achado in glob.glob(os.path.join(_DATA, "**", pad), recursive=True):
            return achado
    return None


def _baixar_planalto_atual(fallback_path: str | None) -> str | None:
    """Baixa o CTB atual do Planalto; cai no HTML commitado se a rede falhar."""
    try:
        import ssl
        import urllib.request
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(_PLANALTO_URL, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=30, context=ctx).read()
        if len(html) > 200_000 and b"253-A" in html:
            destino = "/tmp/ctb_planalto_atual.html"
            open(destino, "wb").write(html)
            print(f"[planalto] baixado da fonte oficial ({len(html)} bytes).")
            return destino
        print("[planalto] download suspeito (curto/incompleto); usando HTML commitado.")
    except Exception as e:
        print(f"[planalto] falha no download ({type(e).__name__}); usando HTML commitado.")
    return fallback_path


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingestão do CTB consolidado (lei + doutrina + resoluções).")
    ap.add_argument("--planalto", default=os.environ.get("CTB_PLANALTO_HTML"),
                    help="Caminho do HTML do CTB (Planalto). Se omitido, procura em api/data/.")
    ap.add_argument("--celso", default=os.environ.get("CTB_CELSO_PDF"),
                    help="Caminho do PDF comentado (Celso). Se omitido, procura em api/data/.")
    ap.add_argument("--leg360", default=os.environ.get("CTB_360_PDF"),
                    help="Caminho do PDF Legislação 360. Se omitido, procura em api/data/.")
    ap.add_argument("--res432", default=os.environ.get("CTB_RES432_TXT"),
                    help="Caminho do TXT da Resolução 432/2013. Se omitido, procura em api/data/.")
    ap.add_argument("--baixar-planalto", action="store_true",
                    help="Baixa o CTB atual do Planalto na hora (fallback: HTML commitado).")
    ap.add_argument("--version", default="2024", help="version_label do documento (default 2024).")
    ap.add_argument("--dataset", default="seguramultas_ctb", help="nome do dataset no RAGFlow.")
    ap.add_argument("--write-db", action="store_true",
                    help="Grava em legal_documents/legal_document_chunks (usa POSTGRES_*).")
    ap.add_argument("--push-ragflow", action="store_true",
                    help="Empurra os chunks pro RAGFlow e grava os ids (requer --write-db + RAGFLOW_*).")
    args = ap.parse_args()

    # auto-descoberta dos arquivos em api/data/ (tenta os nomes sugeridos E os originais)
    if not args.planalto:
        args.planalto = _achar("ctb_planalto.html", "*planalto*.htm*", "l9503*.htm*", "*9503*.htm*")
    if not args.celso:
        args.celso = _achar("celso_comentado.pdf", "*Celso*.pdf", "*comentado*.pdf", "*Martins*.pdf")
    if not args.leg360:
        args.leg360 = _achar("legislacao_360.pdf", "CTB_*.pdf", "*360*.pdf", "*2024*.pdf")
    if not args.res432:
        args.res432 = _achar("resolucao_432_2013.txt", "*432*.txt", "*resolucao*.txt")

    if args.baixar_planalto:
        args.planalto = _baixar_planalto_atual(args.planalto)

    print("=== ARQUIVOS LOCALIZADOS ===")
    for rot, cam in [("planalto", args.planalto), ("celso", args.celso),
                     ("leg360", args.leg360), ("res432", args.res432)]:
        print(f"  {rot}: {cam or '(não encontrado)'}")

    if not any([args.planalto, args.celso, args.leg360, args.res432]):
        ap.error("nenhuma fonte encontrada em api/data/ nem informada por argumento.")
    for rotulo, caminho in [("planalto", args.planalto), ("celso", args.celso),
                            ("leg360", args.leg360), ("res432", args.res432)]:
        if caminho and not os.path.exists(caminho):
            ap.error(f"arquivo não encontrado ({rotulo}): {caminho}")

    # relatório de consolidação (sem tocar o banco)
    from app.services.ctb_consolidate import (  # noqa: E402
        consolidar_ctb, parse_resolucao_432, relatorio,
    )
    planalto_html = None
    if args.planalto:
        planalto_html = open(args.planalto, "rb").read().decode("latin-1", errors="ignore")
    consolidados = consolidar_ctb(
        planalto_html=planalto_html, celso_pdf=args.celso, leg360_pdf=args.leg360,
    )
    res432 = parse_resolucao_432(args.res432) if args.res432 else []
    print("=== RELATÓRIO DA CONSOLIDAÇÃO ===")
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
                res432_txt=args.res432,
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
