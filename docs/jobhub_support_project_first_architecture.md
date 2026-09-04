# JobHub Support: project-first architecture

## Confirmed product rules

- A project owns one or more crews.
- A crew starts with a driver and a vehicle. A driving-licence mark alone does not make a worker a driver.
- The employer enters crew shifts directly for selected calendar dates: start, end and break. Schedule templates are not part of the new flow.
- Passengers belong to a crew schedule on concrete dates, not to a driver.
- A default passenger roster may be applied to all future published crew days; selected-date changes affect only those days.
- Replacing a driver is effective-dated. The crew keeps its identity and history.
- The worker page is for individual exceptions only. Main crew and schedule management belongs to the project page.
- Workers, housing and the vehicle registry must survive the staging reset. Legacy projects, templates, shifts, routes, crews and test passenger assignments will be reset only during the controlled cutover stage.

## Stage 1: isolated data foundation

Stage 1 adds new tables alongside the legacy `TransportCrew` implementation. No existing page, API or production workflow reads from them yet.

- `ProjectCrew`: stable crew identity owned by a project.
- `ProjectCrewResourceAssignment`: effective-dated driver and vehicle history. Only one open driver, vehicle and resource assignment is allowed.
- `ProjectCrewPassenger`: default future passenger roster.
- `ProjectCrewShift`: one directly entered published/cancelled crew shift per date.
- `ProjectCrewShiftMember`: the exact driver/passenger composition for a concrete crew day, including the vehicle snapshot for the driver.

Cross-organization assignments, drivers without a confirmed licence, duplicate drivers for one shift and invalid shift/break periods are rejected by validation and database constraints.

## Stage 2: transactional service layer

The isolated service layer now supports:

- creating a project crew with its first driver and vehicle;
- publishing/replacing selected calendar days;
- releasing selected calendar days while preserving their historical snapshot;
- applying or removing passengers for future or selected days;
- permanent driver replacement with vehicle transfer/release rules;
- exact conflict reporting and append-only audit events.

All multi-record changes run inside a database transaction. A capacity,
licence, resource or driver conflict rolls the complete operation back.
Ordinary passenger conflicts are replaced for the selected dates; a driver of
another crew is never silently converted into a passenger on overlapping days.

## Stage 3: isolated employer preview

The project-first employer workspace is available only when both server-side
switches are on:

- `SUPPORT_FEATURE_ENABLED=1`;
- `SUPPORT_PROJECT_FIRST_ENABLED=1`.

The workspace lists active projects and lets an owner/deputy with unrestricted
worker access:

- create a crew from an available licensed driver and vehicle;
- publish or replace several directly entered calendar days;
- release selected published days;
- add or remove a passenger for selected days or all future days;
- permanently replace a driver with a licensed passenger of that crew;
- see the exact service validation reason instead of a generic form error.

Every write uses the stage-2 transactional services. The current staging
employer navigation uses this project-first workflow. Legacy operation models
and endpoints remain compatibility surfaces for older clients; they are not
the canonical employer web editor. Production remains isolated by feature
flags. Russian, English, Polish and Ukrainian interface copy is present in the
template context.

## Stage 4: guarded staging reset and cutover preparation

The reset is an explicit operator-only management command. It is never run by
the deployment script and defaults to a read-only dry run:

```text
python manage.py reset_support_project_first_staging \
  --organization <organization-public-uuid>
```

The preview reports organization-specific deletion and preservation counts.
Applying that exact plan requires all three protections:

1. temporary server setting `SUPPORT_PROJECT_FIRST_RESET_ALLOWED=1`;
2. command option `--apply`;
3. exact option `--confirm RESET-<organization-public-uuid>`.

An optional `--actor-email` must resolve to an active staff account and is
written to the append-only audit event. The apply transaction removes both
legacy and isolated-preview project operations: projects, worksites, project
templates, planned shifts/batches, routes, crews, driver/vehicle assignments
and worker/project assignments. It preserves and verifies unchanged counts for
workers, housing sites/rooms/places/assignments, the vehicle registry and
factual work-time entries. A preserved factual entry loses only its deleted
planned-shift link through `SET_NULL`.

No staging data has been reset by adding this command. Manual preview testing
and an operator-approved dry-run report are required before cutover. The legacy
workspace remains the default until that separate decision.

