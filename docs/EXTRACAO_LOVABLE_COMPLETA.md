# EXTRAÇÃO COMPLETA DO LOVABLE — Segura Multas / Defesa Lucrativa
> **Fonte:** projeto Lovable `f1b3f2ab-743c-4160-b5a2-2fac0d33ef83` (Supabase interno `usioiiolalelwzzdeuov`)
> **Data da extração:** 30/06/2026
> **Propósito:** O Lovable será ABANDONADO. Tudo será reescrito no FastAPI/VPS. Este documento
> preserva 100% dos prompts, lógica de negócio, contratos de API e fluxos para reúso/melhoria.
> **Método:** leitura direta via `Lovable:read_file` de cada arquivo.

---

# ÍNDICE
1. Decisões de produto (com as MUDANÇAS do Djair vs. Lovable)
2. Edge function `analyze-fine` — prompts LITERAIS + lógica
3. Edge function `generate-resource` — prompts LITERAIS + lógica + gerador PDF
4. Edge function `mbft` — proxy VPS (contrato de API)
5. Edge function `create-payment` — pagamento Asaas (inferida do front)
6. Frontend — páginas e fluxos
7. Questionário do condutor — perguntas LITERAIS
8. Esquema de dados (Supabase)
9. O que falta construir NOVO

---

# 1. DECISÕES DE PRODUTO

## Confirmadas (mantidas do Lovable)
- **Preço:** 20% do valor da multa, teto R$ 300, calculado NO SERVIDOR.
- **Pagamento:** Asaas (PIX, boleto, cartão), com polling de confirmação (5s).
- **Modelo freemium:** veredito + nulidades grátis; cobra só o PDF do recurso.
- **Veredito:** analisa → sem defesa: informa "sem defesa". Com defesa: informa "tem defesa"
  e habilita botão "contratar defesa" → redireciona pro pagamento Asaas.

## MUDANÇAS definidas pelo Djair (DIVERGEM do Lovable)
- **[MUDANÇA] Embriaguez (CTB art. 165 / 165-A): 100% AUTOMATIZADO.**
  O Lovable retornava `CONTACT_REQUIRED` (atendimento humano). AGORA gera recurso automático
  como qualquer outra infração. → REMOVER a exceção de embriaguez no código novo.
- **[MUDANÇA] Anônimo NÃO analisa.**
  O Lovable permitia análise sem login (com `claim_token` + reivindicação pós-cadastro).
  AGORA exige cadastro/login ANTES de analisar. → ELIMINAR claim_token e fluxo de reivindicação.
  Todo registro de análise tem dono (user_id obrigatório).

---

# 2. EDGE FUNCTION `analyze-fine`

**Runtime:** Deno. **LLM:** Lovable Gateway `https://ai.gateway.lovable.dev/v1/chat/completions`,
modelo `google/gemini-2.5-flash`. **No novo:** trocar por OpenAI/Anthropic direto.

**Entrada:** `multipart/form-data` com `file` (imagem/PDF) + `questionnaire_answers` (JSON string).
**Auth no Lovable:** JWT opcional (anônimo permitido). **No novo: JWT obrigatório.**

## PROMPT STEP 0 — Validação "é multa?" (LITERAL)
```
Analise este documento e determine se ele é uma NOTIFICAÇÃO DE INFRAÇÃO DE TRÂNSITO, MULTA DE TRÂNSITO, ou AUTO DE INFRAÇÃO de trânsito brasileiro.

Documentos válidos incluem:
- Notificação de Autuação (NIA)
- Notificação de Penalidade (NIP)
- Auto de Infração de Trânsito (AIT)
- Multas emitidas por DETRAN, PRF, DER, Prefeituras, DNIT
- Boletos de multa de trânsito com dados da infração

Documentos NÃO válidos:
- Contratos, documentos pessoais (RG, CPF, CNH)
- Boletos bancários comuns (não relacionados a multas)
- Extratos bancários
- Notas fiscais
- Qualquer documento que não seja relacionado a infração de trânsito

Retorne APENAS um JSON no formato:
{
  "is_traffic_fine": true ou false,
  "confidence": "alta" ou "media" ou "baixa",
  "document_type": "descrição curta do tipo de documento identificado",
  "reason": "motivo da classificação"
}

IMPORTANTE: Retorne APENAS o JSON, sem markdown.
```
Se `is_traffic_fine=false` → erro `NOT_TRAFFIC_FINE` com mensagem amigável (status 400).

