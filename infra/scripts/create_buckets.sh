#!/usr/bin/env bash
# Cria os 4 buckets PRIVADOS no MinIO da VPS. Requer o cliente 'mc' instalado.
# Lê credenciais do ambiente (carregue o .env antes, ou exporte as variáveis).
set -euo pipefail

: "${MINIO_ENDPOINT:?defina MINIO_ENDPOINT (URL completa, ex. https://s3.seguramultas.com.br)}"
: "${MINIO_ACCESS_KEY:?defina MINIO_ACCESS_KEY}"
: "${MINIO_SECRET_KEY:?defina MINIO_SECRET_KEY}"

# 'mc alias set' aceita a URL completa (com esquema http/https).
mc alias set sm "${MINIO_ENDPOINT}" "${MINIO_ACCESS_KEY}" "${MINIO_SECRET_KEY}" >/dev/null

for b in \
  "${BUCKET_FOTOS:-fotos}" \
  "${BUCKET_RECURSOS:-recursos}" \
  "${BUCKET_CONHECIMENTO:-conhecimento}" \
  "${BUCKET_TIMBRADOS:-timbrados}"; do
  if mc ls "sm/${b}" >/dev/null 2>&1; then
    echo "= bucket já existe: ${b}"
  else
    mc mb "sm/${b}"
    echo "+ bucket criado:    ${b}"
  fi
  mc anonymous set none "sm/${b}" >/dev/null 2>&1 || true   # garante privado
done

echo "Buckets prontos (privados)."
