import re

from django import forms

from .city_catalog import CITY_CATALOG
from .currency_catalog import CURRENCY_CODES
from .country_choices import (
    MAX_AUDIENCE_COUNTRY_SELECTIONS,
    MIN_AUDIENCE_COUNTRY_SELECTIONS,
    VACANCY_COUNTRY_CHOICES,
    decode_audience_country_codes,
    encode_audience_country_codes,
)
from .driver_licenses import (
    DRIVER_LICENSE_CHOICES as DRIVER_LICENSE_CATEGORY_CHOICES,
    MAX_DRIVER_LICENSE_SELECTIONS,
    encode_driver_license_categories,
    decode_driver_license_categories,
)
from .models import UserProfile, Vacancy
from .text_filters import censor_minimal, contains_link
from .web_choice_labels import (
    CATEGORY_LABELS,
    EMPLOYMENT_LABELS,
    EXPERIENCE_LABELS,
    HOUSING_LABELS,
    SALARY_TAX_LABELS,
    SOURCE_LABELS,
    localized_choices,
    localized_country_choices,
)
from .web_i18n import FIELD_LABELS, normalize_lang

PHONE_RE = re.compile(r"^[0-9+()\-\s]+$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_FIELDS = ("phone", "additional_phone", "additional_phone_2", "additional_phone_3")
MESSENGERS = ("whatsapp", "viber", "telegram")
DEFAULT_SALARY_HOURS_MONTH = 168
HOUSING_COST_RE = re.compile(r"^\s*(\d{1,4})\s+([A-Za-z]{3})\s*/\s*([A-Za-z]+)\s*$")
HOUSING_CURRENCY_CODES = CURRENCY_CODES
HOUSING_PERIOD_CODES = ("day", "week", "month")
HOUSING_PERIOD_LABELS = {
    "ru": {"day": "день", "week": "неделя", "month": "месяц"},
    "en": {"day": "day", "week": "week", "month": "month"},
    "pl": {"day": "dzień", "week": "tydzień", "month": "miesiąc"},
    "uk": {"day": "день", "week": "тиждень", "month": "місяць"},
}

ERRORS = {'ru': {'title_required': 'Введите название вакансии.',
        'title_length': 'Название вакансии должно быть не длиннее 50 символов.',
        'city_required': 'Введите город.',
        'city_length': 'Город должен быть не длиннее 20 символов.',
        'links': 'Ссылки в этом поле не допускаются.',
        'description_required': 'Добавьте описание вакансии.',
        'description_length': 'Описание должно быть не длиннее 300 символов.',
        'description_lines': 'Описание должно быть не длиннее 50 строк.',
        'salary_required': 'Заполните зарплату: от/до, валюту, тип ставки и часы в месяц.',
        'salary_range': 'Зарплата должна быть от 1 до 99.',
        'salary_order': 'Зарплата от не может быть больше зарплаты до.',
        'hours_range': 'Часы в месяц должны быть от 1 до 300.',
        'housing_cost_required': 'Для платного жилья укажите стоимость от 1 до 9999.',
        'phone_format': 'Телефон может содержать только цифры, +, (), - и пробелы.',
        'phone_length': 'Телефон должен быть не длиннее 15 символов.',
        'hide_phone': 'Если скрыть основной телефон, добавьте дополнительный номер или email.',
        'contact_required': 'Укажите телефон или email для связи.',
        'email_length': 'Email должен быть не длиннее 30 символов.',
        'email_format': 'Введите корректный email.',
        'audience_min': 'Выберите хотя бы одну страну гражданства.',
        'audience_max': 'Можно выбрать не больше 20 стран.',
        'driver_max': 'Можно выбрать не больше 3 категорий прав.'},
 'en': {'title_required': 'Enter the vacancy title.',
        'title_length': 'The title must be 50 characters or shorter.',
        'city_required': 'Enter the city.',
        'city_length': 'The city must be 20 characters or shorter.',
        'links': 'Links are not allowed in this field.',
        'description_required': 'Add the vacancy description.',
        'description_length': 'The description must be 300 characters or shorter.',
        'description_lines': 'The description must be 50 lines or shorter.',
        'salary_required': 'Fill salary from/to, currency, rate type, and monthly hours.',
        'salary_range': 'Salary must be from 1 to 99.',
        'salary_order': 'Salary from cannot be higher than salary to.',
        'hours_range': 'Monthly hours must be from 1 to 300.',
        'housing_cost_required': 'For paid housing, enter a cost from 1 to 9999.',
        'phone_format': 'Phone can contain only digits, +, (), - and spaces.',
        'phone_length': 'Phone must be 15 characters or shorter.',
        'hide_phone': 'If the primary phone is hidden, add an additional phone or email.',
        'contact_required': 'Enter a phone or email contact.',
        'email_length': 'Email must be 30 characters or shorter.',
        'email_format': 'Enter a valid email.',
        'audience_min': 'Select at least one citizenship country.',
        'audience_max': 'You can select up to 20 countries.',
        'driver_max': 'You can select up to 3 license categories.'},
 'pl': {'title_required': 'Podaj nazwę oferty.',
        'title_length': 'Nazwa oferty może mieć maksymalnie 50 znaków.',
        'city_required': 'Podaj miasto.',
        'city_length': 'Miasto może mieć maksymalnie 20 znaków.',
        'links': 'Linki w tym polu są niedozwolone.',
        'description_required': 'Dodaj opis oferty.',
        'description_length': 'Opis może mieć maksymalnie 300 znaków.',
        'description_lines': 'Opis może mieć maksymalnie 50 wierszy.',
        'salary_required': 'Uzupełnij wynagrodzenie: od/do, walutę, typ stawki i godziny miesięcznie.',
        'salary_range': 'Wynagrodzenie musi być od 1 do 99.',
        'salary_order': 'Wynagrodzenie od nie może być większe niż do.',
        'hours_range': 'Godziny miesięcznie muszą być od 1 do 300.',
        'housing_cost_required': 'Dla płatnego zakwaterowania podaj koszt od 1 do 9999.',
        'phone_format': 'Telefon może zawierać tylko cyfry, +, (), - i spacje.',
        'phone_length': 'Telefon może mieć maksymalnie 15 znaków.',
        'hide_phone': 'Jeśli ukrywasz główny telefon, dodaj dodatkowy numer lub email.',
        'contact_required': 'Podaj telefon lub email do kontaktu.',
        'email_length': 'Email może mieć maksymalnie 30 znaków.',
        'email_format': 'Podaj poprawny email.',
        'audience_min': 'Wybierz co najmniej jeden kraj obywatelstwa.',
        'audience_max': 'Można wybrać maksymalnie 20 krajów.',
        'driver_max': 'Można wybrać maksymalnie 3 kategorie prawa jazdy.'},
 'uk': {'title_required': 'Введіть назву вакансії.',
        'title_length': 'Назва вакансії має бути не довше 50 символів.',
        'city_required': 'Введіть місто.',
        'city_length': 'Місто має бути не довше 20 символів.',
        'links': 'Посилання в цьому полі не допускаються.',
        'description_required': 'Додайте опис вакансії.',
        'description_length': 'Опис має бути не довше 300 символів.',
        'description_lines': 'Опис має бути не довше 50 рядків.',
        'salary_required': 'Заповніть зарплату: від/до, валюту, тип ставки та години на місяць.',
        'salary_range': 'Зарплата має бути від 1 до 99.',
        'salary_order': 'Зарплата від не може бути більшою за зарплату до.',
        'hours_range': 'Години на місяць мають бути від 1 до 300.',
        'housing_cost_required': 'Для платного житла вкажіть вартість від 1 до 9999.',
        'phone_format': 'Телефон може містити лише цифри, +, (), - і пробіли.',
        'phone_length': 'Телефон має бути не довше 15 символів.',
        'hide_phone': 'Якщо приховати основний телефон, додайте додатковий номер або email.',
        'contact_required': "Вкажіть телефон або email для зв'язку.",
        'email_length': 'Email має бути не довше 30 символів.',
        'email_format': 'Введіть коректний email.',
        'audience_min': 'Виберіть хоча б одну країну громадянства.',
        'audience_max': 'Можна вибрати не більше 20 країн.',
        'driver_max': 'Можна вибрати не більше 3 категорій прав.'}}

def _err(lang, key):
    return ERRORS.get(lang, ERRORS["ru"]).get(key, key)


def _strip(value):
    return (value or "").strip()


def _sort_choices_for_display(choices):
    return sorted(list(choices), key=lambda item: str(item[1]).casefold())


CITY_INVALID_ERRORS = {
    "ru": "Выберите город из списка.",
    "en": "Select a city from the list.",
    "pl": "Wybierz miasto z listy.",
    "uk": "Виберіть місто зі списку.",
}

class EmployerVacancyForm(forms.ModelForm):
    audience_countries = forms.MultipleChoiceField(
        choices=VACANCY_COUNTRY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "jh-check"}),
    )
    driver_license_categories = forms.MultipleChoiceField(
        choices=DRIVER_LICENSE_CATEGORY_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"class": "jh-check"}),
    )

    phone_whatsapp = forms.BooleanField(required=False)
    phone_viber = forms.BooleanField(required=False)
    phone_telegram = forms.BooleanField(required=False)
    additional_phone_whatsapp = forms.BooleanField(required=False)
    additional_phone_viber = forms.BooleanField(required=False)
    additional_phone_telegram = forms.BooleanField(required=False)
    additional_phone_2_whatsapp = forms.BooleanField(required=False)
    additional_phone_2_viber = forms.BooleanField(required=False)
    additional_phone_2_telegram = forms.BooleanField(required=False)
    additional_phone_3_whatsapp = forms.BooleanField(required=False)
    additional_phone_3_viber = forms.BooleanField(required=False)
    additional_phone_3_telegram = forms.BooleanField(required=False)
    housing_cost = forms.IntegerField(
        required=False,
        min_value=1,
        max_value=9999,
        widget=forms.NumberInput(attrs={"class": "jh-input", "min": "1", "max": "9999"}),
    )
    housing_cost_currency = forms.ChoiceField(
        choices=[(code, code) for code in HOUSING_CURRENCY_CODES],
        required=False,
        initial="EUR",
        widget=forms.Select(attrs={"class": "jh-input"}),
    )
    housing_cost_period = forms.ChoiceField(
        choices=[(code, code) for code in HOUSING_PERIOD_CODES],
        required=False,
        initial="month",
        widget=forms.Select(attrs={"class": "jh-input"}),
    )

    class Meta:
        model = Vacancy
        fields = [
            "title",
            "country",
            "city",
            "category",
            "audience_countries",
            "employment_type",
            "experience_required",
            "driver_license_categories",
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "description",
            "housing_type",
            "housing_cost",
            "phone",
            "additional_phone",
            "additional_phone_2",
            "additional_phone_3",
            "hide_primary_phone",
            "email",
            "source",
        ]
        widgets = {
            "title": forms.TextInput(attrs={"class": "jh-input", "maxlength": "50"}),
            "country": forms.Select(attrs={"class": "jh-input"}),
            "city": forms.Select(attrs={"class": "jh-input", "autocomplete": "off"}),
            "category": forms.Select(attrs={"class": "jh-input"}),
            "employment_type": forms.Select(attrs={"class": "jh-input"}),
            "experience_required": forms.Select(attrs={"class": "jh-input"}),
            "salary_from": forms.NumberInput(attrs={"class": "jh-input", "min": "1", "max": "99"}),
            "salary_to": forms.NumberInput(attrs={"class": "jh-input", "min": "1", "max": "99"}),
            "salary_currency": forms.Select(attrs={"class": "jh-input"}),
            "salary_tax_type": forms.Select(attrs={"class": "jh-input"}),
            "salary_hours_month": forms.NumberInput(attrs={"class": "jh-input", "min": "1", "max": "300", "placeholder": "168"}),
            "description": forms.Textarea(attrs={"class": "jh-input", "rows": "6", "maxlength": "300"}),
            "housing_type": forms.Select(attrs={"class": "jh-input"}),
            "phone": forms.TextInput(attrs={"class": "jh-input", "maxlength": "15"}),
            "additional_phone": forms.TextInput(attrs={"class": "jh-input", "maxlength": "15"}),
            "additional_phone_2": forms.TextInput(attrs={"class": "jh-input", "maxlength": "15"}),
            "additional_phone_3": forms.TextInput(attrs={"class": "jh-input", "maxlength": "15"}),
            "hide_primary_phone": forms.CheckboxInput(attrs={"class": "jh-checkbox"}),
            "email": forms.EmailInput(attrs={"class": "jh-input", "maxlength": "30"}),
            "source": forms.Select(attrs={"class": "jh-input"}),
        }

    def __init__(self, *args, **kwargs):
        self.draft_mode = bool(kwargs.pop("draft_mode", False))
        self.lang = normalize_lang(kwargs.pop("lang", "ru"))
        self.user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)

        labels = FIELD_LABELS.get(self.lang, FIELD_LABELS["ru"])
        for field_name, label in labels.items():
            if field_name == "audience_help":
                continue
            if field_name in self.fields:
                self.fields[field_name].label = label
        self.fields["audience_countries"].help_text = labels.get("audience_help", "")

        self.fields["country"].choices = _sort_choices_for_display(localized_country_choices(VACANCY_COUNTRY_CHOICES, self.lang))
        self.fields["audience_countries"].choices = _sort_choices_for_display(localized_country_choices(VACANCY_COUNTRY_CHOICES, self.lang))
        self.fields["city"].choices = self._city_choices_for_selected_country()
        self.fields["category"].choices = localized_choices(Vacancy.CATEGORY_CHOICES, CATEGORY_LABELS, self.lang)
        self.fields["employment_type"].choices = localized_choices(Vacancy.EMPLOYMENT_TYPE_CHOICES, EMPLOYMENT_LABELS, self.lang)
        self.fields["experience_required"].choices = [("", "---------")] + localized_choices(Vacancy.EXPERIENCE_CHOICES, EXPERIENCE_LABELS, self.lang)
        self.fields["housing_type"].choices = localized_choices(Vacancy.HOUSING_TYPE_CHOICES, HOUSING_LABELS, self.lang)
        period_labels = HOUSING_PERIOD_LABELS.get(self.lang, HOUSING_PERIOD_LABELS["ru"])
        self.fields["housing_cost_currency"].choices = [(code, code) for code in HOUSING_CURRENCY_CODES]
        self.fields["housing_cost_period"].choices = [(code, period_labels.get(code, code)) for code in HOUSING_PERIOD_CODES]
        self.fields["salary_tax_type"].choices = [("", "---------")] + localized_choices(Vacancy.SALARY_TAX_TYPE_CHOICES, SALARY_TAX_LABELS, self.lang)
        self.fields["source"].choices = localized_choices(Vacancy.SOURCE_CHOICES, SOURCE_LABELS, self.lang)
        self.fields["salary_hours_month"].widget.attrs.update({"min": "1", "max": "300"})

        optional_fields = [
            "experience_required",
            "salary_from",
            "salary_to",
            "salary_currency",
            "salary_tax_type",
            "salary_hours_month",
            "housing_cost",
            "housing_cost_currency",
            "housing_cost_period",
            "phone",
            "additional_phone",
            "additional_phone_2",
            "additional_phone_3",
            "email",
        ]
        for field_name in optional_fields:
            self.fields[field_name].required = False

        if self.instance and self.instance.pk:
            self.initial["audience_countries"] = decode_audience_country_codes(self.instance.audience_country_codes)
            self.initial["driver_license_categories"] = decode_driver_license_categories(self.instance.driver_license_categories)
            self._init_housing_cost_fields_from_instance()
            self._init_messenger_checks_from_instance()
        elif not self.is_bound:
            self._init_primary_phone_from_profile()
        self.initial.setdefault("source", "direct")
        self.initial.setdefault("housing_cost_currency", "EUR")
        self.initial.setdefault("housing_cost_period", "month")
        if not self.is_bound and self.initial.get("salary_hours_month") in (None, ""):
            self.initial["salary_hours_month"] = DEFAULT_SALARY_HOURS_MONTH


    def _city_choices_for_selected_country(self):
        country = None
        if self.is_bound:
            country = self.data.get(self.add_prefix("country"))
        if not country and self.instance and self.instance.pk:
            country = self.instance.country
        if not country:
            country = self.initial.get("country")
        cities = sorted(CITY_CATALOG.get(country or "", []), key=str.casefold)
        return [("", "---------")] + [(city, city) for city in cities]

    def _init_primary_phone_from_profile(self):
        if not self.user or not getattr(self.user, "is_authenticated", False):
            return
        profile = UserProfile.objects.filter(user=self.user, phone_verified=True).first()
        if profile and profile.phone_e164:
            self.initial.setdefault("phone", profile.phone_e164)

    def _init_messenger_checks_from_instance(self):
        phones = {name: _strip(getattr(self.instance, name, "")) for name in PHONE_FIELDS}
        for messenger in MESSENGERS:
            value = _strip(getattr(self.instance, messenger, ""))
            if not value:
                continue
            for phone_field, phone_value in phones.items():
                if phone_value and value == phone_value:
                    self.initial[f"{phone_field}_{messenger}"] = True
                    break

    def _init_housing_cost_fields_from_instance(self):
        raw = _strip(getattr(self.instance, "housing_cost", ""))
        if not raw:
            return
        match = HOUSING_COST_RE.match(raw)
        if not match:
            amount_match = re.search(r"\d{1,4}", raw)
            if amount_match:
                self.initial["housing_cost"] = amount_match.group(0)
            return
        amount, currency, period = match.groups()
        currency = currency.upper()
        period = period.lower()
        self.initial["housing_cost"] = amount
        if currency in HOUSING_CURRENCY_CODES:
            self.initial["housing_cost_currency"] = currency
        if period in HOUSING_PERIOD_CODES:
            self.initial["housing_cost_period"] = period

    def clean_audience_countries(self):
        value = self.cleaned_data.get("audience_countries") or []
        if self.draft_mode and not value:
            return ""
        try:
            return encode_audience_country_codes(
                value,
                min_selections=MIN_AUDIENCE_COUNTRY_SELECTIONS,
                max_selections=MAX_AUDIENCE_COUNTRY_SELECTIONS,
            )
        except ValueError as exc:
            code = str(exc)
            if code == "too_many_audience_countries":
                raise forms.ValidationError(_err(self.lang, "audience_max"))
            raise forms.ValidationError(_err(self.lang, "audience_min"))

    def clean_driver_license_categories(self):
        value = self.cleaned_data.get("driver_license_categories") or []
        try:
            return encode_driver_license_categories(value, max_selections=MAX_DRIVER_LICENSE_SELECTIONS)
        except ValueError:
            raise forms.ValidationError(_err(self.lang, "driver_max"))

    def _clean_short_text(self, cleaned, field, *, required=False, max_len=None):
        value = censor_minimal(_strip(cleaned.get(field)))
        cleaned[field] = value
        if required and not value:
            self.add_error(field, _err(self.lang, f"{field}_required"))
            return value
        if max_len and len(value) > max_len:
            self.add_error(field, _err(self.lang, f"{field}_length"))
        if value and contains_link(value):
            self.add_error(field, _err(self.lang, "links"))
        return value

    def clean(self):
        cleaned = super().clean()
        self._clean_short_text(cleaned, "title", required=not self.draft_mode, max_len=50)
        city = self._clean_short_text(cleaned, "city", required=not self.draft_mode, max_len=20)
        country = cleaned.get("country")
        if city and country and city not in CITY_CATALOG.get(country, []):
            self.add_error("city", CITY_INVALID_ERRORS.get(self.lang, CITY_INVALID_ERRORS["ru"]))

        description = censor_minimal(_strip(cleaned.get("description")))
        cleaned["description"] = description
        if not self.draft_mode and not description:
            self.add_error("description", _err(self.lang, "description_required"))
        if len(description) > 300:
            self.add_error("description", _err(self.lang, "description_length"))
        if description.count("\n") + 1 > 50:
            self.add_error("description", _err(self.lang, "description_lines"))
        if description and contains_link(description):
            self.add_error("description", _err(self.lang, "links"))

        self._validate_salary(cleaned)
        self._validate_housing(cleaned)
        self._validate_contacts(cleaned)

        cleaned["city_code"] = ""
        cleaned["salary"] = self._salary_text(cleaned)
        self._apply_messenger_values(cleaned)
        return cleaned

    def _validate_salary(self, cleaned):
        salary_from = cleaned.get("salary_from")
        salary_to = cleaned.get("salary_to")
        currency = _strip(cleaned.get("salary_currency"))
        tax_type = _strip(cleaned.get("salary_tax_type"))
        hours = cleaned.get("salary_hours_month")
        if hours is None and (salary_from or salary_to or currency or tax_type or not self.draft_mode):
            hours = DEFAULT_SALARY_HOURS_MONTH
            cleaned["salary_hours_month"] = hours

        has_salary = salary_from or salary_to or currency or tax_type or hours
        if self.draft_mode and not has_salary:
            return
        if not (salary_from or salary_to) or not currency or not tax_type or not hours:
            self.add_error("salary_from", _err(self.lang, "salary_required"))
            return
        for field, value in (("salary_from", salary_from), ("salary_to", salary_to)):
            if value is not None and (value < 1 or value > 99):
                self.add_error(field, _err(self.lang, "salary_range"))
        if salary_from and salary_to and salary_from > salary_to:
            self.add_error("salary_to", _err(self.lang, "salary_order"))
        if hours and (hours < 1 or hours > 300):
            self.add_error("salary_hours_month", _err(self.lang, "hours_range"))

    def _salary_text(self, cleaned):
        salary_from = cleaned.get("salary_from")
        salary_to = cleaned.get("salary_to")
        currency = _strip(cleaned.get("salary_currency"))
        tax_type = _strip(cleaned.get("salary_tax_type"))
        if salary_from and salary_to:
            return f"from {salary_from} to {salary_to} {currency} {tax_type}".strip()
        if salary_from:
            return f"from {salary_from} {currency} {tax_type}".strip()
        if salary_to:
            return f"to {salary_to} {currency} {tax_type}".strip()
        return ""

    def _validate_housing(self, cleaned):
        housing_type = _strip(cleaned.get("housing_type"))
        housing_cost = cleaned.get("housing_cost")
        currency = _strip(cleaned.get("housing_cost_currency")).upper()
        period = _strip(cleaned.get("housing_cost_period")).lower()
        if housing_type != "paid":
            cleaned["housing_cost"] = ""
            return

        if housing_cost in ("", None):
            housing_cost_value = None
        else:
            try:
                housing_cost_value = int(housing_cost)
            except (TypeError, ValueError):
                housing_cost_value = None

        has_housing_cost = housing_cost_value is not None or currency or period
        if self.draft_mode and not has_housing_cost:
            cleaned["housing_cost"] = ""
            return

        valid_cost = housing_cost_value is not None and 1 <= housing_cost_value <= 9999
        valid_currency = currency in HOUSING_CURRENCY_CODES
        valid_period = period in HOUSING_PERIOD_CODES
        if valid_cost and valid_currency and valid_period:
            cleaned["housing_cost"] = f"{housing_cost_value} {currency}/{period}"
            return

        cleaned["housing_cost"] = ""
        if not self.draft_mode:
            if housing_cost_value is None or housing_cost_value < 1 or housing_cost_value > 9999:
                self.add_error("housing_cost", _err(self.lang, "housing_cost_required"))
            if not valid_currency:
                self.add_error("housing_cost_currency", _err(self.lang, "housing_cost_required"))
            if not valid_period:
                self.add_error("housing_cost_period", _err(self.lang, "housing_cost_required"))

    def _validate_contacts(self, cleaned):
        for field in PHONE_FIELDS:
            value = _strip(cleaned.get(field))
            cleaned[field] = value
            if not value:
                continue
            if len(value) > 15:
                self.add_error(field, _err(self.lang, "phone_length"))
            if not PHONE_RE.match(value):
                self.add_error(field, _err(self.lang, "phone_format"))

        email = _strip(cleaned.get("email"))
        cleaned["email"] = email
        if email:
            if len(email) > 30:
                self.add_error("email", _err(self.lang, "email_length"))
            if not EMAIL_RE.match(email):
                self.add_error("email", _err(self.lang, "email_format"))

        visible_phone = "" if cleaned.get("hide_primary_phone") else cleaned.get("phone")
        additional_visible = any(cleaned.get(name) for name in PHONE_FIELDS[1:])
        if cleaned.get("hide_primary_phone") and not additional_visible and not email and not self.draft_mode:
            self.add_error("hide_primary_phone", _err(self.lang, "hide_phone"))
        if not self.draft_mode and not (visible_phone or additional_visible or email):
            self.add_error("phone", _err(self.lang, "contact_required"))

    def _apply_messenger_values(self, cleaned):
        for messenger in MESSENGERS:
            cleaned[messenger] = ""
            for phone_field in PHONE_FIELDS:
                if cleaned.get(f"{phone_field}_{messenger}") and cleaned.get(phone_field):
                    cleaned[messenger] = cleaned[phone_field]
                    break

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.audience_country_codes = self.cleaned_data.get("audience_countries", "")
        instance.driver_license_categories = self.cleaned_data.get("driver_license_categories", "")
        instance.city_code = ""
        instance.salary = self.cleaned_data.get("salary", "")
        instance.whatsapp = self.cleaned_data.get("whatsapp", "")
        instance.viber = self.cleaned_data.get("viber", "")
        instance.telegram = self.cleaned_data.get("telegram", "")
        if commit:
            instance.save()
            self.save_m2m()
        return instance
