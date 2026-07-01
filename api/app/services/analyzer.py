"""Análise de multa — o cérebro do Segura Multas (multi-provider + RAG-ready).

Pipeline em 3 passos, herdado do analyze-fine do Lovable e ampliado:
  (0) validar se o documento é mesmo uma multa de trânsito
  (1) extrair os 12 campos do auto (visão/OCR)
  (2) analisar nulidades + veredito (null/weak/valid)

Diferença sobre o Lovable:
  - MULTI-PROVIDER com fallback. Admin escolhe o provider ativo (dropdown →
    settings.analyzer_provider). Se o principal falhar, cai para a Groq.
    Diretos: openai | anthropic | deepseek | kimi. Fallback: groq.
  - Dois dialetos de API: 'openai' (compatível — OpenAI/DeepSeek/Kimi/Groq) e
    'anthropic' (API própria da Claude).
  - Prompts RICOS (categorias formais/materiais/processuais do Lovable).
  - Passo 2 aceita CONTEXTO DE RAG (MBFT+CTB) injetado no prompt — o veredito
    passa a citar artigo real, não "achismo" da LLM. (ver analyze() e rag_context)

O fallback determinístico (sem nenhuma chave de LLM) nunca fabrica dados da imagem;
devolve campos nulos com nota e deriva nulidades das respostas do questionário.
"""
from __future__ import annotations

import json
import re

import httpx

from app.core.config import settings

EXTRACTED_FIELDS = [
    "numero_auto", "codigo_infracao", "descricao_infracao", "data_infracao",
    "hora_infracao", "local_infracao", "placa_veiculo", "marca_modelo",
    "orgao_autuador", "valor_multa", "pontos", "data_limite_recurso",
]
_EMPTY = {k: None for k in EXTRACTED_FIELDS}
_IMAGE_OR_PDF = ("image/", "application/pdf")
_TIMEOUT = 90


# ============================================================ infra multi-provider
def provider_chain() -> list[str]:
    """Ordem de providers a tentar (ativo -> Groq). [] se nenhum tem chave."""
    return settings.provider_chain()


def llm_available() -> bool:
    return bool(provider_chain())


def provider_label() -> str:
    chain = provider_chain()
    return chain[0] if chain else "fallback"


def _strip_json(text: str) -> str:
    return re.sub(r"```json\n?|```\n?", "", text or "").strip()


def _parse_json_loose(text: str) -> dict:
    """Extrai o primeiro objeto JSON do texto (tolera preambulo/lixo)."""
    cleaned = _strip_json(text)
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            return json.loads(m.group(0))
        raise


# ---- dialeto OpenAI-compativel (OpenAI, DeepSeek, Kimi, Groq) -------------------
def _openai_compatible_chat(cfg: dict, messages: list[dict], *, vision: bool) -> str:
    model = cfg["vision_model"] if vision else cfg["model"]
    resp = httpx.post(
        f"{cfg['base_url'].rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {cfg['api_key']}",
            "Content-Type": "application/json",
        },
        json={"model": model, "messages": messages, "temperature": 0},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


# ---- dialeto Anthropic (API propria da Claude) ----------------------------------
def _anthropic_chat(cfg: dict, messages: list[dict], *, vision: bool) -> str:
    """Converte mensagens estilo-OpenAI para o formato Anthropic e chama a API."""
    model = cfg["vision_model"] if vision else cfg["model"]
    system_txt = ""
    conv: list[dict] = []
    for m in messages:
        if m["role"] == "system":
            system_txt += (m["content"] if isinstance(m["content"], str) else "") + "\n"
            continue
        content = m["content"]
        if isinstance(content, str):
            conv.append({"role": m["role"], "content": content})
            continue
        blocks = []
        for part in content:
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": part["text"]})
            elif part.get("type") == "image_url":
                url = part["image_url"]["url"]
                mt = re.match(r"data:([^;]+);base64,(.*)", url, re.DOTALL)
                if mt:
                    blocks.append({
                        "type": "image",
                        "source": {"type": "base64", "media_type": mt.group(1), "data": mt.group(2)},
                    })
        conv.append({"role": m["role"], "content": blocks})

    resp = httpx.post(
        f"{cfg['base_url'].rstrip('/')}/messages",
        headers={
            "x-api-key": cfg["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 2048,
            "temperature": 0,
            **({"system": system_txt.strip()} if system_txt.strip() else {}),
            "messages": conv,
        },
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()
    parts = data.get("content", [])
    return "".join(b.get("text", "") for b in parts if b.get("type") == "text")


def _call_one(provider: str, messages: list[dict], *, vision: bool) -> str:
    cfg = settings.provider_config(provider)
    if cfg["dialect"] == "anthropic":
        return _anthropic_chat(cfg, messages, vision=vision)
    return _openai_compatible_chat(cfg, messages, vision=vision)


def _chat(messages: list[dict], *, vision: bool = False) -> str:
    """Chama o provider ativo; em erro, cai para os proximos da cadeia (Groq)."""
    chain = provider_chain()
    if not chain:
        raise RuntimeError("nenhum provider de LLM configurado")
    last_err: Exception | None = None
    for provider in chain:
        try:
            return _call_one(provider, messages, vision=vision)
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("falha em todos os providers")


def _vision_msg(prompt: str, image_b64: str, mime: str) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ],
    }]