## Stage 5: staging validation

`SUPPORT_PROJECT_FIRST_ENABLED=1` is enabled only on staging. The isolated
workspace has been validated with a QA crew, four published calendar days and
a passenger without changing the legacy workspace. Russian, English, Polish
and Ukrainian were checked for replacement-character regressions. Automated
web, service and reset tests remain green.

## Stage 6: controlled staging cutover

The project-first project list now links to an organization-specific read-only
reset report. It uses the same shared planning service as the management
command, so the browser report and the command cannot drift into different
deletion scopes. Opening the report never enables or applies the reset.

Before changing any production employer navigation:

1. review the organization-specific reset command in dry-run mode;
2. obtain explicit owner approval for that exact report;
3. temporarily enable `SUPPORT_PROJECT_FIRST_RESET_ALLOWED`;
4. apply the one-time reset with the exact confirmation token;
5. verify preserved workers, housing, vehicles and factual time entries;
6. switch the employer's project navigation to the project-first workspace.

The staging employer navigation has moved to the project-first pages. This does
not authorize a production reset or production cutover. Production requires a
fresh organization-specific dry run, explicit approval and preservation
verification.

## Stage 7: driver absence and substitution lifecycle

The project calendar keeps the primary driver assignment unchanged while
tracking date-specific absences and substitute drivers separately. A
substitute can be selected only for a published future crew day on which the
primary driver is absent. Current crew passengers with a confirmed driving
licence are shown first; other licensed workers are shown only when they have
no conflicting schedule, day off or crew absence on every selected date.

Replacing a substitute closes the previous records instead of overwriting
them. Cancelling a crew shift, giving the substitute a day off, removing that
worker from the crew or permanently replacing the primary driver also closes
the relevant active substitution. Closed records remain available in the
employer's substitution history, while the calendar again marks an uncovered
driver day clearly.

## Stage 8: integration baseline

The complete web flow is covered from project and crew creation through shift
publication, passenger assignment, primary-driver absence, substitute
assignment, worker calendar synchronization and substitution closure. Service,
model and employer-web regression suites run against an isolated test database.

All project-first operation errors have explicit Russian, English, Polish and
Ukrainian interface text. Automated checks reject replacement characters and
the literal `???` in this translation contract. The existing public JobHub
product and production navigation remain isolated behind the Support and
project-first feature switches until a separate production approval is
completed. The current page inventory and remaining manual checks are
maintained in `WEB_CURRENT_STATE.md` and `WEB_ACCEPTANCE_MATRIX.md`.

## Stage 9: versioned project-first read API

The first mobile-safe project-first slice is read-only and uses the same
canonical `ProjectCrew*` tables as the employer web workspace:

```text
GET /api/v2/support/organizations/{organization_id}/project-first/projects/
GET /api/v2/support/organizations/{organization_id}/project-first/projects/{project_id}/workspace/?month=YYYY-MM
```

The monthly workspace returns the project, active crews, effective
driver/vehicle assignments, default passenger roster, every calendar day,
published shifts with exact day membership, crew absences, driver
substitutions and worker days off. External references use public UUIDs; raw
database identifiers are not part of the contract.

This whole-crew response is currently limited to the owner or an unrestricted
organization manager who has both `schedule.manage` and `transport.manage`.
Feature flags and organization boundaries return `404`, preventing public UUID
probing. Project writes are described in stage 10. Crew and calendar writes
remain deliberately absent until each transactional contract is implemented.

Known compromises and their exit criteria are tracked in
`TECHNICAL_DEBT.md`. They must not be hidden in chat notes or inferred from
TODO comments.

## Stage 10: first project-first write family

The project resource now has a versioned transactional write contract:

```text
POST   /api/v2/support/organizations/{organization_id}/project-first/projects/
PATCH  /api/v2/support/organizations/{organization_id}/project-first/projects/{project_id}/
DELETE /api/v2/support/organizations/{organization_id}/project-first/projects/{project_id}/
```

Creation accepts the same project/address/contact structure used by the web
workspace and requires a UUID `Idempotency-Key` header. Repeating the same key
with the same normalized payload returns the existing project; reusing it with
different data fails without creating a duplicate. The audit log stores only
the request UUID and a payload hash, not contact or instruction contents.

