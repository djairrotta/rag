# SEGURA MULTAS — Contratos de API (v3)

> Contrato dos serviços. Acompanha `docs/blueprint-v3.md`.
> API principal = FastAPI. Tudo JSON, UTF-8. Datas em ISO-8601 UTC. IDs = UUID.

---

## 0. Convenções

**Base URLs**
- API principal: `https://api.seguramultas.com.br`
- RAG (interno): `http://rag:8000`
- open-notebook (externo, consumido): `https://notebook.seguramultas.com.br`

**Auth**
- Header: `Authorization: Bearer <access_token>` (JWT).
- Endpoints marcados **[anon]** aceitam sem token. **[user]**, **[admin]**, **[partner]** exigem o papel.
- Chamadas internas entre serviços: header `X-Internal-Secret: <INTERNAL_SECRET>`.

**Escopo multi-tenant (sem RLS — aplicado no backend)**
- `admin` enxerga tudo. `partner` só o próprio `partner_id`. `user` só o próprio `user_id`.
- Toda listagem/leitura aplica o filtro derivado do JWT. Acesso cruzado → `403`.

**Envelope de erro (padrão)**
```json
{ "error": { "code": "CONTACT_REQUIRED", "message": "texto legível", "details": {} } }
```

**Paginação (listagens)**
- Query: `?page=1&page_size=20` (máx 100). Resposta: `{ "items": [...], "total": N, "page": 1, "page_size": 20 }`.

**Status HTTP usados:** 200, 201, 202 (geração assíncrona), 400, 401, 403, 404, 409 (regra de negócio), 422 (validação), 429 (rate limit), 500.

---

## 1. Auth & sessões

### POST /auth/register **[anon]**
```json
// req
{ "email": "a@b.com", "password": "•••", "name": "Fulano" }
// res 201
{ "user": { "id": "uuid", "email": "a@b.com", "name": "Fulano", "role": "user" },
  "access_token": "jwt", "refresh_token": "jwt" }
```

### POST /auth/login **[anon]**
```json
// req
{ "email": "a@b.com", "password": "•••" }
// res 200 — mesmo shape do register
```

### POST /auth/refresh **[anon]**
```json
// req
{ "refresh_token": "jwt" }
// res 200
{ "access_token": "jwt", "refresh_token": "jwt" }   // rotação de refresh
```

### GET /auth/me **[user]**
```json
// res 200
{ "id": "uuid", "email": "a@b.com", "name": "Fulano", "role": "user", "partner_id": null }
```

### POST /auth/logout **[user]**
```json
// req
{ "refresh_token": "jwt" }      // revoga o refresh
// res 204
```

---

## 2. Análise (B2C, anônima)

### POST /analyses/upload **[anon]** — multipart
- Campo `file` (imagem/PDF). Salva no bucket `fotos`. Não dispara análise ainda.
```json
// res 201
{ "upload_id": "uuid", "url": "https://signed-url..." }
```

### POST /analyses **[anon]**
- Roda extração + veredito (modelo da tarefa `analisar`, definido no admin) + RAG (`mbft` por código).
```json
// req
{ "upload_id": "uuid" }
// res 201
{ "analysis_id": "uuid",
  "claim_token": "uuid",                 // guardar no cliente p/ reivindicar após cadastro
  "veredito": {
    "status": "green",                   // green | yellow | red
    "nulidades": [ { "tipo": "...", "descricao": "...", "base": "CTB art. 280 ..." } ],
    "base_legal": [ "CTB art. 280", "Res. CONTRAN 619/2016 ..." ],
    "campos": { "orgao": "DETRAN-SP", "codigo": "545-00", "artigo_ctb": "181-XVII",
                "valor": 195.23, "data": "2026-05-01", "local": "..." }
  },
  "contact_required": false,             // true p/ embriaguez (165/165-A)
  "price_preview_brl": 39.05             // 20% do valor, teto 300 (informativo)
}
```

### GET /analyses/{id} **[anon+claim_token | user | admin]**
- Anônimo lê passando `?claim_token=...`; logado/admin pelo escopo.
```json
// res 200 — análise completa (campos, veredito, status de pagamento, recurso se houver)
```

