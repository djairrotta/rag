"""Teste e2e B2 — casos + upload + fila + worker.

Storage local, fila em memória (sem Redis), sem LLM. Valida intake autenticado,
enfileiramento, ciclo do job (queued→done) pelo worker, e auth/validação.

Uso:
    STORAGE_DIR=/tmp/sm-storage-test QUEUE_BACKEND=memory \
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433 POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=localtest POSTGRES_DB=seguramultas \
    /tmp/sm-venv/bin/python tests_e2e_b2.py
"""
import os
import sys
import uuid

os.environ.setdefault("STORAGE_DIR", "/tmp/sm-storage-test")
os.environ.setdefault("QUEUE_BACKEND", "memory")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("OPENAI_API_KEY", "")

from fastapi.testclient import TestClient

from app.main import app
from app.db.session import SessionLocal
from app.models import Case, CaseFile, ProcessingJob
from app.services import jobs, queue

client = TestClient(app)
PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


def novo_usuario(tag):
    email = f"b2-{tag}-{uuid.uuid4().hex[:8]}@exemplo.com.br"
    r = client.post("/auth/register", json={"email": email, "password": "Senha#Forte123", "name": "B2"})
    j = r.json()
    return j["access_token"], j["user"]["id"]


JPG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 64  # bytes arbitrários; B2 não analisa conteúdo

tokenA, uidA = novo_usuario("a")
tokenB, uidB = novo_usuario("b")
HA = {"Authorization": f"Bearer {tokenA}"}
HB = {"Authorization": f"Bearer {tokenB}"}

print("=== CRIAÇÃO DE CASO ===")
r = client.post("/cases", json={"title": "Multa Av. Brasil"}, headers=HA)
check("POST /cases → 201", r.status_code == 201, r.text)
case = r.json()
cid = case.get("id")
check("retorna id do caso", bool(cid), str(case))
check("status inicial = uploaded", case.get("status") == "uploaded", str(case))

print("\n=== AUTH / OWNERSHIP / VALIDAÇÃO ===")
r = client.post("/cases", json={"title": "x"})
check("POST /cases sem token → 401", r.status_code == 401, r.text)

r = client.get(f"/cases/{cid}", headers=HB)
check("GET caso de outro dono → 403", r.status_code == 403 and r.json()["error"]["code"] == "NOT_OWNER", r.text)

r = client.post(f"/cases/{cid}/files", files={"file": ("m.jpg", JPG, "image/jpeg")}, headers=HB)
check("upload em caso alheio → 403", r.status_code == 403, r.text)

r = client.get(f"/cases/{uuid.uuid4()}", headers=HA)
check("caso inexistente → 404", r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND", r.text)

r = client.post(f"/cases/{cid}/files", files={"file": ("vazio.jpg", b"", "image/jpeg")}, headers=HA)
check("arquivo vazio → 400 VALIDATION", r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION", r.text)

r = client.post(f"/cases/{cid}/files", files={"file": ("a.txt", b"texto", "text/plain")}, headers=HA)
check("mime não suportado → 400 VALIDATION", r.status_code == 400 and r.json()["error"]["code"] == "VALIDATION", r.text)

print("\n=== UPLOAD + ENFILEIRAMENTO ===")
q = queue.get_queue()
check("backend de fila = memory", q.backend() == "memory", q.backend())
size_before = q.size()
r = client.post(f"/cases/{cid}/files", files={"file": ("multa.jpg", JPG, "image/jpeg")},
                data={"file_type": "ticket"}, headers=HA)
check("upload válido → 201", r.status_code == 201, r.text)
up = r.json()
fid, jid = up.get("file_id"), up.get("job_id")
check("retorna file_id e job_id", bool(fid) and bool(jid), str(up))
check("status = queued", up.get("status") == "queued", str(up))
check("storage = local (sem MinIO)", up.get("storage") == "local", str(up))
check("fila cresceu em 1", q.size() == size_before + 1, f"{size_before}->{q.size()}")

with SessionLocal() as db:
    cf = db.get(CaseFile, uuid.UUID(fid))
    check("CaseFile gravado (pending)", cf is not None and cf.processing_status == "pending",
          getattr(cf, "processing_status", None))
    check("CaseFile tem sha256 e tamanho", bool(cf.sha256) and cf.size_bytes == len(JPG))
    jb = db.get(ProcessingJob, uuid.UUID(jid))
    check("ProcessingJob criado (queued)", jb is not None and jb.status == "queued", getattr(jb, "status", None))
    check("job é process_case", jb.job_type == "process_case", jb.job_type)
    # caso passou a processing ao receber arquivo
    cs = db.get(Case, uuid.UUID(cid))
    check("caso → processing após upload", cs.status == "processing", cs.status)

print("\n=== WORKER DRENA A FILA ===")
with SessionLocal() as db:
    n = jobs.drain(db, queue=q)
check("drain processou 1 job", n == 1, str(n))

with SessionLocal() as db:
    jb = db.get(ProcessingJob, uuid.UUID(jid))
    check("job → done", jb.status == "done", jb.status)
    check("job.result tem nota do B2/B4", bool(jb.result) and "B4" in (jb.result.get("note") or ""), str(jb.result))
    check("job.attempts = 1", jb.attempts == 1, str(jb.attempts))
    check("job.finished_at preenchido", jb.finished_at is not None)
    cf = db.get(CaseFile, uuid.UUID(fid))
    check("CaseFile → received", cf.processing_status == "received", cf.processing_status)
    cs = db.get(Case, uuid.UUID(cid))
    check("caso.current_step = intake_done", cs.current_step == "intake_done", cs.current_step)

check("drain novamente → 0 (fila vazia)", jobs.drain(SessionLocal(), queue=q) == 0)

print("\n=== GET /cases/{id} (polling) ===")
r = client.get(f"/cases/{cid}", headers=HA)
check("GET caso → 200", r.status_code == 200, r.text)
body = r.json()
check("lista 1 arquivo recebido", len(body["files"]) == 1 and body["files"][0]["processing_status"] == "received",
      str(body.get("files")))
check("lista 1 job concluído", len(body["jobs"]) == 1 and body["jobs"][0]["status"] == "done",
      str(body.get("jobs")))

print(f"\n=== RESULTADO B2: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