## PROMPT STEP 1 — Extração OCR por visão (LITERAL)
```
Você é um especialista em análise de multas de trânsito brasileiras. Analise esta imagem de notificação de infração de trânsito e extraia TODOS os dados visíveis.

Retorne um JSON com os seguintes campos (use null se não encontrar):
{
  "numero_auto": "número do auto de infração",
  "codigo_infracao": "código da infração (ex: 74550)",
  "descricao_infracao": "descrição completa da infração",
  "data_infracao": "data da infração no formato DD/MM/AAAA",
  "hora_infracao": "hora da infração",
  "local_infracao": "endereço/local completo onde ocorreu",
  "placa_veiculo": "placa do veículo",
  "marca_modelo": "marca e modelo do veículo",
  "orgao_autuador": "órgão que aplicou a multa",
  "valor_multa": "valor da multa em reais",
  "pontos": número de pontos (apenas o número),
  "data_limite_recurso": "data limite para recurso"
}

IMPORTANTE: Retorne APENAS o JSON, sem markdown, sem explicações.
```

## PROMPT STEP 2 — Análise de nulidades (LITERAL)
System: `Você é um advogado especialista em direito de trânsito brasileiro com vasta experiência em recursos de multas.`
User:
```
Você é um advogado especialista em direito de trânsito brasileiro. Analise os dados desta multa e identifique TODAS as possíveis nulidades e vícios formais.

DADOS DA MULTA:
${JSON.stringify(extractedData, null, 2)}

RESPOSTAS DO QUESTIONÁRIO (informações adicionais do condutor):
${JSON.stringify(parsedAnswers, null, 2)}

Com base no Código de Trânsito Brasileiro (CTB), resoluções do CONTRAN e jurisprudência, identifique:

1. NULIDADES FORMAIS (vícios na notificação):
   - Ausência de dados obrigatórios (Art. 280 CTB)
   - Erro na identificação do veículo ou condutor
   - Falta de descrição clara da infração
   - Ausência de local preciso
   - Falta de assinatura ou identificação do agente
   - Prazo de notificação excedido (30 dias - Art. 281 CTB)

2. NULIDADES MATERIAIS:
   - Erro no enquadramento da infração
   - Impossibilidade física da infração
   - Ausência de equipamento de aferição aferido/calibrado
   - Falta de sinalização adequada no local

3. VÍCIOS PROCESSUAIS:
   - Cerceamento de defesa
   - Falta de dupla notificação
   - Inconsistências nos dados

Retorne APENAS um JSON no formato:
{
  "status": "null" | "weak" | "valid",
  "nullities": [
    {
      "titulo": "Título curto da nulidade",
      "base_legal": "Artigo de lei ou resolução",
      "descricao": "Explicação detalhada de por que isso é uma nulidade",
      "gravidade": "alta" | "media" | "baixa"
    }
  ],
  "summary": "Resumo geral da análise em 2-3 frases",
  "recommendation": "Recomendação clara sobre o que fazer"
}

REGRAS PARA STATUS:
- "null": Encontrou nulidades de alta gravidade que provavelmente anulam a multa
- "weak": Encontrou nulidades de média gravidade que podem contestar a multa
- "valid": Não encontrou nulidades significativas, multa parece válida

IMPORTANTE: Retorne APENAS o JSON, sem markdown.
```

