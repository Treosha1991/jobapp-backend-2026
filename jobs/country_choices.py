VACANCY_COUNTRY_CHOICES = [
    ("PL", "Poland"),
    ("DE", "Germany"),
    ("FR", "France"),
    ("ES", "Spain"),
    ("IT", "Italy"),
    ("NL", "Netherlands"),
    ("BE", "Belgium"),
    ("AT", "Austria"),
    ("SE", "Sweden"),
    ("FI", "Finland"),
    ("DK", "Denmark"),
    ("IE", "Ireland"),
    ("PT", "Portugal"),
    ("GR", "Greece"),
    ("CZ", "Czechia"),
    ("SK", "Slovakia"),
    ("HU", "Hungary"),
    ("RO", "Romania"),
    ("BG", "Bulgaria"),
    ("HR", "Croatia"),
    ("SI", "Slovenia"),
    ("LT", "Lithuania"),
    ("LV", "Latvia"),
    ("EE", "Estonia"),
    ("LU", "Luxembourg"),
    ("MT", "Malta"),
    ("CY", "Cyprus"),
    ("UK", "United Kingdom"),
    ("CH", "Switzerland"),
    ("US", "USA"),
    ("CA", "Canada"),
    ("UA", "Ukraine"),
    ("BY", "Belarus"),
    ("OTHER", "Other"),
]

MIN_AUDIENCE_COUNTRY_SELECTIONS = 1
MAX_AUDIENCE_COUNTRY_SELECTIONS = 20

_COUNTRY_ORDER = {
    code: index for index, (code, _) in enumerate(VACANCY_COUNTRY_CHOICES)
}
_COUNTRY_CODES = set(_COUNTRY_ORDER)


def _country_tokens(value):
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


def normalize_audience_country_codes(
    value,
    *,
    min_selections=None,
    max_selections=None,
):
    codes = []
    seen = set()
    for item in _country_tokens(value):
        code = str(item).strip().upper()
        if not code:
            continue
        if code not in _COUNTRY_CODES:
            raise ValueError(f"invalid_audience_country:{code}")
        if code in seen:
            continue
        seen.add(code)
        codes.append(code)

    codes.sort(key=lambda code: _COUNTRY_ORDER[code])

    if min_selections is not None and len(codes) < min_selections:
        raise ValueError("too_few_audience_countries")
    if max_selections is not None and len(codes) > max_selections:
        raise ValueError("too_many_audience_countries")
    return codes


def encode_audience_country_codes(
    value,
    *,
    min_selections=None,
    max_selections=None,
):
    codes = normalize_audience_country_codes(
        value,
        min_selections=min_selections,
        max_selections=max_selections,
    )
    if not codes:
        return ""
    return f"|{'|'.join(codes)}|"


def decode_audience_country_codes(value):
    try:
        return normalize_audience_country_codes(value)
    except ValueError:
        return []
