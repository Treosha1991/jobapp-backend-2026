# Employer Portal Notes

## Important follow-up

When the employer cabinet is connected more tightly with the public website, auto-detect the selected website language and pass it into the cabinet.

Current implementation:
- the employer cabinet has its own language switcher available before login;
- the selected language is stored in the `jobhub_employer_lang` cookie;
- supported languages: `ru`, `en`, `pl`, `uk`.

Future connection plan:
- public site should append/pass `?lang=<code>` when opening `/employer/`;
- employer cabinet should keep writing the selected language into its cookie;
- optionally align this with the public site `jobhub_lang` localStorage value through a landing bridge page.
