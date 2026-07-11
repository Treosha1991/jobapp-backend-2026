import re


# Telegram public usernames are Latin-only handles.  Keep the canonical value
# without "@" so every client can build the same https://t.me/<username> link.
TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")
MAX_TELEGRAM_USERNAMES = 3


def normalize_telegram_username(value):
    """Return a canonical Telegram username or raise ValueError."""
    username = (value or "").strip()
    if username.startswith("@"):
        username = username[1:]
    if not username:
        return ""
    if "@" in username or not TELEGRAM_USERNAME_RE.fullmatch(username):
        raise ValueError("invalid_telegram_username")
    return username


def is_telegram_username(value):
    try:
        return bool(normalize_telegram_username(value))
    except ValueError:
        return False


def normalize_telegram_usernames(values, *, max_items=MAX_TELEGRAM_USERNAMES):
    """Return unique Telegram handles without @, preserving input order."""
    if values in (None, ""):
        return []
    if not isinstance(values, (list, tuple)):
        raise ValueError("invalid_telegram_usernames")

    normalized = []
    seen = set()
    for value in values:
        username = normalize_telegram_username(value)
        if not username:
            continue
        key = username.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(username)
    if len(normalized) > max_items:
        raise ValueError("too_many_telegram_usernames")
    return normalized


def vacancy_telegram_usernames(vacancy):
    """Read the canonical list and retain a valid legacy primary handle."""
    raw_values = getattr(vacancy, "telegram_usernames", None) or []
    try:
        values = normalize_telegram_usernames(raw_values)
    except ValueError:
        values = []

    legacy_primary = getattr(vacancy, "telegram_username", "")
    if is_telegram_username(legacy_primary) and legacy_primary not in values:
        values.insert(0, normalize_telegram_username(legacy_primary))
    return values[:MAX_TELEGRAM_USERNAMES]
