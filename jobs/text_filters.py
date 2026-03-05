import re

# Link detection is intentionally conservative:
# - explicit URL schemes / www.
# - plain domains like example.com
_URL_PREFIX_RE = re.compile(r"(?i)(https?://|ftp://|www\.)")
_URL_DOMAIN_RE = re.compile(
    r"(?i)(^|[\s(])(?:[a-z0-9-]{2,}\.)+[a-z]{2,}(?:[/?:#]|$)"
)

# Digits and common "number emoji" variants.
_ASCII_DIGIT_RE = re.compile(r"[0-9]")
_KEYCAP_RE = re.compile(r"[0-9#*]\uFE0F?\u20E3")
_TEN_EMOJI_RE = re.compile(r"\U0001F51F")

# Minimal profanity roots (multilingual, intentionally short list).
_PROFANITY_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in [
        r"\bfuck(?:ing|er|ed)?\b",
        r"\bshit(?:ty|ter)?\b",
        r"\bbitch(?:es)?\b",
        r"\basshole\b",
        r"\bdick\b",
        r"\bpussy\b",
        r"\bkurwa\b",
        r"\bchuj\b",
        r"\bpizd(?:a|ec|ziec)?\b",
        r"\bсук(?:а|и|у|е|ой)?\b",
        r"\bбля(?:дь|д|ть|ха)?\b",
        r"\bхуй(?:ня|ло|ли|овый|ов)?\b",
        r"\bпизд(?:а|ец|юк|юля|ишь|ит)?\b",
        r"\bеб(?:ать|ан|ало|ись|ёш|ешь|ну)\b",
    ]
]


def contains_link(value):
    text = (value or "").strip()
    if not text:
        return False
    return bool(_URL_PREFIX_RE.search(text) or _URL_DOMAIN_RE.search(text))


def contains_digit_or_number_emoji(value):
    text = (value or "")
    if not text:
        return False
    return bool(
        _ASCII_DIGIT_RE.search(text)
        or _KEYCAP_RE.search(text)
        or _TEN_EMOJI_RE.search(text)
    )


def censor_minimal(value):
    text = value or ""
    if not text:
        return text

    def _mask(match):
        token = match.group(0)
        return "*" * max(3, len(token))

    out = text
    for pattern in _PROFANITY_PATTERNS:
        out = pattern.sub(_mask, out)
    return out


def normalize_newlines(value):
    return (value or "").replace("\r\n", "\n").replace("\r", "\n")


def line_constraints_error(value, *, max_lines, max_chars_per_line):
    text = normalize_newlines(value)
    if not text:
        return None
    lines = text.split("\n")
    if len(lines) > max_lines:
        return "too_many_lines"
    if any(len(line) > max_chars_per_line for line in lines):
        return "line_too_long"
    return None
