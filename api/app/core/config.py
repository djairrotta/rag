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

    # LLM (admin escolhe modelo por tarefa; M4 usa o analyzer_model p/ visão+nulidades)
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    deepseek_api_key: str = ""
    glm_api_key: str = ""
    minimax_api_key: str = ""
    kimi_api_key: str = ""
    analyzer_provider: str = "openai"          # openai | anthropic (fallback se sem chave)
    analyzer_model: str = "gpt-4o-mini"        # modelo de visão p/ validar/extrair/analisar
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
    b2c_price_percent: float = 0.20
    b2c_price_cap_brl: float = 300.0
    b2c_price_fallback_brl: float = 69.90
    partner_credit_multiplier: int = 3

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
