# JobHub Support: technical debt register

This register contains known engineering compromises that are intentionally
kept out of the critical path. A debt item is not complete until its exit
criteria are verified. New project-first work must update this file when it
adds, reduces or removes debt.

## Priority definitions

- `P0`: blocks a safe release or can expose/corrupt tenant data.
- `P1`: must be removed before the affected feature is enabled in production.
- `P2`: contained limitation that may be scheduled after the pilot.
- `P3`: maintainability or performance improvement with no current product
  blocker.

## Open items

### TD-001 — Project-first write API is incomplete (`P1`)

- **Area:** Django API / future Flutter staff workspace.
- **Current containment:** project and crew create/update/archive,
  selected-date shift replace/release, passenger roster apply/remove and
  permanent driver/vehicle-pair replacement endpoints now reuse the canonical
  transactional services. Retryable multi-record writes require an
  idempotency key; writes have stable localized error codes and
  permission/rollback tests. Crew PATCH is deliberately rename-only;
  permanent driver replacement is a dedicated effective-dated operation.
  Driver absence and substitution mutations still run through the tested
  Django web forms and canonical services. Flutter must not reproduce these
  remaining mutations locally.
- **Exit criteria:** versioned create/update/delete endpoints for projects,
  crews, selected-date shifts, passengers, driver replacement, absence and
  substitution; stable error codes; idempotency for retryable multi-record
  writes; permission and rollback tests for every family.

### TD-002 — Legacy operation APIs coexist with project-first models (`P1`)

- **Area:** Django API and compatibility surface.
- **Current containment:** legacy routes are explicitly compatibility-only;
  the employer project workspace and new read API use `ProjectCrew*` as the
  source of truth.
- **Exit criteria:** confirm no supported client needs legacy writes, migrate
  required historical reads, deprecate with telemetry, then remove legacy
  routes and models in a separate migration plan.

### TD-003 — Crew-scoped staff read access is not available (`P2`)

- **Area:** authorization.
- **Current containment:** whole-project crew snapshots are limited to the
  owner or an organization manager with unrestricted worker access plus both
  `schedule.manage` and `transport.manage`. A scoped manager receives `404`.
- **Exit criteria:** define crew/project scope grants, filter every nested
  worker/resource/shift record by that scope, and add positive and negative
  cross-scope contract tests.

### TD-004 — Project list summary is not paginated or aggregate-optimized (`P3`)

- **Area:** database performance.
- **Current containment:** the pilot has a small number of projects and the
  query is prefetch-based, avoiding per-row lazy loading.
- **Exit criteria:** introduce pagination and database aggregates after
  measuring representative pilot data; add a query-budget test.

### TD-005 — Mobile codebase has pre-existing analyzer warnings (`P2`)

- **Area:** Flutter maintainability.
- **Current containment:** changed files and targeted tests must remain clean;
  the warnings are not treated as regressions caused by Support work.
- **Exit criteria:** classify warnings, remove obsolete/deprecated usage in
  bounded batches, and make the full analyzer warning-free in CI.

### TD-006 — Notification shade cleanup lacks real-device proof (`P1`)

- **Area:** iOS/Android push notifications.
- **Current containment:** target-aware read reconciliation exists in the
  contract and automated logic tests; no production claim is made.
- **Exit criteria:** verify on a real iPhone and Android device that opening a
  destination manually or through the push removes only matching delivered
  notifications, then record OS/app versions and evidence in the acceptance
  matrix.

### TD-007 — Dynamic private-message translation provider is undecided (`P1`)

- **Area:** privacy and localization.
- **Current containment:** original text remains available; private content is
  not sent to an unapproved external provider. Static UI copy follows the
  RU/EN/PL/UK UTF-8 rule.
- **Exit criteria:** approve provider and data-processing terms, document
  retention and regional processing, implement opt-in/error behavior, and
  test all four languages.

### TD-008 — Mobile staff project-first UI needs device acceptance (`P2`)

- **Area:** Flutter product surface.
- **Current containment:** the Flutter staff workspace reads canonical
  project/crew/calendar state and uses only implemented versioned write
  families for project/crew lifecycle, shifts, passengers and driver
  operations. Project/crew create and edit forms consume server-provided
  tenant-scoped creation options; the server remains authoritative for every
  permission, availability and conflict check. The first staff visual pass
  groups the organization, attention queues and management tools and aligns
  project cards without changing the public JobHub shell. The second pass
  gives the project workspace a compact project/crew/resource/calendar/
  passenger hierarchy and visibly separates shifts without a driver from
  absences and substitutions. Changed files are analyzer-clean and have
  targeted model/contract tests. No
  production-readiness claim is made without real-device visual and
  interaction evidence.
- **Exit criteria:** complete real iPhone and Android acceptance for list,
  create/edit, calendar and crew operations; record evidence and OS/app
  versions; cover remaining enabled write families; then enable the staff
  workspace only through the approved release switch.

## Closed items

Move an item here only with the closing change, tests/evidence and date. Do not
delete historical debt entries: they explain why compatibility code or a
migration exists.
