"""Teste e2e M3 — RAG. Roda em modo local (Qdrant :memory:), sem chave OpenAI
(usa o fallback determinístico). Valida contrato + ingestão + busca + filtros + auth.

Uso: QDRANT_LOCATION=:memory: RAG_API_KEY=segredo /tmp/sm-venv/bin/python tests_e2e_m3.py
"""
import os
import sys

# força modo local e uma chave de RAG p/ testar o Bearer
os.environ.setdefault("QDRANT_LOCATION", ":memory:")
os.environ.setdefault("RAG_API_KEY", "segredo-rag")

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
H = {"Authorization": f"Bearer {os.environ['RAG_API_KEY']}"}

PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


print("=== AUTH ===")
r = client.get("/health")
check("health sem token → 200 (livre)", r.status_code == 200 and r.json().get("ok") is True, r.text)
r = client.get("/status")
check("status sem token → 401", r.status_code == 401, r.text)
r = client.get("/status", headers={"Authorization": "Bearer errado"})
check("status token errado → 401", r.status_code == 401, r.text)

print("\n=== INGESTÃO (reindex/seed) ===")
r = client.post("/reindex", json={}, headers=H)
check("reindex → 200 + job_id", r.status_code == 200 and "job_id" in r.json(), r.text)
job_id = r.json().get("job_id")
check("reindex status=completed", r.json().get("status") == "completed", r.text)

r = client.get(f"/reindex/{job_id}", headers=H)
check("reindex/{job_id} → completed", r.status_code == 200 and r.json().get("status") == "completed", r.text)
result = r.json().get("result", {})
check("ingestão indexou chunks", result.get("chunks", 0) > 0, str(result))
check("usou >1 coleção", len(result.get("collections", [])) >= 2, str(result.get("collections")))

r = client.get(f"/reindex/inexistente", headers=H)
check("reindex/{desconhecido} → unknown", r.json().get("status") == "unknown", r.text)

print("\n=== STATUS ===")
r = client.get("/status", headers=H)
st = r.json()
check("status → 200 healthy", r.status_code == 200 and st.get("healthy") is True, r.text)
check("chunks > 0", st.get("chunks", 0) > 0, str(st))
check("fichas_indexed (mbft) > 0", st.get("fichas_indexed", 0) > 0, str(st))
check("embed_provider=fallback (sem chave)", st.get("embed_provider") == "fallback", str(st))
check("embed_dim=3072", st.get("embed_dim") == 3072, str(st))
check("last_ingested_at preenchido", bool(st.get("last_ingested_at")), str(st))

print("\n=== BUSCA SEMÂNTICA ===")
r = client.post("/search", json={"consulta": "estacionamento sem sinalização no local", "top_k": 5}, headers=H)
check("search → 200 + results", r.status_code == 200 and "results" in r.json(), r.text)
results = r.json()["results"]
check("retornou resultados", len(results) > 0, str(len(results)))
if results:
    top = results[0]
    check("resultado tem texto+score+fonte", {"texto", "score", "fonte"} <= top.keys(), str(top.keys()))
    check("top relevante (fala de sinalização)", "sinaliz" in top.get("texto", "").lower(), top.get("texto", "")[:80])

# consulta vazia → vazio
r = client.post("/search", json={"consulta": "   ", "top_k": 5}, headers=H)
check("consulta vazia → results vazio", r.json().get("results") == [], r.text)

print("\n=== FILTROS (metadado) ===")
r = client.post("/search", json={"consulta": "recurso trânsito", "filtros": {"tipo": "acordao"}, "top_k": 8}, headers=H)
res_f = r.json()["results"]
check("filtro tipo=acordao só traz acórdãos",
      len(res_f) > 0 and all(x.get("tipo") == "acordao" for x in res_f), str([x.get("tipo") for x in res_f]))

r = client.post("/search", json={"consulta": "qualquer", "filtros": {"codigo": "501-00"}, "top_k": 8}, headers=H)
res_c = r.json()["results"]
check("filtro codigo=501-00 restringe",
      len(res_c) > 0 and all(x.get("codigo") == "501-00" for x in res_c), str([x.get("codigo") for x in res_c]))

print("\n=== VISIBILIDADE POR PARCEIRO ===")
# ingesta um doc exclusivo de um parceiro
from app.core import ingest as _ing
PID = "11111111-1111-1111-1111-111111111111"
_ing.ingest_documents(
    [{"type": "mbft", "tipo": "ficha", "fonte": "parceiro/exclusiva", "tema": "exclusivo-parceiro",
      "codigo": "999-99", "texto": "Conteúdo exclusivo do parceiro sobre tema reservado e particular."}],
    default_partner_id=PID,
)
# visitante (sem partner_id) NÃO vê o doc do parceiro
r = client.post("/search", json={"consulta": "tema reservado particular", "filtros": {"codigo": "999-99"}, "top_k": 5}, headers=H)
check("global NÃO enxerga doc do parceiro", len(r.json()["results"]) == 0, str(r.json()["results"]))
# com o partner_id correto, vê
r = client.post("/search", json={"consulta": "tema reservado particular", "filtros": {"codigo": "999-99"}, "partner_id": PID, "top_k": 5}, headers=H)
check("parceiro enxerga o próprio doc", len(r.json()["results"]) > 0, str(r.json()["results"]))

print(f"\n=== RESULTADO M3: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
