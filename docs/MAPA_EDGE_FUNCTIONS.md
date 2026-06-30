# Mapa das Edge Functions do Lovable (projeto f1b3f2ab)
# Extraído via Lovable:read_file — para migração ao FastAPI

## Supabase project_id interno: usioiiolalelwzzdeuov

## Edge functions CONFIRMADAS (lidas inteiras):

### 1. analyze-fine  (→ já portada como api/app/services/analyzer.py)
Pipeline 3 passos via Lovable AI Gateway (google/gemini-2.5-flash):
- STEP 0: validação "é multa de trânsito?" (prompt completo extraído)
- STEP 1: extração OCR por visão (12 campos: numero_auto, codigo_infracao, etc.)
- STEP 2: análise de nulidades (formais/materiais/processuais) + status null|weak|valid
Persiste em tabela `analyses` (Supabase). Gating REQUIRE_PAYMENT. Upload bucket fine-documents.
claim_token para anônimos. Auth: JWT user opcional.

### 2. generate-resource  (→ já portada como api/app/services/recurso_gen.py)
Geração 2 passos (buildDraftPrompt + buildReviewPrompt) via gemini-2.5-flash.
System draft: advogado trânsito. System review: revisor jurídico sênior.
Estrutura: cabeçalho JARI / qualificação / auto / I-FATOS / II-DIREITO / III-PEDIDO.
Gerador de PDF próprio (buildPdf, A4 Helvetica, sem libs).
Auth: JWT user OU x-internal-agent-secret. Gating: resource_available OU (internal+force).
Upload bucket `resources`, signed URL 7 dias. Tabela analyses.

### 3. mbft  (PROXY para a VPS FastAPI — JÁ ESPERA O BACKEND)
Proxy admin→VPS. Ações: status (GET /status), reindex (POST /reindex), search (POST /search).
Auth: x-internal-agent-secret OU admin (user_roles role=admin).
Secrets esperados: MBFT_API_URL, MBFT_API_KEY.
Payload search: { consulta, filtros:{codigo}, top_k }.
Espera resposta search: { results:[{ texto, codigo, artigo_ctb, ficha, pagina, score, fonte }] }.
Espera resposta status: { fichas_indexed, chunks, collection, healthy, last_ingested_at }.

## Frontend (páginas que chamam edge functions):
- Resultado.tsx → invoke("generate-resource", { analysis_id }) ; PaymentModal
- Admin.tsx → invoke("mbft", { action }) — dashboard com aba MBFT (status/reindex/search de teste)
- Analise.tsx → (provável invoke analyze-fine) — A LER
- PaymentModal → (provável edge function de pagamento) — A LER

## Tabela Supabase `analyses` (campos inferidos):
id, user_id, claim_token, status, document_type, document_url,
extracted_data (jsonb), questionnaire_answers (jsonb), nullities (jsonb),
result (jsonb: summary, recommendation), resource_available (bool), resource_url

## Rotas frontend: Index, Auth, Analise, Resultado/:id, Dashboard, Empresas,
## Documentacao, Privacidade, Termos, Admin, ComoFunciona, Recursos, FAQ

## AINDA A MAPEAR:
- Analise.tsx (fluxo de entrada, chamada analyze-fine, questionário)
- PaymentModal / edge function de pagamento (Asaas? Stripe?)
- Possível edge function de chat/OpenNotebook (chat-resource NÃO existe — 404)
- supabase/functions/_shared (se existir)
