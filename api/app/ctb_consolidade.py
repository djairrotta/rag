"""Pipeline de consolidação e ingestão do CTB no dataset `seguramultas_ctb`.

Opção B — Planalto como base, livros enriquecem
-----------------------------------------------
Para cada artigo do CTB (1..341), monta UM chunk consolidado contendo, nesta ordem:
  1. TEXTO OFICIAL DA LEI (Planalto — completo, atual, domínio público)
  2. COMENTÁRIO doutrinário (Celso, casado por número — quando existe)
  3. (referências do 360, quando agregam)
  4. RESOLUÇÕES CONTRAN citadas (estruturadas no metadata — munição de defesa)
Cada fonte carrega sua ficha bibliográfica para citação (LDA art. 46).

Além dos artigos, ingere a RESOLUÇÃO 432/2013 inteira (chunks por artigo dela) — base
das teses de defesa em embriaguez (art. 165/165-A/306).

Uso (no container da VPS, igual ao MBFT):
    python scripts/ingest_ctb.py --planalto /caminho/ctb_planalto.html \
        --celso /caminho/celso.pdf --leg360 /caminho/360.pdf \
        --res432 /caminho/resolucao_432_2013.txt \
        --dataset seguramultas_ctb --write-db --write-rag
"""
from __future__ import annotations

import re

from app.services.ctb_parser import ArtigoChunk, BIBLIO, parse_livro
from app.services.ctb_planalto_parser import parse_planalto


def _num_int(num: str) -> tuple[int, str]:
    b = re.match(r"(\d+)", num)
    return (int(b.group(1)) if b else 9999, num)


def _index_por_artigo(chunks: list[ArtigoChunk]) -> dict[str, ArtigoChunk]:
    """Indexa chunks por número de artigo (último vence — já vêm consolidados)."""
    return {c.art_numero: c for c in chunks}


def consolidar_ctb(
    *,
    planalto_html: str | None = None,
    celso_pdf: str | None = None,
    leg360_pdf: str | None = None,
) -> list[dict]:
    """Casa as 3 fontes por número de artigo e devolve chunks consolidados (dicts prontos
    para Postgres/RAGFlow). Planalto é a base; Celso e 360 enriquecem.

    Cada chunk consolidado tem:
      content: texto montado (LEI + COMENTÁRIO)
      metadata: art_numero, ctb_article, fontes_usadas, resolucoes_citadas,
                citacao_lei, citacao_doutrina, alteracoes_legais, source_type
    """
    base = _index_por_artigo(parse_planalto(planalto_html)) if planalto_html else {}
    celso = _index_por_artigo(parse_livro(celso_pdf, "ctb_comentado_celso")) if celso_pdf else {}
    leg360 = _index_por_artigo(parse_livro(leg360_pdf, "ctb_360")) if leg360_pdf else {}

    # universo de artigos = união das três fontes (Planalto cobre quase tudo)
    todos = sorted(set(base) | set(celso) | set(leg360), key=_num_int)

    consolidados: list[dict] = []
    for num in todos:
        b = base.get(num)
        c = celso.get(num)
        g = leg360.get(num)

        partes: list[str] = []
        fontes_usadas: list[str] = []
        resolucoes: list[dict] = []
        citacao_lei = None
        citacao_doutrina = None
        alteracoes: list[str] = []

        # 1) LEI — prioriza Planalto (oficial); cai no 360 e depois no Celso
        fonte_lei = b or g or c
        if fonte_lei is not None:
            partes.append(f"LEI — {fonte_lei.metadata['ctb_article']} (CTB):\n{fonte_lei.texto}")
            fontes_usadas.append(fonte_lei.fonte)
            citacao_lei = fonte_lei.metadata.get("citacao")
            alteracoes = fonte_lei.metadata.get("alteracoes_legais", []) or []

        # 2) COMENTÁRIO — do Celso, quando tem doutrina de fato
        if c is not None and c.tem_comentario:
            partes.append(
                f"COMENTÁRIO DOUTRINÁRIO ({c.metadata['autor']}):\n{c.texto}"
            )
            if "ctb_comentado_celso" not in fontes_usadas:
                fontes_usadas.append("ctb_comentado_celso")
            citacao_doutrina = c.metadata.get("citacao")

        # 3) RESOLUÇÕES citadas — agrega de todas as fontes (dedup por número)
        vistos = set()
        for src in (c, g, b):
            if src is None:
                continue
            for r in src.metadata.get("resolucoes_citadas", []) or []:
                if r["numero"] not in vistos:
                    vistos.add(r["numero"])
                    resolucoes.append(r)

        content = "\n\n".join(partes).strip()
        if not content:
            continue

        # source_type: doutrina se há comentário; senão lei
        source_type = "doutrina" if (c is not None and c.tem_comentario) else "lei"

        consolidados.append({
            "article": f"Art. {num}",
            "content": content,
            "metadata": {
                "art_numero": num,
                "ctb_article": f"Art. {num}",
                "dataset": "ctb",
                "source": "ctb_consolidado",
                "source_type": source_type,
                "fontes_usadas": fontes_usadas,
                "resolucoes_citadas": resolucoes,
                "alteracoes_legais": alteracoes,
                "tem_comentario": bool(c is not None and c.tem_comentario),
                "citacao_lei": citacao_lei,
                "citacao_doutrina": citacao_doutrina,
                "allow_verbatim": True,
            },
        })
    return consolidados


