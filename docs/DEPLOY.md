# Deploy no EasyPanel — backend (api + rag)

O backend são **dois serviços**, cada um com seu `Dockerfile`. Postgres, Qdrant e MinIO
você já tem na VPS (são dependências externas, não estão neste zip).

## Pré-requisitos (na VPS, já existentes)
- **Postgres**: criar role + DB com `infra/scripts/create_db.sql` (extensões pgcrypto/citext
  são criadas pela migração, mas o DB e o role precisam existir).
- **MinIO**: criar os 4 buckets com `infra/scripts/create_buckets.sh`.
- **Qdrant**: acessível em `http://qdrant:6333` (as 3 coleções são criadas pelo RAG no start).

## Serviço `api`
1. EasyPanel → novo serviço **App** → fonte = este repositório, **Build do `/api`** (Dockerfile).
2. **Environment**: preencher conforme `docs/ENV.md` (seção `api`). Mínimo p/ subir:
   `APP_BASE_URL`, `API_BASE_URL`, `INTERNAL_SECRET`, `JWT_SECRET` (≥32 bytes),
   `POSTGRES_*`, `MINIO_*` + `BUCKET_*`, `RAG_API_URL=http://rag:8000`, `RAG_API_KEY`.
   Para a M4 com IA real: `OPENAI_API_KEY` (ou outra) + `ANALYZER_MODEL`. Sem chave, roda em fallback.
3. **Porta**: 8000. **Domínio**: `api.seguramultas.com.br` (Traefik/EasyPanel cuida do TLS).
4. No start, o container **roda as migrações sozinho** (`alembic upgrade head`) e sobe o uvicorn.
   Healthcheck embutido em `/health`.

## Serviço `rag`
1. Novo serviço **App** → **Build do `/rag`** (Dockerfile).
2. **Environment** (seção `rag` do `docs/ENV.md`): `RAG_API_KEY` (igual ao da api),
   `QDRANT_URL=http://qdrant:6333`, `OPENAI_API_KEY` (embeddings), `EMBED_MODEL`, `EMBED_DIM=3072`,
   e p/ ingestão `MINIO_*` + `BUCKET_CONHECIMENTO`.
3. **Porta**: 8000. Sem domínio público necessário (a `api` chama internamente por `http://rag:8000`).
   Se quiser expor: `notebook`/`rag` subdomínio — opcional.
4. Popular a base: `POST /reindex` (hoje usa seed sintético; com os insumos reais do MBFT no
   bucket `conhecimento`, troca-se a fonte da ingestão).

## Ordem
1. Sobe o `rag` (a `api` depende dele em runtime, mas tolera RAG fora no boot).
2. Sobe a `api` (migra e serve).
3. Testa: `GET https://api.seguramultas.com.br/health` → `{"ok":true}`;
   `GET /health/db` → confirma Postgres.

## Segredos
Nada de `.env` em texto no Git. Use o painel de Environment do EasyPanel (verdade de produção)
ou `infra/scripts/encrypt-env.sh` (SOPS) p/ versionar `.env.enc`. Ver lista de segredos em `docs/ENV.md`.
