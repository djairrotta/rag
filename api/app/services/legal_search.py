"""Busca estruturada na base legal (código de enquadramento / artigo / inciso).

Complementa a busca SEMÂNTICA (RAGFlow, via serviço `rag`) com busca DETERMINÍSTICA
no Postgres, onde as fichas do MBFT (e futuramente CTB/jurisprudência) já estão com
metadados estruturados: `infraction_code`, `ctb_article`, `gravidade`, etc.

Por que existe
--------------
O código de enquadramento (ex.: "501-00") e o artigo/inciso (ex.: "Art. 181, III")
são IDENTIFICADORES EXATOS. Buscá-los por similaridade vetorial é a ferramenta errada:
o embedding de "501-00" não tem significado semântico e devolve fichas quase aleatórias
(observado em produção: 3 resultados com score idêntico). Para identificador exato,
SQL é 100% preciso, instantâneo e não gasta embedding.

Arquitetura (núcleo compartilhado)
----------------------------------
Este módulo é o "núcleo de conhecimento legal": serve tanto o app de RECURSO
(defesa: "o caso se encaixa em 'Quando NÃO Autuar'?") quanto, no futuro, o app de
GUIA POLICIAL (acusação: "como autuar?"). Por isso fica desacoplado de qualquer
produto — recebe identificadores, devolve fichas; nada de lógica de recurso aqui.

Roteamento (fallback híbrido)
-----------------------------
`buscar_contexto_legal` é o ponto de entrada. Tem código → busca exata. Tem artigo →
busca exata. SEMPRE permite complementar com a busca semântica (no chamador), sem
prejuízo: o exato dá precisão (a ficha certa), o semântico dá amplitude (teses
relacionadas). Nunca retorna vazio — se o exato não acha, sinaliza para o chamador
cair na semântica.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import LegalDocumentChunk

# --------------------------------------------------------------------------- #
# Normalização de CÓDIGO de enquadramento
# --------------------------------------------------------------------------- #
# Formato canônico do MBFT: NNN-NN (três dígitos, hífen, dois dígitos). Ex.: 501-00.
# O frontend deve mandar normalizado, mas aplicamos normalização DEFENSIVA: o OCR/LLM
# pode escorregar ("50100", "501 00", "501.00"). Custa pouco e blinda o match.
_CODE_CANON_RE = re.compile(r"^\d{3}-\d{2}$")
_CODE_DIGITS_RE = re.compile(r"\d")


def normalize_codigo(raw: str | None) -> str | None:
    """Reduz qualquer grafia de código a NNN-NN. Devolve None se não der 5 dígitos."""
    if not raw:
        return None
    digits = "".join(_CODE_DIGITS_RE.findall(raw))
    if len(digits) != 5:
        # já está canônico? aceita; senão, não é um código reconhecível
        s = raw.strip()
        return s if _CODE_CANON_RE.match(s) else None
    return f"{digits[:3]}-{digits[3:]}"


# --------------------------------------------------------------------------- #
# Normalização de ARTIGO / parágrafo / inciso
# --------------------------------------------------------------------------- #
# Dados reais no Postgres (variam!):
#   "Art. 181, III."        (com ponto final)
#   "Art. 181, II"          (sem ponto)
#   "Art. 330, § 5º e § 6º."(parágrafos)
#   "Art. 253-A"            (artigo com letra)
#   "Art. 253-A, §1º"       (artigo-letra + parágrafo)
# A query do cliente pode vir "art 181 III", "Art. 181, inciso III", "181, 3".
# Estratégia: extrair (numero_artigo, sufixo_letra, inciso_romano) de ambos os lados
# e comparar a forma canônica. Inciso arábico é convertido para romano.

_ROMAN = [
    ("M", 1000), ("CM", 900), ("D", 500), ("CD", 400), ("C", 100), ("XC", 90),
    ("L", 50), ("XL", 40), ("X", 10), ("IX", 9), ("V", 5), ("IV", 4), ("I", 1),
]


def _int_to_roman(n: int) -> str:
    out = ""
    for sym, val in _ROMAN:
        while n >= val:
            out += sym
            n -= val
    return out


# número do artigo, com sufixo opcional -A/-B (ex.: 253-A)
_ART_NUM_RE = re.compile(r"art(?:igo|\.)?\s*(\d+)(?:\s*[-–]\s*([A-Za-z]))?", re.IGNORECASE)
# inciso: romano (III) OU "inciso 3" OU ", 3"
_INCISO_ROMAN_RE = re.compile(r"\b([IVXLCDM]{1,6})\b")
_INCISO_NUM_RE = re.compile(r"inciso\s*(\d+)|,\s*(\d+)\b", re.IGNORECASE)


def parse_artigo(raw: str | None) -> dict | None:
    """Extrai {numero, letra, inciso_romano} de uma grafia livre de artigo.

    Devolve None se nem o número do artigo for encontrado.
    `inciso_romano` é None quando o usuário pediu o artigo inteiro (todos os incisos).
    """
    if not raw:
        return None
    m = _ART_NUM_RE.search(raw)
    if not m:
        return None
    numero = m.group(1)
    letra = (m.group(2) or "").upper() or None

    inciso = None
    # corta a parte do "Art. N" para procurar o inciso só no resto
    resto = raw[m.end():]
    mr = _INCISO_ROMAN_RE.search(resto)
    if mr:
        inciso = mr.group(1).upper()
    else:
        mn = _INCISO_NUM_RE.search(resto)
        if mn:
            num = mn.group(1) or mn.group(2)
            if num:
                inciso = _int_to_roman(int(num))
    return {"numero": numero, "letra": letra, "inciso": inciso}


def _canon_artigo_from_db(article_db: str | None) -> dict | None:
    """Mesma extração, aplicada ao valor armazenado (LegalDocumentChunk.article)."""
    return parse_artigo(article_db)


def _artigo_matches(query: dict, db_value: str | None, *, exigir_inciso: bool) -> bool:
    """Compara o artigo pedido com o valor do banco.

    - exigir_inciso=False  → casa se o NÚMERO (+letra) do artigo bate (todos os incisos).
    - exigir_inciso=True   → casa só se o inciso também bate (ficha específica).
    """
    cand = _canon_artigo_from_db(db_value)
    if not cand:
        return False
    if cand["numero"] != query["numero"]:
        return False
    if (query.get("letra") or None) != (cand.get("letra") or None):
        return False
    if not exigir_inciso:
        return True
    return bool(query.get("inciso")) and query["inciso"] == cand.get("inciso")


# --------------------------------------------------------------------------- #
# Buscas no Postgres
# --------------------------------------------------------------------------- #
def _chunk_to_dict(c: LegalDocumentChunk) -> dict:
    """Forma de saída padronizada — mesmo shape que o /search semântico produz,
    para o gerador consumir os dois sem ramificar."""
    meta = c.chunk_metadata or {}
    return {
        "texto": c.content,
        "codigo": meta.get("infraction_code"),
        "artigo": c.article or meta.get("ctb_article"),
        "gravidade": meta.get("gravidade"),
        "pontuacao": meta.get("pontuacao"),
        "penalidade": meta.get("penalidade"),
        "tipificacao": meta.get("tipificacao_resumida"),
        "fonte": meta.get("source"),
        "origem": "estruturada",   # marca a procedência (vs. "semantica")
        "score": 1.0,              # match exato = confiança máxima
    }


def buscar_por_codigo(db: Session, codigos: list[str]) -> list[dict]:
    """Busca exata por um ou mais códigos de enquadramento. Multi-infração OK."""
    alvos = [c for c in (normalize_codigo(x) for x in codigos) if c]
    if not alvos:
        return []
    # filtro no JSONB: metadata->>'infraction_code' IN (...)
    rows = db.execute(
        select(LegalDocumentChunk).where(
            LegalDocumentChunk.chunk_metadata["infraction_code"].astext.in_(alvos)
        )
    ).scalars().all()
    return [_chunk_to_dict(r) for r in rows]


def buscar_por_artigo(db: Session, artigo_raw: str, inciso: str | None = None) -> list[dict]:
    """Busca por artigo do CTB.

    Regra de produto: artigo isolado ("Art. 181") devolve TODOS os incisos;
    inciso específico devolve a ficha detalhada daquele inciso.

    `inciso` pode vir EMBUTIDO na string ("Art. 181, III") ou SEPARADO (o frontend
    controlado manda o artigo num campo e o inciso noutro — ex.: artigo="Art. 181",
    inciso="III" ou "3"). Quando separado, tem precedência sobre o que vier na string.
    """
    q = parse_artigo(artigo_raw)
    if not q:
        return []
    # inciso passado à parte (campo do frontend) sobrepõe o extraído da string
    if inciso:
        inc = inciso.strip().upper()
        if inc.isdigit():
            inc = _int_to_roman(int(inc))
        q["inciso"] = inc
    exigir_inciso = bool(q.get("inciso"))

    # Pré-filtro barato no banco: artigo cujo texto do campo contém o número.
    # (Filtro fino — letra/inciso/romano — é feito em Python, onde a normalização mora.)
    like = f"%{q['numero']}%"
    rows = db.execute(
        select(LegalDocumentChunk).where(LegalDocumentChunk.article.ilike(like))
    ).scalars().all()

    out = [r for r in rows if _artigo_matches(q, r.article, exigir_inciso=exigir_inciso)]

    # Fallback dentro da própria busca por artigo: pediu inciso específico mas não há
    # ficha exata? devolve todos os incisos do artigo (melhor que vazio).
    if exigir_inciso and not out:
        out = [r for r in rows if _artigo_matches(q, r.article, exigir_inciso=False)]

    return [_chunk_to_dict(r) for r in out]


# --------------------------------------------------------------------------- #
# Roteador (ponto de entrada do núcleo)
# --------------------------------------------------------------------------- #
def buscar_contexto_legal(
    db: Session,
    *,
    codigos: list[str] | None = None,
    artigo: str | None = None,
    inciso: str | None = None,
) -> dict:
    """Roteia para a busca estruturada conforme os identificadores disponíveis.

    Retorna {"fichas": [...], "encontrou_exato": bool}. O chamador decide se
    complementa com a busca SEMÂNTICA (sempre recomendável) — este módulo nunca
    fala com o RAGFlow; só com o Postgres.

    `inciso` é aceito separado do artigo (frontend controlado manda em campos distintos).
    """
    fichas: list[dict] = []
    vistos: set[str] = set()

    def _add(items: list[dict]) -> None:
        for it in items:
            key = (it.get("codigo") or "") + "|" + (it.get("artigo") or "") + "|" + it["texto"][:40]
            if key not in vistos:
                vistos.add(key)
                fichas.append(it)

    if codigos:
        _add(buscar_por_codigo(db, codigos))
    if artigo:
        _add(buscar_por_artigo(db, artigo, inciso=inciso))

    return {"fichas": fichas, "encontrou_exato": bool(fichas)}
