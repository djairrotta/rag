"""Regras de preço do Segura Multas (server-side; o cliente NUNCA calcula preço).

B2C (cliente final):
- Preço do recurso = 20% do valor da multa, teto R$300.
- Sem valor legível => fallback R$69,90.
- Embriaguez (CTB 165 e 165-A) => analisada normalmente, mas com PREÇO diferenciado
  (b2c_drunk_price_brl) e AVISO próprio. NÃO é mais CONTACT_REQUIRED.

B2B (parceiro):
- R$250 FIXO por recurso gerado (partner_price_per_recurso_brl). Sem faixa, sem %, sem mensalidade.

Todos os valores vêm de settings (config.py) e serão editáveis pelo admin na dashboard (Fase 5).
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
    kind: str                       # "PRICE" (sempre, agora) — mantido p/ compat.
    amount: float | None = None
    source: str | None = None       # "percentage" | "fallback" | "drunk" | "partner_flat"
    fine_value: float | None = None
    reason: str | None = None       # "DRUNK_DRIVING" (informativo)
    message: str | None = None      # aviso ao cliente (embriaguez tem aviso próprio)


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
    """Preço B2C (cliente final). SEMPRE retorna PRICE (nunca mais CONTACT_REQUIRED).

    - Embriaguez (CTB 165/165-A): analisada normalmente, mas com PREÇO diferenciado
      (settings.b2c_drunk_price_brl) e um AVISO próprio. Não manda mais pro WhatsApp.
    - Demais: 20% da multa (teto R$300); sem valor legível → fallback R$69,90.
    """
    if is_drunk_driving(extracted):
        return PriceDecision(
            kind="PRICE",
            amount=round(settings.b2c_drunk_price_brl, 2),
            source="drunk",
            fine_value=parse_fine_value((extracted or {}).get("valor_multa")),
            reason="DRUNK_DRIVING",
            message=(
                "Este é um caso de embriaguez ao volante (CTB art. 165/165-A), matéria de "
                "maior complexidade. A análise é feita normalmente, com valor diferenciado, "
                "e recomendamos acompanhamento jurídico especializado para o recurso."
            ),
        )
    fine_value = parse_fine_value((extracted or {}).get("valor_multa"))
    if fine_value is None:
        return PriceDecision(kind="PRICE", amount=settings.b2c_price_fallback_brl, source="fallback", fine_value=None)
    capped = min(fine_value * settings.b2c_price_percent, settings.b2c_price_cap_brl)
    return PriceDecision(kind="PRICE", amount=round(capped, 2), source="percentage", fine_value=round(fine_value, 2))


def decide_partner_price(extracted: dict | None = None) -> PriceDecision:
    """Preço B2B (parceiro): R$250 FIXO por recurso gerado. Sem faixa, sem %, sem mensalidade.

    Recebe `extracted` só por simetria de assinatura (não é usado no cálculo, mas permite
    aviso de embriaguez se o parceiro quiser exibir). O valor vem de settings (editável pelo admin).
    """
    msg = None
    if is_drunk_driving(extracted):
        msg = "Caso de embriaguez (CTB 165/165-A) — matéria complexa; recomendável revisão do recurso."
    return PriceDecision(
        kind="PRICE",
        amount=round(settings.partner_price_per_recurso_brl, 2),
        source="partner_flat",
        message=msg,
    )
