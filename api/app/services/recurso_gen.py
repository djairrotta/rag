"""Geração do texto do recurso (porte de generate-resource).

LLM em 2 passos (rascunho + revisão) quando há chave; senão, fallback determinístico
que monta um recurso estruturado a partir das nulidades + dados extraídos. Enriquecimento
opcional via RAG (best-effort; ignora se o serviço estiver fora).
"""
from __future__ import annotations

import json

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.services import legal_search

TITULO = "RECURSO ADMINISTRATIVO DE MULTA DE TRÂNSITO"

_SYS_DRAFT = (
    "Você é um advogado especialista em direito de trânsito brasileiro, com domínio do "
    "CTB (Lei 9.503/97), Resoluções CONTRAN e do MBFT/DENATRAN."
)
_SYS_REVIEW = (
    "Você é um revisor jurídico sênior em direito de trânsito. Corrige, fortalece a "
    "fundamentação legal e devolve o texto final pronto para protocolo."
)


def _semantica_nulidades(nullities: list[dict]) -> list[str]:
    """Busca semântica no RAG a partir dos títulos das nulidades (best-effort)."""
    if not nullities:
        return []
    consulta = " ".join(n.get("titulo", "") for n in nullities[:3]).strip()
    if not consulta:
        return []
    try:
        headers = {"Authorization": f"Bearer {settings.rag_api_key}"} if settings.rag_api_key else {}
        r = httpx.post(f"{settings.rag_api_url}/search",
                       json={"consulta": consulta, "top_k": 5}, headers=headers, timeout=4)
        r.raise_for_status()
        results = r.json().get("results", [])
        # o rag devolve o texto em 'content' (e às vezes 'texto'); aceita os dois
        return [x.get("texto") or x.get("content") or "" for x in results if (x.get("texto") or x.get("content"))]
    except Exception:
        return []


def _ficha_exata(db: Session | None, extracted: dict) -> list[str]:
    """Busca DETERMINÍSTICA da ficha do MBFT pela infração autuada (código e/ou artigo).

    É a peça que faltava: o gerador tinha o `codigo_infracao` em mãos mas só usava busca
    semântica. Agora injeta a ficha EXATA (com 'Quando NÃO Autuar', amparo legal, gravidade)
    — o material mais valioso para fundamentar o recurso. Sem `db`, degrada para vazio.
    """
    if db is None or not extracted:
        return []
    codigo = extracted.get("codigo_infracao")
    artigo = extracted.get("ctb_article") or extracted.get("artigo")
    if not codigo and not artigo:
        return []
    try:
        ctx = legal_search.buscar_contexto_legal(
            db,
            codigos=[codigo] if codigo else None,
            artigo=artigo,
        )
        return [f.get("texto", "") for f in ctx["fichas"] if f.get("texto")]
    except Exception:
        return []


def _contexto_legal(db: Session | None, extracted: dict, nullities: list[dict]) -> str:
    """Monta o contexto legal do recurso combinando, SEM prejuízo:
      1. FICHA EXATA do MBFT (busca estruturada por código/artigo) — precisão;
      2. fundamentos SEMÂNTICOS das nulidades (busca no RAG) — amplitude.
    A ficha exata vem primeiro (é o esteio da tese de enquadramento/“Quando NÃO Autuar”).
    """
    blocos: list[str] = []
    vistos: set[str] = set()

    def _add(trechos: list[str]) -> None:
        for t in trechos:
            chave = (t or "")[:80]
            if t and chave not in vistos:
                vistos.add(chave)
                blocos.append(t)

    _add(_ficha_exata(db, extracted or {}))        # 1. exata (estruturada)
    _add(_semantica_nulidades(nullities or []))    # 2. semântica (nulidades)

    return "\n\n".join(blocos[:6])


def _call_llm(system: str, user: str) -> str:
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={"model": settings.analyzer_model, "temperature": 0.2,
              "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]},
        timeout=120,
    )
    resp.raise_for_status()
    return (resp.json()["choices"][0]["message"]["content"] or "").strip()


def _llm_available() -> bool:
    return bool(settings.openai_api_key)


