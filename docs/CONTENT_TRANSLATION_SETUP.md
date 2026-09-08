# Content translation: activation checklist

## What is already in the product

Published vacancies and Support announcements are translated only when a
worker opens a particular language for the first time.  The result is saved
and reused for every later reader.  Editing the original wording makes the
next request create a fresh cached translation.

Only published, non-private content is sent to the translation provider.
Employee chats, attachments and documents are excluded.

The code is safe to deploy while disabled.  With the default configuration,
the original text remains visible and no request is sent to Google.

## One-time owner action before enabling real translations

1. In the JobHub Google Cloud account, create or choose a project and attach
   a billing account.  Cloud Translation is usage-based; no monthly
   subscription is required.
2. Enable **Cloud Translation API** for that project.
3. Create an API key restricted to that API.  Do not put the key in the mobile
   app, desktop application, repository or browser code.
4. In the Render environment for the intended deployment, add these values:

   ```text
   JOBHUB_CONTENT_TRANSLATION_PROVIDER=google_cloud
   GOOGLE_CLOUD_TRANSLATION_API_KEY=the-new-restricted-key
   JOBHUB_CONTENT_TRANSLATION_MONTHLY_CHARACTER_LIMIT=450000
   ```

5. Deploy and test one short Dutch announcement and one Dutch vacancy in a
   worker account set to Russian, English, Polish and Ukrainian.

The 450,000-character limit is an application hard stop shared by vacancies
and announcements.  It is intentionally below the current 500,000-character
monthly free allowance.  Change it only after reviewing real usage and the
current Google pricing.

## Expected behaviour

- Original language may be selected explicitly or detected automatically.
- A Dutch original is detected by Google and translated to each of the four
  JobHub languages on demand.
- The manager writes one title and one body; manual four-language entry is no
  longer required for new web announcements.
- Existing announcements with manually entered translations continue to show
  those translations first and do not spend the Google quota.
