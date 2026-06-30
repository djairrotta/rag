"""Teste e2e B3-RAGFlow (api) — cliente RAGFlow com HTTP mockado + backfill no Postgres.

Não toca a instância real: usa httpx.MockTransport para simular a API do RAGFlow,
validando o shape das requisições, o parsing das respostas e a gravação dos
ragflow_*_id em legal_document_chunks.

Uso:
    POSTGRES_HOST=127.0.0.1 POSTGRES_PORT=5433 POSTGRES_USER=postgres \
    POSTGRES_PASSWORD=localtest POSTGRES_DB=seguramultas \
    /tmp/sm-venv/bin/python tests_e2e_b3_ragflow.py
"""
import json
import os
import sys
import uuid

os.environ.setdefault("RAGFLOW_DATASET_NAME", "seguramultas_mbft")

import httpx

from app.services.ragflow_client import RagflowClient
from app.services import legal_ingest

PASS, FAIL = 0, 0


def check(label, cond, extra=""):
    global PASS, FAIL
    mark = "✓" if cond else "✗"
    PASS, FAIL = (PASS + 1, FAIL) if cond else (PASS, FAIL + 1)
    print(f"  [{mark}] {label}" + (f"  ({extra})" if extra and not cond else ""))


def make_handler(calls, *, dataset_exists=False, docs_existing=None, chunk_counter=None):
    docs_existing = docs_existing if docs_existing is not None else []
    chunk_counter = chunk_counter if chunk_counter is not None else {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        calls.append((method, path, request))
        # datasets
        if path.endswith("/api/v1/datasets") and method == "GET":
            data = [{"id": "ds1", "name": "seguramultas_mbft"}] if dataset_exists else []
            return httpx.Response(200, json={"code": 0, "data": data})
        if path.endswith("/api/v1/datasets") and method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"id": "ds1", "name": "seguramultas_mbft"}})
        # documents
        if path.endswith("/documents") and method == "GET":
            return httpx.Response(200, json={"code": 0, "data": {"docs": list(docs_existing)}})
        if path.endswith("/documents") and method == "POST":
            return httpx.Response(200, json={"code": 0, "data": [{"id": "doc1", "name": "MBFT.txt"}]})
        if path.endswith("/documents") and method == "DELETE":
            return httpx.Response(200, json={"code": 0, "data": True})
        # chunks
        if path.endswith("/chunks") and method == "POST":
            chunk_counter["n"] += 1
            return httpx.Response(200, json={"code": 0, "data": {"chunk": {"id": f"ch_{chunk_counter['n']}"}}})
        # retrieval
        if path.endswith("/api/v1/retrieval") and method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"chunks": [
                {"id": "c1", "content": "Ficha 501-00 ...", "document_id": "doc1",
                 "similarity": 0.81, "important_keywords": ["501-00", "Art. 95."]},
                {"id": "c2", "content": "Ficha 542-81 ...", "document_id": "doc1",
                 "similarity": 0.55, "important_keywords": ["542-81", "Art. 181, V."]},
            ]}})
        return httpx.Response(404, json={"code": 1, "message": f"no mock for {method} {path}"})

    return handler


print("=== CLIENTE: find_or_create_dataset (cria quando não existe) ===")
calls: list = []
c = RagflowClient("http://rf.test", "k", transport=httpx.MockTransport(make_handler(calls, dataset_exists=False)))
ds = c.find_or_create_dataset("seguramultas_mbft", embedding_model="")
check("retorna id do dataset criado", ds == "ds1", ds)
check("fez GET e POST em /datasets", any(m == "GET" and p.endswith("/datasets") for m, p, _ in calls)
      and any(m == "POST" and p.endswith("/datasets") for m, p, _ in calls))
c.close()

print("\n=== CLIENTE: find_or_create_dataset (reusa quando existe) ===")
calls = []
c = RagflowClient("http://rf.test", "k", transport=httpx.MockTransport(make_handler(calls, dataset_exists=True)))
ds = c.find_or_create_dataset("seguramultas_mbft")
check("reusa dataset existente", ds == "ds1", ds)
check("NÃO fez POST em /datasets", not any(m == "POST" and p.endswith("/datasets") for m, p, _ in calls))
c.close()

print("\n=== CLIENTE: ensure_clean_document (apaga antigo + sobe novo) ===")
calls = []
c = RagflowClient("http://rf.test", "k",
                  transport=httpx.MockTransport(make_handler(calls, docs_existing=[{"id": "old", "name": "MBFT 2022.txt"}])))
