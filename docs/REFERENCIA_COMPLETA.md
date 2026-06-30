# REFERÊNCIA — Extração completa do Lovable (Segura Multas)
> Fonte: projeto Lovable `f1b3f2ab-743c-4160-b5a2-2fac0d33ef83` (Supabase `usioiiolalelwzzdeuov`)
> Propósito: o Lovable NÃO será mais usado. Este documento preserva os prompts, a lógica
> de negócio e os fluxos que valem ser reaproveitados/melhorados no desenvolvimento novo (FastAPI/VPS).

---

## 1. MODELO DE NEGÓCIO (confirmado pelo código)

**Funil:**
1. Cliente envia foto/PDF da multa (sem cadastro) → análise gratuita
2. Veredito grátis: semáforo `null` (nula) / `weak` (fraca) / `valid` (válida)
3. Nulidades exibidas na íntegra, de graça
4. Para gerar o RECURSO em PDF → paga **20% do valor da multa, teto R$ 300**
5. Pagamento via **Asaas** (PIX, boleto, cartão), com polling de confirmação
6. Anônimo recebe `claim_token` → após cadastro, "reivindica" a análise

**Caso especial — embriaguez (CTB art. 165 / 165-A):**
NÃO gera recurso automático. Retorna `CONTACT_REQUIRED` → encaminha p/ atendimento
humano (WhatsApp/e-mail). Decisão de produto importante: casos graves = atendimento especializado.

**Preço calculado no SERVIDOR** (nunca no front): 20% da `valor_multa` extraída, teto R$ 300.
Se não conseguir ler o valor da multa → preço fallback. Campo `price_source: percentage|fallback`.

---

## 2. PIPELINE DE ANÁLISE (analyze-fine) — 3 passos

LLM: Lovable Gateway `google/gemini-2.5-flash` (no novo: trocar por OpenAI/Anthropic direto).

### STEP 0 — Validação "é multa de trânsito?"
Evita processar documento errado. Retorna JSON:
`{ is_traffic_fine: bool, confidence: alta|media|baixa, document_type, reason }`
Válidos: NIA, NIP, AIT, multas DETRAN/PRF/DER/DNIT/Prefeitura.
Inválidos: contrato, RG/CPF/CNH, boleto comum, extrato, NF.
Se não for multa → erro `NOT_TRAFFIC_FINE` com mensagem amigável.

### STEP 1 — Extração OCR por visão
12 campos: numero_auto, codigo_infracao, descricao_infracao, data_infracao, hora_infracao,
local_infracao, placa_veiculo, marca_modelo, orgao_autuador, valor_multa, pontos, data_limite_recurso.
Retorna null em campo não encontrado. (No novo já existe em analyzer.py — EXTRACTED_FIELDS.)

### STEP 2 — Análise de nulidades
Advogado de trânsito. Divide em 3 categorias:
- FORMAIS: ausência de dados obrigatórios (Art. 280 CTB), erro identificação veículo/condutor,
  falta descrição clara, ausência local preciso, falta assinatura/identificação agente,
  prazo notificação excedido (30 dias - Art. 281 CTB)
- MATERIAIS: erro enquadramento, impossibilidade física, ausência aferição/calibração equipamento,
  falta sinalização adequada
- PROCESSUAIS: cerceamento de defesa, falta dupla notificação, inconsistências
Retorna: `{ status, nullities:[{titulo, base_legal, descricao, gravidade}], summary, recommendation }`
Regra de status:
- `null`: nulidades de ALTA gravidade que provavelmente anulam
- `weak`: nulidades de MÉDIA gravidade que podem contestar
- `valid`: sem nulidades significativas

---

## 3. GERAÇÃO DO RECURSO (generate-resource) — 2 passos

### Passo 1 — Rascunho (system: advogado especialista trânsito)
Estrutura OBRIGATÓRIA:
1. Cabeçalho "ILUSTRÍSSIMO SENHOR PRESIDENTE DA JARI / AUTORIDADE DE TRÂNSITO DO ÓRGÃO AUTUADOR"
2. Qualificação do recorrente (placeholders [NOME COMPLETO], [CPF], [CNH], [ENDEREÇO])
3. Identificação do auto (número, data, local, código, descrição, placa)
4. I — DOS FATOS (narrativa objetiva)
5. II — DO DIREITO (cada nulidade = subtópico com base legal: CTB Lei 9.503/97, Resoluções CONTRAN, MBFT, súmulas, jurisprudência)
6. III — DO PEDIDO (cancelamento, arquivamento, devolução de pontos)
7. Fechamento ("Nestes termos, pede deferimento.", local/data, assinatura)
Regras: não inventar fatos; citar artigo exato (ex.: "art. 280, VI, do CTB"); texto puro sem markdown.

### Passo 2 — Revisão (system: revisor jurídico sênior)
Mantém estrutura, corrige gramática/direito, reforça fundamentação (CTB/CONTRAN/MBFT), remove redundância.

(No novo já existe em recurso_gen.py — prompts equivalentes. GAP: não usa busca estruturada por código.)

---

## 4. QUESTIONÁRIO DO CONDUTOR (QuestionsStep) — 6 perguntas booleanas

Foco atual: VELOCIDADE/RADAR (estratégia jurídica revela o nicho mais lucrativo).
1. speed — "A multa é por excesso de velocidade?" (radares fixos/móveis/portáteis)
2. radarType — "O tipo de radar aparece na multa?"
3. portableRadar — "Havia viatura parada com radar portátil?"
4. authorization — "Você encontrou publicação autorizando radar naquele trecho?"
5. locationClear — "O local da infração está claro na multa?"
6. descriptionMatch — "A multa descreve exatamente o que você fez?"