# ============================================================ passo 0 - validacao
_VALIDATION_PROMPT = (
    "Analise este documento e determine se ele e uma NOTIFICACAO DE INFRACAO DE TRANSITO, "
    "MULTA DE TRANSITO ou AUTO DE INFRACAO de transito brasileiro.\n\n"
    "Documentos VALIDOS: Notificacao de Autuacao (NIA), Notificacao de Penalidade (NIP), "
    "Auto de Infracao de Transito (AIT), multas de DETRAN/PRF/DER/Prefeituras/DNIT, "
    "boletos de multa de transito com dados da infracao.\n"
    "Documentos NAO validos: contratos, documentos pessoais (RG/CPF/CNH), boletos bancarios "
    "comuns, extratos, notas fiscais, qualquer coisa nao relacionada a infracao de transito.\n\n"
    'Retorne APENAS JSON: {"is_traffic_fine": true|false, "confidence": "alta|media|baixa", '
    '"document_type": "descricao curta", "reason": "motivo"}. Sem markdown.'
)


def validate_document(image_b64: str, mime: str, file_size: int) -> dict:
    if llm_available():
        try:
            data = _parse_json_loose(_chat(_vision_msg(_VALIDATION_PROMPT, image_b64, mime), vision=True))
            return {
                "is_traffic_fine": bool(data.get("is_traffic_fine")),
                "confidence": data.get("confidence", "media"),
                "document_type": data.get("document_type", ""),
                "reason": data.get("reason", ""),
            }
        except Exception:
            pass
    ok = file_size > 0 and any(mime.startswith(p) for p in _IMAGE_OR_PDF)
    return {
        "is_traffic_fine": ok,
        "confidence": "baixa",
        "document_type": "(fallback) presumido auto de infracao" if ok else "tipo nao suportado",
        "reason": "validacao sem LLM" if ok else "arquivo vazio ou tipo nao-imagem/PDF",
    }


# ============================================================ passo 1 - extracao
_EXTRACTION_PROMPT = (
    "Voce e um especialista em analise de multas de transito brasileiras. Analise esta imagem "
    "de notificacao de infracao e extraia TODOS os dados visiveis.\n\n"
    "Retorne um JSON com os campos (use null se nao encontrar):\n"
    '{"numero_auto": "...", "codigo_infracao": "ex 74550", "descricao_infracao": "...", '
    '"data_infracao": "DD/MM/AAAA", "hora_infracao": "...", "local_infracao": "endereco completo", '
    '"placa_veiculo": "...", "marca_modelo": "...", "orgao_autuador": "...", "valor_multa": "R$...", '
    '"pontos": numero, "data_limite_recurso": "DD/MM/AAAA"}\n\n'
    "IMPORTANTE: Retorne APENAS o JSON, sem markdown, sem explicacoes."
)


def extract_data(image_b64: str, mime: str) -> dict:
    if llm_available():
        try:
            data = _parse_json_loose(_chat(_vision_msg(_EXTRACTION_PROMPT, image_b64, mime), vision=True))
            return {k: data.get(k) for k in EXTRACTED_FIELDS}
        except Exception:
            pass
    out = dict(_EMPTY)
    out["descricao_infracao"] = "(extracao indisponivel - configure uma chave de LLM)"
    return out


# ============================================================ passo 2 - analise (3 frentes)
# O analyzer avalia TRES frentes; o veredito final combina as tres.
#   1. NULIDADE      - vicio formal/processual do auto (CTB 280/281, Res.432)
#   2. MERITO        - a autuacao estava correta? ("quando NAO autuar", MBFT)
#   3. ENTENDIMENTO  - doutrina/tese defensiva (livros indexados no RAG)
#
# Cada frente tem seu PROMPT (constante isolada) e sua BUSCA RAG (consulta propria).
# Os prompts comecam aqui no codigo; serao migrados para tabela no Postgres na Fase 5
# (admin CRUD pela dashboard, inclusive editar os existentes).