PATCH is partial, rejects unknown fields, and validates the complete merged
period before writing. It updates project and worksite atomically, never
rewrites published shifts, and refuses a capacity below the current permanent
crew roster. DELETE is an idempotent archive operation: active crews and
assignments are released through the existing canonical service while history
remains auditable.

All three methods retain the stage-9 organization, feature-flag and
unrestricted-access guard. Validation failures use stable codes with localized
RU/EN/PL/UK messages. Calendar and roster write endpoints remain open work
under `TD-001`.

## Stage 11: crew lifecycle write family

The stable crew resource now has its first versioned write contract:

```text
POST   /api/v2/support/organizations/{organization_id}/project-first/projects/{project_id}/crews/
PATCH  /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/
DELETE /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/
```

Creation requires a UUID `Idempotency-Key` and atomically creates the crew
with its initial licensed driver and available vehicle through the canonical
project-first service. A same-key retry with the same normalized payload
returns the existing crew; different data fails without duplicating records.
Tenant-scoped UUID lookups prevent disclosure of workers, vehicles or crews
from another organization.

PATCH intentionally renames the crew only. Driver/vehicle replacement is a
separate effective-dated business operation and must not be disguised as an
ordinary object patch. DELETE archives the crew, cancels its active calendar
days, mirrors those cancellations to worker calendars, and closes open
resource and passenger assignments while retaining history. Repeating DELETE
is safe. Responses return a canonical crew summary with public UUIDs.

## Stage 12: selected-date shift write family

The project-first calendar now has transactional selected-date writes:

```text
POST /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/shifts/replace/
POST /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/shifts/release/
```

`replace` accepts one to 62 unique ISO dates, local start/end times and break
minutes. It creates missing days or replaces existing days through
`publish_project_crew_shifts`, including the canonical crew membership,
capacity/conflict validation and mirrored `ScheduledWorkShift` worker
calendars. `release` cancels the selected crew days through
`release_project_crew_shifts`, retains their history, removes crew-specific
absence markers and mirrors the cancellation to every affected worker.

Both operations require a UUID `Idempotency-Key`. Retrying the same normalized
operation returns the current canonical state of those dates; reusing the key
with different dates or shift data fails with
`shift_idempotency_key_reused`. The response contains `crew`,
`affected_dates` and exact `days`, including `null` for a requested day that
has no stored shift. Strict input rejects unknown fields and empty date sets.

The existing feature flag, tenant-scoped public UUID lookup, unrestricted
worker-access rule and combined `schedule.manage`/`transport.manage`
permissions remain mandatory. Tests cover create, replace, release, worker
calendar mirroring, idempotent replay, strict validation, capacity rollback,
scoped-manager denial and cross-organization concealment. Resource replacement
and day-exception mutations remain open under `TD-001`.

## Stage 13: passenger roster write family

Passenger composition now has two versioned transactional endpoints:

```text
POST /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/passengers/apply/
POST /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/passengers/remove/
```

Both accept a tenant-scoped `connection_id` and an explicit scope. The
`all_future` scope requires `effective_on`; `selected_dates` requires one to
62 unique `work_dates`. Mixing the two date forms is rejected. Future writes
change the effective-dated default roster and every already-published crew day
from the chosen date. Selected-date writes change only those daily crew
snapshots and leave the permanent roster intact.

The endpoints reuse `assign_project_crew_passenger` and
`remove_project_crew_passenger`, including vehicle capacity, day-off,
crew-absence and cross-crew driver conflict rules plus worker-calendar
mirroring. Each command requires a UUID `Idempotency-Key`; a safe replay
returns canonical current state while reuse with another payload fails with
`passenger_idempotency_key_reused`. Responses include the crew, passenger,
scope, effective date, affected dates and exact affected-day snapshots.

Strict input, localized RU/EN/PL/UK errors, unrestricted worker access,
combined schedule/transport permissions and tenant-concealing UUID lookups
match the other project-first write families. Tests cover both scopes,
apply/remove, worker-calendar projection, retry safety, validation, atomic
rollback, scoped-manager denial and cross-organization concealment.

## Stage 14: permanent driver and vehicle-pair replacement API

Permanent replacement now has a dedicated transactional endpoint:

```text
POST /api/v2/support/organizations/{organization_id}/project-first/crews/{crew_id}/driver/replace/
```

