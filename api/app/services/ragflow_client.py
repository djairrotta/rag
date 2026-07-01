"""Cliente HTTP do RAGFlow (API REST v1) — blueprint B3.

Cobre o que o SEGURA MULTAS precisa: achar/criar dataset, (re)criar um documento,
inserir chunks manuais (preservando os limites de ficha e o código do MBFT) e
fazer retrieval. Auth via Bearer (API key gerada no RAGFlow).

A API do RAGFlow responde sempre `{"code": 0, "data": ...}` em sucesso; code != 0
é erro (com `message`). O shape de algumas listas variou entre versões, então o
parsing é defensivo.
"""
from __future__ import annotations

import httpx

from app.core.config import settings


class RagflowError(RuntimeError):
    pass


class RagflowClient:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ragflow_base_url).rstrip("/")
        self.api_key = api_key or settings.ragflow_api_key
        if not self.base_url:
            raise RagflowError("RAGFLOW_BASE_URL não configurado.")
        self._http = httpx.Client(
            base_url=f"{self.base_url}/api/v1",
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
            transport=transport,
        )

    # -- infra ------------------------------------------------------------
    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "RagflowClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _unwrap(self, resp: httpx.Response) -> object:
        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            raise RagflowError(f"resposta não-JSON do RAGFlow: {resp.text[:200]}")
        if isinstance(body, dict) and body.get("code") not in (0, None):
            raise RagflowError(f"RAGFlow code={body.get('code')}: {body.get('message')}")
        return body.get("data") if isinstance(body, dict) else body

    # -- datasets ---------------------------------------------------------
    def find_dataset(self, name: str) -> dict | None:
        data = self._unwrap(self._http.get("/datasets", params={"name": name})) or []
        items = data if isinstance(data, list) else data.get("datasets") or data.get("kbs") or []
        for d in items:
            if d.get("name") == name:
                return d
        return None

    def create_dataset(self, name: str, *, embedding_model: str = "", chunk_method: str = "naive") -> dict:
        payload: dict = {"name": name, "chunk_method": chunk_method}
        if embedding_model:
            payload["embedding_model"] = embedding_model
        return self._unwrap(self._http.post("/datasets", json=payload))  # type: ignore[return-value]

    def find_or_create_dataset(self, name: str, *, embedding_model: str = "", chunk_method: str = "naive") -> str:
        existing = self.find_dataset(name)
        if existing:
            return existing["id"]
        return self.create_dataset(name, embedding_model=embedding_model, chunk_method=chunk_method)["id"]

    # -- documentos -------------------------------------------------------
    def list_documents(self, dataset_id: str, *, name: str | None = None) -> list[dict]:
        # NOTA: o filtro server-side ?name= é bugado no RAGFlow v0.26.2 — retorna
        # code=102 "You don't own the document <X>" para QUALQUER valor de name (até
        # nomes inexistentes), enquanto a listagem SEM name funciona normalmente.
        # Por isso listamos sem filtro e filtramos por nome aqui no cliente.
        data = self._unwrap(self._http.get(f"/datasets/{dataset_id}/documents")) or {}
        docs = data if isinstance(data, list) else (data.get("docs") or data.get("documents") or [])
        if name:
            docs = [d for d in docs if d.get("name") == name]
        return docs

    def upload_document(self, dataset_id: str, filename: str, blob: bytes,
                        content_type: str = "text/plain") -> str:
        files = {"file": (filename, blob, content_type)}
        data = self._unwrap(self._http.post(f"/datasets/{dataset_id}/documents", files=files))
        items = data if isinstance(data, list) else [data]
        return items[0]["id"]

    def delete_documents(self, dataset_id: str, ids: list[str]) -> None:
        # DELETE com body: usa request() porque httpx.delete não aceita json diretamente
        self._unwrap(self._http.request("DELETE", f"/datasets/{dataset_id}/documents", json={"ids": ids}))

    def ensure_clean_document(self, dataset_id: str, name: str, blob: bytes) -> str:
        """(Re)cria um documento limpo com este nome — idempotência da re-ingestão."""
        old = [d["id"] for d in self.list_documents(dataset_id, name=name) if d.get("name") == name]
        if old:
            self.delete_documents(dataset_id, old)
        return self.upload_document(dataset_id, name, blob)

    # -- chunks -----------------------------------------------------------
    def add_chunk(self, dataset_id: str, document_id: str, content: str,
                  important_keywords: list[str] | None = None) -> str:
        payload: dict = {"content": content}
        if important_keywords:
            payload["important_keywords"] = [k for k in important_keywords if k]
        data = self._unwrap(
            self._http.post(f"/datasets/{dataset_id}/documents/{document_id}/chunks", json=payload)
        )
        if isinstance(data, dict):
            chunk = data.get("chunk") or data
            return chunk.get("id") or chunk.get("chunk_id")
        raise RagflowError(f"resposta inesperada ao criar chunk: {data!r}")

    # -- retrieval --------------------------------------------------------
    def retrieval(self, question: str, dataset_ids: list[str], *, top_k: int = 8,
                  document_ids: list[str] | None = None, similarity_threshold: float = 0.2,
                  keyword: bool = False) -> list[dict]:
        payload: dict = {
            "question": question,
            "dataset_ids": dataset_ids,
            "top_k": top_k,
            "page": 1,
            "page_size": top_k,
            "similarity_threshold": similarity_threshold,
            "keyword": keyword,
        }
        if document_ids:
            payload["document_ids"] = document_ids
        data = self._unwrap(self._http.post("/retrieval", json=payload)) or {}
        if isinstance(data, list):
            return data
        return data.get("chunks") or []