def _draft_prompt(extracted: dict, nullities: list[dict], answers: dict, summary: str, rag: str) -> str:
    return (
        f"Redija um {TITULO} completo, em português, formal e fundamentado.\n\n"
        f"DADOS DA MULTA:\n{json.dumps(extracted, ensure_ascii=False, indent=2)}\n\n"
        f"NULIDADES IDENTIFICADAS:\n{json.dumps(nullities, ensure_ascii=False, indent=2)}\n\n"
        f"INFORMAÇÕES DO CONDUTOR:\n{json.dumps(answers, ensure_ascii=False, indent=2)}\n\n"
        f"SÍNTESE PRÉVIA:\n{summary}\n\n"
        + (f"FUNDAMENTOS DA BASE (MBFT/jurisprudência):\n{rag}\n\n" if rag else "")
        + "ESTRUTURA OBRIGATÓRIA:\n"
        "1. Cabeçalho (ILUSTRÍSSIMO SENHOR PRESIDENTE DA JARI / AUTORIDADE DE TRÂNSITO).\n"
        "2. Qualificação do recorrente (placeholders [NOME COMPLETO], [CPF], [CNH], [ENDEREÇO] se ausente).\n"
        "3. Identificação do auto (número, data, local, código, descrição, placa).\n"
        "4. I — DOS FATOS.\n5. II — DO DIREITO (cada nulidade vira subtópico com base legal exata).\n"
        "6. III — DO PEDIDO (cancelamento, arquivamento, devolução de pontos).\n"
        "7. Fechamento (Nestes termos, pede deferimento. + local/data/assinatura).\n\n"
        "REGRAS: não invente fatos; cite artigo exato (ex.: art. 280, VI, do CTB); texto puro, sem markdown."
    )


def _fallback_text(extracted: dict, nullities: list[dict], answers: dict, summary: str, rag: str) -> str:
    g = lambda k: extracted.get(k) or "[NÃO INFORMADO]"  # noqa: E731
    L = []
    L.append("ILUSTRÍSSIMO(A) SENHOR(A) PRESIDENTE DA JARI / AUTORIDADE DE TRÂNSITO DO ÓRGÃO AUTUADOR")
    L.append("")
    L.append("[NOME COMPLETO], portador(a) do CPF [CPF] e da CNH [CNH], residente em [ENDEREÇO], "
             "vem, respeitosamente, apresentar")
    L.append("")
    L.append(TITULO)
    L.append("")
    L.append(f"em face do Auto de Infração nº {g('numero_auto')}, lavrado em {g('data_infracao')} "
             f"no local {g('local_infracao')}, sob o código {g('codigo_infracao')} "
             f"({g('descricao_infracao')}), referente ao veículo placa {g('placa_veiculo')}, "
             f"pelos fundamentos a seguir.")
    L.append("")
    L.append("I — DOS FATOS")
    L.append(summary or "O recorrente foi autuado conforme o auto em epígrafe e dele discorda pelos "
             "vícios adiante demonstrados.")
    L.append("")
    L.append("II — DO DIREITO")
    if nullities:
        for i, n in enumerate(nullities, 1):
            L.append(f"{i}. {n.get('titulo', 'Vício')}")
            base = n.get("base_legal")
            if base:
                L.append(f"Fundamento legal: {base}.")
            L.append(n.get("descricao", ""))
            L.append("")
    else:
        L.append("Ainda que sucinta a notificação, impõe-se a observância do art. 280 do CTB e das "
                 "Resoluções do CONTRAN quanto aos requisitos de validade da autuação.")
        L.append("")
    if rag:
        L.append("Reforçam a tese os seguintes fundamentos da base normativa e jurisprudencial:")
        L.append(rag)
        L.append("")
    L.append("III — DO PEDIDO")
    L.append("Diante do exposto, requer-se o CANCELAMENTO do auto de infração, o consequente "
             "ARQUIVAMENTO do processo administrativo e, se já computados, a DEVOLUÇÃO dos pontos "
             "lançados na CNH do recorrente.")
    L.append("")
    L.append("Nestes termos, pede deferimento.")
    L.append("")
    L.append("[LOCAL], [DATA].")
    L.append("")
    L.append("__________________________________")
    L.append("[NOME COMPLETO] — CPF [CPF]")
    return "\n".join(L)


def generate_text(*, extracted: dict, nullities: list[dict], answers: dict, summary: str,
                  db: Session | None = None) -> tuple[str, str]:
    """Retorna (texto_final, engine).

    `db` (opcional) habilita a injeção da FICHA EXATA do MBFT via busca estruturada
    (código/artigo da infração). Sem `db`, cai apenas na busca semântica — comportamento
    anterior preservado, nada quebra.
    """
    rag = _contexto_legal(db, extracted or {}, nullities or [])
    if _llm_available():
        try:
            draft = _call_llm(_SYS_DRAFT, _draft_prompt(extracted or {}, nullities or [], answers or {}, summary or "", rag))
            try:
                final = _call_llm(_SYS_REVIEW, "Revise e finalize, mantendo a estrutura, sem markdown:\n\n" + draft)
            except Exception:
                final = draft
            return final, settings.analyzer_provider
        except Exception:
            pass
    return _fallback_text(extracted or {}, nullities or [], answers or {}, summary or "", rag), "fallback"
