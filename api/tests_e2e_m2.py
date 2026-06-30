"""Teste e2e M2 — auth (register/login/me/refresh com rotação/logout) + claim uso único.
Roda contra o Postgres de teste (env POSTGRES_* já apontando p/ localhost:5433).
Uso: /tmp/sm-venv/bin/python tests_e2e_m2.py
"""
import uuid
import sys

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Analysis

client = TestClient(app)

PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    if cond:
        PASS += 1
    else:
        FAIL += 1
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


email = f"teste-{uuid.uuid4().hex[:8]}@exemplo.com.br"
pw = "Senha#Forte123"

print("=== AUTH ===")

# 1. register
r = client.post("/auth/register", json={"email": email, "password": pw, "name": "Fulano de Tal"})
check("register → 201", r.status_code == 201, r.text)
body = r.json()
check("register devolve user+access+refresh",
      {"user", "access_token", "refresh_token"} <= body.keys(), str(body.keys()))
check("register: role=user", body.get("user", {}).get("role") == "user")
check("register: e-mail ecoado", body.get("user", {}).get("email") == email)
access1, refresh1 = body["access_token"], body["refresh_token"]

# 2. /me com access do register
r = client.get("/auth/me", headers={"Authorization": f"Bearer {access1}"})
check("me(access) → 200", r.status_code == 200, r.text)
check("me: e-mail correto", r.json().get("email") == email)
check("me: partner_id presente (null p/ user)", "partner_id" in r.json())

# 3. login
r = client.post("/auth/login", json={"email": email, "password": pw})
check("login → 200", r.status_code == 200, r.text)
login_body = r.json()
access2, refresh2 = login_body["access_token"], login_body["refresh_token"]
check("login: refresh diferente do register", refresh2 != refresh1)

# 4. refresh com rotação
r = client.post("/auth/refresh", json={"refresh_token": refresh2})
check("refresh → 200", r.status_code == 200, r.text)
ref_body = r.json()
access3, refresh3 = ref_body["access_token"], ref_body["refresh_token"]
check("refresh: novo par emitido", refresh3 != refresh2 and access3 != access2)

# 5. reusar o refresh já rotacionado → 401
r = client.post("/auth/refresh", json={"refresh_token": refresh2})
check("refresh reusado (rotação) → 401", r.status_code == 401, r.text)
check("envelope de erro {error:{code,message}}",
      "error" in r.json() and "code" in r.json().get("error", {}), r.text)

# 6. /me com access do refresh
r = client.get("/auth/me", headers={"Authorization": f"Bearer {access3}"})
check("me(access pós-refresh) → 200", r.status_code == 200, r.text)

# 7. logout (revoga refresh3)
r = client.post("/auth/logout", json={"refresh_token": refresh3})
check("logout → 204", r.status_code == 204, r.text)

# 8. refresh após logout → 401
r = client.post("/auth/refresh", json={"refresh_token": refresh3})
check("refresh pós-logout (revogado) → 401", r.status_code == 401, r.text)

# 9. register duplicado → 409
r = client.post("/auth/register", json={"email": email, "password": pw, "name": "Outro"})
check("register duplicado → 409", r.status_code == 409, r.text)
check("código EMAIL_TAKEN", r.json().get("error", {}).get("code") == "EMAIL_TAKEN", r.text)

# 10. /me sem token → 401
r = client.get("/auth/me")
check("me sem token → 401", r.status_code == 401, r.text)

# 11. /me com token lixo → 401
r = client.get("/auth/me", headers={"Authorization": "Bearer xxx.yyy.zzz"})
check("me token inválido → 401", r.status_code == 401, r.text)

# 12. login senha errada → 401
r = client.post("/auth/login", json={"email": email, "password": "errada"})
check("login senha errada → 401", r.status_code == 401, r.text)

print("\n=== CLAIM (uso único) ===")

# cria uma análise anônima direto no banco (simula fluxo M4) com claim_token conhecido
ct = uuid.uuid4()
with SessionLocal() as db:
    an = Analysis(claim_token=ct, status="ready")
    db.add(an)
    db.commit()
    db.refresh(an)
    analysis_id = str(an.id)

# 13. claim correto → 200
r = client.post(f"/analyses/{analysis_id}/claim",
                json={"claim_token": str(ct)},
                headers={"Authorization": f"Bearer {access1}"})
check("claim correto → 200", r.status_code == 200, r.text)
check("claim: claimed=true e dono setado",
      r.json().get("claimed") is True and "user_id" in r.json(), r.text)

# 14. claim repetido (token já consumido) → 400 CLAIM_INVALID
r = client.post(f"/analyses/{analysis_id}/claim",
                json={"claim_token": str(ct)},
                headers={"Authorization": f"Bearer {access1}"})
check("claim repetido → 400", r.status_code == 400, r.text)
check("código CLAIM_INVALID", r.json().get("error", {}).get("code") == "CLAIM_INVALID", r.text)

# 15. claim em análise inexistente → 404
r = client.post(f"/analyses/{uuid.uuid4()}/claim",
                json={"claim_token": str(uuid.uuid4())},
                headers={"Authorization": f"Bearer {access1}"})
check("claim análise inexistente → 404", r.status_code == 404, r.text)

# 16. claim sem auth → 401
r = client.post(f"/analyses/{analysis_id}/claim", json={"claim_token": str(ct)})
check("claim sem auth → 401", r.status_code == 401, r.text)

print(f"\n=== RESULTADO: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
