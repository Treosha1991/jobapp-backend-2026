"""Currencies supported by JobHub vacancy salary and housing fields.

The list follows the countries currently available in ``country_choices``.
Currency codes are intentionally used as UI labels: ISO 4217 codes are stable
and understandable across all JobHub languages.
"""

CURRENCY_CODES = (
    "EUR",
    "PLN",
    "USD",
    "CAD",
    "CHF",
    "GBP",
    "UAH",
    "BYN",
    "CZK",
    "HUF",
    "RON",
    "BGN",
    "SEK",
    "DKK",
    "NOK",
    "ISK",
    "RSD",
    "BAM",
    "MKD",
    "ALL",
    "MDL",
    "TRY",
    "GEL",
    "AMD",
    "AZN",
    "ILS",
    "AED",
    "SAR",
    "QAR",
    "KZT",
    "KGS",
    "UZS",
    "KRW",
    "AUD",
    "NZD",
)

CURRENCY_CHOICES = tuple((code, code) for code in CURRENCY_CODES)