doc_id = c.ensure_clean_document("ds1", "MBFT 2022.txt", b"prov")
check("retorna id do documento novo", doc_id == "doc1", doc_id)
check("apagou o documento antigo (DELETE)", any(m == "DELETE" for m, p, _ in calls))
check("subiu o documento novo (POST documents)", any(m == "POST" and p.endswith("/documents") for m, p, _ in calls))
c.close()

print("\n=== CLIENTE: add_chunk + retrieval (shape) ===")
calls = []
c = RagflowClient("http://rf.test", "k", transport=httpx.MockTransport(make_handler(calls)))
ch = c.add_chunk("ds1", "doc1", "conteúdo da ficha", ["501-00", "Art. 95."])
check("add_chunk devolve id", ch == "ch_1", ch)
res = c.retrieval("ausência de sinalização", ["ds1"], top_k=5)
check("retrieval devolve 2 chunks", len(res) == 2, str(len(res)))
# valida o corpo da requisição de retrieval
ret_req = [r for m, p, r in calls if p.endswith("/retrieval")][0]
body = json.loads(ret_req.content)
check("retrieval envia question/dataset_ids/top_k", body.get("question") and body.get("dataset_ids") == ["ds1"]
      and body.get("top_k") == 5, json.dumps(body))
c.close()

# ---------------------------------------------------------------------------
# Backfill no Postgres
# ---------------------------------------------------------------------------
if os.environ.get("POSTGRES_HOST"):
    print("\n=== PUSH + BACKFILL dos ragflow_*_id (Postgres) ===")
    from app.db.session import SessionLocal
    from app.models import LegalDocument, LegalDocumentChunk

    VER = "ragflowtest"
    db = SessionLocal()
    try:
        # limpa eventual resíduo de execuções anteriores
        old = db.query(LegalDocument).filter(
            LegalDocument.document_type == "mbft", LegalDocument.version_label == VER).all()
        for d in old:
            db.query(LegalDocumentChunk).filter(LegalDocumentChunk.legal_document_id == d.id).delete()
            db.delete(d)
        db.commit()

        doc = LegalDocument(name="MBFT teste", document_type="mbft", version_label=VER, is_active=True)
        db.add(doc); db.flush()
        for i in range(2):
            db.add(LegalDocumentChunk(
                legal_document_id=doc.id, chunk_index=i, section="Ficha de Fiscalização",
                article=("Art. 95." if i == 0 else "Art. 181, V."),
                content=f"conteúdo ficha {i}",
                chunk_metadata={"infraction_code": ("501-00" if i == 0 else "542-81"), "gravidade": "Gravíssima"},
                content_hash=f"h{i}",
            ))
        db.commit()

        calls = []
        client = RagflowClient("http://rf.test", "k",
                               transport=httpx.MockTransport(make_handler(calls)))
        rep = legal_ingest.push_mbft_to_ragflow(db, version_label=VER, client=client)
        check("push reportou 2 chunks", rep["chunks_pushed"] == 2, str(rep))
        check("dataset_id no relatório", rep["dataset_id"] == "ds1", str(rep))

        rows = db.query(LegalDocumentChunk).filter(
            LegalDocumentChunk.legal_document_id == doc.id).order_by(LegalDocumentChunk.chunk_index).all()
        check("ambos os chunks com ragflow_dataset_id", all(r.ragflow_dataset_id == "ds1" for r in rows))
        check("ambos com ragflow_document_id", all(r.ragflow_document_id == "doc1" for r in rows))
        check("ambos com ragflow_chunk_id distinto", len({r.ragflow_chunk_id for r in rows}) == 2
              and all(r.ragflow_chunk_id for r in rows),
              str([r.ragflow_chunk_id for r in rows]))
        # keyword do código foi enviada no add_chunk
        chunk_reqs = [json.loads(r.content) for m, p, r in calls if p.endswith("/chunks")]
        check("add_chunk enviou o código como keyword",
              any("501-00" in (cr.get("important_keywords") or []) for cr in chunk_reqs))

        # limpeza
        db.query(LegalDocumentChunk).filter(LegalDocumentChunk.legal_document_id == doc.id).delete()
        db.delete(doc); db.commit()
    finally:
        db.close()
else:
    print("\n(SKIP backfill — POSTGRES_HOST não definido)")

print(f"\n=== RESULTADO B3-RAGFLOW: {PASS} passaram, {FAIL} falharam ===")
sys.exit(1 if FAIL else 0)
