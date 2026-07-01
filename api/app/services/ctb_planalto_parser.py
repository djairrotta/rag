"""Parser do CTB oficial do Planalto (HTML compilado) em chunks por artigo.

O Planalto é a ESPINHA DORSAL: texto oficial, completo e atualizado da Lei 9.503/97
(até a Lei 14.861/2024). Domínio público (LDA art. 8º). Serve de base; a doutrina
(Celso) e os extras (360) enriquecem por casamento de número de artigo.

Fonte: https://www.planalto.gov.br/ccivil_03/leis/l9503compilado.htm
"""
from __future__ import annotations

import re

from app.services.ctb_parser import ArtigoChunk, BIBLIO


# Entidades HTML comuns no Planalto (encoding latin-1)
_ENTIDADES = {
    "&nbsp;": " ", "&aacute;": "á", "&atilde;": "ã", "&ccedil;": "ç",
    "&eacute;": "é", "&iacute;": "í", "&oacute;": "ó", "&uacute;": "ú",
    "&ecirc;": "ê", "&ocirc;": "ô", "&acirc;": "â", "&agrave;": "à",
    "&Aacute;": "Á", "&Atilde;": "Ã", "&Ccedil;": "Ç", "&Eacute;": "É",
    "&Iacute;": "Í", "&Oacute;": "Ó", "&Uacute;": "Ú", "&ordm;": "º",
    "&ordf;": "ª", "&deg;": "º", "&sect;": "§", "&amp;": "&",
    "&quot;": '"', "&lt;": "<", "&gt;": ">", "&#8211;": "–", "&#8212;": "—",
    "&uacute;": "ú", "&otilde;": "õ", "&Otilde;": "Õ", "&ntilde;": "ñ",
}

# Início de artigo no texto do Planalto: "Art. 1º", "Art. 253-A"
_ART_RE = re.compile(r"(?m)^\s*Art\.\s*(\d+)(?:\s*-\s*([A-Z]))?\s*[º°]?")
# Marca de lei alteradora: "(Incluído pela Lei nº 13.281, de 2016)"
_ALTERACAO_RE = re.compile(
    r"\((?:Inclu[íi]do|Redação dada|Revogado|Vide|Alterado)[^)]*Lei[^)]*\)",
    re.IGNORECASE,
)


def _html_para_texto(html: str) -> str:
    """Converte o HTML do Planalto em texto limpo, preservando quebras de artigo."""
    # troca <br>, <p>, </p> por quebras de linha antes de remover tags
    html = re.sub(r"<\s*br\s*/?\s*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<\s*/?\s*p[^>]*>", "\n", html, flags=re.IGNORECASE)
    html = re.sub(r"<[^>]+>", " ", html)              # remove tags restantes
    for ent, char in _ENTIDADES.items():
        html = html.replace(ent, char)
    html = re.sub(r"&#\d+;", " ", html)               # entidades numéricas restantes
    html = html.replace("\r", " ")
    html = re.sub(r"[ \t]+", " ", html)

    # O HTML do Planalto quebra linhas de forma arbitrária (no meio de frases e até de
    # números: "Lei nº 13.\n281"). Estratégia: colapsar TODAS as quebras em espaço e
    # depois reintroduzir quebras só nos limites estruturais reais (Art., §, incisos,
    # "Parágrafo único", "Penalidade", "Infração"). Resultado: texto corrido e legível.
    html = re.sub(r"\n+", " ", html)
    html = re.sub(r"\s{2,}", " ", html)

    # reintroduz quebras antes de marcadores estruturais
    html = re.sub(r"\s+(Art\.\s*\d)", r"\n\1", html)
    html = re.sub(r"\s+(§\s*\d)", r"\n\1", html)
    html = re.sub(r"\s+(Parágrafo único)", r"\n\1", html)
    html = re.sub(r"\s+(Penalidade\s*[-–])", r"\n\1", html)
    html = re.sub(r"\s+(Infração\s*[-–])", r"\n\1", html)
    html = re.sub(r"\s+(Medida [Aa]dministrativa)", r"\n\1", html)
    # incisos romanos seguidos de travessão: " II - ", " XIV - "
    html = re.sub(r"\s+([IVXLC]{1,5}\s*[-–]\s)", r"\n\1", html)

    return html.strip()


def parse_planalto(html: str) -> list[ArtigoChunk]:
    """Parseia o HTML compilado do CTB em chunks por artigo."""
    biblio = BIBLIO["ctb_planalto"]
    texto = _html_para_texto(html)

    # corta o preâmbulo (tudo antes do "Art. 1º")
    m0 = _ART_RE.search(texto)
    if m0:
        texto = texto[m0.start():]

    matches = list(_ART_RE.finditer(texto))
    chunks: list[ArtigoChunk] = []
    for i, m in enumerate(matches):
        ini = m.start()
        fim = matches[i + 1].start() if i + 1 < len(matches) else len(texto)
        bloco = texto[ini:fim].strip()
        if len(bloco) < 20:
            continue

        num = m.group(1) + (f"-{m.group(2)}" if m.group(2) else "")
        # leis alteradoras citadas no artigo (números limpos, p/ saber a versão vigente)
        leis = sorted(set(re.findall(r"Lei\s+n[ºo°]?\s*([\d.]+),?\s*de\s*(\d{4})", bloco)))
        alteracoes = [f"Lei nº {n.rstrip('.')}/{ano}" for n, ano in leis]

        meta = {
            "art_numero": num,
            "ctb_article": f"Art. {num}",
            "fonte": "ctb_planalto",
            "source_type": "lei",
            "tem_comentario": False,
            "tem_remissao": False,
            "resolucoes_citadas": [],
            "alteracoes_legais": alteracoes,
            "pagina": None,    # HTML não tem página
            "allow_verbatim": True,
            "obra": biblio["obra"],
            "autor": biblio["autor"],
            "editora": biblio["editora"],
            "local": biblio["local"],
            "ano": biblio["ano"],
            "edicao": biblio["edicao"],
            "isbn": biblio["isbn"],
            "citacao": (
                f'BRASIL. {biblio["obra"]}. Disponível em planalto.gov.br. '
                f'Art. {num}.'
            ),
        }
        chunks.append(ArtigoChunk(
            art_numero=num, fonte="ctb_planalto", pagina=0, texto=bloco,
            tem_comentario=False, tem_remissao=False, metadata=meta,
        ))

    # consolida duplicatas (mantém o bloco mais longo por número)
    melhor: dict[str, ArtigoChunk] = {}
    for c in chunks:
        if c.art_numero not in melhor or len(c.texto) > len(melhor[c.art_numero].texto):
            melhor[c.art_numero] = c

    def _ordem(num: str):
        b = re.match(r"(\d+)", num)
        return (int(b.group(1)) if b else 9999, num)

    return [melhor[k] for k in sorted(melhor, key=_ordem)]