The strict body contains only `new_driver_connection_id` and optional
`effective_on`. It intentionally rejects `vehicle_id`: the effective crew
vehicle is server-owned canonical state and moves with the crew to the new
driver. This prevents an offline or stale mobile screen from silently moving
another fleet vehicle.

The canonical `replace_project_crew_driver` service locks the organization
and crew, verifies the licensed replacement is already a crew passenger,
closes the previous effective-dated resource, creates the new driver/vehicle
pair and updates every future published daily crew snapshot and mirrored
worker calendar. The previous driver becomes a passenger. A replacement who
drove another crew releases that crew's future driver/vehicle snapshots while
leaving its passengers and shifts visible for repair. Any overlap conflict
rolls back the whole transaction.

A UUID `Idempotency-Key` is mandatory. A same-payload retry returns the stored
canonical resource without duplicating history; reusing the key with another
driver or date fails with `driver_replacement_idempotency_key_reused`. The
response includes the canonical crew, replacement resource, affected dates
and exact affected-day payloads. Tests cover success, former-driver passenger
conversion, vehicle preservation, idempotent replay, strict input, passenger
eligibility, conflict rollback, scoped-manager denial and cross-organization
concealment.

## Stage 15: mobile project and crew lifecycle UI

The Flutter staff workspace now uses the versioned project-first contract for
project and crew creation/editing instead of reproducing employer-web form
rules. The project list response includes a bounded `creation_options` block
with tenant-scoped licensed drivers, active vehicles, each driver's preferred
vehicle and a `project_vehicle_locked` marker. This is display data only: the
create endpoint remains the authority for licence, availability, capacity and
cross-project validation.

The mobile project form covers the canonical address, capacity, active period,
contact and instruction fields. Start dates default to the current local date;
end dates stay optional. Crew creation requires a name, licensed driver,
vehicle and start date. Selecting a driver preselects the server-provided
preferred vehicle; a vehicle already used by that driver on another project is
shown as locked. Crew PATCH remains rename-only.

All create commands use the API client's UUID `Idempotency-Key`; validation and
permission failures display the localized server message. Static UI copy is
available in RU/EN/PL/UK. Targeted Django contract tests, Flutter model tests
and changed-file analyzer checks cover this slice. Real iPhone/Android visual
and interaction acceptance remains required before production enablement.

## Stage 16: mobile staff visual foundation

The staff home screen now presents the selected organization, the current
worker count, attention queues and permitted management areas as separate
visual groups. It keeps the existing permission gates and navigation targets;
the redesign does not infer access from the client or introduce a second
source of business truth. The bottom navigation remains isolated to Support
staff mode so the public JobHub navigation is not restyled accidentally.

Project cards use the same visual language and expose the project address,
permanent occupancy and crew count without opening the workspace. Editing is
kept as an explicit secondary action. New static copy is complete for
RU/EN/PL/UK, changed files pass Flutter analysis, and the existing
project-first model tests remain green. Exact spacing, text wrapping and tap
comfort still require real-device acceptance before the design is treated as
final.

## Stage 17: mobile project workspace visual hierarchy

The project workspace now presents canonical project occupancy and crew count
before operational controls. Crew selection, rename and creation share one
compact control; the current driver, vehicle and occupied-seat summary form a
single resource card. The monthly calendar is contained in its own surface,
retains the existing shift and selection behavior, and distinguishes driver
absence, substitute assignment and a published shift without a driver.

Passenger management is grouped into permanent and selected-day membership
without changing the project-first write commands or permission model. All
new static labels remain available in RU/EN/PL/UK. This is a presentation-only
pass: server validation, idempotency and canonical project/crew state remain
unchanged. Real-device visual and interaction acceptance is still required.

## Stage 18: confirmed fleet swap and internal contact card

The canonical crew resource API exposes one preview-and-confirm operation for
replacing a crew vehicle with a free vehicle or atomically swapping vehicles
between two crews. The service locks the organization, resources and vehicles,
checks both crews' permanent and future published occupancy, updates future
driver/substitution vehicle snapshots, records one idempotent audit event and
notifies the affected drivers. Drivers and passengers remain in their crews.

