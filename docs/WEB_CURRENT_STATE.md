# JobHub Support: current employer web state

**As of:** 2026-08-18
**Environment:** staging-only, protected by `SUPPORT_FEATURE_ENABLED` and
`SUPPORT_PROJECT_FIRST_ENABLED`. Production JobHub is not switched by these
notes or by ordinary staging deploys.

## Canonical operating model

The employer web cabinet now follows one source of truth:

`project -> crew -> calendar day -> driver + vehicle + passengers`

- The project page is the main editor for crews, dates and staffing.
- A crew has a stable identity. Driver, vehicle and passengers are resolved for
  concrete dates and remain in history when changed.
- The worker page is a read/exception workspace: it shows the worker's real
  project, crew and calendar and allows only individual actions such as release
  from selected days or a day off.
- A driving-licence flag makes a person eligible to drive; the person becomes a
  driver only when a vehicle is assigned for the relevant operation.
- Housing and the vehicle registry are independent registries and survive
  project cleanup.

Legacy work-assignment drafts, route drafts and recurring schedule-template
endpoints still exist only for compatibility with older clients. They are not
the canonical employer web workflow and must not be used as the foundation for
the new mobile manager interface.

## Employer pages

| Area | Current state | Important boundary |
|---|---|---|
| Home | Implemented | Summary and navigation, not the worker table. |
| Applications / onboarding | Implemented | Candidate remains outside the worker list until the employer advances the stage. |
| Document requests | Implemented | JobHub stores request/status/code only; documents go to the verified employer e-mail. |
| Workers | Implemented | Search, sorting, current project/crew, housing, work and driver/vehicle state. |
| Projects and crews | Implemented core | Main place for crew creation, calendar shifts, passengers, driver absence and substitution. |
| Housing | Implemented core | Houses, rooms, automatic places, occupancy dates and worker links. |
| Fleet | Implemented core | Vehicles, effective driver history and project/route summary. |
| Work time | Implemented pilot slice | Weekly review, correction, confirmation and CSV export for manual accounting reconciliation. |
| Worker requests | Implemented | Includes urgent absence flow and manager review. |
| Conversations | Implemented pilot slice | Worker/staff tabs, unread state, internal forwarding and read reconciliation. No Support attachments. |
| Team and access | Implemented core | Organization membership, permissions and worker scopes remain server-enforced. |
| Tasks and announcements | API/mobile foundation only | No complete dedicated employer web workspace yet. |
| Organization audit | Data/service foundation only | No complete employer-facing audit viewer yet. |
| Subscription management | Partial | Access state exists; commercial App Store/Google Play lifecycle is not pilot-ready. |
| Dynamic translation | Partial | RU/EN/PL/UK interface copy exists; private dynamic translation requires an approved provider. |

## Safety invariants

- Every organization query is tenant-scoped on the server.
- Object permissions are checked on writes; hiding a button is never treated as
  authorization.
- Multi-record crew/calendar changes are transactional: all changes succeed or
  all are rolled back.
- Conflicting driver shifts are rejected across projects. Passenger changes may
  replace the passenger's selected dates but never silently convert another
  crew's driver.
- Support chats accept text only. Passport, visa, bank and other document files
  are not a JobHub Support upload workflow.
- New or changed interface copy must be UTF-8 and supplied in RU/EN/PL/UK. A
  literal `???` or replacement character is a release blocker.

## Readiness decision

The web cabinet is suitable for continued staging QA and a controlled demo.
It is not yet a declaration of commercial, legal or production readiness.
Before the mobile manager interface is built, freeze a versioned project-first
API, keep legacy endpoints read-only/compatible, and complete the manual
acceptance checks in `WEB_ACCEPTANCE_MATRIX.md`.