## Lógica de persistência e gating (analyze-fine)
- `REQUIRE_PAYMENT` (env) → se true, `resource_available=false` até pagar.
- `hasFindings = status==="null" || status==="weak"`.
- Autenticado: upload do doc no bucket `fine-documents`, cria signed URL (1 ano), insere em `analyses`.
- [Lovable] Anônimo + paywall: insere SEM o documento, com `claim_token`. **[NOVO: remover — sem anônimo]**
- Retorno: `{ success, analysis_id, claim_token, is_authenticated, status, extracted_data, nullities, summary, recommendation }`.
- Tratamento de erro: 429 (rate limit), 402 (créditos), 500.

---

# 3. EDGE FUNCTION `generate-resource`

**LLM:** mesmo gateway, `google/gemini-2.5-flash`. **Entrada:** JSON `{ analysis_id, force? }`.
**Auth:** JWT user OU header `x-internal-agent-secret`. **Gating:** `resource_available` OU (internal+force).

## PROMPT RASCUNHO — buildDraftPrompt (LITERAL)
System: `Você é um advogado especialista em direito de trânsito brasileiro, com domínio do CTB (Lei 9.503/97), Resoluções CONTRAN e do Manual Brasileiro de Fiscalização de Trânsito (MBFT/DENATRAN).`
User:
```
Redija um RECURSO ADMINISTRATIVO DE MULTA DE TRÂNSITO completo, em português brasileiro, formal, juridicamente fundamentado.

DADOS DA MULTA:
${JSON.stringify(extracted, null, 2)}

NULIDADES IDENTIFICADAS:
${JSON.stringify(nullities, null, 2)}

INFORMAÇÕES ADICIONAIS DO CONDUTOR:
${JSON.stringify(answers, null, 2)}

SÍNTESE DA ANÁLISE PRÉVIA:
${summary}

ESTRUTURA OBRIGATÓRIA do documento:
1. Cabeçalho ("ILUSTRÍSSIMO SENHOR PRESIDENTE DA JARI / AUTORIDADE DE TRÂNSITO DO ÓRGÃO AUTUADOR")
2. Qualificação do recorrente (use placeholders [NOME COMPLETO], [CPF], [CNH], [ENDEREÇO] quando o dado não estiver presente)
3. Identificação do auto de infração (número, data, local, código, descrição, placa)
4. I — DOS FATOS (narrativa objetiva)
5. II — DO DIREITO (cada nulidade vira um subtópico com base legal explícita: CTB Lei 9.503/97, Resoluções CONTRAN aplicáveis, MBFT/DENATRAN, súmulas e jurisprudência pertinente)
6. III — DO PEDIDO (cancelamento do auto, arquivamento, devolução de pontos)
7. Fechamento ("Nestes termos, pede deferimento.", local e data, assinatura)

REGRAS:
- Não invente fatos que não estejam nos dados.
- Cite artigo e dispositivo exatos (ex.: "art. 280, VI, do CTB").
- Linguagem técnica, sem floreios.
- Saída em TEXTO PURO (sem markdown, sem ```).
```

## PROMPT REVISÃO — buildReviewPrompt (LITERAL)
System: `Você é um revisor jurídico sênior em direito de trânsito brasileiro. Corrige, fortalece a fundamentação legal e devolve o texto final pronto para protocolo.`
User:
```
Revise o recurso administrativo abaixo. Mantenha a estrutura. Corrija erros gramaticais e jurídicos, reforce a fundamentação legal (CTB, Resoluções CONTRAN, MBFT) e remova redundâncias. Não invente fatos novos. Devolva APENAS o texto final, sem markdown.

