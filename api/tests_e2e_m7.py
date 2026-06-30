"""Teste e2e M7 — POST /recursos (geração + entrega). Storage local, sem LLM (fallback).

Uso: STORAGE_DIR=/tmp/sm-storage-test INTERNAL_SECRET=test-internal OPENAI_API_KEY="" \
     /tmp/sm-venv/bin/python tests_e2e_m7.py
"""
import os
import sys
import uuid

os.environ.setdefault("STORAGE_DIR", "/tmp/sm-storage-test")
os.environ.setdefault("INTERNAL_SECRET", "test-internal")
os.environ.setdefault("OPENAI_API_KEY", "")

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Analysis, Recurso

client = TestClient(app)
INTERNAL = {"X-Internal-Secret": os.environ["INTERNAL_SECRET"]}
PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


def novo_usuario():
    email = f"m7-{uuid.uuid4().hex[:8]}@exemplo.com.br"
    r = client.post("/auth/register", json={"email": email, "password": "Senha#Forte123", "name": "M7"})
    return r.json()["access_token"], r.json()["user"]["id"]


def nova_analise(user_id, *, resource_available):
    with SessionLocal() as db:
        an = Analysis(
            user_id=uuid.UUID(user_id), status="null", resource_available=resource_available,
            campos={"numero_auto": "AB-12345", "valor_multa": "293,47", "codigo_infracao": "501-00",
                    "data_infracao": "10/03/2026", "local_infracao": "Av. Brasil, 100", "placa_veiculo": "ABC1D23"},
            questionario={"sem_sinalizacao": True},
            nulidades=[{"titulo": "Ausência de sinalização regulamentar",
                        "base_legal": "art. 280, VI, do CTB", "descricao": "Local sem placa.",
                        "gravidade": "alta"}],
            veredito={"summary": "Indícios de nulidade por ausência de sinalização.",
                      "recommendation": "Recorrer."},
        )
        db.add(an); db.commit(); db.refresh(an)
        return str(an.id)


tokenA, uidA = novo_usuario()
HA = {"Authorization": f"Bearer {tokenA}"}

print("=== GATE DE PAGAMENTO ===")
aid = nova_analise(uidA, resource_available=False)
r = client.post("/recursos", json={"analysis_id": aid}, headers=HA)
check("não pago → 402 PAYMENT_REQUIRED", r.status_code == 402 and r.json().get("error_code") == "PAYMENT_REQUIRED", r.text)

print("\n=== GERAÇÃO INTERNA (webhook: internal + force) ===")
r = client.post("/recursos", json={"analysis_id": aid, "force": True}, headers=INTERNAL)
j = r.json()
check("interno+force → 200 success", r.status_code == 200 and j.get("success") is True, r.text)
check("generated=true", j.get("generated") is True, str(j))
check("recurso_id presente", bool(j.get("recurso_id")), str(j))
check("engine=fallback (sem LLM)", j.get("engine") == "fallback", str(j))
check("storage=local", j.get("storage") == "local", str(j))
check("download_url presente", bool(j.get("download_url")), str(j))
rec_id = j["recurso_id"]

print("\n=== CONTEÚDO DO RECURSO ===")
with SessionLocal() as db:
    rec = db.get(Recurso, uuid.UUID(rec_id))
    md = rec.md or ""
    check("tem 'DOS FATOS'", "DOS FATOS" in md, md[:60])
    check("tem 'DO DIREITO'", "DO DIREITO" in md, "")
    check("tem 'DO PEDIDO'", "DO PEDIDO" in md, "")
    check("cita a base legal da nulidade (art. 280, VI)", "art. 280, VI" in md, "")
    check("usa dados do auto (número AB-12345)", "AB-12345" in md, "")
    check("fecha com 'pede deferimento'", "pede deferimento" in md.lower(), "")

print("\n=== CACHE (já gerado) ===")
r = client.post("/recursos", json={"analysis_id": aid, "force": True}, headers=INTERNAL)
check("regenerar → generated=false (cache)", r.json().get("generated") is False, r.text)
check("engine=cache", r.json().get("engine") == "cache", r.text)

print("\n=== METADADOS + DOWNLOAD ===")
r = client.get(f"/recursos/{rec_id}", headers=HA)
check("GET recurso (dono) → 200", r.status_code == 200 and r.json().get("status") == "ready", r.text)
r = client.get(f"/recursos/{rec_id}/download", headers=HA)
check("download → 200", r.status_code == 200, str(r.status_code))
ct = r.headers.get("content-type", "")
check("content-type DOCX", "wordprocessingml" in ct, ct)
check("arquivo .docx válido (magic PK)", r.content[:2] == b"PK" and len(r.content) > 1000, str(len(r.content)))

print("\n=== POSSE / AUTENTICAÇÃO / PAGO ===")
tokenB, _ = novo_usuario()
r = client.get(f"/recursos/{rec_id}/download", headers={"Authorization": f"Bearer {tokenB}"})
check("download por não-dono → 403", r.status_code == 403, r.text)
r = client.post("/recursos", json={"analysis_id": aid, "force": True})  # sem token, sem internal
check("sem auth → 401", r.status_code == 401, r.text)

# caminho pago (não-interno): análise liberada por pagamento
aid_paid = nova_analise(uidA, resource_available=True)
r = client.post("/recursos", json={"analysis_id": aid_paid}, headers=HA)
check("dono + pago → 200 generated", r.status_code == 200 and r.json().get("generated") is True, r.text)

# B tentando gerar recurso de análise paga de A → 403
r = client.post("/recursos", json={"analysis_id": aid_paid}, headers={"Authorization": f"Bearer {tokenB}"})
check("gerar recurso de análise alheia → 403", r.status_code == 403, r.text)

r = client.post("/recursos", json={"analysis_id": str(uuid.uuid4())}, headers=INTERNAL)
check("análise inexistente → 404", r.status_code == 404, r.text)

print(f"\n=== RESULTADO M7: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
