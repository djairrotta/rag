"""Teste e2e B3-RAGFlow (rag) — backend de retrieval do /search via RAGFlow (mockado).

Valida o mapeamento da resposta do RAGFlow para o shape do contrato /search e o
filtro exato por código. Não toca a instância real (httpx.MockTransport).

Uso: /tmp/sm-venv/bin/python tests_e2e_b3_ragflow.py
"""
import sys

import httpx

from app.core import ragflow

PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


def handler(request: httpx.Request) -> httpx.Response:
    if request.url.path.endswith("/api/v1/retrieval"):
        return httpx.Response(200, json={"code": 0, "data": {"chunks": [
            {"id": "c1", "content": "Ficha 501-00: iniciar obra ...", "document_id": "doc1",
             "similarity": 0.82, "important_keywords": ["501-00", "Art. 95."]},
            {"id": "c2", "content": "Ficha 502-91: outra ...", "document_id": "doc1",
             "similarity": 0.40, "important_keywords": ["502-91", "Art. 95."]},
        ]}})
    return httpx.Response(404, json={"code": 1, "message": "no mock"})


# configura o backend RAGFlow e injeta o transport mockado
ragflow.settings.ragflow_base_url = "http://rf.test"
ragflow.settings.ragflow_api_key = "k"
ragflow.settings.ragflow_dataset_id = "ds1"   # fixa o dataset (pula lookup por nome)
ragflow._dataset_id_cache = None
ragflow._client = lambda: httpx.Client(base_url="http://rf.test/api/v1", transport=httpx.MockTransport(handler))

print("=== enabled() reflete base_url ===")
check("enabled() = True com base_url setado", ragflow.enabled() is True)

print("\n=== /search sem filtro de código (semântico) ===")
res = ragflow.search("ausência de sinalização", None, top_k=5)
check("devolve 2 resultados", len(res) == 2, str(len(res)))
check("mapeia content/score/source", res[0]["content"].startswith("Ficha 501-00")
      and res[0]["score"] == 0.82 and res[0]["source"] == "ragflow", str(res[0]))
check("extrai código dos important_keywords", res[0]["codigo"] == "501-00" and res[1]["codigo"] == "502-91",
      str([r["codigo"] for r in res]))
check("carrega ids do RAGFlow", res[0]["ragflow_chunk_id"] == "c1" and res[0]["ragflow_document_id"] == "doc1")

print("\n=== /search com filtro exato de código ===")
res = ragflow.search("iniciar obra", {"codigo": "501-00"}, top_k=5)
check("filtro exato restringe ao código", len(res) == 1 and res[0]["codigo"] == "501-00", str(res))

print("\n=== filtro de código sem match → devolve semântico (não vazio) ===")
res = ragflow.search("qualquer", {"codigo": "999-99"}, top_k=5)
check("sem match exato, não zera os resultados", len(res) == 2, str(len(res)))

print(f"\n=== RESULTADO B3-RAGFLOW(rag): {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