### POST /analyses/{id}/claim **[user]**
```json
// req
{ "claim_token": "uuid" }
// res 200 — análise agora pertence ao usuário (uso único do token)
```

---

## 3. Pagamento (Asaas) + webhook

### POST /payments **[user]**
- Preço calculado **no servidor** (20% / teto 300 / fallback 69,90). Exige posse da análise.
- Embriaguez → `409 CONTACT_REQUIRED`.
```json
// req
{ "analysis_id": "uuid", "method": "pix" }    // pix | credit_card | boleto
// res 201
{ "payment_id": "uuid", "amount_brl": 39.05, "status": "pending",
  "checkout_url": "https://asaas...", "pix": { "qr": "...", "copia_e_cola": "..." } }
```

### GET /payments/{id} **[user]** — polling
```json
{ "payment_id": "uuid", "status": "confirmed", "analysis_id": "uuid" }
```

### POST /webhooks/asaas **[anon, assinado]** — Asaas → nós
- Valida `ASAAS_WEBHOOK_TOKEN`. **Idempotente** por id do evento.
- Em `PAYMENT_CONFIRMED`/`PAYMENT_RECEIVED`: marca pago, `resource_available=true`, dispara pipeline de geração.
```json
// req (resumo do payload Asaas)
{ "event": "PAYMENT_CONFIRMED", "payment": { "id": "pay_...", "externalReference": "analysis_uuid", "value": 39.05 } }
// res 200
{ "received": true }
```

---

## 4. Coleta de dados + perguntas da LLM

### POST /analyses/{id}/intake **[user]** — exige pago
```json
// req
{ "nome": "...", "cpf": "...", "cnh": "...", "endereco": "...", "extra": {} }
// res 200 { "ok": true }
```

### POST /analyses/{id}/questions/generate **[user]**
- "Needs assessment": dado o auto + nulidades, gera perguntas objetivas só se faltar info.
```json
// res 200
{ "questions": [ { "id": "uuid", "pergunta": "Você era o condutor?", "tipo": "boolean" },
                 { "id": "uuid", "pergunta": "Foi notificado no prazo?", "tipo": "boolean" } ] }
// se nada faltar: { "questions": [] }
```

### POST /analyses/{id}/questions/answers **[user]**
```json
// req
{ "answers": [ { "question_id": "uuid", "answer": "sim" } ] }
// res 200 { "ok": true }
```

---

## 5. Geração + entrega do recurso

### POST /recursos **[user]** — dispara geração (assíncrona)
- Pré-req: pago + intake + perguntas respondidas (se houver). Redação 2 passos + RAG + timbrado.
```json
// req
{ "analysis_id": "uuid" }
// res 202
{ "recurso_id": "uuid", "status": "generating" }
```

### GET /recursos/{id} **[user|admin]** — polling/leitura
```json
{ "recurso_id": "uuid", "status": "ready",          // generating | ready | error
  "md": "## RECURSO ADMINISTRATIVO ...",
  "docx_url": "https://signed...", "pdf_url": "https://signed...",
  "fontes": [ { "ficha": "...", "codigo": "545-00", "artigo_ctb": "181-XVII", "fonte": "mbft/fichas/..." } ],
  "custo_real_brl": 0.42 }                            // admin/partner; oculto p/ user final
```

### POST /recursos/{id}/deliver **[user]**
```json
// req
{ "channel": "email", "destino": "a@b.com" }          // download | email | whatsapp ; destino p/ email/whatsapp
// res 200 { "delivered": true }
```

### GET /recursos/{id}/download?format=pdf **[user|admin]**
```json
{ "url": "https://signed-url...", "format": "pdf" }   // format: pdf | docx
```

---

## 6. Editor (sincronização open-notebook)

### GET /recursos/{id}/note **[user]**
- Puxa o conteúdo da nota (fonte da verdade após a 1ª geração).
```json
{ "onbook_id": "note_...", "content_md": "## RECURSO ..." }
```

### PUT /recursos/{id}/note **[user]**
```json
// req
{ "content_md": "## RECURSO (editado) ..." }
// res 200 { "ok": true }                              // grava na nota via API do open-notebook
```