Respostas (sim/não/null) vão como `questionnaire_answers` pro STEP 2 da análise.
NOTA p/ desenvolvimento novo: o produto novo terá DOIS modos de entrada (foto OU digitação de campos)
e o questionário deve ser DINÂMICO conforme o tipo de infração (não só radar).

---

## 5. PAGAMENTO (create-payment + usePaymentStatus)

Edge function `create-payment` (NÃO lida ainda — inferida pelo front):
- Recebe analysis_id, calcula preço no servidor (20%/teto 300), cria cobrança no Asaas
- Retorna: `{ payment_id, invoice_url, bank_slip_url, pix_code, value, due_date, status, price_source, fine_value }`
- Caso embriaguez → `CONTACT_REQUIRED`
Confirmação: polling a cada 5s (usePaymentStatus) consultando status do payment_id.
Provider: **Asaas** (já é o gateway do Djair em outros projetos).

---

## 6. INTEGRAÇÃO FRONTEND ↔ VPS (edge function `mbft` = PROXY)

JÁ DESENHADA. O front (Admin) chama a edge `mbft` que repassa pra VPS FastAPI.
Endpoints que a VPS DEVE expor (contrato já definido pelo proxy):
- `GET  /status`  → { fichas_indexed, chunks, collection, healthy, last_ingested_at }
- `POST /reindex` → dispara job de ingestão
- `POST /search`  → recebe { consulta, filtros:{codigo}, top_k }
                    retorna { results:[{ texto, codigo, artigo_ctb, ficha, pagina, score, fonte }] }
Secrets: MBFT_API_URL, MBFT_API_KEY, INTERNAL_AGENT_SECRET.

>>> IMPORTANTE: o formato de /search já prevê `codigo` E `artigo_ctb` separados —
>>> exatamente os eixos da busca estruturada (legal_search.py). Casa perfeitamente.
>>> No desenvolvimento novo, o frontend novo pode manter esse MESMO contrato de API
>>> (é bom), apenas chamando a VPS direto em vez de via edge function.

---

## 7. DASHBOARD ADMIN (Admin.tsx) — o que já existia

Abas: Dashboard (gráficos recharts), Análises (lista+busca+detalhe), Usuários, **MBFT**.
Aba MBFT: status do índice RAG, botão Reindexar, busca de teste COM filtro por código.
Stats: total análises, nulas/fracas/válidas, usuários, taxa de sucesso.
NOTA: a "dashboard de ingestão" que o Djair quer JÁ EXISTE em embrião aqui.
No novo: expandir para upload de PDF (resolução/jurisprudência/livro) → chunk no RAGFlow,
com classificação de fonte (source_type, allow_verbatim) por causa do copyright.

---

## 8. ESQUEMA DE DADOS (tabela `analyses` no Supabase)

Campos: id, user_id, claim_token, status, document_type, document_url,
extracted_data(jsonb), questionnaire_answers(jsonb), nullities(jsonb),
result(jsonb:{summary,recommendation}), resource_available(bool), resource_url.
(No novo FastAPI: equivalente em case.py/case_analyses + recurso.py — JÁ MODELADO.)

Buckets storage: `fine-documents` (multas), `resources` (PDFs gerados).
Tabela `user_roles` (role=admin) para gating de admin.

---

## 9. EDGE FUNCTIONS — inventário

| Função | Status | Equivalente FastAPI |
|---|---|---|
| analyze-fine | LIDA ✓ | analyzer.py (portado) |
| generate-resource | LIDA ✓ | recurso_gen.py (portado) |
| mbft | LIDA ✓ (proxy) | precisa /status /reindex /search na VPS |
| create-payment | inferida (front) | payments.py (existe, validar Asaas) |
| chat-resource | NÃO EXISTE (404) | OpenNotebook é a CONSTRUIR do zero |

---

## 10. O QUE FALTA CONSTRUIR NOVO (não existe no Lovable)

1. **Tela OpenNotebook** — chat de aprimoramento do recurso (cliente pede "complementa",
   "detalha o argumento X"). NÃO existe no Lovable. Construir do zero.
2. **Dois modos de entrada** — foto OU digitação de campos (Lovable só tem foto+questionário).
3. **Questionário dinâmico** por tipo de infração (Lovable só cobre radar/velocidade).
4. **Busca estruturada** por código/artigo (legal_search.py — JÁ FEITO, falta plugar).
5. **CTB no RAG** (dataset separado).
6. **Proteção** anti prompt-injection / anti-leak (antes de expor a VPS ao front).
7. **App polícia** (Produto 2) — guia de autuação, comando de voz, usa o mesmo MBFT.

---

## 11. PROMPTS COMPLETOS (transcritos para reúso)

> Os prompts completos de validação, extração e nulidades (analyze-fine) e de
> rascunho/revisão (generate-resource) estão no transcrito desta sessão e nos arquivos
> originais. Os system prompts canônicos:

- DRAFT system: "Você é um advogado especialista em direito de trânsito brasileiro, com
  domínio do CTB (Lei 9.503/97), Resoluções CONTRAN e do Manual Brasileiro de Fiscalização
  de Trânsito (MBFT/DENATRAN)."
- REVIEW system: "Você é um revisor jurídico sênior em direito de trânsito brasileiro.
  Corrige, fortalece a fundamentação legal e devolve o texto final pronto para protocolo."
- NULLITY system: "Você é um advogado especialista em direito de trânsito brasileiro com
  vasta experiência em recursos de multas."

(Prompts de usuário completos preservados em analyzer.py / recurso_gen.py e no transcrito.)
