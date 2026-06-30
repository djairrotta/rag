#!/usr/bin/env python3
"""Smoke test do SEGURA MULTAS — conectividade dos 4 serviços de infra.

Critério de avanço (M1 -> M2): TODOS verdes.
Serviços: Postgres, Qdrant, MinIO, open-notebook.

Uso (a partir da raiz do repo):
    pip install -r infra/scripts/requirements-smoke.txt
    python infra/scripts/smoke_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
except Exception:
    pass

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

results: "list[tuple[str, bool, str]]" = []


def check(name: str):
    """Executa a verificação na hora e guarda (nome, ok, detalhe)."""
    def deco(fn):
        try:
            detail = fn() or ""
            results.append((name, True, detail))
        except Exception as exc:  # noqa: BLE001
            results.append((name, False, f"{type(exc).__name__}: {exc}"))
        return fn
    return deco


@check("Postgres")
def _pg() -> str:
    import psycopg

    dsn = (
        f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
        f"port={os.getenv('POSTGRES_PORT', '5432')} "
        f"dbname={os.getenv('POSTGRES_DB', 'seguramultas')} "
        f"user={os.getenv('POSTGRES_USER', 'seguramultas')} "
        f"password={os.getenv('POSTGRES_PASSWORD', '')}"
    )
    with psycopg.connect(dsn, connect_timeout=5) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version();")
            row = cur.fetchone()
    return row[0].split(",")[0] if row else "conectado"


@check("Qdrant")
def _qdrant() -> str:
    from qdrant_client import QdrantClient

    client = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://localhost:6333"),
        api_key=os.getenv("QDRANT_API_KEY") or None,
        timeout=5,
    )
    nomes = sorted(c.name for c in client.get_collections().collections)
    return f"{len(nomes)} coleções: {', '.join(nomes) if nomes else '(nenhuma ainda)'}"


@check("MinIO")
def _minio() -> str:
    from minio import Minio

    raw = os.getenv("MINIO_ENDPOINT", "localhost:9000")
    parsed = urlparse(raw if "://" in raw else f"//{raw}")
    endpoint = parsed.netloc or parsed.path
    secure = parsed.scheme != "http"  # https (ou sem esquema) => seguro
    client = Minio(
        endpoint,
        access_key=os.getenv("MINIO_ACCESS_KEY", ""),
        secret_key=os.getenv("MINIO_SECRET_KEY", ""),
        secure=secure,
    )
    esperados = [
        os.getenv("BUCKET_FOTOS", "fotos"),
        os.getenv("BUCKET_RECURSOS", "recursos"),
        os.getenv("BUCKET_CONHECIMENTO", "conhecimento"),
        os.getenv("BUCKET_TIMBRADOS", "timbrados"),
    ]
    faltando = [b for b in esperados if not client.bucket_exists(b)]
    if faltando:
        raise RuntimeError(
            f"buckets ausentes: {', '.join(faltando)} (rode create_buckets.sh)"
        )
    return f"4 buckets ok: {', '.join(esperados)}"


@check("open-notebook")
def _onbook() -> str:
    import httpx

    base = os.getenv("ONBOOK_API_URL")
    if not base:
        raise RuntimeError("ONBOOK_API_URL não definido")
    token = os.getenv("ONBOOK_API_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    for path in ("/health", "/api/health", "/"):
        try:
            resp = httpx.get(base.rstrip("/") + path, headers=headers, timeout=5)
            if resp.status_code < 500:
                return f"{path} -> HTTP {resp.status_code}"
        except Exception:
            continue
    raise RuntimeError("sem resposta de /health, /api/health ou /")


def main() -> int:
    print("\n SEGURA MULTAS · smoke test de infra")
    print(" " + "-" * 52)
    for name, ok, detail in results:
        mark = f"{GREEN}OK   {RESET}" if ok else f"{RED}FALHA{RESET}"
        print(f"  {mark}  {name:<14} {detail}")
    print(" " + "-" * 52)
    if all(ok for _, ok, _ in results):
        print(f" {GREEN}Tudo verde — pode avançar para a M2.{RESET}\n")
        return 0
    print(f" {YELLOW}Há serviços com falha — ajuste .env/infra e rode de novo.{RESET}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