### POST /recursos/{id}/reexport **[user]**
- Regera DOCX/PDF a partir do conteúdo **editado** da nota.
```json
// res 200 { "docx_url": "https://signed...", "pdf_url": "https://signed..." }
```

---

## 7. Painel do usuário

### GET /me/analyses **[user]** — paginado
```json
{ "items": [ { "id": "uuid", "criado_em": "...", "veredito_status": "green",
               "foto_url": "https://signed...", "pago": true, "recurso_id": "uuid|null" } ],
  "total": 12, "page": 1, "page_size": 20 }
```

### GET /me/recursos **[user]** — paginado
```json
{ "items": [ { "id": "uuid", "analysis_id": "uuid", "status": "ready",
               "docx_url": "...", "pdf_url": "...", "editado": false } ], "total": 8, "page": 1, "page_size": 20 }
```

---

## 8. Admin (role=admin)

### Usuários
- `GET /admin/users` (paginado, filtros `?role=&q=`) · `POST /admin/users` · `GET /admin/users/{id}` ·
  `PATCH /admin/users/{id}` · `DELETE /admin/users/{id}`

### Pagamentos
- `GET /admin/payments` (filtros `?status=&from=&to=`) · `PATCH /admin/payments/{id}` (marcações)

### Erros do sistema
- `GET /admin/errors` (`?resolved=false`) · `GET /admin/errors/{id}` · `PATCH /admin/errors/{id}` (resolver)

### Prompts (versionados, por tarefa)
- `GET /admin/prompts?task=analisar|perguntas|redigir` · `POST /admin/prompts` · `GET /admin/prompts/{id}` ·
  `PATCH /admin/prompts/{id}` (cria nova versão) · `DELETE /admin/prompts/{id}`
```json
// POST /admin/prompts
{ "task": "redigir", "name": "recurso-v2", "content": "Você é um advogado...", "active": true }
```

### LLM configs (modelo por tarefa + preço por token)
- `GET /admin/llm-configs` · `POST /admin/llm-configs` · `PATCH /admin/llm-configs/{id}` · `DELETE /admin/llm-configs/{id}`
```json
// POST /admin/llm-configs
{ "task": "redigir", "provider": "anthropic", "model": "claude-...",
  "price_in_per_1k": 0.003, "price_out_per_1k": 0.015, "active": true }
```

### Conhecimento (upload → Docling → chunk → Qdrant)
- `POST /admin/knowledge` (multipart: `file`, `type`, `partner_id?`) → `202 { "document_id": "uuid", "status": "indexing" }`
  - `type`: `mbft` | `jurisprudencia` | `modelo_recurso` | `timbrado`
- `GET /admin/knowledge` (`?type=&partner_id=`) · `GET /admin/knowledge/{id}` ·
  `POST /admin/knowledge/{id}/reindex` · `DELETE /admin/knowledge/{id}`
```json
// GET item
{ "id": "uuid", "type": "mbft", "arquivo": "conhecimento/...", "md_url": "...",
  "chunks": 134, "partner_id": null, "status": "indexed" }
```

### E-mail (poste.io)
- `GET /admin/email-config` · `PUT /admin/email-config` · `POST /admin/email-config/test` (`{ "to": "a@b.com" }`)

### Métricas / custo
- `GET /admin/metrics` (receita, nº recursos, custo, margem) · `GET /admin/token-usage` (`?from=&to=&model=`)

---

## 9. Parceiros (role=partner — área /parceiros)

### Carteira
- `GET /partner/wallet` → `{ "saldo_brl": 152.30, "transactions": [ { "tipo": "debito", "valor": 1.26, "recurso_id": "uuid", "em": "..." } ] }`
- `POST /partner/wallet/recharge` → cria cobrança Asaas
```json
// req { "amount_brl": 200.00 }
// res 201 { "payment_id": "uuid", "checkout_url": "...", "pix": { ... } }
```

### Assinatura (recorrente, Asaas)
- `GET /partner/subscription` → `{ "status": "active", "next_due": "..." }`
- `POST /partner/subscription` → cria/gerencia assinatura

