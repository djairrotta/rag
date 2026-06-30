"""Splitter das Fichas de Fiscalização do MBFT (blueprint B3, item 9 do handoff).

O MBFT 2022 é um PDF de ~830 páginas com ~411 fichas, uma por enquadramento.
Cada ficha tem cabeçalho fixo "FICHA DE FISCALIZAÇÃO" e campos rotulados.

Regras-chave (validadas contra o documento real):
1. **Corte por ficha** no cabeçalho "FICHA DE FISCALIZAÇÃO".
2. **Código lido SÓ do rótulo "Código (do/de) Enquadramento:"** — nunca de um
   `NNN-NN` solto, porque o corpo da ficha cita enquadramentos específicos
   relacionados (ex.: "utilizar enquadramento específico: 542-82, art. 181, V").
   O rótulo aparece em 3 variantes no MBFT: "Código do Enquadramento:",
   "Código de Enquadramento:" e "Código Enquadramento:".
3. **Deduplicação por código**: o PDF reimprime algumas fichas (mesmo código em
   segmentos consecutivos, ex.: 542-81); mantém-se uma só.
4. **Validação de formato** `NNN-NN` e relatório de anomalias.
5. Limpeza de page-chrome (rodapé do CNT/MBFT, form-feeds, números de página).

Extração de texto via `pdftotext -layout` (poppler) — preserva as colunas, o que
torna a leitura do código (coluna direita do cabeçalho) determinística. Para
testar/reaproveitar, `parse_fichas(text)` aceita texto já extraído.
"""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from dataclasses import dataclass, field, asdict

# ---------------------------------------------------------------------------
# Marcadores e rótulos
# ---------------------------------------------------------------------------
BANNER_RE = re.compile(r"FICHA DE FISCALIZA\w*", re.IGNORECASE)
# rótulo do código nas 3 variantes ("do" | "de" | sem preposição)
CODIGO_LABEL_RE = re.compile(r"C[oó]digo\s+(?:d[eo]\s+)?Enquadramento\s*:", re.IGNORECASE)
AMPARO_LABEL_RE = re.compile(r"Amparo\s+Legal\s*:", re.IGNORECASE)
TIPIF_RESUMIDA_LABEL_RE = re.compile(r"Tipifica[çc][aã]o\s+Resumida\s*:", re.IGNORECASE)
TIPIF_ENQ_LABEL_RE = re.compile(r"Tipifica[çc][aã]o\s+do\s+Enquadramento\s*:", re.IGNORECASE)
GRAVIDADE_LABEL_RE = re.compile(r"Gravidade\s*:", re.IGNORECASE)
INFRATOR_LABEL_RE = re.compile(r"Infrator\s*:", re.IGNORECASE)
PONTUACAO_LABEL_RE = re.compile(r"Pontua[çc][aã]o\s*:", re.IGNORECASE)
CONSTATACAO_LABEL_RE = re.compile(r"Constata[çc][aã]o\s+da\s+Infra[çc][aã]o\s*:", re.IGNORECASE)

CODE_RE = re.compile(r"\b(\d{3}-\d{2})\b")
CODE_FULL_RE = re.compile(r"^\d{3}-\d{2}$")

GRAVIDADE_VALUES = ("Gravíssima", "Gravissima", "Grave", "Média", "Media", "Leve", "Não aplicável", "Nao aplicavel")

# linhas de rodapé/cabeçalho de página a remover do conteúdo da ficha
PAGE_CHROME_RE = re.compile(
    r"^\s*(?:CONSELHO NACIONAL DE TR[ÂA]NSITO"
    r"|MANUAL BRASILEIRO DE FISCALIZA[ÇC][ÃA]O DE TR[ÂA]NSITO.*"
    r"|P[áa]gina\s+\d+.*"
    r"|\d{1,4})\s*$",
    re.IGNORECASE,
)


@dataclass
class Ficha:
    """Uma Ficha de Fiscalização do MBFT, com os campos do cabeçalho + texto íntegro."""

    codigo: str | None
    amparo_legal: str | None
    tipificacao_resumida: str | None
    tipificacao_enquadramento: str | None = None
    gravidade: str | None = None
    penalidade: str | None = None
    pontuacao: str | None = None
    constatacao: str | None = None
    raw_text: str = ""
    anomalias: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Extração de texto
