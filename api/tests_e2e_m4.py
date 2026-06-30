"""Teste e2e M4 — POST /analyses (análise grátis), sem paywall (require_payment=False).
Sem chave de LLM => usa o fallback (veredito derivado do questionário).

Uso: POSTGRES_* apontando p/ o PG de teste; /tmp/sm-venv/bin/python tests_e2e_m4.py
"""
import json
import sys
import uuid

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Analysis

client = TestClient(app)
PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64  # bytes quaisquer, mime image/png


def analisar(answers=None, token=None, fname="multa.png", content=PNG, mime="image/png"):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client.post(
        "/analyses",
        files={"file": (fname, content, mime)},
        data={"questionnaire_answers": json.dumps(answers or {})},
        headers=headers,
    )


print("=== REJEIÇÃO ===")
r = analisar(fname="documento.txt", content=b"isto nao e uma multa", mime="text/plain")
check("não-multa (.txt) → 400", r.status_code == 400, r.text)
check("error_code NOT_TRAFFIC_FINE", r.json().get("error_code") == "NOT_TRAFFIC_FINE", r.text)
check("details.message presente", "message" in r.json().get("details", {}), r.text)

r = analisar(content=b"", fname="vazio.png")
check("arquivo vazio → 400", r.status_code == 400, r.text)
check("mensagem 'obrigatório'", "obrigat" in (r.json().get("error", "").lower()), r.text)

print("\n=== ANÔNIMO (sem paywall: não persiste) ===")
r = analisar(answers={})
check("sem respostas → 200", r.status_code == 200, r.text)
j = r.json()
check("status=valid (nada marcado)", j.get("status") == "valid", str(j))
check("sem nulidades", j.get("nullities") == [], str(j.get("nullities")))
check("analysis_id null (não persistiu)", j.get("analysis_id") is None, str(j))
check("claim_token null", j.get("claim_token") is None, str(j))
check("is_authenticated false", j.get("is_authenticated") is False, str(j))
check("engine=fallback (sem LLM)", j.get("engine") == "fallback", str(j))

r = analisar(answers={"placa_divergente": True})
j = r.json()
check("dica forte (placa) → status null", j.get("status") == "null", str(j))
check("nulidade de gravidade alta", any(n.get("gravidade") == "alta" for n in j.get("nullities", [])), str(j))

r = analisar(answers={"algo_generico": True})
j = r.json()
check("dica fraca → status weak", j.get("status") == "weak", str(j))
check("nulidade de gravidade media", any(n.get("gravidade") == "media" for n in j.get("nullities", [])), str(j))

print("\n=== AUTENTICADO (persiste) ===")
email = f"m4-{uuid.uuid4().hex[:8]}@exemplo.com.br"
reg = client.post("/auth/register", json={"email": email, "password": "Senha#Forte123", "name": "Teste M4"})
token = reg.json()["access_token"]

r = analisar(answers={"sem_sinalizacao": True}, token=token)
j = r.json()
check("autenticado → 200", r.status_code == 200, r.text)
check("is_authenticated true", j.get("is_authenticated") is True, str(j))
check("analysis_id presente (persistiu)", bool(j.get("analysis_id")), str(j))
check("status null (sinalização)", j.get("status") == "null", str(j))
aid = j.get("analysis_id")

# confere persistência no banco
with SessionLocal() as db:
    an = db.get(Analysis, uuid.UUID(aid)) if aid else None
    check("linha existe no banco", an is not None)
    if an:
        check("campos/questionario/nulidades gravados",
              an.campos is not None and an.questionario == {"sem_sinalizacao": True} and bool(an.nulidades), str(an.status))
        check("resource_available=true (tem findings, sem paywall)", an.resource_available is True, str(an.resource_available))
        check("veredito tem summary+recommendation",
              isinstance(an.veredito, dict) and "summary" in an.veredito, str(an.veredito))

print(f"\n=== RESULTADO M4: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