### White-label
- `GET /partner/branding` · `PUT /partner/branding`
```json
// PUT { "logo_url": "...", "cores": { "primary": "#0B2C3D", "accent": "#C9A84C" }, "timbrado_id": "uuid" }
```

**Regra de cobrança do parceiro:** ao gerar um recurso de parceiro, `custo_real_brl × 3` é debitado da carteira.
Se `saldo < débito` → `409 INSUFFICIENT_BALANCE` (pede recarga). O fluxo de análise/recurso do parceiro usa os
mesmos endpoints de B2C, mas escopados ao `partner_id` e debitando carteira (sem o preço fixo B2C).

---

## 10. RAG (serviço interno) — contrato fixo
- `GET /health` → `{ "ok": true }`
- `GET /status` → `{ fichas_indexed, chunks, collection, last_ingested_at, healthy }`
- `POST /search` `{ consulta, filtros?: { codigo?, tema?, tipo? }, partner_id?, top_k=8 }`
  → `{ results: [ { texto, score, ficha, codigo, artigo_ctb, pagina, fonte } ] }`
- `POST /reindex` `{ type?, partner_id? }` → `{ job_id, status }` ; `GET /reindex/{job_id}` → `{ status, ... }`
- Auth: `Authorization: Bearer <RAG_API_KEY>`.

---

## 11. open-notebook (contrato externo consumido pelo app)
> A API principal fala com a sua instância via um **adapter** (`ONBOOK_API_URL` + `ONBOOK_API_TOKEN`).
> O app precisa apenas destas 3 operações lógicas; mapeie-as para os endpoints reais da sua instância:

| Operação lógica | Uso no app | Mapear para (sua API) |
|---|---|---|
| **create_note(content_md) → note_id** | ao gerar o recurso pela 1ª vez | `POST /notes` (ou equivalente) |
| **get_note(note_id) → content_md** | abrir o editor | `GET /notes/{id}` |
| **update_note(note_id, content_md)** | salvar edição | `PUT /notes/{id}` |

Auth do adapter: `Authorization: Bearer <ONBOOK_API_TOKEN>`.
*(Na M8, confirmar os caminhos/payloads reais da instância e ajustar o adapter.)*

---

## 12. Webhooks & segurança
- **Asaas:** validar `ASAAS_WEBHOOK_TOKEN`; idempotência por id do evento; só agir em `PAYMENT_CONFIRMED`/`RECEIVED`.
- **MinIO:** todo acesso a objeto por **URL assinada** (expira); buckets privados.
- **Rate limit:** endpoints públicos (`/analyses`, `/auth/*`, `/webhooks/*`) com limite por IP.
- **Validação:** Pydantic em toda entrada; limite de tamanho/tipo no upload (imagem/PDF).
- **Internas:** `X-Internal-Secret` para chamadas serviço→serviço (ex.: webhook → geração).

---

## 13. Códigos de erro (negócio)
| code | HTTP | quando |
|---|---|---|
| `CONTACT_REQUIRED` | 409 | embriaguez (CTB 165/165-A) — fora do fluxo automático |
| `PAYMENT_REQUIRED` | 402 | tentar gerar/baixar recurso sem pagamento confirmado |
| `INSUFFICIENT_BALANCE` | 409 | parceiro sem saldo p/ o débito (3× custo) |
| `CLAIM_INVALID` | 400 | claim_token inválido/já usado |
| `NOT_OWNER` | 403 | acesso cruzado entre tenants/usuários |
| `ANALYSIS_NOT_READY` | 409 | gerar recurso antes de intake/perguntas |

---

## 14. Apêndice — medição de tokens & custo
- Toda chamada de LLM grava em `token_usage`: `{ ref_type: analise|recurso, ref_id, model, tokens_in, tokens_out, custo_brl }`.
- `custo_brl = tokens_in/1000 × price_in_per_1k + tokens_out/1000 × price_out_per_1k` (de `llm_configs`).
- `recursos.custo_real_brl = Σ(custo_brl das chamadas daquele recurso)`.
- **Parceiro:** débito na carteira = `recursos.custo_real_brl × 3`.
- **B2C:** preço fixo (20%/teto 300); `custo_real_brl` é só margem interna.