# ---------------------------------------------------------------------------
def extract_text_layout(pdf_path: str) -> str:
    """Extrai o texto preservando layout via `pdftotext -layout` (poppler-utils)."""
    exe = shutil.which("pdftotext")
    if not exe:
        raise RuntimeError(
            "pdftotext não encontrado. Instale poppler-utils "
            "(apt-get install -y poppler-utils) — necessário para o splitter do MBFT."
        )
    out = subprocess.run(
        [exe, "-layout", "-enc", "UTF-8", pdf_path, "-"],
        capture_output=True, check=True,
    )
    return out.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Helpers de limpeza/extração
# ---------------------------------------------------------------------------
def _collapse_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def _clean_content(seg: str) -> str:
    """Remove form-feeds, rodapés do MBFT e números de página soltos; normaliza."""
    lines = []
    for raw in seg.replace("\f", "\n").split("\n"):
        line = raw.rstrip()
        if PAGE_CHROME_RE.match(line):
            continue
        lines.append(line)
    # colapsa runs de linhas em branco
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _between(seg: str, start_re: re.Pattern, end_re: re.Pattern | None) -> str | None:
    m = start_re.search(seg)
    if not m:
        return None
    rest = seg[m.end():]
    if end_re is not None:
        e = end_re.search(rest)
        if e:
            rest = rest[: e.start()]
    val = _collapse_ws(rest)
    return val or None


def _extract_codigo(seg: str) -> str | None:
    """Código SÓ do rótulo: primeira ocorrência NNN-NN entre o rótulo e 'Amparo Legal:'."""
    m = CODIGO_LABEL_RE.search(seg)
    if not m:
        return None
    window = seg[m.end():]
    amp = AMPARO_LABEL_RE.search(window)
    if amp:
        window = window[: amp.start()]
    cm = CODE_RE.search(window)
    return cm.group(1) if cm else None


def _extract_tipificacao_resumida(seg: str, codigo: str | None) -> str | None:
    """Texto do cabeçalho antes de 'Amparo Legal:', sem os rótulos e sem o código."""
    end = AMPARO_LABEL_RE.search(seg)
    region = seg[: end.start()] if end else seg
    region = BANNER_RE.sub(" ", region)
    region = TIPIF_RESUMIDA_LABEL_RE.sub(" ", region)
    region = CODIGO_LABEL_RE.sub(" ", region)
    if codigo:
        region = region.replace(codigo, " ")
    val = _collapse_ws(region)
    return val or None


def _extract_gravidade(seg: str) -> str | None:
    region = _between(seg, GRAVIDADE_LABEL_RE, INFRATOR_LABEL_RE) or ""
    for g in GRAVIDADE_VALUES:
        if re.search(re.escape(g), region, re.IGNORECASE):
            return "Gravíssima" if g.lower().startswith("grav\u00edss") or g.lower() == "gravissima" else g
    return None


def _extract_penalidade(seg: str) -> str | None:
    region = _between(seg, GRAVIDADE_LABEL_RE, INFRATOR_LABEL_RE) or ""
    return "Multa" if re.search(r"\bMulta\b", region, re.IGNORECASE) else None


def _extract_pontuacao(seg: str) -> str | None:
    val = _between(seg, PONTUACAO_LABEL_RE, CONSTATACAO_LABEL_RE)
    if not val:
        return None
    mnum = re.search(r"\b(\d{1,2})\b", val)
    if mnum:
        return mnum.group(1)
    mtxt = re.search(r"(N[ãa]o\s+comput[áa]vel)", val, re.IGNORECASE)
    return "Não computável" if mtxt else val


# Pontuação base por gravidade (CTB art. 259). Usada como fallback quando o valor
# não é recuperável do layout (fica na linha de baixo, em coluna).
_PONTOS_GRAVIDADE = {"Leve": "3", "Média": "4", "Grave": "5", "Gravíssima": "7"}


def _pontuacao_por_gravidade(gravidade: str | None) -> str | None:
    return _PONTOS_GRAVIDADE.get(gravidade or "")