_STRONG_HINTS = ("sinaliz", "placa", "prazo", "notific", "veiculo", "agente")

_ANALYSIS_SYSTEM = (
    "Voce e um advogado especialista em direito de transito brasileiro com vasta experiencia "
    "em recursos de multas. Trabalha com o CTB, resolucoes do CONTRAN, o Manual Brasileiro de "
    "Fiscalizacao de Transito (MBFT) e doutrina especializada. Regra inviolavel: nunca invente "
    "artigos, numeros de resolucao, fichas do MBFT ou citacoes doutrinarias. Se nao tiver certeza "
    "da base, diga isso no campo descricao em vez de citar norma/fonte inexistente. Fundamente "
    "SEMPRE que houver contexto recuperado."
)

# ---- PROMPT 1: NULIDADE (vicio formal/processual) — herdado do Lovable, ampliado
_PROMPT_NULIDADE = (
    "FRENTE 1 - NULIDADE (vicios formais e processuais do auto de infracao).\n"
    "Com base no CTB e nas resolucoes do CONTRAN, identifique vicios de FORMA:\n"
    "1. NULIDADES FORMAIS: ausencia de dados obrigatorios (Art. 280 CTB); erro na identificacao "
    "do veiculo/condutor; falta de descricao clara; ausencia de local preciso; falta de "
    "assinatura/identificacao do agente; prazo de notificacao excedido (30 dias - Art. 281 CTB).\n"
    "2. NULIDADES MATERIAIS DE FORMA: erro no enquadramento; ausencia de equipamento de afericao "
    "aferido/calibrado (Res. CONTRAN 432/2013 p/ etilometro e medidor de velocidade); falta de "
    "sinalizacao adequada.\n"
    "3. VICIOS PROCESSUAIS: cerceamento de defesa; falta de dupla notificacao (autuacao + "
    "penalidade); inconsistencias nos dados."
)

# ---- PROMPT 2: MERITO (a autuacao em si estava correta? — base MBFT)
_PROMPT_MERITO = (
    "FRENTE 2 - MERITO (a autuacao estava materialmente correta?).\n"
    "Com base no Manual Brasileiro de Fiscalizacao de Transito (MBFT) e no procedimento correto de "
    "fiscalizacao da infracao autuada, avalie o MERITO do fato - nao a forma:\n"
    "- O agente DEVERIA ter autuado nesta situacao? Consulte as hipoteses de 'quando NAO autuar' "
    "da ficha do MBFT desta infracao.\n"
    "- O procedimento de fiscalizacao correto foi observado (condicoes, tolerancias, requisitos "
    "materiais especificos daquela infracao)?\n"
    "- Havia condicao que descaracteriza a infracao no merito (nao um vicio de forma, mas o fato "
    "em si nao configurar a infracao conforme o MBFT)?"
)

# ---- PROMPT 3: ENTENDIMENTO / TESE (doutrina dos livros indexados)
_PROMPT_ENTENDIMENTO = (
    "FRENTE 3 - ENTENDIMENTO / TESE DEFENSIVA (doutrina especializada).\n"
    "Com base na doutrina especializada em direito de transito (livros de referencia), aponte "
    "teses defensivas e interpretacoes aplicaveis a esta situacao concreta: entendimentos "
    "consolidados, interpretacoes favoraveis ao condutor, argumentos de defesa que a doutrina "
    "sustenta para casos como este. Cite a fonte doutrinaria apenas se ela constar do contexto "
    "recuperado; nao invente autor, obra ou pagina."
)

# formato de saida comum as tres frentes
_OUTPUT_SPEC = (
    'Retorne APENAS JSON no formato:\n'
    '{"status": "null|weak|valid", "points": [{"titulo": "...", "base_legal": "artigo/resolucao/ficha/fonte REAL", '
    '"descricao": "por que se aplica ao caso concreto", "gravidade": "alta|media|baixa"}], '
    '"summary": "resumo em 1-2 frases"}\n'
    'REGRAS PARA STATUS: "null"=fundamento forte (alta gravidade); "weak"=fundamento medio, '
    'contestavel; "valid"=sem fundamento relevante nesta frente.\n'
    "IMPORTANTE: Retorne APENAS o JSON, sem markdown."
)


