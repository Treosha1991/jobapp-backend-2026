from django.conf import settings


def _r2_client():
    # Lazy import keeps startup safe before dependencies are installed.
    import boto3  # type: ignore

    endpoint = (getattr(settings, "R2_ENDPOINT_URL", "") or "").strip()
    key_id = (getattr(settings, "R2_ACCESS_KEY_ID", "") or "").strip()
    secret = (getattr(settings, "R2_SECRET_ACCESS_KEY", "") or "").strip()
    region = (getattr(settings, "R2_REGION", "auto") or "auto").strip()

    if not endpoint or not key_id or not secret:
        raise RuntimeError("avatar_storage_not_configured")

    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=region,
    )


def is_avatar_storage_configured():
    bucket = (getattr(settings, "R2_BUCKET", "") or "").strip()
    endpoint = (getattr(settings, "R2_ENDPOINT_URL", "") or "").strip()
    key_id = (getattr(settings, "R2_ACCESS_KEY_ID", "") or "").strip()
    secret = (getattr(settings, "R2_SECRET_ACCESS_KEY", "") or "").strip()
    public_base = (getattr(settings, "AVATAR_PUBLIC_BASE_URL", "") or "").strip()
    return bool(bucket and endpoint and key_id and secret and public_base)


def upload_avatar_bytes(*, object_key: str, payload: bytes, content_type: str):
    bucket = (getattr(settings, "R2_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("avatar_storage_not_configured")

    client = _r2_client()
    client.put_object(
        Bucket=bucket,
        Key=(object_key or "").lstrip("/"),
        Body=payload,
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )


def delete_avatar_object(object_key: str):
    key = (object_key or "").strip().lstrip("/")
    if not key:
        return

    bucket = (getattr(settings, "R2_BUCKET", "") or "").strip()
    if not bucket:
        return

    client = _r2_client()
    client.delete_object(Bucket=bucket, Key=key)