# ---------------------------------------------------------------------------
# Parsing principal
# ---------------------------------------------------------------------------
def parse_fichas(text: str) -> list[Ficha]:
    """Segmenta o texto por ficha, extrai campos e deduplica por código."""
    starts = [m.start() for m in BANNER_RE.finditer(text)]
    if not starts:
        return []
    bounds = starts + [len(text)]
    raw_segments = [text[bounds[i]: bounds[i + 1]] for i in range(len(bounds) - 1)]

    fichas: list[Ficha] = []
    seen_codes: dict[str, int] = {}  # codigo -> índice em `fichas`

    for seg in raw_segments:
        codigo = _extract_codigo(seg)

        # dedup: ficha reimpressa (mesmo código já visto) — mantém a mais longa
        if codigo and codigo in seen_codes:
            idx = seen_codes[codigo]
            if len(seg) > len(fichas[idx].raw_text):
                # substitui pelo conteúdo mais completo, preservando anomalia
                fichas[idx] = _build_ficha(seg, codigo)
            fichas[idx].anomalias.append("duplicada_no_pdf")
            continue

        ficha = _build_ficha(seg, codigo)
        if codigo:
            seen_codes[codigo] = len(fichas)
        fichas.append(ficha)

    return fichas


def _build_ficha(seg: str, codigo: str | None) -> Ficha:
    amparo = _between(seg, AMPARO_LABEL_RE, TIPIF_ENQ_LABEL_RE)
    tip_enq = _between(seg, TIPIF_ENQ_LABEL_RE, GRAVIDADE_LABEL_RE)
    resumida = _extract_tipificacao_resumida(seg, codigo)
    gravidade = _extract_gravidade(seg)
    ficha = Ficha(
        codigo=codigo,
        amparo_legal=amparo,
        tipificacao_resumida=resumida,
        tipificacao_enquadramento=tip_enq,
        gravidade=gravidade,
        penalidade=_extract_penalidade(seg),
        pontuacao=_extract_pontuacao(seg) or _pontuacao_por_gravidade(gravidade),
        raw_text=_clean_content(seg),
    )
    if not codigo:
        ficha.anomalias.append("sem_codigo")
    elif not CODE_FULL_RE.match(codigo):
        ficha.anomalias.append("codigo_formato_invalido")
    if not amparo:
        ficha.anomalias.append("sem_amparo_legal")
    if not resumida:
        ficha.anomalias.append("sem_tipificacao_resumida")
    return ficha


def split_mbft_pdf(pdf_path: str) -> list[Ficha]:
    """Conveniência: extrai texto do PDF e devolve as fichas."""
    return parse_fichas(extract_text_layout(pdf_path))


# ---------------------------------------------------------------------------
# Validação e mapeamento para o RAG / legal_document_chunks
# ---------------------------------------------------------------------------
def validate_fichas(fichas: list[Ficha]) -> dict:
    """Relatório de sanidade do parsing."""
    codigos = [f.codigo for f in fichas if f.codigo]
    unicos = sorted(set(codigos))
    sem_codigo = [f for f in fichas if not f.codigo]
    fmt_invalido = [c for c in codigos if not CODE_FULL_RE.match(c)]
    sem_amparo = [f.codigo for f in fichas if not f.amparo_legal]
    duplicadas = sum(1 for f in fichas if "duplicada_no_pdf" in f.anomalias)
    prefixos = [int(c[:3]) for c in unicos]
    return {
        "fichas": len(fichas),
        "codigos_unicos": len(unicos),
        "sem_codigo": len(sem_codigo),
        "codigo_formato_invalido": len(fmt_invalido),
        "sem_amparo_legal": len(sem_amparo),
        "duplicadas_no_pdf_descartadas": duplicadas,
        "prefixo_min": min(prefixos) if prefixos else None,
        "prefixo_max": max(prefixos) if prefixos else None,
        "amparo_cobertura_pct": round(100 * (len(fichas) - len(sem_amparo)) / max(len(fichas), 1), 1),
    }


def fichas_to_chunks(fichas: list[Ficha], *, document_label: str = "MBFT 2022") -> list[dict]:
    """Mapeia cada ficha para um dict no formato de `legal_document_chunks` (§6.8).

    O conteúdo do chunk é a ficha íntegra (já limpa), pois as colunas
    'Quando NÃO Autuar' e 'Definições e Procedimentos' são justamente o material
    para fundamentar o recurso. Campos estruturados vão pra `metadata`/colunas.
    """
    chunks = []
    for i, f in enumerate(fichas):
        content = f.raw_text
        chunks.append({
            "chunk_index": i,
            "title": (f.tipificacao_resumida or "")[:200] or None,
            "section": "Ficha de Fiscalização",
            "article": f.amparo_legal,
            "content": content,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "metadata": {
                "source": document_label,
                "infraction_code": f.codigo,
                "ctb_article": f.amparo_legal,
                "tipificacao_resumida": f.tipificacao_resumida,
                "gravidade": f.gravidade,
                "penalidade": f.penalidade,
                "pontuacao": f.pontuacao,
            },
        })
    return chunks
