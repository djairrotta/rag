# SEGURA MULTAS — Blueprint do Projeto (v3)

> Especificação técnica do produto. Documento-fonte do repositório.
> Self-hosted na VPS (EasyPanel/Docker/Traefik). Multi-tenant (marca própria + parceiros white-label).

---

## 1. Visão
SaaS que analisa uma multa de trânsito (foto/PDF), devolve um **veredito gratuito estilo semáforo**
(🟢 boa chance / 🟡 fraca / 🔴 sem nulidade aparente) e vende a **peça de recurso administrativo**,
redigida por LLM e **fundamentada em fontes reais via RAG** (fichas do MBFT + jurisprudência + modelos).
Regra de ouro: **só se cita o que veio da base** — nada inventado.

## 2. Atores
- **Visitante (anônimo):** envia a multa e vê o veredito, sem login.
- **Usuário cadastrado:** compra a defesa, preenche dados, recebe/edita o recurso, tem histórico.
- **Parceiro (white-label):** revende o serviço com logo/timbrado/cores próprios; paga assinatura +
  consome carteira (crédito) por recurso; acessa só `/parceiros`; dados isolados.
- **Admin:** CRUD total (usuários, pagamentos, erros, prompts, LLM, conhecimento, e-mail).

## 3. Jornada B2C (núcleo)
1. **Captura** — foto/PDF/câmera (arrastar, selecionar ou "Tirar foto"), sem login.
2. **Veredito grátis** — a LLM extrai os campos do auto (órgão, código/enquadramento, artigo CTB, valor,
   data, local) e decide 🟢/🟡/🔴 + nulidades + base legal, embasada no RAG (`mbft` por código).
   Exibido num **modal semáforo** com botão **"Adquirir defesa"**.
   Embriaguez (CTB 165/165-A) → `CONTACT_REQUIRED` (contato humano, fora do fluxo automático).
3. **Pagamento (Asaas)** — preço **fixo: 20% do valor da multa, teto R$ 300** (fallback R$ 69,90).
   Pix (principal) + cartão + boleto. O recurso só é gerado **após** confirmação.
4. **Cadastro/claim + dados** — se anônimo, cria conta e a análise é reivindicada (claim_token).
   Formulário coleta dados do peticionário (nome, CPF, CNH, endereço…).
5. **Perguntas condicionais** — se faltar informação, a LLM faz perguntas objetivas
   ("era o condutor?", "notificado no prazo?", "CNH definitiva?") antes de redigir.
6. **Geração** — redação em 2 passos (redige + revisa) com RAG (mbft/jurisprudência/modelos) + **timbrado**.
   Saídas: **DOCX** (python-docx/docxtpl, padrão Visual Law) e **PDF** (LibreOffice headless).
   **Acentos PT corretos** é critério de aceite.
7. **Entrega (à escolha)** — **download** (URL assinada), **e-mail** (poste.io) ou **WhatsApp** (Evolution API).
8. **Edição** — editor nativo (markdown) sincronizado com **open-notebook** (cada recurso ↔ nota `onbook_id`);
   a nota vira a fonte da verdade; reexporta DOCX/PDF do conteúdo editado.

## 4. Painéis
**Usuário:** histórico de multas, cópias das fotos e dos recursos, status de pagamento, reedição.
**Admin (CRUD total):** usuários, pagamentos, **erros do sistema**, **prompts** (versionados por tarefa),
**LLM + preço por token** por modelo, **upload de conhecimento** (PDF → Docling→md / chunk Qdrant),
**config de e-mail** (poste.io) com envio de teste.
**Parceiros:** área `/parceiros`, white-label (logo/timbrado/cores), assinatura + carteira em R$,
recarga via Asaas, isolamento por `partner_id`.

