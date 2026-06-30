# Variáveis de ambiente — por serviço

Três serviços, três conjuntos de env. **Regra de ouro:** segredo nunca vai pro
frontend (é público, vai pro navegador). Tudo que é chave/token fica só nos
serviços de backend (`api` e `rag`). Verdade em produção = **EasyPanel** (cada
serviço tem o seu painel de Environment). Versionar com segurança = SOPS → `.env.enc`.

Legenda: **🔒 segredo** · **obrig.** obrigatório pra subir · **opc.** opcional (liga
recurso quando preenchido).

---

## 1) Serviço `frontend` (Vite/React — build estático)

O frontend é **público**. Só recebe o que pode aparecer no navegador. **Zero segredos.**
No Vite, só variáveis com prefixo `VITE_` chegam ao bundle.

| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `VITE_API_BASE_URL` | obrig. | não | URL pública da API. Prod: `https://api.seguramultas.com.br`. Dev: `http://localhost:8000`. |
| `VITE_APP_NAME` | opc. | não | Nome exibido. Default `Segura Multas`. |
| `VITE_WHATSAPP_FALLBACK` | opc. | não | Número (E.164) p/ botão "falar no WhatsApp" nos casos `CONTACT_REQUIRED`. Ex.: `+5519999999999`. |

> Tudo o mais (preço, chaves, lógica de veredito) vem da API em runtime. O cliente
> **nunca** vê chave nem define valor.

---

## 2) Serviço `api` (FastAPI — backend principal)

O cérebro. Fala com Postgres, MinIO, RAG, LLMs, Asaas, e-mail, WhatsApp, open-notebook.

### App / segurança
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `APP_BASE_URL` | obrig. | não | URL do site. `https://seguramultas.com.br`. Usada em CORS e links de e-mail. |
| `API_BASE_URL` | obrig. | não | URL da própria API. `https://api.seguramultas.com.br`. |
| `INTERNAL_SECRET` | obrig. | 🔒 | Token do header `X-Internal-Secret` (chamadas internas/admin sensíveis). Gere: `openssl rand -hex 32`. |
| `JWT_SECRET` | obrig. | 🔒 | Assina os JWT. **Mín. 32 bytes.** Gere: `openssl rand -hex 32`. Trocar = desloga todo mundo. |
| `JWT_ACCESS_EXPIRY` | opc. | não | Segundos do access token. Default `900` (15 min). |
| `JWT_REFRESH_EXPIRY` | opc. | não | Segundos do refresh. Default `2592000` (30 dias). |

### Postgres (instância que já existe; DB dedicado)
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `POSTGRES_HOST` | obrig. | não | Host interno. No EasyPanel, o nome do serviço (ex.: `postgres`). |
| `POSTGRES_PORT` | obrig. | não | `5432`. |
| `POSTGRES_DB` | obrig. | não | `seguramultas`. |
| `POSTGRES_USER` | obrig. | não | `seguramultas`. |
| `POSTGRES_PASSWORD` | obrig. | 🔒 | Senha do role. Gere forte. |

### MinIO (buckets dedicados)
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `MINIO_ENDPOINT` | obrig. | não | URL S3. `https://s3.seguramultas.com.br`. |
| `MINIO_ACCESS_KEY` | obrig. | 🔒 | Access key do MinIO. |
| `MINIO_SECRET_KEY` | obrig. | 🔒 | Secret key do MinIO. |
| `BUCKET_FOTOS` | obrig. | não | `fotos` (uploads de multa). |
| `BUCKET_RECURSOS` | obrig. | não | `recursos` (PDFs gerados). |
| `BUCKET_CONHECIMENTO` | obrig. | não | `conhecimento` (fonte do RAG: MBFT, jurisprudência, modelos). |
| `BUCKET_TIMBRADOS` | obrig. | não | `timbrados` (papéis timbrados dos parceiros). |

### RAG (cliente — a `api` chama o serviço `rag`)
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `RAG_API_URL` | obrig. | não | URL interna do RAG. EasyPanel: `http://rag:8000`. |
| `RAG_API_KEY` | obrig. | 🔒 | Bearer que a `api` manda pro `rag`. **Tem que ser igual** ao `RAG_API_KEY` do serviço `rag`. |

### LLMs (admin escolhe o modelo por tarefa: analisar / perguntar / redigir)
Preencha **ao menos uma**. As demais ficam vazias até você querer usar.
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | opc.* | 🔒 | console.anthropic.com. |
| `OPENAI_API_KEY` | opc.* | 🔒 | platform.openai.com. (Também serve de fallback de embeddings.) |
| `DEEPSEEK_API_KEY` | opc. | 🔒 | platform.deepseek.com. |
| `GLM_API_KEY` | opc. | 🔒 | open.bigmodel.cn (Zhipu/GLM). |
| `MINIMAX_API_KEY` | opc. | 🔒 | minimax.io. |
| `KIMI_API_KEY` | opc. | 🔒 | platform.moonshot.cn (Kimi). |