def parse_resolucao_432(txt_path: str) -> list[dict]:
    """Parseia a Resolução 432/2013 (texto oficial) em chunks por artigo dela."""
    biblio = BIBLIO["resolucao_432"]
    texto = open(txt_path, encoding="utf-8").read()

    art_re = re.compile(r"(?m)^\s*Art\.\s*(\d+)[º°]?\.?")
    matches = list(art_re.finditer(texto))
    # cabeçalho (antes do Art. 1º): ementa + considerandos → vira um chunk "ementa"
    out: list[dict] = []
    if matches:
        cab = texto[:matches[0].start()].strip()
        if len(cab) > 50:
            out.append({
                "article": "Res. 432/2013 — Ementa",
                "content": cab,
                "metadata": {
                    "art_numero": "0", "dataset": "ctb", "source": "resolucao_432",
                    "source_type": "lei", "resolucao": "432/2013",
                    "citacao": f'{biblio["obra"]}. {biblio["editora"]}, {biblio["ano"]}.',
                    "allow_verbatim": True,
                },
            })
    for i, m in enumerate(matches):
        ini = m.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        bloco = texto[ini:fim].strip()
        if len(bloco) < 15:
            continue
        num = m.group(1)
        out.append({
            "article": f"Res. 432/2013, Art. {num}",
            "content": bloco,
            "metadata": {
                "art_numero": num,
                "dataset": "ctb",
                "source": "resolucao_432",
                "source_type": "lei",
                "resolucao": "432/2013",
                "ctb_artigos_relacionados": ["165", "165-A", "276", "277", "306"],
                "citacao": (
                    f'{biblio["obra"]}, art. {num}. {biblio["editora"]}, {biblio["ano"]}.'
                ),
                "allow_verbatim": True,
            },
        })
    return out


def relatorio(consolidados: list[dict], res432: list[dict]) -> dict:
    com_doutrina = sum(1 for c in consolidados if c["metadata"]["tem_comentario"])
    com_resolucao = sum(1 for c in consolidados if c["metadata"]["resolucoes_citadas"])
    return {
        "artigos_ctb": len(consolidados),
        "com_comentario_doutrina": com_doutrina,
        "com_resolucoes_citadas": com_resolucao,
        "chunks_resolucao_432": len(res432),
        "total_chunks": len(consolidados) + len(res432),
    }
