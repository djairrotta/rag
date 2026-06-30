"""Embeddings plugáveis.

- Com OPENAI_API_KEY: usa a API real (text-embedding-3-large, 3072 dims).
- Sem chave: fallback determinístico (hashing de tokens no espaço de `embed_dim`),
  normalizado. Não é semântico de verdade, mas é estável e dá similaridade não-trivial
  entre textos que compartilham termos — suficiente p/ validar o pipeline em teste.

A dimensão de saída SEMPRE é settings.embed_dim, então as coleções do Qdrant não mudam
quando você liga a chave real depois.
"""
from __future__ import annotations

import hashlib
import math
import re

from app.core.config import settings

_WORD = re.compile(r"[0-9a-zà-ú]+", re.IGNORECASE)


def _normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _fallback_embed(text: str, dim: int) -> list[float]:
    """Bag-of-words hashing: cada token incrementa algumas posições determinísticas."""
    vec = [0.0] * dim
    tokens = _WORD.findall(text.lower())
    if not tokens:
        return _normalize([1.0] + [0.0] * (dim - 1))
    for tok in tokens:
        h = hashlib.sha256(tok.encode("utf-8")).digest()
        # usa 4 posições por token p/ enriquecer o vetor
        for k in range(4):
            idx = int.from_bytes(h[k * 4:k * 4 + 4], "big") % dim
            sign = 1.0 if h[16 + k] & 1 else -1.0
            vec[idx] += sign
    return _normalize(vec)


class Embedder:
    """Abstrai o provedor. `provider` expõe qual caminho está ativo (p/ /status)."""

    def __init__(self) -> None:
        self.dim = settings.embed_dim
        self.model = settings.embed_model
        self._client = None
        if settings.openai_api_key:
            try:
                from openai import OpenAI

                self._client = OpenAI(api_key=settings.openai_api_key)
                self.provider = "openai"
            except Exception:
                self._client = None
                self.provider = "fallback"
        else:
            self.provider = "fallback"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._client is not None:
            resp = self._client.embeddings.create(model=self.model, input=texts)
            return [d.embedding for d in resp.data]
        return [_fallback_embed(t, self.dim) for t in texts]

    def embed_one(self, text: str) -> list[float]:
        return self.embed([text])[0]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