\* *Pelo menos uma chave de LLM é necessária pra análise e geração funcionarem.*

### Asaas (pagamentos)
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `ASAAS_API_KEY` | obrig. (M5+) | 🔒 | Painel Asaas → Integrações → API. Use a chave do ambiente que casar com `ASAAS_ENV`. |
| `ASAAS_WEBHOOK_TOKEN` | obrig. (M5+) | 🔒 | Token que você define no webhook do Asaas; a `api` valida cada chamada. Gere forte. |
| `ASAAS_ENV` | obrig. (M5+) | não | `sandbox` (homologação) ou `production`. |

### E-mail (poste.io)
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `SMTP_HOST` | obrig. (entrega e-mail) | não | `mail.seguramultas.com.br`. |
| `SMTP_PORT` | obrig. | não | `587` (STARTTLS) ou `465`. |
| `SMTP_USER` | obrig. | 🔒 | Usuário SMTP criado no poste.io. |
| `SMTP_PASS` | obrig. | 🔒 | Senha desse usuário. |
| `SMTP_FROM` | obrig. | não | Remetente. `no-reply@seguramultas.com.br`. |

### WhatsApp (Evolution API) — opcional, liga entrega por Zap
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `EVOLUTION_API_URL` | opc. | não | URL da sua Evolution. |
| `EVOLUTION_API_KEY` | opc. | 🔒 | API key da Evolution. |
| `EVOLUTION_INSTANCE` | opc. | não | Nome da instância conectada. |

### open-notebook (editor nativo) — opcional até a M8
| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `ONBOOK_API_URL` | opc. | não | `https://notebook.seguramultas.com.br`. |
| `ONBOOK_API_TOKEN` | opc. | 🔒 | Token da API do open-notebook. |

---

## 3) Serviço `rag` (FastAPI — microserviço de RAG)

Indexa e busca na base de conhecimento. Precisa do Qdrant, de embeddings e (p/
ingestão) de ler os documentos-fonte do MinIO.

| Variável | Obrig.? | Segredo? | O que é / onde obter |
|---|---|---|---|
| `RAG_API_KEY` | obrig. | 🔒 | Bearer que **este** serviço exige. Tem que bater com o `RAG_API_KEY` da `api`. |
| `QDRANT_URL` | obrig. | não | `http://qdrant:6333` (nome do serviço no EasyPanel). |
| `QDRANT_API_KEY` | opc. | 🔒 | Só se seu Qdrant exigir auth. |
| `OPENAI_API_KEY` | obrig.** | 🔒 | Embeddings `text-embedding-3-large`. platform.openai.com. |
| `EMBED_MODEL` | opc. | não | Default `text-embedding-3-large`. |
| `EMBED_DIM` | opc. | não | Default `3072`. **Tem que casar** com a dimensão das coleções. |
| `MINIO_ENDPOINT` | obrig. (ingestão) | não | Igual ao da `api`. |
| `MINIO_ACCESS_KEY` | obrig. (ingestão) | 🔒 | Igual ao da `api`. |
| `MINIO_SECRET_KEY` | obrig. (ingestão) | 🔒 | Igual ao da `api`. |
| `BUCKET_CONHECIMENTO` | obrig. (ingestão) | não | `conhecimento`. |

\*\* *Sem `OPENAI_API_KEY` o RAG sobe em modo degradado (busca vazia). Em produção é
obrigatória; nos meus testes uso um fallback determinístico só pra validar o pipeline.*

---

## Resumo: o que é segredo (🔒) e nunca pode vazar
`INTERNAL_SECRET` · `JWT_SECRET` · `POSTGRES_PASSWORD` · `MINIO_ACCESS_KEY` ·
`MINIO_SECRET_KEY` · `RAG_API_KEY` · todas as `*_API_KEY` de LLM · `ASAAS_API_KEY` ·
`ASAAS_WEBHOOK_TOKEN` · `SMTP_USER` · `SMTP_PASS` · `EVOLUTION_API_KEY` ·
`ONBOOK_API_TOKEN` · `QDRANT_API_KEY`.

## Lembretes do blueprint (§13)
- **Revogar o PAT do GitHub** que apareceu em conversa anterior.
- Preço B2C (20% / teto R$300 / fallback R$69,90) e multiplicador do parceiro (3×)
  **não** são env — são server-side, configuráveis no admin (princípio nº 5).
