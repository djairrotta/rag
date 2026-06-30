"""Armazenamento de arquivos: MinIO/S3 quando configurado; senão, disco local (dev/teste).

A mesma chave (`key`) é usada nos dois modos. `store_bytes` devolve uma referência
("s3://bucket/key" ou "local://key") guardada no banco. `presigned_url` só existe no MinIO.
"""
from __future__ import annotations

from pathlib import Path

from app.core.config import settings


def minio_enabled() -> bool:
    return bool(settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key)


def provider_label() -> str:
    return "minio" if minio_enabled() else "local"


def _s3_client():
    import boto3  # import tardio: só em produção
    return boto3.client(
        "s3",
        endpoint_url=settings.minio_endpoint,
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=settings.minio_region,
    )


def _local_file(key: str) -> Path:
    p = Path(settings.storage_dir) / key
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def store_bytes(key: str, data: bytes, content_type: str, bucket: str | None = None) -> str:
    bucket = bucket or settings.bucket_recursos
    if minio_enabled():
        _s3_client().put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type)
        return f"s3://{bucket}/{key}"
    p = _local_file(key)
    p.write_bytes(data)
    return f"local://{key}"


def local_path(ref: str) -> Path | None:
    if ref and ref.startswith("local://"):
        return Path(settings.storage_dir) / ref[len("local://"):]
    return None


def presigned_url(ref: str, expires: int = 7 * 24 * 3600) -> str | None:
    if ref and ref.startswith("s3://"):
        _, _, rest = ref.partition("s3://")
        bucket, _, key = rest.partition("/")
        return _s3_client().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires)
    return None
