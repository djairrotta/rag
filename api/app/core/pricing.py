"""Regras de preço B2C — porte fiel de supabase/functions/_shared/pricing.ts.

- Preço do recurso = 20% do valor da multa, teto R$300.
- Sem valor legível => fallback R$69,90.
- Embriaguez (CTB 165 e 165-A) => CONTACT_REQUIRED (não cobra/gera automático).

O cliente NUNCA calcula preço — é sempre server-side (princípio nº 5 do blueprint).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.config import settings

DRUNK_KEYWORDS = (
    "alcool", "álcool", "alcoolemia", "embriaguez", "embriagado", "bafometro",
    "bafômetro", "etilometro", "etilômetro", "substancia psicoativa",
    "substância psicoativa", "165-a", "165 a", "art. 165", "artigo 165",
)


@dataclass
class PriceDecision:
    kind: str                       # "PRICE" | "CONTACT_REQUIRED"
    amount: float | None = None
    source: str | None = None       # "percentage" | "fallback"
    fine_value: float | None = None
    reason: str | None = None       # "DRUNK_DRIVING"
    message: str | None = None


def parse_fine_value(raw) -> float | None:
    """Aceita 'R$ 1.234,56', '1234.56', '1.234,56', '293,47'. Retorna None se não-positivo."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw) if raw > 0 else None
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    s = re.sub(r"[Rr]\$\s*", "", s)
    s = re.sub(r"\s+", "", s)
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        s = s.replace(".", "").replace(",", ".")
    elif has_comma:
        s = s.replace(",", ".")
    s = re.sub(r"[^\d.]", "", s)
    if not s:
        return None
    try:
        n = float(s)
    except ValueError:
        return None
    return n if n > 0 else None


def is_drunk_driving(extracted: dict | None) -> bool:
    """CTB art. 165 (enquadramentos 7455x) e 165-A (7571x — recusa do bafômetro)."""
    if not extracted:
        return False
    codigo = re.sub(r"\D", "", str(extracted.get("codigo_infracao") or ""))
    if codigo.startswith("7455") or codigo.startswith("7571"):
        return True
    desc = str(extracted.get("descricao_infracao") or "").lower()
    return any(k in desc for k in DRUNK_KEYWORDS) if desc else False


def decide_price(extracted: dict | None) -> PriceDecision:
    if is_drunk_driving(extracted):
        return PriceDecision(
            kind="CONTACT_REQUIRED",
            reason="DRUNK_DRIVING",
            message=(
                "Casos de embriaguez (CTB art. 165 e 165-A) exigem atendimento "
                "especializado. Entre em contato pelo WhatsApp ou e-mail para "
                "avaliarmos o seu caso."
            ),
        )
    fine_value = parse_fine_value((extracted or {}).get("valor_multa"))
    if fine_value is None:
        return PriceDecision(kind="PRICE", amount=settings.b2c_price_fallback_brl, source="fallback", fine_value=None)
    capped = min(fine_value * settings.b2c_price_percent, settings.b2c_price_cap_brl)
    return PriceDecision(kind="PRICE", amount=round(capped, 2), source="percentage", fine_value=round(fine_value, 2))
