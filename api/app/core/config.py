"""Configuração da API via variáveis de ambiente (pydantic-settings).

Nomes espelham o blueprint v3. Defaults seguros para importar sem .env.
Preço B2C e multiplicador do parceiro ficam aqui (server-side), não no .env.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_base_url: str = "https://seguramultas.com.br"
    api_base_url: str = "https://api.seguramultas.com.br"
    internal_secret: str = "change-me-internal"

    # Auth / JWT (sem Supabase) — expiries em SEGUNDOS
    jwt_secret: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_expiry: int = 900          # 15 min
    jwt_refresh_expiry: int = 2592000     # 30 dias

    # Postgres (partes; a URL é derivada)
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "seguramultas"
    postgres_user: str = "seguramultas"
    postgres_password: str = ""

    # RAG (serviço interno)
    rag_api_url: str = "http://rag:8000"
    rag_api_key: str = ""

    # RAGFlow (motor de RAG self-hosted). Vazio = push desabilitado (usa só Postgres/Qdrant).
    ragflow_base_url: str = ""              # ex.: https://emai-ragflow.kiaahh.easypanel.host
    ragflow_api_key: str = ""
    ragflow_dataset_name: str = "seguramultas_mbft"
    ragflow_embed_model: str = ""           # vazio = usa o default do tenant configurado no RAGFlow

    # Fila assíncrona (B2). queue_backend: auto|redis|memory.
    # 'auto' usa Redis se redis_url estiver setado; senão, fila em memória (dev/teste).
    redis_url: str = ""
    queue_backend: str = "auto"
    queue_name: str = "sm:jobs"

    # ---------------------------------------------------------------- LLM
    # Multi-provider com fallback. O admin escolhe o provider ativo por dropdown
    # na dashboard (grava em analyzer_provider). Cada provider tem base_url + key
    # no .env da stack. Groq é o fallback universal quando o principal falha.
    #
    # Providers diretos suportados: openai | anthropic | deepseek | kimi
    # Fallback: groq (compatível-OpenAI, cobre vários modelos)
    analyzer_provider: str = "openai"          # provider ativo (dropdown do admin)

    # OpenAI (compatível-OpenAI)
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"          # texto (nulidades)
    openai_vision_model: str = "gpt-4o-mini"   # visão (validar/extrair)

    # Anthropic (API própria — dialeto diferente)
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.anthropic.com/v1"
    anthropic_model: str = "claude-sonnet-4-6"
    anthropic_vision_model: str = "claude-sonnet-4-6"

    # DeepSeek (compatível-OpenAI)
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com/v1"
    deepseek_model: str = "deepseek-chat"
    deepseek_vision_model: str = "deepseek-chat"

    # Kimi / Moonshot (compatível-OpenAI)
    kimi_api_key: str = ""
    kimi_base_url: str = "https://api.moonshot.cn/v1"
    kimi_model: str = "moonshot-v1-32k"
    kimi_vision_model: str = "moonshot-v1-32k-vision-preview"

    # Groq — FALLBACK universal (compatível-OpenAI)
    groq_api_key: str = ""
    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "llama-3.3-70b-versatile"
    groq_vision_model: str = "llama-3.2-90b-vision-preview"

    # (legado — mantidos p/ não quebrar .env antigos; não usados no novo analyzer)
    glm_api_key: str = ""
    minimax_api_key: str = ""
    analyzer_model: str = "gpt-4o-mini"        # legado; use *_model por provider

    require_payment: bool = False              # paywall: True = recurso só após pagamento

    # Asaas (pagamentos)
    asaas_api_key: str = ""
    asaas_webhook_token: str = ""
    asaas_env: str = "sandbox"                 # sandbox | production

    # MinIO / S3 (armazenamento de arquivos)
    minio_endpoint: str = ""                   # ex.: https://s3.seguramultas.com.br
    minio_access_key: str = ""
    minio_secret_key: str = ""
    minio_region: str = "us-east-1"
    bucket_fotos: str = "fotos"
    bucket_recursos: str = "recursos"
    bucket_conhecimento: str = "conhecimento"
    bucket_timbrados: str = "timbrados"
    storage_dir: str = "/tmp/sm-storage"       # fallback local (dev/teste) quando sem MinIO

    # Regras de negócio server-side (não vêm do .env)
    b2c_price_percent: float = 0.20            # B2C: 20% do valor da multa
    b2c_price_cap_brl: float = 300.0           # teto do preço B2C
    b2c_price_fallback_brl: float = 69.90      # sem valor legível na multa
    b2c_drunk_price_brl: float = 249.90        # embriaguez (CTB 165/165-A): preço diferenciado
    partner_price_per_recurso_brl: float = 250.0  # B2B: R$250 fixo por recurso gerado
    partner_credit_multiplier: int = 3         # (legado)

    def provider_config(self, name: str) -> dict:
        """Retorna {dialect, base_url, api_key, model, vision_model} de um provider.

        dialect: 'openai' (compatível-OpenAI) ou 'anthropic' (API própria).
        Providers vazios (sem api_key) são detectáveis por api_key == ''.
        """
        p = name.lower()
        if p == "anthropic":
            return {
                "dialect": "anthropic",
                "base_url": self.anthropic_base_url,
                "api_key": self.anthropic_api_key,
                "model": self.anthropic_model,
                "vision_model": self.anthropic_vision_model,
            }
        table = {
            "openai": (self.openai_base_url, self.openai_api_key, self.openai_model, self.openai_vision_model),
            "deepseek": (self.deepseek_base_url, self.deepseek_api_key, self.deepseek_model, self.deepseek_vision_model),
            "kimi": (self.kimi_base_url, self.kimi_api_key, self.kimi_model, self.kimi_vision_model),
            "groq": (self.groq_base_url, self.groq_api_key, self.groq_model, self.groq_vision_model),
        }
        base_url, api_key, model, vision_model = table.get(p, table["openai"])
        return {
            "dialect": "openai",
            "base_url": base_url,
            "api_key": api_key,
            "model": model,
            "vision_model": vision_model,
        }

    def provider_chain(self) -> list[str]:
        """Ordem de tentativa: provider ativo primeiro, Groq como fallback final.

        Só inclui providers COM api_key configurada. Groq entra por último se tiver
        chave e não for já o ativo. Se nada tiver chave, devolve [] (analyzer usa fallback determinístico).
        """
        chain: list[str] = []
        active = (self.analyzer_provider or "openai").lower()
        if self.provider_config(active)["api_key"]:
            chain.append(active)
        if self.groq_api_key and active != "groq":
            chain.append("groq")
        return chain

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origins_list(self) -> list[str]:
        return [o for o in (self.app_base_url, "http://localhost:5173") if o]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
