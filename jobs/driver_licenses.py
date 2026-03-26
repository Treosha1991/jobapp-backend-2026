DRIVER_LICENSE_CHOICES = [
    ("A", "A"),
    ("A1", "A1"),
    ("AM", "AM"),
    ("B", "B"),
    ("B1", "B1"),
    ("BE", "BE"),
    ("C", "C"),
    ("C1", "C1"),
    ("CE", "CE"),
    ("C1E", "C1E"),
    ("D", "D"),
    ("D1", "D1"),
    ("DE", "DE"),
    ("D1E", "D1E"),
    ("T", "T"),
]

MAX_DRIVER_LICENSE_SELECTIONS = 3

_DRIVER_LICENSE_ORDER = {
    code: index for index, (code, _) in enumerate(DRIVER_LICENSE_CHOICES)
}
_DRIVER_LICENSE_CODES = set(_DRIVER_LICENSE_ORDER)


def _driver_license_tokens(value):
    if value in (None, ""):
        return []
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return []
        if "|" in raw:
            return [part for part in raw.split("|") if part.strip()]
        return [part for part in raw.split(",") if part.strip()]
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def normalize_driver_license_categories(value, max_selections=None):
    codes = []
    seen = set()
    for item in _driver_license_tokens(value):
        code = str(item).strip().upper()
        if not code:
            continue
        if code not in _DRIVER_LICENSE_CODES:
            raise ValueError(f"invalid_driver_license_category:{code}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)

    codes.sort(key=lambda code: _DRIVER_LICENSE_ORDER[code])
    if max_selections is not None and len(codes) > max_selections:
        raise ValueError("too_many_driver_license_categories")
    return codes


def encode_driver_license_categories(value, max_selections=None):
    codes = normalize_driver_license_categories(
        value,
        max_selections=max_selections,
    )
    if not codes:
        return ""
    return f"|{'|'.join(codes)}|"


def decode_driver_license_categories(value):
    try:
        return normalize_driver_license_categories(value)
    except ValueError:
        return []


def driver_license_categories_overlap(left, right):
    left_codes = set(decode_driver_license_categories(left))
    if not left_codes:
        return False
    right_codes = set(decode_driver_license_categories(right))
    if not right_codes:
        return False
    return bool(left_codes & right_codes)
