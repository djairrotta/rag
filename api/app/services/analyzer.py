"""Análise de multa (porte do analyze-fine).

Pipeline em 3 passos: (0) validar se é multa, (1) extrair dados (visão),
(2) analisar nulidades + veredito. Provider pluggável (OpenAI-compatível com chave;
fallback determinístico sem chave, derivando o veredito das respostas do questionário).

O fallback NUNCA fabrica dados da imagem (devolve campos nulos com nota), mas pode
derivar nulidades das respostas do condutor — que são entrada real do usuário.
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


def _llm_key() -> str:
    if settings.analyzer_provider == "openai":
        return settings.openai_api_key
    if settings.analyzer_provider == "anthropic":
        return settings.anthropic_api_key
    return ""


def llm_available() -> bool:
    return bool(_llm_key())


def provider_label() -> str:
    return settings.analyzer_provider if llm_available() else "fallback"


def _strip_json(text: str) -> str:
    return re.sub(r"```json\n?|```\n?", "", text or "").strip()


def _openai_chat(messages: list[dict]) -> str:
    """Chamada compatível-OpenAI (chat/completions). Suporta conteúdo multimodal."""
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {settings.openai_api_key}", "Content-Type": "application/json"},
        json={"model": settings.analyzer_model, "messages": messages, "temperature": 0},
        timeout=90,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"] or ""


def _vision_msg(prompt: str, image_b64: str, mime: str) -> list[dict]:
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
        ],
    }]


# ---------------------------------------------------------------- passo 0
def validate_document(image_b64: str, mime: str, file_size: int) -> dict:
    if llm_available():
        prompt = (
            "Analise este documento e diga se é uma NOTIFICAÇÃO/AUTO DE INFRAÇÃO DE "
            "TRÂNSITO brasileiro (NIA, NIP, AIT, multa de DETRAN/PRF/DER/DNIT/Prefeitura). "
            'Retorne APENAS JSON: {"is_traffic_fine": bool, "confidence": "alta|media|baixa", '
            '"document_type": "...", "reason": "..."}'
        )
        try:
            data = json.loads(_strip_json(_openai_chat(_vision_msg(prompt, image_b64, mime))))
            return {
                "is_traffic_fine": bool(data.get("is_traffic_fine")),
                "confidence": data.get("confidence", "media"),
                "document_type": data.get("document_type", ""),
                "reason": data.get("reason", ""),
            }
        except Exception:
            pass  # cai no fallback
    # fallback determinístico: aceita imagem/PDF não-vazio
    ok = file_size > 0 and any(mime.startswith(p) for p in _IMAGE_OR_PDF)
    return {
        "is_traffic_fine": ok,
        "confidence": "baixa",
        "document_type": "(fallback) presumido auto de infração" if ok else "tipo não suportado",
        "reason": "validação sem LLM" if ok else "arquivo vazio ou tipo não-imagem/PDF",
    }


# ---------------------------------------------------------------- passo 1
def extract_data(image_b64: str, mime: str) -> dict:
    if llm_available():
        prompt = (
            "Você é especialista em multas de trânsito brasileiras. Extraia TODOS os dados "
            "visíveis desta notificação. Retorne APENAS JSON com os campos "
            f"{EXTRACTED_FIELDS} (use null se não encontrar; 'pontos' é número)."
        )
        try:
            data = json.loads(_strip_json(_openai_chat(_vision_msg(prompt, image_b64, mime))))
            return {k: data.get(k) for k in EXTRACTED_FIELDS}
        except Exception:
            pass
    out = dict(_EMPTY)
    out["descricao_infracao"] = "(extração indisponível — configure uma chave de LLM)"
    return out


# ---------------------------------------------------------------- passo 2
_STRONG_HINTS = ("sinaliz", "placa", "prazo", "notific", "veiculo", "veículo", "agente")


def _fallback_nullities(answers: dict) -> dict:
    flagged = [k for k, v in (answers or {}).items() if v is True]
    if not flagged:
        return {
            "status": "valid",
            "nullities": [],
            "summary": "Sem indícios de nulidade a partir das respostas fornecidas.",
            "recommendation": "A multa aparenta regularidade. Reavalie com um especialista se desejar.",
        }
    strong = [k for k in flagged if any(h in k.lower() for h in _STRONG_HINTS)]
    nullities = []
    for k in flagged:
        alta = k in strong
        nullities.append({
            "titulo": f"Possível vício relacionado a '{k}'",
            "base_legal": "CTB art. 280/281 (a confirmar com a análise jurídica)",
            "descricao": (
                "Resposta do condutor indica possível irregularidade neste ponto; "
                "requer verificação documental."
            ),
            "gravidade": "alta" if alta else "media",
        })
    status = "null" if strong else "weak"
    return {
        "status": status,
        "nullities": nullities,
        "summary": f"{len(nullities)} ponto(s) potencialmente questionável(is) a partir do questionário.",
        "recommendation": (
            "Há fundamentos para recurso." if status == "null"
            else "Cabe contestação, com análise mais detalhada."
        ),
    }


def analyze_nullities(extracted: dict, answers: dict) -> dict:
    if llm_available():
        prompt = (
            "Você é advogado de trânsito (CTB/CONTRAN/jurisprudência). Dados da multa:\n"
            f"{json.dumps(extracted, ensure_ascii=False)}\n\nRespostas do condutor:\n"
            f"{json.dumps(answers, ensure_ascii=False)}\n\n"
            "Identifique nulidades formais (art. 280/281), materiais e processuais. "
            'Retorne APENAS JSON: {"status":"null|weak|valid","nullities":[{"titulo","base_legal",'
            '"descricao","gravidade":"alta|media|baixa"}],"summary","recommendation"}. '
            'Regra: "null"=nulidade de alta gravidade; "weak"=média; "valid"=sem nulidade relevante.'
        )
        try:
            messages = [
                {"role": "system", "content": "Advogado especialista em recursos de multas de trânsito."},
                {"role": "user", "content": prompt},
            ]
            data = json.loads(_strip_json(_openai_chat(messages)))
            return {
                "status": data.get("status", "valid"),
                "nullities": data.get("nullities", []),
                "summary": data.get("summary", ""),
                "recommendation": data.get("recommendation", ""),
            }
        except Exception:
            pass
    return _fallback_nullities(answers)


def analyze(*, image_b64: str, mime: str, file_size: int, answers: dict) -> dict:
    """Orquestra os 3 passos. Retorna dict com 'rejected' quando não é multa."""
    validation = validate_document(image_b64, mime, file_size)
    if not validation["is_traffic_fine"]:
        return {"rejected": True, "validation": validation}
    extracted = extract_data(image_b64, mime)
    verdict = analyze_nullities(extracted, answers)
    return {"rejected": False, "extracted": extracted, "verdict": verdict}
