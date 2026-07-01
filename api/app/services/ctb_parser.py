"""Parser dos livros de CTB (comentado + lei) em chunks por artigo.

Produz chunks no MESMO shape do MBFT (legal_document_chunks), prontos para Postgres
e RAGFlow, no dataset `seguramultas_ctb`. Cada chunk é UM ARTIGO inteiro (lei +
comentário + remissão, como o autor escreveu — preserva o contexto para o RAG e para
a geração de recurso).

Citação juridicamente segura (LDA art. 46, III)
-----------------------------------------------
Cada chunk carrega os metadados bibliográficos COMPLETOS da fonte (obra, autor, editora,
local, ano, ISBN, página). O gerador de recurso pode então citar sempre a fonte exata:
"Conforme Celso Luiz Martins (Código Brasileiro de Trânsito Comentado, Rio de Janeiro:
Elsevier/Campus, 2012, p. 19), ...". A flag `allow_verbatim` controla reprodução literal
por decisão do titular do escritório.

Duas fontes, um dataset
-----------------------
- `ctb_comentado_celso` → doutrina (comentário + remissões a resoluções CONTRAN)
- `ctb_360`             → letra da lei com destaques de estudo
Ambas no dataset `seguramultas_ctb`; `fonte` no metadata distingue a origem.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import fitz  # pymupdf


# --------------------------------------------------------------------------- #
# Fichas bibliográficas (para citação) — extraídas das fichas catalográficas
# --------------------------------------------------------------------------- #
BIBLIO = {
    "ctb_comentado_celso": {
        "obra": "Código Brasileiro de Trânsito Comentado",
        "autor": "Celso Luiz Martins",
        "editora": "Elsevier/Campus",
        "local": "Rio de Janeiro",
        "ano": "2012",
        "edicao": "1. ed.",
        "serie": "Provas e Concursos",
        "isbn": "978-85-352-4816-6",
        "source_type": "doutrina",
    },
    "ctb_360": {
        "obra": "Código de Trânsito Brasileiro — Caderno de Estudos (Legislação 360)",
        "autor": "Legislação 360",
        "editora": "Legislação 360",
        "local": "Brasil",
        "ano": "2024",
        "edicao": "ed. 2024.1 (25.01.2024)",
        "serie": "Legislação 360",
        "isbn": "",
        "source_type": "lei",
    },
    "ctb_planalto": {
        "obra": "Lei nº 9.503/1997 — Código de Trânsito Brasileiro (texto compilado)",
        "autor": "Brasil",
        "editora": "Presidência da República",
        "local": "Brasília",
        "ano": "2024",
        "edicao": "texto compilado (atualizado até Lei nº 14.861/2024)",
        "serie": "",
        "isbn": "",
        "source_type": "lei",   # domínio público (LDA art. 8º)
    },
    "resolucao_432": {
        "obra": "Resolução CONTRAN nº 432, de 23 de janeiro de 2013",
        "autor": "CONTRAN — Conselho Nacional de Trânsito",
        "editora": "Ministério dos Transportes",
        "local": "Brasília",
        "ano": "2013",
        "edicao": "publicada no DOU de 29.01.2013",
        "serie": "",
        "isbn": "",
        "source_type": "lei",   # ato normativo público (LDA art. 8º)
    },
}


@dataclass
class ArtigoChunk:
    art_numero: str            # "12", "253-A"
    fonte: str                 # chave em BIBLIO
    pagina: int
    texto: str                 # artigo inteiro (lei + comentário + remissão)
    tem_comentario: bool
    tem_remissao: bool
    metadata: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Extração de texto com rastreamento de página
# --------------------------------------------------------------------------- #
def _texto_com_paginas(pdf_path: str) -> tuple[str, list[tuple[int, int]]]:
    """Devolve (texto_contínuo, [(offset_inicial, num_pagina), ...])."""
    doc = fitz.open(pdf_path)
    texto = ""
    offsets: list[tuple[int, int]] = []
    for p in range(len(doc)):
        offsets.append((len(texto), p + 1))   # página 1-indexed
        texto += doc[p].get_text() + "\n"
    doc.close()
    return texto, offsets


def _pagina_do_offset(offset: int, offsets: list[tuple[int, int]]) -> int:
    pag = 1
    for off, p in offsets:
        if off <= offset:
            pag = p
        else:
            break
    return pag


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
# Início de artigo no começo de linha. Os dois livros usam formatos diferentes:
#   comentado (Celso): "Art. 12." / "Art. 253-A."  (termina com ponto)
#   360 (lei):         "Art. 1º"  / "Art. 7º-A"     (termina com º, depois quebra de linha)
# Regex unificado: "Art." + número (+ sufixo-letra opcional) + (º|.) — exige que logo após
# o número venha um delimitador de fim de cabeçalho (ponto, º, ou fim de linha), para não
# casar com citações de artigo no meio de frases ("...nos termos do art. 5 acima...").
_ART_RE = re.compile(
    r"(?m)^\s*Art\.\s*(\d+)(?:\s*-\s*([A-Z]))?\s*(?:[º°]\b|\.|\s*$)"
)

# Resoluções CONTRAN citadas (remissões) — munição de defesa nos recursos.
# Captura "Resolução nº 244, de 22 de junho de 2007".
_RESOLUCAO_RE = re.compile(
    r"Resolu[çc][ãa]o\s+n[oº°]?\s*\.?\s*(\d+)(?:[,/]?\s*de\s+(\d{1,2}\s+de\s+\w+\s+de\s+\d{4}))?",
    re.IGNORECASE,
)


def _extrai_resolucoes(texto: str) -> list[dict]:
    """Extrai as resoluções CONTRAN citadas no artigo, deduplicadas por número.
    Vira campo estruturado no metadata para o gerador cruzar/citar nos recursos."""
    achados: dict[str, dict] = {}
    for m in _RESOLUCAO_RE.finditer(texto):
        num = m.group(1)
        data = (m.group(2) or "").strip()
        if num not in achados or (data and not achados[num].get("data")):
            achados[num] = {"numero": num, "data": data, "orgao": "CONTRAN"}
    return list(achados.values())


def _limpa(texto: str) -> str:
    """Normaliza espaços e remove números de página soltos no meio do texto."""
    # remove linhas que são só número (página) ou espaços
    linhas = [ln for ln in texto.split("\n") if ln.strip() and not re.fullmatch(r"\s*\d+\s*", ln)]
    t = "\n".join(linhas)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def parse_livro(pdf_path: str, fonte: str) -> list[ArtigoChunk]:
    """Parseia um PDF de CTB em chunks por artigo. `fonte` deve existir em BIBLIO."""
    if fonte not in BIBLIO:
        raise ValueError(f"fonte desconhecida: {fonte}")
    biblio = BIBLIO[fonte]

    texto, offsets = _texto_com_paginas(pdf_path)
    matches = list(_ART_RE.finditer(texto))

    chunks: list[ArtigoChunk] = []
    for i, m in enumerate(matches):
        ini = m.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        bloco = _limpa(texto[ini:fim])
        if len(bloco) < 30:   # lixo/fragmento
            continue

        num = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        pagina = _pagina_do_offset(ini, offsets)
        tem_com = bool(re.search(r"COMENT[ÁA]RIO", bloco, re.I))
        tem_rem = bool(re.search(r"Remiss[ãa]o", bloco, re.I))
        resolucoes = _extrai_resolucoes(bloco)

        # source_type por chunk: se a fonte é doutrina mas este artigo não tem comentário,
        # ele é só lei reproduzida → ainda assim cita a obra (é a transcrição do autor).
        source_type = biblio["source_type"]
        if biblio["source_type"] == "doutrina" and not tem_com:
            source_type = "lei"  # trecho de lei dentro da obra de doutrina

        meta = {
            "art_numero": num,
            "ctb_article": f"Art. {num}",
            "fonte": fonte,
            "source_type": source_type,
            "tem_comentario": tem_com,
            "tem_remissao": tem_rem,
            "resolucoes_citadas": resolucoes,   # munição de defesa (CONTRAN)
            "pagina": pagina,
            "allow_verbatim": True,   # decisão do titular do escritório (citar sempre a fonte)
            # ficha bibliográfica completa (para citação LDA art. 46)
            "obra": biblio["obra"],
            "autor": biblio["autor"],
            "editora": biblio["editora"],
            "local": biblio["local"],
            "ano": biblio["ano"],
            "edicao": biblio["edicao"],
            "isbn": biblio["isbn"],
            "citacao": (
                f'{biblio["autor"]}. {biblio["obra"]}. {biblio["local"]}: '
                f'{biblio["editora"]}, {biblio["ano"]}, p. {pagina}.'
            ),
        }
        chunks.append(ArtigoChunk(
            art_numero=num, fonte=fonte, pagina=pagina, texto=bloco,
            tem_comentario=tem_com, tem_remissao=tem_rem, metadata=meta,
        ))

    # Consolida duplicatas: o mesmo nº de artigo pode casar várias vezes (citações,
    # repetições de layout). Mantém, por número, o bloco MAIS LONGO — que é o artigo
    # real com seu conteúdo, não uma menção curta. Reordena por número do artigo.
    melhor: dict[str, ArtigoChunk] = {}
    for c in chunks:
        atual = melhor.get(c.art_numero)
        if atual is None or len(c.texto) > len(atual.texto):
            melhor[c.art_numero] = c

    def _ordem(num: str) -> tuple[int, str]:
        base = re.match(r"(\d+)", num)
        return (int(base.group(1)) if base else 9999, num)

    return [melhor[k] for k in sorted(melhor, key=_ordem)]


def relatorio(chunks: list[ArtigoChunk]) -> dict:
    """Métricas de sanidade do parsing."""
    if not chunks:
        return {"chunks": 0}
    nums = [c.art_numero for c in chunks]
    return {
        "chunks": len(chunks),
        "artigos_unicos": len(set(nums)),
        "com_comentario": sum(c.tem_comentario for c in chunks),
        "com_remissao": sum(c.tem_remissao for c in chunks),
        "pagina_min": min(c.pagina for c in chunks),
        "pagina_max": max(c.pagina for c in chunks),
        "chars_total": sum(len(c.texto) for c in chunks),
        "chars_medio_por_art": sum(len(c.texto) for c in chunks) // len(chunks),
    }
