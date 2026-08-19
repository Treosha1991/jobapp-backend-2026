# JobHub Support: employer web acceptance matrix

**As of:** 2026-08-18
Statuses: `PASS` is covered by automated regression tests; `MANUAL` still
requires browser/device confirmation; `BLOCKED` requires a product or external
dependency.

| Priority | Scenario | Status | Exit check |
|---|---|---|---|
| P0 | Organization isolation and direct-URL protection | PASS | A user cannot read or mutate another organization. |
| P0 | Project/crew/calendar writes are atomic | PASS | Conflicts roll the whole operation back with an explicit error. |
| P0 | Driver/vehicle/passenger date conflicts | PASS | Cross-project driver overlap is rejected; selected passenger dates are deterministic. |
| P0 | Worker calendar mirrors project crew days | PASS | Adding, replacing or releasing a crew day produces the same worker view. |
| P0 | Candidate remains outside Workers during onboarding | PASS | Onboarding, chat and document requests work without opening the worker operations page. |
| P0 | Support document upload is unavailable | PASS | Only request, verified e-mail, account code and status are stored. |
| P0 | Main production isolation | MANUAL | Staging feature flags and database are confirmed separate before every pilot deploy. |
| P1 | Projects, workers and fleet live search | PASS | Search works without exposing another organization. |
| P1 | Housing occupancy and overlapping dates | PASS | Places distinguish draft/current occupancy and explain conflicts. |
| P1 | Weekly time review and CSV | PASS | Minutes and decimal hours agree; edits retain revision history. |
| P1 | Chat unread/read reconciliation | PASS | Opening the conversation clears the server unread state. |
| P1 | Browser visual layout in supported widths | MANUAL | Header, modals, tables and calendars have no double scroll or clipped controls. |
| P1 | Push removal after manual navigation | MANUAL | Real iPhone and Android tests clear the correct delivered notification. |
| P1 | RU/EN/PL/UK visual and encoding pass | MANUAL | No `???`, replacement glyphs or missing action labels in any supported language. |
| P1 | Dynamic message translation | BLOCKED | Select and configure an approved translation provider and privacy terms. |
| P2 | Tasks/announcements employer UI | BLOCKED | Dedicated web workflow and acceptance rules are still required. |
| P2 | Employer audit viewer | BLOCKED | Decide visible event scope, retention and export rules. |
| P2 | Commercial subscription lifecycle | BLOCKED | Store billing, server receipt validation, legal copy and support process are required. |

## Gate before mobile manager development

1. Full backend `support` regression suite is green.
2. Project-first mobile API contract is reviewed and versioned.
3. Mobile manager writes do not call legacy route/template operations.
4. Error responses use stable codes and field/conflict details instead of a
   generic failure banner.
5. At least one complete staging scenario is retained as fixture/seed data:
   candidate -> onboarding -> worker -> housing -> project crew -> time entry.