## 5. Arquitetura (VPS / EasyPanel / Traefik)
| Serviço | Stack | Exposição |
|---|---|---|
| frontend | React/Vite + Tailwind/shadcn (nginx) | público `seguramultas.com.br` |
| api | FastAPI (auth, negócio, Asaas, multi-LLM, tokens, DOCX/PDF, entrega) | público `api.…` |
| rag | FastAPI + Docling + Qdrant + embeddings large | **interno** |
| Postgres | relacional | **interno** |
| Qdrant | vetores | **interno** |
| MinIO | objetos (fotos/recursos/conhecimento/timbrados) | interno (ou `s3.…` se servir direto) |
| open-notebook (+SurrealDB) | edição do recurso (expõe API) | público `notebook.…` |
| Evolution API | WhatsApp | conforme instância |
| poste.io | e-mail SMTP | externo |
| Asaas | pagamentos | externo |
| Graphify | grafo (fase posterior) | interno |

## 6. Stack de decisões travadas
- **Auth/dados:** Postgres + **auth próprio FastAPI** (JWT access+refresh, argon2, roles). Sem Supabase.
- **Isolamento multi-tenant:** sem RLS → **camada de escopo no backend** aplica `user_id`/`partner_id`
  do JWT em toda query (admin vê tudo; partner só seu tenant; user só o seu).
- **LLM:** multi-provedor, **admin escolhe por tarefa** (analisar/perguntas/redigir):
  Anthropic, GPT, DeepSeek, GLM, MiniMax, Kimi.
- **Embeddings:** OpenAI **text-embedding-3-large (3072 dims)**.
- **E-mail:** poste.io. **Editor:** nativo + open-notebook. **Segredos:** SOPS/age + env no EasyPanel.
- **Infra:** reaproveita instâncias existentes (DB/coleções/buckets dedicados).

## 7. RAG (o diferencial)
- **3 coleções Qdrant:** `mbft`, `jurisprudencia`, `modelos_recurso` (vetor 3072, cosine).
  Índices de payload: `codigo`, `artigo_ctb`, `tema`, `partner_id`, `source_type`.
  `partner_id` no payload (null = base global da marca) → **isolamento**.
- **Ingestão (Docling):** PDF (MinIO) → markdown estruturado → **HybridChunker por ficha/artigo**
  (sem cortar tabelas) → metadados (`codigo`, `artigo_ctb`, `tema`, `fonte`, `pagina`, `source_type`)
  → embeddings large → upsert com **IDs determinísticos** (reindex idempotente).
- **Fontes:** ficha individual = 1 doc; manual completo (~830 págs) = dividido por heading/ficha;
  jurisprudência = por ementa/tema; modelos = por seção.
- **Busca híbrida:** semântica + **filtro por metadado** (código da multa / tema). Dedup opcional (ficha > manual).
- **Geração:** a LLM recebe os trechos + a fonte e cita **apenas** o que veio da base; tudo rastreável.

O contrato fixo da API RAG está em `docs/api-contracts.md`.

## 8. Subsistema de créditos & medição de tokens
- Toda chamada de LLM (análise/perguntas/redação) registra **tokens (in+out)** vinculados ao **modelo**.
- Cada modelo tem **preço por token** (admin) → **custo real em R$ por recurso** = Σ(tokens × preço).
- `recursos.custo_real_brl` é gravado em todo recurso (admin vê margem).
- **Parceiro:** cada recurso **debita 3× o custo real** da carteira (R$); recarrega via Asaas; assinatura à parte.
- **B2C:** preço **fixo** (20%/teto 300); a medição serve só para custo/margem interno.

## 9. Modelo de dados (Postgres)
- `users` (role user/admin/partner, partner_id?)
- `partners` (logo, timbrado, cores, status)
- `partner_wallet` (saldo R$) + `wallet_transactions` (recarga/débito)
- `subscriptions` (Asaas, parceiro)
- `analyses` (foto, campos extraídos, veredito, nulidades, claim_token, partner_id?)
- `payments` (Asaas, B2C)
- `recursos` (md, docx_url, pdf_url, status, entrega, editado, onbook_id, custo_real_brl)
- `questions` (perguntas/respostas da LLM)
- `prompts` (versionados, por tarefa, CRUD)
- `llm_configs` (tarefa → provedor+modelo + preço por token in/out, CRUD)
- `token_usage` (por chamada: recurso/análise, modelo, tokens_in, tokens_out, custo_brl)
- `knowledge_documents` (tipo, arquivo, md, nº chunks, partner_id?)
- `system_errors` · `email_config` · `audit_log`

