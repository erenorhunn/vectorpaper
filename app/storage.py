import io

from minio import Minio

from .config import settings

_client: Minio | None = None


def client() -> Minio:
    global _client
    if _client is None:
        _client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
    return _client


def ensure_bucket() -> None:
    c = client()
    if not c.bucket_exists(settings.minio_bucket):
        c.make_bucket(settings.minio_bucket)


def put_pdf(key: str, data: bytes) -> None:
    client().put_object(
        settings.minio_bucket, key, io.BytesIO(data), len(data), content_type="application/pdf"
    )


def delete_pdf(key: str) -> None:
    client().remove_object(settings.minio_bucket, key)


def get_pdf(key: str) -> bytes:
    resp = client().get_object(settings.minio_bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()
