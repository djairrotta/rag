#!/usr/bin/env python3
"""Worker assíncrono do SEGURA MULTAS (blueprint B2 / §10).

Consome a fila (Redis em produção; memória em dev) e executa os jobs registrados
em app.services.jobs. Rode como processo/serviço separado da API:

    POSTGRES_HOST=... REDIS_URL=redis://redis:6379/0 python scripts/worker.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.jobs import run_worker  # noqa: E402
from app.services.queue import get_queue  # noqa: E402


def main() -> int:
    worker_id = os.environ.get("WORKER_ID", "worker-1")
    print(f"[worker] iniciando (backend={get_queue().backend()}, id={worker_id})", flush=True)
    run_worker(worker_id=worker_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
