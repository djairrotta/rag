"""Teste e2e M5 — pagamentos Asaas (modo simulado, sem chave) + webhook.
Preço SEMPRE server-side. Webhook valida asaas-access-token.

Uso: ASAAS_WEBHOOK_TOKEN=whsec_test ASAAS_API_KEY="" /tmp/sm-venv/bin/python tests_e2e_m5.py
"""
import os
import sys
import uuid

os.environ.setdefault("ASAAS_WEBHOOK_TOKEN", "whsec_test")
os.environ.setdefault("ASAAS_API_KEY", "")  # força simulado

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Analysis, Payment

client = TestClient(app)
WT = {"asaas-access-token": os.environ["ASAAS_WEBHOOK_TOKEN"]}
PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


def novo_usuario():
    email = f"m5-{uuid.uuid4().hex[:8]}@exemplo.com.br"
    r = client.post("/auth/register", json={"email": email, "password": "Senha#Forte123", "name": "M5"})
    return r.json()["access_token"], r.json()["user"]["id"]


def nova_analise(user_id, campos):
    with SessionLocal() as db:
        an = Analysis(user_id=uuid.UUID(user_id), status="weak", campos=campos, nulidades=[{"x": 1}])
        db.add(an); db.commit(); db.refresh(an)
        return str(an.id)


tokenA, uidA = novo_usuario()
HA = {"Authorization": f"Bearer {tokenA}"}

print("=== PREÇO SERVER-SIDE (simulado) ===")
# 293,47 -> 20% = 58.69
aid = nova_analise(uidA, {"valor_multa": "293,47", "codigo_infracao": "545-00"})
r = client.post("/payments", json={"analysis_id": aid}, headers=HA)
j = r.json()
check("pagamento criado → 200 success", r.status_code == 200 and j.get("success") is True, r.text)
check("valor 20% = 58.69", j.get("value") == 58.69, str(j.get("value")))
check("price_source percentage", j.get("price_source") == "percentage", str(j))
check("payment_id simulado", str(j.get("payment_id", "")).startswith("pay_sim_"), str(j))
check("status PENDING", j.get("status") == "PENDING", str(j))
check("engine simulado", j.get("engine") == "simulado", str(j))
asaas_pid = j["payment_id"]

# 5000 -> 20% = 1000 -> teto 300
aid_cap = nova_analise(uidA, {"valor_multa": "5000,00", "codigo_infracao": "545-00"})
r = client.post("/payments", json={"analysis_id": aid_cap}, headers=HA)
check("teto R$300 aplicado", r.json().get("value") == 300, str(r.json().get("value")))

# sem valor -> fallback 69.90
aid_fb = nova_analise(uidA, {"valor_multa": None, "codigo_infracao": "545-00"})
r = client.post("/payments", json={"analysis_id": aid_fb}, headers=HA)
check("fallback R$69,90", r.json().get("value") == 69.9 and r.json().get("price_source") == "fallback", str(r.json()))

print("\n=== EMBRIAGUEZ → CONTACT_REQUIRED ===")
aid_drunk = nova_analise(uidA, {"valor_multa": "2000,00", "codigo_infracao": "74550"})
r = client.post("/payments", json={"analysis_id": aid_drunk}, headers=HA)
j = r.json()
check("CONTACT_REQUIRED (200, success false)", r.status_code == 200 and j.get("success") is False, r.text)
check("error_code CONTACT_REQUIRED", j.get("error_code") == "CONTACT_REQUIRED", str(j))
with SessionLocal() as db:
    an = db.get(Analysis, uuid.UUID(aid_drunk))
    check("analysis.contact_required=true", an.contact_required is True, str(an.contact_required))

print("\n=== POSSE / AUTENTICAÇÃO ===")
tokenB, uidB = novo_usuario()
aid_b = nova_analise(uidB, {"valor_multa": "100,00"})
r = client.post("/payments", json={"analysis_id": aid_b}, headers=HA)  # A tentando pagar análise de B
check("análise de outro dono → 403", r.status_code == 403, r.text)
r = client.post("/payments", json={"analysis_id": aid})  # sem token
check("sem token → 401", r.status_code == 401, r.text)
r = client.post("/payments", json={"analysis_id": str(uuid.uuid4())}, headers=HA)
check("análise inexistente → 404", r.status_code == 404, r.text)

print("\n=== STATUS ===")
# pega o id interno do pagamento criado p/ a análise aid
with SessionLocal() as db:
    pay = db.query(Payment).filter(Payment.asaas_id == asaas_pid).first()
    pay_id = str(pay.id)
r = client.get(f"/payments/{pay_id}", headers=HA)
check("status → 200 PENDING", r.status_code == 200 and r.json().get("status") == "PENDING", r.text)
r = client.get(f"/payments/{pay_id}", headers={"Authorization": f"Bearer {tokenB}"})
check("status de pagamento alheio → 403", r.status_code == 403, r.text)

print("\n=== WEBHOOK ===")
r = client.post("/webhooks/asaas", json={"event": "PAYMENT_CONFIRMED", "payment": {"id": asaas_pid}},
                headers={"asaas-access-token": "errado"})
check("token errado → 401", r.status_code == 401, r.text)

r = client.post("/webhooks/asaas", json={}, headers=WT)
check("payload inválido → 400", r.status_code == 400, r.text)

r = client.post("/webhooks/asaas",
                json={"event": "PAYMENT_CONFIRMED", "payment": {"id": asaas_pid, "billingType": "PIX"}}, headers=WT)
check("confirma → 200 success", r.status_code == 200 and r.json().get("success") is True, r.text)
with SessionLocal() as db:
    pay = db.get(Payment, uuid.UUID(pay_id))
    an = db.get(Analysis, uuid.UUID(aid))
    check("pagamento CONFIRMED + paid_at", pay.status == "CONFIRMED" and pay.paid_at is not None, str(pay.status))
    check("método atualizado p/ PIX", pay.method == "PIX", str(pay.method))
    check("recurso liberado (resource_available=true)", an.resource_available is True, str(an.resource_available))

# idempotência
r = client.post("/webhooks/asaas",
                json={"event": "PAYMENT_RECEIVED", "payment": {"id": asaas_pid}}, headers=WT)
check("reconfirma → 200 (idempotente)", r.status_code == 200, r.text)

# pagamento desconhecido → tolerante
r = client.post("/webhooks/asaas",
                json={"event": "PAYMENT_CONFIRMED", "payment": {"id": "pay_inexistente"}}, headers=WT)
check("pagamento desconhecido → 200 tolerante", r.status_code == 200 and r.json().get("success") is True, r.text)

# reembolso revoga o recurso
r = client.post("/webhooks/asaas",
                json={"event": "PAYMENT_REFUNDED", "payment": {"id": asaas_pid}}, headers=WT)
with SessionLocal() as db:
    pay = db.get(Payment, uuid.UUID(pay_id))
    an = db.get(Analysis, uuid.UUID(aid))
    check("reembolso → status REFUNDED", pay.status == "REFUNDED", str(pay.status))
    check("recurso revogado (resource_available=false)", an.resource_available is False, str(an.resource_available))

print(f"\n=== RESULTADO M5: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
