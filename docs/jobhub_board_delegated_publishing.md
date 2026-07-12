# JobHub Board: delegated employer publishing

Use this workflow only when the operator explicitly supplies an active private
employer code in the format `N-123456` together with the vacancy source text.

## Required checks before calling the import API

1. Convert the supplied vacancy into the approved JobHub import payload.
2. Do not invent salary, location, contacts, documents, housing terms, or a
   Telegram username. Leave an unknown optional field empty.
3. Put the private employer code in `employer_board_code`.
4. Send the payload to `POST /api/internal/import-vacancy/` using the existing
   internal import token.
5. A `201` response with `published_for_employer_id` means it was published on
   the employer's profile. A `409 employer_board_authorization_inactive` means
   the employer has not approved, or has revoked, the authorization: stop and
   do not retry without a new active code.

## Publishing rules

- A delegated vacancy is published automatically only after the API accepts an
  active employer code. Do not send `moderation_status=pending` for this flow.
- Do not add any public marker explaining that JobHub prepared the vacancy.
- Keep the original source text and extraction notes in the request for the
  internal audit trail; they are not shown publicly.
- Treat the employer code as private. Never include it in vacancy text,
  screenshots, candidate messages, or public notes.
- If the employer revokes authorization, do not create new vacancies with the
  old code. Existing vacancies remain live unless JobHub separately removes
  them.

## Example

```json
{
  "employer_board_code": "N-482193",
  "title": "Warehouse worker",
  "country": "DE",
  "city": "Berlin",
  "category": "warehouse",
  "audience_countries": ["UA", "PL"],
  "employment_type": "full",
  "salary_from": 14,
  "salary_to": 16,
  "salary_currency": "EUR",
  "salary_tax_type": "netto",
  "salary_hours_month": 168,
  "description": "Clear job requirements and conditions.",
  "housing_type": "paid",
  "housing_cost": "200 EUR per month",
  "phone": "+48123456789",
  "telegram_username": "employer_hr",
  "source": "agency",
  "source_text": "Original text received by JobHub Board",
  "extraction_notes": "Only confirmed details were included."
}
```
