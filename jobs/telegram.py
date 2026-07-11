import re


# Telegram public usernames are Latin-only handles.  Keep the canonical value
# without "@" so every client can build the same https://t.me/<username> link.
TELEGRAM_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{5,32}$")


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
