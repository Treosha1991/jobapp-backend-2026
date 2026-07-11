# Employer Vacancy Web Form Plan

Goal: make `/employer/vacancies/new/` behave like vacancy creation in the mobile app.

## Scope

1. Validation and limits
- Copy the same character limits, required fields, allowed symbols, and business validation rules used in the mobile app/backend vacancy creation flow.
- Apply these rules to the employer web form too.

2. Localized choices
- Country, category, vacancy audience, employment type, experience, housing, and source choices must be displayed in the currently selected employer cabinet language: RU / EN / PL / UK.

3. City selection
- City field must use the same city list/search behavior as the app where possible.
- Do not require manual free-text entry if the app uses predefined city data.

4. Remove unused field
- Remove `city_code` from the employer web form UI because we do not use it.

5. Multi-select fields UX
- `audience_countries` and `driver_license_categories` must not be always expanded.
- They should be hidden behind a compact button/dropdown UI.
- User must be able to select options via checkboxes.

6. Salary fields
- Remove `salary` free-text field from the web form.
- Use structured required salary fields the same way the app does.

7. Phone fields
- Primary phone should be auto-filled from the verified phone of the logged-in account, like in the app.
- Add a small checkbox next to/near primary phone: hide primary phone.
- Keep additional phone fields, but remove separate Viber/WhatsApp/Telegram input fields.
- Under every phone number field, add three messenger checkboxes: Viber, WhatsApp, Telegram.
- These messenger flags should connect the icon/action to that exact phone number when contacts are opened.

## Important
Implement step by step and verify after each group, because this touches moderation, vacancy contact display, and paid contact-opening behavior.
