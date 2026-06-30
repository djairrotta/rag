"""Fila de jobs assíncronos (blueprint B2 / §10, §6.11).

A fila transporta apenas o `id` do `processing_jobs`; o estado durável (payload,
status, tentativas, resultado) vive na tabela. Em produção o backend é Redis
(lista BRPOP/LPUSH); em dev/teste, uma fila em memória (process-local) com a
mesma interface — assim a suíte roda sem Redis.

`get_queue()` devolve um singleton de processo, de modo que o endpoint que enfileira
e o worker que consome (no mesmo processo, ex.: testes) compartilham a instância.
"""
from __future__ import annotations

import threading
from collections import deque

from app.core.config import settings


class Queue:
    """Interface mínima da fila."""

    def enqueue(self, job_id: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def dequeue(self, timeout: float = 1.0) -> str | None:  # pragma: no cover - interface
        raise NotImplementedError

    def size(self) -> int:  # pragma: no cover - interface
        raise NotImplementedError

    def backend(self) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class InMemoryQueue(Queue):
    """Fila process-local (dev/teste). Thread-safe o suficiente p/ um worker."""

    def __init__(self) -> None:
        self._dq: deque[str] = deque()
        self._lock = threading.Lock()

    def enqueue(self, job_id: str) -> None:
        with self._lock:
            self._dq.append(job_id)

    def dequeue(self, timeout: float = 1.0) -> str | None:
        with self._lock:
            return self._dq.popleft() if self._dq else None

    def size(self) -> int:
        with self._lock:
            return len(self._dq)

    def backend(self) -> str:
        return "memory"


class RedisQueue(Queue):
    """Fila durável em Redis (produção). Import tardio do cliente redis."""

    def __init__(self, url: str, key: str) -> None:
        import redis  # import tardio: só quando há Redis configurado

        self._r = redis.Redis.from_url(url)
        self._key = key

    def enqueue(self, job_id: str) -> None:
        self._r.lpush(self._key, job_id)

    def dequeue(self, timeout: float = 1.0) -> str | None:
        res = self._r.brpop(self._key, timeout=timeout)
        if not res:
            return None
        _, val = res
        return val.decode() if isinstance(val, (bytes, bytearray)) else str(val)

    def size(self) -> int:
        return int(self._r.llen(self._key))

    def backend(self) -> str:
        return "redis"


def _build_queue() -> Queue:
    mode = (settings.queue_backend or "auto").lower()
    use_redis = mode == "redis" or (mode == "auto" and bool(settings.redis_url))
    if use_redis:
        return RedisQueue(settings.redis_url, settings.queue_name)
    return InMemoryQueue()


_QUEUE: Queue | None = None
_QUEUE_LOCK = threading.Lock()


def get_queue() -> Queue:
    """Singleton de processo da fila."""
    global _QUEUE
    if _QUEUE is None:
        with _QUEUE_LOCK:
            if _QUEUE is None:
                _QUEUE = _build_queue()
    return _QUEUE


def reset_queue() -> None:
    """Zera o singleton (útil em testes)."""
    global _QUEUE
    with _QUEUE_LOCK:
        _QUEUE = None