## 10. Roadmap de missões (M0 → M13)
- **M0** Fundações: monorepo, `.env.example`, `.gitignore`, `.sops.yaml`, README, stubs, SOPS/age. **[entregue]**
- **M1** Infra base: DB+usuário dedicados, 4 buckets privados, smoke test, Traefik. **[entregue]**
- **M2** Backend core: FastAPI, auth (argon2+JWT access/refresh), roles, **camada de isolamento**,
  migrações Alembic de todas as tabelas, `claim_analysis`, healthchecks.
- **M3** RAG funcional: coleções Qdrant, ingestão Docling, `/search` filtrável, `/status`, `/reindex`.
- **M4** Análise grátis/anônima: upload/câmera → extração → **veredito semáforo no modal**; persiste + claim_token.
- **M5** Pagamento Asaas (B2C): preço dinâmico (20%/teto 300), `create-payment`, `asaas-webhook` idempotente.
- **M6** Dados + perguntas da LLM: cadastro/claim + formulário + perguntas condicionais (needs assessment).
- **M7** Geração + entrega + medição: redação 2 passos + RAG + timbrado; DOCX e PDF; 3 entregas; token_usage+custo.
- **M8** Painel do usuário + editor open-notebook: histórico + editor nativo sincronizado + reexport.
- **M9** Admin (CRUD total): usuários, pagamentos, erros, prompts, LLM+preço/token, upload de conhecimento, e-mail.
- **M10** Parceiros: assinatura Asaas + carteira R$ + débito 3× custo + recarga; white-label; isolamento.
- **M11** Migração & cutover: portar prompts/dados do Supabase, baixar edge functions, apontar domínio, desativar Lovable.
- **M12** Hardening + LGPD: auditoria de isolamento, rate limit, URLs assinadas, retenção/consentimento, backups testados.
- **M13** Graphify (opcional): grafo ficha↔artigo↔jurisprudência expandindo a recuperação do RAG.

> Sequencial; **M3 pode andar em paralelo à M2**. Frontends (M4/M8/M9/M10) reaproveitam a identidade
> visual "O Veredito" e os prompts do Supabase, lidos do Lovable como referência.

## 11. Requisitos externos
- **Chaves:** Asaas (prod+sandbox+webhook); LLM (Anthropic/OpenAI/DeepSeek/GLM/MiniMax/Kimi) + OpenAI embeddings;
  Evolution API; poste.io; domínios (app, api, s3, qdrant, notebook).
- **Conteúdo:** fichas MBFT (MinIO) + manual completo; jurisprudência inicial; modelos de recurso; timbrados.
- **Da VPS:** acesso open-notebook (API+token), Evolution, poste.io, Postgres/Qdrant/MinIO.
- **Pendências de número (não travam arquitetura):** valor da assinatura do parceiro; preço por token por modelo;
  quais dados de produção migrar (se algum).

O contrato de variáveis canônico está em `/.env.example`.

## 12. Princípios não-negociáveis
1. **RAG verificável:** citar só o que veio da base; nada inventado.
2. **Isolamento multi-tenant** é responsabilidade do backend (sem RLS) — auditar na M12.
3. **Acentuação PT** correta em DOCX/PDF.
4. **Segredos** nunca em texto puro no Git (SOPS/age + EasyPanel).
5. **Preço server-side** (o cliente nunca define valor).
6. **LGPD:** foto/CPF/CNH são sensíveis — retenção, purga de anônimos, exclusão a pedido, consentimento.

## 13. Lembretes
- **NUNCA** comitar segredo em texto puro; usar SOPS/age + variáveis no EasyPanel.
- **Acentos PT** corretos no DOCX/PDF é critério de aceite da M7.
- **Regra de ouro do RAG:** citar só o que veio da base; nada inventado.
- **Isolamento multi-tenant** (sem RLS) é responsabilidade do backend — auditar na M12.
- Há um **PAT do GitHub** exposto em conversa anterior que precisa ser **revogado**.
