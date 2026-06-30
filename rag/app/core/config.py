"""Configuração do serviço RAG via variáveis de ambiente (blueprint v3)."""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    qdrant_url: str = "http://qdrant:6333"
    qdrant_api_key: str = ""
    qdrant_location: str = ""  # se setado (ex.: ":memory:" ou path), usa modo local — p/ testes
    qdrant_collection_mbft: str = "mbft"
    qdrant_collection_juris: str = "jurisprudencia"
    qdrant_collection_modelos: str = "modelos_recurso"

    embed_model: str = "text-embedding-3-large"
    embed_dim: int = 3072

    openai_api_key: str = ""
    rag_api_key: str = ""  # Bearer exigido pelo contrato (vazio = libera em dev)

    # RAGFlow (motor de RAG self-hosted). Se ragflow_base_url estiver setado, o /search
    # usa o RAGFlow; senão, cai no Qdrant (fallback). O contrato /search não muda.
    ragflow_base_url: str = ""
    ragflow_api_key: str = ""
    ragflow_dataset_name: str = "seguramultas_mbft"
    ragflow_dataset_id: str = ""            # opcional: fixa o dataset e evita lookup por nome

    # Fonte da ingestão em produção (MinIO). Opcional — ingestão também aceita docs locais.
    minio_endpoint: str = ""
    minio_access_key: str = ""
    minio_secret_key: str = ""
    bucket_conhecimento: str = "conhecimento"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