def _frente_prompt(frente_prompt: str, extracted: dict, answers: dict, rag_context: str) -> str:
    ctx = ""
    if rag_context.strip():
        ctx = (
            "\n\nCONTEXTO RECUPERADO (trechos REAIS da base - CTB, resolucoes, MBFT ou doutrina - "
            "referentes a esta infracao; fundamente citando estes trechos):\n"
            f"{rag_context.strip()}\n"
        )
    return (
        f"{frente_prompt}\n\n"
        f"DADOS DA MULTA:\n{json.dumps(extracted, ensure_ascii=False, indent=2)}\n\n"
        f"RESPOSTAS DO QUESTIONARIO (informacoes do condutor):\n{json.dumps(answers, ensure_ascii=False, indent=2)}\n"
        f"{ctx}\n"
        f"{_OUTPUT_SPEC}"
    )


def _run_frente(frente_prompt: str, extracted: dict, answers: dict, rag_context: str) -> dict:
    """Roda UMA frente de analise. Retorna {status, points, summary} ou vazio se a LLM falhar."""
    try:
        messages = [
            {"role": "system", "content": _ANALYSIS_SYSTEM},
            {"role": "user", "content": _frente_prompt(frente_prompt, extracted, answers, rag_context)},
        ]
        data = _parse_json_loose(_chat(messages, vision=False))
        return {
            "status": data.get("status", "valid"),
            "points": data.get("points", []),
            "summary": data.get("summary", ""),
        }
    except Exception:
        return {"status": "valid", "points": [], "summary": ""}


# ---- combinacao das tres frentes num veredito unico
_STATUS_RANK = {"null": 2, "weak": 1, "valid": 0}
_RANK_STATUS = {2: "null", 1: "weak", 0: "valid"}


def _combine(nulidade: dict, merito: dict, entendimento: dict) -> dict:
    """Combina as 3 frentes. Status final = o mais forte entre as frentes.

    Cada ponto vira uma 'nullity' com o campo 'frente' indicando a origem
    (compatibilidade com o formato antigo + o recurso_gen, que consome 'nullities').
    """
    frentes = [("nulidade", nulidade), ("merito", merito), ("entendimento", entendimento)]
    pontos: list[dict] = []
    rank = 0
    for nome, fr in frentes:
        rank = max(rank, _STATUS_RANK.get(fr.get("status", "valid"), 0))
        for p in fr.get("points", []):
            p = dict(p)
            p["frente"] = nome
            pontos.append(p)
    status = _RANK_STATUS[rank]
    partes = [fr.get("summary", "") for _, fr in frentes if fr.get("summary")]
    summary = " ".join(partes).strip() or "Sem fundamentos relevantes identificados."
    if status == "null":
        recommendation = "Ha fundamentos fortes para recurso (nulidade, merito e/ou tese doutrinaria)."
    elif status == "weak":
        recommendation = "Cabe recurso; os fundamentos sao contestaveis e merecem analise detalhada."
    else:
        recommendation = "A multa aparenta regularidade nas tres frentes. Reavalie com um especialista se desejar."
    return {
        "status": status,
        "nullities": pontos,          # nome mantido p/ compatibilidade com recurso_gen/frontend
        "por_frente": {
            "nulidade": nulidade.get("status", "valid"),
            "merito": merito.get("status", "valid"),
            "entendimento": entendimento.get("status", "valid"),
        },
        "summary": summary,
        "recommendation": recommendation,
    }


def _fallback_nullities(answers: dict) -> dict:
    """Sem LLM: deriva pontos das respostas do questionario (nunca fabrica dados da imagem)."""
    flagged = [k for k, v in (answers or {}).items() if v is True]
    if not flagged:
        return {
            "status": "valid",
            "nullities": [],
            "por_frente": {"nulidade": "valid", "merito": "valid", "entendimento": "valid"},
            "summary": "Sem indicios de fundamento a partir das respostas fornecidas.",
            "recommendation": "A multa aparenta regularidade. Reavalie com um especialista se desejar.",
        }
    strong = [k for k in flagged if any(h in k.lower() for h in _STRONG_HINTS)]
    pontos = []
    for k in flagged:
        pontos.append({
            "titulo": f"Possivel fundamento relacionado a '{k}'",
            "base_legal": "CTB art. 280/281 (a confirmar com a analise juridica)",
            "descricao": "Resposta do condutor indica possivel irregularidade; requer verificacao documental.",
            "gravidade": "alta" if k in strong else "media",
            "frente": "nulidade",
        })
    status = "null" if strong else "weak"
    return {
        "status": status,
        "nullities": pontos,
        "por_frente": {"nulidade": status, "merito": "valid", "entendimento": "valid"},
        "summary": f"{len(pontos)} ponto(s) potencialmente questionavel(is) a partir do questionario.",
        "recommendation": "Ha fundamentos para recurso." if status == "null" else "Cabe contestacao, com analise mais detalhada.",
    }


