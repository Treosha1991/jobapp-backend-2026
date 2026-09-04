import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import timedelta
from io import BytesIO

from django.conf import settings
from django.db.models import Max
from django.utils import timezone


@dataclass(frozen=True)
class ProcessedChatImage:
    payload: bytes
    content_type: str
    extension: str
    width: int
    height: int
    sha256: str


def _client():
    import boto3  # type: ignore

    endpoint = (getattr(settings, "CHAT_MEDIA_R2_ENDPOINT_URL", "") or "").strip()
    key_id = (getattr(settings, "CHAT_MEDIA_R2_ACCESS_KEY_ID", "") or "").strip()
    secret = (
        getattr(settings, "CHAT_MEDIA_R2_SECRET_ACCESS_KEY", "") or ""
    ).strip()
    region = (getattr(settings, "CHAT_MEDIA_R2_REGION", "auto") or "auto").strip()
    if not endpoint or not key_id or not secret:
        raise RuntimeError("chat_media_storage_not_configured")
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name=region,
    )


def is_chat_media_storage_configured():
    return bool(
        (getattr(settings, "CHAT_MEDIA_R2_BUCKET", "") or "").strip()
        and (getattr(settings, "CHAT_MEDIA_R2_ENDPOINT_URL", "") or "").strip()
        and (getattr(settings, "CHAT_MEDIA_R2_ACCESS_KEY_ID", "") or "").strip()
        and (getattr(settings, "CHAT_MEDIA_R2_SECRET_ACCESS_KEY", "") or "").strip()
    )


def build_chat_image_object_key(*, organization_id: int, extension: str) -> str:
    safe_extension = extension if extension in {".jpg", ".png", ".webp"} else ".jpg"
    return re.sub(
        r"/+",
        "/",
        f"support-chat/{int(organization_id)}/{uuid.uuid4().hex}{safe_extension}",
    )


def process_chat_image(uploaded_file) -> ProcessedChatImage:
    from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore

    max_bytes = int(getattr(settings, "CHAT_MEDIA_MAX_BYTES", 10 * 1024 * 1024))
    if uploaded_file is None:
        raise ValueError("chat_image_required")
    if getattr(uploaded_file, "size", 0) > max_bytes:
        raise ValueError("chat_image_too_large")
    raw = uploaded_file.read()
    if not raw:
        raise ValueError("chat_image_empty")
    if len(raw) > max_bytes:
        raise ValueError("chat_image_too_large")

    try:
        with Image.open(BytesIO(raw)) as source:
            if source.width * source.height > 40_000_000:
                raise ValueError("chat_image_dimensions_too_large")
            source.load()
            image = ImageOps.exif_transpose(source)
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            width, height = image.size
            output = BytesIO()
            source_format = (source.format or "").upper()
            if source_format == "PNG":
                has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
                clean = image.convert("RGBA" if has_alpha else "RGB")
                clean.save(output, format="PNG", optimize=True)
                content_type, extension = "image/png", ".png"
            elif source_format == "WEBP":
                clean = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                clean.save(output, format="WEBP", quality=88, method=6)
                content_type, extension = "image/webp", ".webp"
            else:
                clean = image.convert("RGB")
                clean.save(
                    output,
                    format="JPEG",
                    quality=88,
                    optimize=True,
                    progressive=True,
                )
                content_type, extension = "image/jpeg", ".jpg"
            payload = output.getvalue()
            return ProcessedChatImage(
                payload=payload,
                content_type=content_type,
                extension=extension,
                width=width,
                height=height,
                sha256=hashlib.sha256(payload).hexdigest(),
            )
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise ValueError("chat_image_invalid") from exc


def schedule_chat_images_after_message_deleted(message, *, deleted_at=None):
    """Start retention only when an asset has no remaining visible message."""

    deleted_at = deleted_at or message.deleted_at or timezone.now()
    retention = timedelta(days=30)
    image_ids = list(message.image_links.values_list("image_id", flat=True))
    if not image_ids:
        return

    from support.models import SupportChatImage

    for image in SupportChatImage.objects.filter(id__in=image_ids):
        links = image.message_links.all()
        if links.filter(message__deleted_at__isnull=True).exists():
            if image.purge_after is not None:
                image.purge_after = None
                image.save(update_fields=["purge_after"])
            continue
        latest_deleted_at = links.aggregate(
            value=Max("message__deleted_at")
        )["value"] or deleted_at
        purge_after = latest_deleted_at + retention
        if image.purge_after != purge_after:
            image.purge_after = purge_after
            image.save(update_fields=["purge_after"])


def upload_chat_image(*, object_key: str, image: ProcessedChatImage):
    bucket = (getattr(settings, "CHAT_MEDIA_R2_BUCKET", "") or "").strip()
    if not bucket:
        raise RuntimeError("chat_media_storage_not_configured")
    _client().put_object(
        Bucket=bucket,
        Key=object_key.lstrip("/"),
        Body=image.payload,
        ContentType=image.content_type,
        CacheControl="private, max-age=900",
    )


def delete_chat_image(object_key: str):
    bucket = (getattr(settings, "CHAT_MEDIA_R2_BUCKET", "") or "").strip()
    key = (object_key or "").strip().lstrip("/")
    if bucket and key:
        _client().delete_object(Bucket=bucket, Key=key)


def signed_chat_image_url(object_key: str) -> str:
    bucket = (getattr(settings, "CHAT_MEDIA_R2_BUCKET", "") or "").strip()
    key = (object_key or "").strip().lstrip("/")
    if not bucket or not key or not is_chat_media_storage_configured():
        return ""
    ttl = int(getattr(settings, "CHAT_MEDIA_SIGNED_URL_TTL_SECONDS", 900))
    return _client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=max(60, min(ttl, 3600)),
    )
