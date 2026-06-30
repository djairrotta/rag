"""Cliente Asaas (pagamentos) — porte do create-payment.

Real quando ASAAS_API_KEY está setada (sandbox/produção via ASAAS_ENV); sem chave,
simula cliente+cobrança de forma determinística pra exercitar o fluxo em teste.
O cliente NUNCA define o valor — o preço vem do server (pricing.decide_price).
"""
from __future__ import annotations

import uuid

import httpx

from app.core.config import settings

PROD_URL = "https://api.asaas.com/v3"
SANDBOX_URL = "https://sandbox.asaas.com/api/v3"


def enabled() -> bool:
    return bool(settings.asaas_api_key)


def provider_label() -> str:
    return ("asaas:" + settings.asaas_env) if enabled() else "simulado"


def _base_url() -> str:
    return PROD_URL if settings.asaas_env == "production" else SANDBOX_URL


def _headers() -> dict:
    return {"access_token": settings.asaas_api_key, "Content-Type": "application/json"}


def find_or_create_customer(*, email: str, name: str, cpf_cnpj: str | None,
                            phone: str | None = None, address: str | None = None,
                            city: str | None = None, postal_code: str | None = None) -> str:
    if not enabled():
        # simulação determinística por e-mail
        return "cus_sim_" + uuid.uuid5(uuid.NAMESPACE_DNS, email).hex[:16]

    base = _base_url()
    # procura por e-mail
    r = httpx.get(f"{base}/customers", params={"email": email}, headers=_headers(), timeout=30)
    r.raise_for_status()
    data = r.json().get("data") or []
    if data:
        return data[0]["id"]
    # cria
    payload = {
        "name": name or (email.split("@")[0] if email else "Cliente"),
        "email": email,
        "cpfCnpj": (cpf_cnpj or "").replace(".", "").replace("-", "").replace("/", "") or None,
        "phone": (phone or "").replace(" ", "") or None,
        "mobilePhone": (phone or "").replace(" ", "") or None,
        "address": address,
        "addressNumber": "S/N",
        "province": city,
        "postalCode": (postal_code or "").replace("-", "") or None,
        "notificationDisabled": False,
    }
    r = httpx.post(f"{base}/customers", json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def create_payment(*, customer_id: str, value: float, due_date: str, description: str,
                   external_reference: str, billing_type: str = "UNDEFINED") -> dict:
    if not enabled():
        pid = "pay_sim_" + uuid.uuid4().hex[:16]
        return {
            "id": pid,
            "status": "PENDING",
            "value": value,
            "dueDate": due_date,
            "invoiceUrl": f"{settings.api_base_url}/simulado/checkout/{pid}",
            "bankSlipUrl": None,
            "pixQrCodeUrl": f"{settings.api_base_url}/simulado/pix/{pid}",
            "billingType": billing_type,
        }

    base = _base_url()
    payload = {
        "customer": customer_id,
        "billingType": billing_type,
        "value": value,
        "dueDate": due_date,
        "description": description,
        "externalReference": external_reference,
        "postalService": False,
    }
    r = httpx.post(f"{base}/payments", json=payload, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()