----- RECURSO ORIGINAL -----
${draft}
```

## Gerador de PDF (buildPdf) — características
PDF próprio SEM bibliotecas. A4 (595x842pt), margens 50pt, Helvetica 11pt, leading 14pt,
título 14pt, ~90 chars/linha, quebra de linha por palavra, paginação automática.
Monta objetos PDF manualmente (Catalog, Pages, Font, Content/Page por página), xref, trailer.
**No novo:** o FastAPI já tem docx_render.py + skill de PDF — usar Visual Law do escritório
se for documento jurídico, ou manter PDF simples para o produto B2C (decisão pendente).

## Fluxo completo generate-resource
1. Valida analysis_id. 2. Auth (user JWT ou internal secret). 3. Busca análise no Supabase.
4. Checa ownership (não-internal). 5. Gate de pagamento. 6. Se já tem resource_url → retorna signed URL.
7. Rascunho → 8. Revisão → 9. buildPdf → 10. upload bucket `resources` → 11. update resource_url
→ 12. signed URL (7 dias). Retorno: `{ success, analysis_id, resource_path, signed_url, generated }`.

---

# 4. EDGE FUNCTION `mbft` (PROXY PARA A VPS)

Proxy admin→VPS FastAPI. **Contrato que a VPS DEVE implementar:**
```
GET  /status   → { fichas_indexed, chunks, collection, healthy, last_ingested_at }
POST /reindex  → dispara job; retorna { job_id }
POST /search   → body { consulta, filtros:{codigo}?, top_k }
                 retorna { results: [{ texto, codigo, artigo_ctb, ficha, pagina, score, fonte }] }
```
Auth: `x-internal-agent-secret` OU admin (tabela `user_roles` role=admin via getClaims).
Ações: status/reindex exigem admin; search permite qualquer user logado.
Secrets: `MBFT_API_URL`, `MBFT_API_KEY`, `INTERNAL_AGENT_SECRET`.
Se `MBFT_API_URL` ausente → `{ configured: false }` (UI mostra "offline").
Erros: 502 (VPS unreachable), repassa status do upstream.

>>> CRÍTICO: o formato `/search` já separa `codigo` e `artigo_ctb` — exatamente os eixos
>>> da busca estruturada. O endpoint /search híbrido novo DEVE seguir este contrato.

---

# 5. EDGE FUNCTION `create-payment` (inferida do PaymentModal)

Não foi lida diretamente, mas o contrato é claro pelo front:
**Entrada:** `{ analysis_id }`.
**Saída:** `{ payment_id, invoice_url, bank_slip_url, pix_code, value, due_date, status, price_source, fine_value }`.
- Calcula preço no servidor: 20% de `fine_value`, teto R$ 300. `price_source: percentage|fallback`.
- [Lovable] Embriaguez → `CONTACT_REQUIRED`. **[NOVO: remover — embriaguez é automático]**
- Cria cobrança no Asaas. Confirmação via polling (hook usePaymentStatus, 5s).

---

# 6. FRONTEND — páginas e fluxos

**Rotas (App.tsx):** Index, Auth (/login, /cadastro), Analise, Resultado/:id, Dashboard,
Empresas, ComoFunciona, Recursos, FAQ, Documentacao, Privacidade, Termos, Admin, NotFound.

**Fluxo de análise (Analise.tsx):** 2 passos — UploadStep (envia foto) → QuestionsStep (questionário)
→ chama `analyzeFine(file, answers)` → navega pra /resultado/:id.
[Lovable] Salva claim_token no localStorage p/ anônimo. **[NOVO: remover]**

**Resultado (Resultado.tsx):** mostra VerdictStamp (semáforo), summary, recommendation, dados
extraídos, nulidades (grátis, na íntegra), disclaimer. Botões conforme estado:
- tem defesa + não logado → "Criar conta para gerar" **[NOVO: login é antes da análise]**
- tem defesa + logado + não pago → "Pagar e gerar o recurso" (abre PaymentModal)
- tem defesa + logado + pago → "Baixar recurso em PDF" (chama generate-resource)
[Lovable] Painel "contato necessário" para embriaguez. **[NOVO: remover — automático]**

**Admin (Admin.tsx):** abas Dashboard (recharts: linha 30 dias, pizza status, barra semanal),
Análises (lista+busca+detalhe+download), Usuários, MBFT (status/reindex/busca-teste com filtro código).
Stats: total, nulas, fracas, válidas, usuários, taxa de sucesso.
Gating admin: hook useAdmin (tabela user_roles).

---

# 7. QUESTIONÁRIO DO CONDUTOR (QuestionsStep.tsx) — LITERAL

6 perguntas booleanas (sim/não/null). TODAS sobre RADAR/VELOCIDADE:
```
1. speed:          "A multa é por excesso de velocidade?"
                   help: "Inclui radares fixos, móveis ou portáteis."