def analyze_full(extracted: dict, answers: dict, rag: dict | None = None) -> dict:
    """Passo 2 completo: roda as 3 frentes (cada uma com seu RAG) e combina.

    `rag` = {"nulidade": str, "merito": str, "entendimento": str} com o contexto
    recuperado por frente. Se None/vazio, as frentes rodam sem contexto.
    """
    if not llm_available():
        out = _fallback_nullities(answers)
        out["provider"] = "fallback"
        out["grounded"] = False
        return out
    rag = rag or {}
    nulidade = _run_frente(_PROMPT_NULIDADE, extracted, answers, rag.get("nulidade", ""))
    merito = _run_frente(_PROMPT_MERITO, extracted, answers, rag.get("merito", ""))
    entendimento = _run_frente(_PROMPT_ENTENDIMENTO, extracted, answers, rag.get("entendimento", ""))
    combined = _combine(nulidade, merito, entendimento)
    combined["provider"] = provider_label()
    combined["grounded"] = any(rag.get(k, "").strip() for k in ("nulidade", "merito", "entendimento"))
    return combined


def analyze_nullities(extracted: dict, answers: dict, rag_context: str = "") -> dict:
    """Compatibilidade: mantem a assinatura antiga. Injeta o mesmo contexto nas 3 frentes."""
    rag = {"nulidade": rag_context, "merito": rag_context, "entendimento": rag_context} if rag_context else None
    return analyze_full(extracted, answers, rag)


# ============================================================ orquestracao
def _rag_search(consulta: str, top_k: int = 6) -> str:
    """Uma busca semantica no servico rag (MBFT+CTB+livros juntos). Best-effort ('' se cair)."""
    consulta = (consulta or "").strip()
    if not consulta:
        return ""
    try:
        headers = {"Authorization": f"Bearer {settings.rag_api_key}"} if settings.rag_api_key else {}
        r = httpx.post(
            f"{settings.rag_api_url}/search",
            json={"consulta": consulta, "top_k": top_k},
            headers=headers,
            timeout=6,
        )
        r.raise_for_status()
        results = r.json().get("results", [])
    except Exception:
        return ""
    linhas: list[str] = []
    for x in results:
        txt = x.get("texto") or x.get("content") or ""
        src = x.get("codigo") or x.get("source") or x.get("document") or ""
        if txt:
            linhas.append(f"[{src}] {txt}".strip() if src else txt)
    return "\n\n".join(linhas[:top_k])


def _retrieve_rag(extracted: dict) -> dict:
    """Busca RAG por frente (passo 1.3). Cada frente consulta coisas diferentes na base.

    - nulidade: requisitos do auto (codigo + descricao + 'requisitos auto de infracao')
    - merito: a ficha do MBFT da infracao (codigo + 'quando nao autuar procedimento fiscalizacao')
    - entendimento: doutrina/tese (descricao + 'tese defensiva doutrina')
    Retorna {"nulidade","merito","entendimento"} (strings; '' quando nada encontrado).
    """
    codigo = str((extracted or {}).get("codigo_infracao") or "").strip()
    descricao = str((extracted or {}).get("descricao_infracao") or "").strip()
    base = " ".join(x for x in (codigo, descricao) if x).strip()
    if not base:
        return {"nulidade": "", "merito": "", "entendimento": ""}
    return {
        "nulidade": _rag_search(f"{base} requisitos do auto de infracao CTB 280 281"),
        "merito": _rag_search(f"{codigo} {descricao} quando nao autuar procedimento de fiscalizacao MBFT".strip()),
        "entendimento": _rag_search(f"{descricao} tese defensiva doutrina entendimento".strip()),
    }


def analyze(*, image_b64: str, mime: str, file_size: int, answers: dict, use_rag: bool = True) -> dict:
    """Orquestra tudo. Retorna {rejected} quando nao e multa.

    use_rag=True (padrao): busca contexto real por frente (nulidade/merito/entendimento)
    no MBFT/CTB/livros e injeta nos prompts - veredito fundamentado. Se o RAG falhar, segue sem ele.
    """
    validation = validate_document(image_b64, mime, file_size)
    if not validation["is_traffic_fine"]:
        return {"rejected": True, "validation": validation}

    extracted = extract_data(image_b64, mime)
    rag = _retrieve_rag(extracted) if use_rag else {}
    verdict = analyze_full(extracted, answers, rag)

    return {
        "rejected": False,
        "extracted": extracted,
        "verdict": verdict,
        "provider": provider_label(),
    }
