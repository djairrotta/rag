#!/bin/sh
set -e
echo "[entrypoint] aplicando migrações (alembic upgrade head)..."
i=1
while [ "$i" -le 30 ]; do
  if alembic upgrade head; then
    echo "[entrypoint] migrações OK."
    break
  fi
  echo "[entrypoint] banco indisponível, tentativa $i/30..."
  i=$((i + 1))
  sleep 2
done
echo "[entrypoint] iniciando API..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
