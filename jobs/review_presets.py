from datetime import timedelta


# Temporary QA-friendly delay for review button availability.
# Restore to 24 hours before release.
REVIEW_BUTTON_DELAY = timedelta(minutes=2)
REVIEW_EDIT_WINDOW = timedelta(days=7)
MAX_REVIEW_PRESET_SELECTIONS = 3

REVIEW_PRESET_CHOICES = [
    ("fast_reply", "Fast reply"),
    ("delayed_reply", "Delayed reply"),
    ("no_reply", "No reply"),
    ("polite_communication", "Polite communication"),
    ("unpleasant_communication", "Unpleasant communication"),
    ("description_matched", "Description matched"),
    ("conditions_changed", "Conditions changed"),
    ("salary_matched", "Salary matched"),
    ("salary_mismatch", "Salary mismatch"),
    ("housing_matched", "Housing matched"),
    ("housing_mismatch", "Housing mismatch"),
    ("vacancy_actual", "Vacancy was actual"),
    ("vacancy_not_actual", "Vacancy was no longer actual"),
    ("not_recommended", "Not recommended"),
]

REVIEW_PRESET_ORDER = [code for code, _ in REVIEW_PRESET_CHOICES]
REVIEW_PRESET_LABELS = {code: label for code, label in REVIEW_PRESET_CHOICES}
REVIEW_PRESET_SET = set(REVIEW_PRESET_ORDER)


def normalize_review_preset_codes(codes, *, max_selections=MAX_REVIEW_PRESET_SELECTIONS):
    if codes in (None, "", []):
        return []
    if not isinstance(codes, list):
        raise ValueError("invalid_review_presets")

    seen = set()
    normalized = []
    for raw in codes:
        code = str(raw or "").strip().lower()
        if not code:
            continue
        if code not in REVIEW_PRESET_SET:
            raise ValueError("invalid_review_presets")
        if code in seen:
            continue
        seen.add(code)
        normalized.append(code)

    if not normalized:
        return []
    if len(normalized) > max_selections:
        raise ValueError("too_many_review_presets")

    normalized.sort(key=REVIEW_PRESET_ORDER.index)
    return normalized
