# Deploy — SEGURA MULTAS (stack completa, tudo nosso)

A `docker-compose.yml` na raiz sobe TUDO: **Postgres + Redis + MinIO + api + worker + rag**
(api e rag buildados dos Dockerfiles deste repo; buckets do MinIO criados sozinhos).
Nada depende de serviço externo. RAGFlow é opcional e fica como stack à parte.

---

## Caminho recomendado: GitHub + EasyPanel (sem SSH)

O EasyPanel precisa do código onde o build roda; com o conector GitHub isso é nativo.

1. **Crie um repositório no GitHub** (pode ser privado).
2. **Suba o conteúdo deste bundle** para o repo: `Add file → Upload files`, descompacte
   o zip no seu micro e **arraste o conteúdo** (`api/`, `rag/`, `infra/`, `docs/`,
   `docker-compose.yml`, `README.md`, `DEPLOY.md`, `.env.example`, `.gitignore`).
   **NÃO suba o `.env`** — ele guarda segredos (o `.gitignore` já o ignora, mas o upload
   web não respeita .gitignore, então simplesmente não arraste o `.env`).
3. No **EasyPanel**, crie um serviço do tipo **Compose** com **Source = esse repositório**
   (via conector GitHub). Ele clona `api/` + `rag/` + o compose, e os `build: ./api` /
   `build: ./rag` finalmente resolvem.
4. No **Environment** do serviço Compose, defina os segredos (sobrescrevem os defaults):
   ```
   POSTGRES_PASSWORD=...
   JWT_SECRET=...
   INTERNAL_SECRET=...
   RAG_API_KEY=...
   MINIO_ROOT_PASSWORD=...
   OPENAI_API_KEY=...            # quando for ligar o LLM
   RAGFLOW_BASE_URL=...          # quando for ligar o RAGFlow (ex.: https://emai-ragflow...)
   RAGFLOW_API_KEY=...
   ```
   Gere cada segredo com `openssl rand -hex 32` (ou um gerador de senha).
5. **Deploy.** Acompanhe o log do serviço (a api roda `alembic upgrade head` e sobe).
6. **Domínio:** aponte `api.seguramultas...` para o serviço **api**, porta **8000**.
   O `rag` é interno (a api fala com ele em `http://rag:8000`); o console do MinIO,
   se quiser, na porta 9001.

---

## Caminho alternativo: local / SSH

```bash
unzip seguramultas-backend.zip && cd seguramultas
cp .env.example .env        # e preencha os segredos
docker compose up -d --build
docker compose logs -f api
```

---

## Smoke test (após subir)
```bash
curl -s SEU_IP:8080/health        # {"ok":true}  (espere ~30s pós-build)
curl -s SEU_IP:8080/health/db     # {"status":"healthy","db":"ok"}
curl -s SEU_IP:8081/health        # rag {"ok":true}   (se expôs a porta do rag)
```

## Variáveis (mínimo para subir)
Só os segredos acima. MinIO já vem no compose (com buckets criados). RAGFlow e LLM
podem ficar vazios no primeiro boot — o app sobe saudável (storage interno via MinIO
do compose; RAGFlow desligado; analyzer em fallback). Ligue-os depois e redeploy.

## Popular o MBFT no RAGFlow (depois, com RAGFlow no ar e embedding configurado)
O PDF do MBFT não vem no bundle (é insumo). Com a stack rodando:
```bash
docker compose cp mbvt20222.pdf api:/tmp/mbft.pdf
docker compose exec api python scripts/ingest_mbft.py /tmp/mbft.pdf --write-db --push-ragflow
```

## Notas
- **Redis é obrigatório** (api e worker são processos separados); já está no compose.
- A migração roda sozinha no boot da api; o worker espera a api ficar saudável.
- Para usar Postgres/Redis/MinIO que você já tenha, remova esses serviços do compose
  e ajuste `POSTGRES_HOST` / `REDIS_URL` / `MINIO_ENDPOINT` no Environment.

---

## Caminho B: EasyPanel nativo — 3 Apps + templates (o mais à prova de versão)

Use peças que o EasyPanel builda sem pegadinha. Os Dockerfiles de raiz
(`Dockerfile.api`, `Dockerfile.rag`) buildam com contexto = repo, então no App
basta apontar o campo **File**.

**Infra (templates 1-clique do EasyPanel, no projeto `emai`):**
- Postgres → anote host interno, porta, db, user, senha (crie um db dedicado `seguramultas`)
- Redis → anote host interno (e senha, se houver)
- MinIO → anote endpoint S3 interno + access/secret key (opcional no 1º boot)

**Apps (Source = GitHub, Build = Dockerfile):**
| App | Build → File | Porta | Command | Domínio |
|---|---|---|---|---|
| `api` | `Dockerfile.api` | 8000 | (padrão) | `api.seguramultas...` |
| `worker` | `Dockerfile.api` | — | `python scripts/worker.py` | nenhum |
| `rag` | `Dockerfile.rag` | 8000 | (padrão) | nenhum |

**Environment do `api` e do `worker`** (iguais; troque os `<...>`):
```
POSTGRES_HOST=<host interno do Postgres>     # ex.: emai_postgres
POSTGRES_PORT=5432
POSTGRES_DB=seguramultas
POSTGRES_USER=<user>
POSTGRES_PASSWORD=<senha>
REDIS_URL=redis://<host interno do Redis>:6379/0
QUEUE_BACKEND=auto
JWT_SECRET=<rand-hex-32>
INTERNAL_SECRET=<rand-hex-32>
RAG_API_URL=http://<host interno do App rag>:8000
RAG_API_KEY=<rand-hex-32>
ANALYZER_PROVIDER=openai
ANALYZER_MODEL=gpt-4o-mini
REQUIRE_PAYMENT=false
OPENAI_API_KEY=
MINIO_ENDPOINT=
MINIO_ACCESS_KEY=
MINIO_SECRET_KEY=
RAGFLOW_BASE_URL=
RAGFLOW_API_KEY=
RAGFLOW_DATASET_NAME=seguramultas_mbft
```
**Environment do `rag`:** `RAG_API_KEY` (o mesmo do api), `RAGFLOW_BASE_URL`,
`RAGFLOW_API_KEY`, `RAGFLOW_DATASET_NAME`.

Mínimo pra subir verde: Postgres + Redis + api. worker, rag e MinIO podem vir depois.
