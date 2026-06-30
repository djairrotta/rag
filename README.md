# SEGURA MULTAS — backend self-hosted (VPS / EasyPanel / Traefik)

SaaS que analisa multa de trânsito (foto/PDF), entrega um **veredito grátis estilo semáforo**
🟢🟡🔴 e vende a **peça de recurso** fundamentada em **MBFT + jurisprudência reais via RAG**.
Multi-tenant (marca própria + parceiros white-label). **Sem Supabase** — auth e isolamento
são responsabilidade do backend (escopo por `user_id`/`partner_id`).

> Reconstrução do zero descrita no blueprint v3 (`docs/blueprint-v3.md`). O Lovable fica
> apenas como referência de leitura (design "O Veredito", prompts, edge functions).

## Estrutura
```
seguramultas/
├── api/                 FastAPI — auth+roles, negócio, Asaas, multi-LLM, DOCX/PDF, entrega
│   └── app/{routers,services,models,core} + main.py (/health)
├── rag/                 FastAPI + Docling + Qdrant + embeddings (contrato FIXO: /health /status /search /reindex)
├── frontend/            React/Vite + Tailwind/shadcn (scaffold nas missões M4+)
├── infra/
│   ├── scripts/         create_db.sql · create_buckets.sh · smoke_test.py · encrypt/decrypt-env
│   └── traefik/         docker-compose.reference.yml (rótulos Traefik / portas — documentação)
├── docs/                blueprint-v3.md · api-contracts.md
├── .env.example         contrato de variáveis (copie p/ .env e preencha)
└── .sops.yaml           criptografia de segredos (SOPS + age)
```

## Pré-requisitos na VPS (instâncias reaproveitadas, com DB/coleções/buckets dedicados)
Postgres · Qdrant · MinIO · open-notebook (+SurrealDB) · Evolution API · poste.io · Asaas.

## Passo a passo (M0 → M1 → smoke verde → M2)
1. **Segredos**
   ```bash
   cp .env.example .env
   # preencha .env (gere tokens fortes: openssl rand -hex 32)
   age-keygen -o key.txt              # guarde key.txt FORA do Git
   # cole a "Public key: age1..." em .sops.yaml
   export SOPS_AGE_KEY_FILE=$PWD/key.txt
   bash infra/scripts/encrypt-env.sh  # gera .env.enc (versionável)
   ```
2. **Postgres — DB/usuário dedicados**
   ```bash
   psql -h "$POSTGRES_HOST" -U postgres -f infra/scripts/create_db.sql
   ```
3. **MinIO — 4 buckets privados**
   ```bash
   bash infra/scripts/create_buckets.sh   # requer o cliente 'mc'
   ```
4. **Smoke test (critério de avanço)**
   ```bash
   pip install -r infra/scripts/requirements-smoke.txt
   python infra/scripts/smoke_test.py     # tudo VERDE => seguir para a M2
   ```
5. **Subir os serviços (dev local)**
   ```bash
   cd api && pip install -r requirements.txt && uvicorn app.main:app --port 8000 --reload
   cd rag && pip install -r requirements.txt && uvicorn app.main:app --port 8100 --reload
   ```

## Convenções
- **Isolamento multi-tenant:** toda query aplica `user_id`/`partner_id` do JWT; admin vê tudo. Sem RLS.
- **Segredos:** nada em texto puro no Git. `.env` local; `.env.enc` (SOPS) versionável; verdade = EasyPanel.
- **RAG:** a LLM só cita o que veio da base (mbft/jurisprudência/modelos), rastreável a ficha/artigo.
- **Preço server-side:** o cliente nunca define valor (20%/teto 300/fallback 69,90).

## Status atual
- **M0** ✅ · **M1** ✅ · **M2 auth** ✅ · **M3 RAG** ✅ · **M4 análise** ✅ · **M5 pagamento** ✅ · **M7 geração** ✅ — **125 testes e2e verdes**
- M2: 17 tabelas, auth argon2+JWT com rotação, escopo multi-tenant, claim. `api/tests_e2e_m2.py` (27/27).
- M3: RAG (embeddings plugáveis 3072, 3 coleções Qdrant, ingestão, busca + visibilidade por parceiro). `rag/tests_e2e_m3.py` (24/24).
- M4: `POST /analyses` (porte do `analyze-fine`) — valida, extrai (visão), nulidades, veredito `null|weak|valid`,
  persiste (autenticado / anônimo+paywall+claim). Analisador plugável + fallback. `api/tests_e2e_m4.py` (24/24 + 6/6 paywall).
- M5: `POST /payments` + `GET /payments/{id}` + `POST /webhooks/asaas`. Preço **server-side** (20%/teto/fallback),
  embriaguez→CONTACT_REQUIRED, Asaas plugável (simulado sem chave), webhook libera/revoga recurso. `api/tests_e2e_m5.py` (26/26).
- M7: `POST /recursos` + download. Gate de pagamento (402), auth user/interno, geração 2 passos LLM + fallback
  estruturado, enriquecimento RAG best-effort, render **DOCX**, storage local/MinIO. `api/tests_e2e_m7.py` (24/24).
- **Preço e embriaguez:** `app/core/pricing.py`. **Deploy:** API migra sozinha no start (entrypoint).
- **Docs:** `docs/ENV.md` (env por serviço) · `docs/DEPLOY.md` · `docs/FRONTEND-INTEGRACAO.md`.
- **Próximo:** M6 (perguntas) · M8 (painel: listas + editor) · M9 (admin) · M10 (parceiros).

- **Roadmap:** M0 fundações · M1 infra · M2 backend+auth · M3 RAG · M4 análise grátis · M5 Asaas ·
  M6 dados+perguntas · M7 geração+entrega · M8 editor open-notebook · M9 admin · M10 parceiros ·
  M11 migração/cutover · M12 hardening/LGPD · M13 Graphify (opcional). M3 pode ir em paralelo à M2.
