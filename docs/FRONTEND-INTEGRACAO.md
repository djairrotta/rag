# Frontend — adoção do "O Veredito" (Lovable) + religação ao nosso backend

## Decisão
O frontend oficial é o app **"O Veredito" / Segura Multas** que já existe no Lovable
(projeto `f1b3f2ab-743c-4160-b5a2-2fac0d33ef83`, commit `d029672`). É um app completo,
profissional e PWA — **muito além** de um scaffold do zero. O scaffold que eu tinha
começado foi descartado.

Stack do frontend: Vite + React 18 + TypeScript + shadcn/ui (Radix) + Tailwind +
@tanstack/react-query + framer-motion + zod + react-hook-form + sonner + vite-plugin-pwa.
Já vem com `Dockerfile` + `nginx.conf` → **pronto pra subir como serviço estático no EasyPanel**.

## Arquitetura = dois entregáveis (o que você pediu: dois zips)
- **backend** → nosso monorepo FastAPI (`api` + `rag`) → `backend.zip` → serviços no EasyPanel.
- **frontend** → o app React do Lovable (repo próprio, com Docker/nginx) → serviço estático.

O frontend foi feito pra **Supabase**. A única coisa que muda pra ele rodar no nosso
backend é a **camada de dados**: trocar o cliente Supabase por um cliente da nossa API.
Cirúrgico — não mexe em UI, páginas nem componentes.

## Mapa de contrato: o que o frontend chama ⇄ nosso endpoint
| Frontend (hoje, Supabase) | Nosso endpoint (FastAPI) | Estado |
|---|---|---|
| `supabase.auth.signUp` | `POST /auth/register` | ✅ M2 |
| `supabase.auth.signInWithPassword` | `POST /auth/login` | ✅ M2 |
| `supabase.auth.signOut` | `POST /auth/logout` | ✅ M2 |
| `supabase.auth.getSession` / `onAuthStateChange` | sessão local via tokens + `GET /auth/me` | ✅ M2 |
| `analyze-fine` (multipart: file + questionnaire_answers) | `POST /analyses` | ⏳ **M4** |
| reivindicar análise anônima após cadastro | `POST /analyses/{id}/claim` | ✅ M2 |
| `create-payment` (Asaas) | `POST /payments` | ⏳ **M5** |
| `asaas-webhook` | `POST /webhooks/asaas` | ⏳ **M5** |
| `generate-resource` (gera o recurso) | `POST /recursos` | ⏳ **M7** |
| `mbft` (consulta base) | nosso RAG `POST /search` | ✅ M3 (religar) |
| `chat-assistant` | `POST /chat` (opcional) | ⏳ |
| `generate-api-key` (parceiro) | `POST /partners/api-keys` | ⏳ M10 |
| `useAnalyses` / `useProfile` (tabelas) | `GET /analyses`, `GET /auth/me` | ⏳ M8 |

**O contrato do `analyze-fine` (de `useFineAnalysis.ts`) é a régua da M4.** Resposta esperada:
```ts
{
  success: boolean;
  analysis_id: string | null;
  claim_token?: string | null;          // ← casa com nosso claim (M2)
  is_authenticated: boolean;
  status: "null" | "weak" | "valid";     // veredito: null→🟢 vale recorrer · weak→🟡 discutível · valid→🔴 difícil
  extracted_data: { numero_auto, codigo_infracao, descricao_infracao, data_infracao,
                    hora_infracao, local_infracao, placa_veiculo, marca_modelo,
                    orgao_autuador, valor_multa, pontos, data_limite_recurso };
  nullities: { titulo, base_legal, descricao, gravidade: "alta"|"media"|"baixa" }[];
  summary: string;
  recommendation: string;
  error?, error_code?, details?
}
```
Erros do contrato que já temos no backend: `CONTACT_REQUIRED` (embriaguez CTB 165/165-A),
`PAYMENT_REQUIRED`, etc. A regra de preço e a detecção de embriaguez do frontend
(`_shared/pricing.ts`) **são idênticas** à nossa config server-side (20% / teto R$300 /
fallback R$69,90) — então a M5 reusa essa lógica.

## A religação (data layer) — arquivos que mudam
1. **`.env`**: remover `VITE_SUPABASE_URL` e `VITE_SUPABASE_PUBLISHABLE_KEY`; adicionar
   `VITE_API_BASE_URL` (ex.: `https://api.seguramultas.com.br`).
2. **novo** `src/integrations/api/client.ts` — cliente da nossa API (auth + refresh + análise).
3. **reescrever** `src/contexts/AuthContext.tsx` — mesma interface (`user/session/loading/
   signUp/signIn/signOut`), mas batendo em `/auth/*`.
4. **`src/hooks/useFineAnalysis.ts`** — trocar a URL `…/functions/v1/analyze-fine` por
   `${VITE_API_BASE_URL}/analyses` e o header `apikey` por `Authorization: Bearer` (quando logado).
5. **hooks de dados** (`useAnalyses`, `usePaymentStatus`, `useProfile`, `useAdmin`,
   `useApiKeys`) — repontar `supabase.functions.invoke` / `supabase.from` p/ os endpoints acima.
6. remover dep `@supabase/supabase-js` e o `lovable-tagger` (dev) ao sair do Lovable.

Itens 2–4 já estão prontos (ver `integration-kit/` neste repo): um cliente de API
equivalente ao do scaffold, que cobre register/login/me/refresh/logout + claim e tem o
formato pronto pra `POST /analyses`.

## Próximo passo do loop
**M4 backend** = portar a Supabase function `analyze-fine` (extração por LLM de visão →
`extracted_data` → nulidades + `status` → preço via regra do `pricing.ts` → grava `Analysis`
com `claim_token`) como `POST /analyses` no nosso FastAPI, devolvendo exatamente o
`AnalysisResult` acima. Com isso, basta repontar a URL no frontend e o fluxo grátis liga.