Support messages now allow a structured internal contact card. It contains an
organization-scoped worker connection or staff membership reference, never a
phone number, e-mail address or chat history. A permitted staff member chooses
the contact; opening the card performs a fresh access check and creates or
restores the exact private staff/worker or worker/worker conversation.

## Stage 19: worker-owned project-first workspace snapshot

The mobile worker cabinet reads one tenant-safe snapshot from
`GET /api/v2/support/connections/{connection_id}/workspace/mine/?month=YYYY-MM`.
The endpoint requires active Support access and an unarchived connection owned
by the authenticated user. It never reads legacy routes, recurring templates
or `WorkerProjectAssignment` as the operational source.

The snapshot resolves the current assignment from today's published
project-first membership, then the nearest future membership, then the active
permanent driver/passenger roster. It returns the selected month's own shifts,
days off, crew absences and factual time-entry status; exact day details include
only the visible crew members' names and roles. Current/upcoming published
housing, the existing private manager conversation, compact worker-action
counts and server-calculated planned/worked totals for the month and current
week are returned alongside that calendar. No neighbour, staff note, other
profile or legacy transport data is exposed.

## Stage 20: worker ISO week and shift-scoped peer chat

The worker cabinet can load the selected ISO week independently from the
month summary:

```text
GET /api/v2/support/connections/{connection_id}/workspace/mine/week/?selected_date=YYYY-MM-DD
```

The response always contains Monday through Sunday and identifies both the
selected date and today's date. Every day is resolved from the authenticated
worker's actual published `ProjectCrewShiftMember` rows. A shift member payload
contains the public connection UUID, first/display name, avatar URL, role,
`is_self` and a server-calculated `can_open_chat`; members from another day,
crew or organization are never included. `shift` is the primary backwards-
compatible day value and `shifts` preserves every non-overlapping shift when a
worker legitimately has more than one on the same date.
Factual time remains one day-level record: creation is unlocked only after the
latest `ends_at` among all published shifts for that day, and that same
latest-ending calendar shift is stored as its anchor. The primary `shift`
continues to drive the compact day card without weakening this rule.

The corresponding peer-chat command is:

```text
POST /api/v2/support/connections/{connection_id}/project-first/shifts/{shift_id}/open-worker-chat/
{"target_connection_id": "<uuid>"}
```

It requires active Support access, an owned unarchived/non-closed connection,
one published shift in the same organization and exact membership of both
connections on that shift. Self, stale, inactive, cross-tenant and non-member
targets are rejected before a conversation is created or restored. Manager
chat opening remains idempotent and now works throughout every non-closed
connection stage while preserving the exact assigned worker/manager pair.

## Stage 21: questionnaire identity v3 and public avatar projection

`support-questionnaire-v3` is the current application contract. Its optional
serializer fields `first_name` and `last_name` become mandatory at the v3
application boundary. The server applies Unicode NFC normalization, trims and
collapses whitespace, enforces 150 characters, and rejects blank, control,
replacement and placeholder (`???`) input. The application transaction locks
the canonical user and updates the two identity columns together with the
application snapshot. Any later failure rolls both changes back. During the
migration window v2 and v3 are accepted; v2 never mutates canonical identity.

Current staff/worker read models project `first_name`, `last_name`,
`display_name` and a public `avatar_url` only after the existing tenant,
permission and worker-scope checks. This projection is used by the application
queue/onboarding list, worker directory/card, project-first crew and passenger
selectors, housing occupants/choices, fleet drivers, and chat directory,
participants, messages and shared contacts. The storage `avatar_key` is never
part of the Support API. Candidate/profile relations are selected or
prefetched with their parent rows so adding the public avatar does not create a
per-row query.

Vehicle visibility is also worker-scoped. A staff member with transport
permission may still see the vehicle, project and crew needed for fleet work,
but the current driver's UUID, name and avatar are redacted unless that worker
is present in the member's canonical `WorkerAccessScope` (or the member has
unrestricted worker access).

## Stage 22: crew-wide driver comment

`ProjectCrew` stores one current driver-authored operational comment for the
stable crew. Worker month/week snapshots expose the same text on each shift,
plus a server-authored edit capability. A worker-owned PATCH endpoint locks the
crew and accepts the write only from its current or upcoming assigned primary
driver; passengers, former drivers, substitutes and cross-tenant callers cannot
change it. The audit event records only whether the comment was cleared and its
character count, never the text itself.