2. radarType:      "O tipo de radar aparece na multa?"
                   help: "Ex: radar fixo, móvel, estático, portátil."
3. portableRadar:  "Havia viatura parada com radar portátil?"
                   help: "Fiscalização com equipamento segurado por agente."
4. authorization:  "Você encontrou publicação autorizando radar naquele trecho?"
                   help: "Publicação oficial do órgão no site ou diário oficial."
5. locationClear:  "O local da infração está claro na multa?"
                   help: "Endereço específico, KM ou referência identificável."
6. descriptionMatch: "A multa descreve exatamente o que você fez?"
                   help: "A descrição corresponde à realidade dos fatos."
```
**[NOVO]** O produto novo terá DOIS modos de entrada (foto OU digitação) e questionário
DINÂMICO por tipo de infração (não só radar).

---

# 8. ESQUEMA DE DADOS (Supabase)

**Tabela `analyses`:** id, user_id, claim_token[NOVO:remover], status, document_type,
document_url, extracted_data(jsonb), questionnaire_answers(jsonb), nullities(jsonb),
result(jsonb:{summary,recommendation}), resource_available(bool), resource_url, created_at.

**Tabela `user_roles`:** user_id, role (admin) — gating de admin.
**Tabela `profiles`:** full_name, cpf, email, phone, account_type (business|individual).
**Buckets:** `fine-documents` (multas enviadas), `resources` (PDFs gerados).

**Equivalente no FastAPI (JÁ MODELADO):** case.py (cases, case_files, traffic_tickets,
case_analyses, processing_jobs), recurso.py (recursos, payments), generated.py
(generated_resources, generated_resource_versions), partner.py (multi-tenant).

---

# 9. O QUE FALTA CONSTRUIR NOVO (não existe no Lovable)

1. **Tela OpenNotebook** — chat de aprimoramento do recurso. `chat-resource` deu 404, nunca existiu.
2. **Dois modos de entrada** — foto OU digitação de campos normalizados.
3. **Questionário dinâmico** por tipo de infração.
4. **Busca estruturada** código/artigo — JÁ FEITA (legal_search.py), falta plugar no /search.
5. **CTB no RAG** — dataset separado seguramultas_ctb.
6. **Proteção** anti prompt-injection / anti roubo de prompt e dados.
7. **App polícia** (Produto 2) — guia de autuação com comando de voz, usa o mesmo MBFT.
8. **Endpoints VPS** /status, /reindex, /search no formato do contrato do proxy mbft.

---

# RESUMO DAS DIVERGÊNCIAS LOVABLE → NOVO
| Item | Lovable | Desenvolvimento novo |
|---|---|---|
| LLM | gemini-2.5-flash (gateway Lovable) | OpenAI/Anthropic direto (VPS) |
| Embriaguez | atendimento humano (CONTACT_REQUIRED) | 100% automático |
| Análise anônima | permitida (claim_token) | proibida (login obrigatório) |
| Entrada | só foto + questionário radar | foto OU digitação, questionário dinâmico |
| Busca | só semântica | híbrida (estruturada + semântica) |
| Aprimoramento | inexistente | tela OpenNotebook |
| Hospedagem | Lovable + Supabase | VPS própria (FastAPI) |
