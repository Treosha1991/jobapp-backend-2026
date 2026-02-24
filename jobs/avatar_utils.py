import os
import re
import uuid
from io import BytesIO

from django.conf import settings


AVATAR_MAX_BYTES = int(os.environ.get("AVATAR_MAX_BYTES", 2 * 1024 * 1024))
AVATAR_ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
AVATAR_ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def avatar_public_url(avatar_key: str) -> str:
    key = (avatar_key or "").strip().lstrip("/")
    if not key:
        return ""

    base = (getattr(settings, "AVATAR_PUBLIC_BASE_URL", "") or "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{key}"


def sanitize_avatar_filename(filename: str) -> str:
    src = (filename or "").strip()
    ext = os.path.splitext(src)[1].lower()
    if ext not in AVATAR_ALLOWED_EXTENSIONS:
        ext = ".jpg"
    return f"{uuid.uuid4().hex}{ext}"


def is_avatar_content_type_allowed(content_type: str) -> bool:
    ctype = (content_type or "").strip().lower()
    return ctype in AVATAR_ALLOWED_CONTENT_TYPES


def is_avatar_extension_allowed(filename: str) -> bool:
    ext = os.path.splitext((filename or "").strip())[1].lower()
    return ext in AVATAR_ALLOWED_EXTENSIONS


def build_avatar_object_key(*, user_id: int, filename: str) -> str:
    safe_name = sanitize_avatar_filename(filename)
    prefix = f"users/{int(user_id)}/avatar"
    return re.sub(r"/+", "/", f"{prefix}/{safe_name}")


def process_avatar_image(uploaded_file):
    # Lazy PIL import to keep app startup resilient before deps are installed.
    from PIL import Image, ImageOps, UnidentifiedImageError  # type: ignore

    if uploaded_file is None:
        raise ValueError("avatar_required")

    if getattr(uploaded_file, "size", 0) > AVATAR_MAX_BYTES:
        raise ValueError("avatar_too_large")

    # Some mobile galleries send generic/incorrect MIME or uncommon extensions
    # (e.g. jfif, heic). Validate by actual decode instead of metadata only.

    raw = uploaded_file.read()
    if not raw:
        raise ValueError("avatar_empty")
    if len(raw) > AVATAR_MAX_BYTES:
        raise ValueError("avatar_too_large")

    try:
        with Image.open(BytesIO(raw)) as src:
            src_format = (src.format or "").upper()
            img = ImageOps.exif_transpose(src)
            try:
                resample = Image.Resampling.LANCZOS
            except Exception:
                resample = Image.LANCZOS
            img = ImageOps.fit(img, (512, 512), method=resample, centering=(0.5, 0.5))

            out = BytesIO()
            if src_format in {"PNG", "WEBP"}:
                if src_format == "PNG":
                    if img.mode not in {"RGBA", "LA"}:
                        img = img.convert("RGBA")
                    img.save(out, format="PNG", optimize=True)
                    return out.getvalue(), "image/png", ".png"

                if img.mode not in {"RGB", "RGBA"}:
                    img = img.convert("RGBA")
                img.save(out, format="WEBP", quality=88, method=6)
                return out.getvalue(), "image/webp", ".webp"

            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(out, format="JPEG", quality=88, optimize=True, progressive=True)
            return out.getvalue(), "image/jpeg", ".jpg"
    except UnidentifiedImageError as exc:
        raise ValueError("avatar_invalid_image") from exc
