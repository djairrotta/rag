#!/usr/bin/env bash
# Restaura .env a partir de .env.enc (precisa da chave privada age:
#   export SOPS_AGE_KEY_FILE=$PWD/key.txt).
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env.enc ] || { echo "erro: .env.enc não encontrado"; exit 1; }
sops --decrypt --input-type dotenv --output-type dotenv .env.enc > .env
echo "+ .env restaurado a partir de .env.enc"
