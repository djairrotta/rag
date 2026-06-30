#!/usr/bin/env bash
# Criptografa .env -> .env.enc (versionável) com SOPS + age.
set -euo pipefail
cd "$(dirname "$0")/../.."
[ -f .env ] || { echo "erro: .env não encontrado (copie de .env.example)"; exit 1; }
sops --encrypt --input-type dotenv --output-type dotenv .env > .env.enc
echo "+ .env.enc gerado. NUNCA versione o .env em texto puro."
