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

The project-first employer preview is available at
`/employer/support/project-first/` only when both server-side switches are on:

- `SUPPORT_FEATURE_ENABLED=1`;
- `SUPPORT_PROJECT_FIRST_ENABLED=1`.

The preview lists active projects and lets an owner/deputy with unrestricted
worker access:

- create a crew from an available licensed driver and vehicle;
- publish or replace several directly entered calendar days;
- release selected published days;
- add or remove a passenger for selected days or all future days;
- permanently replace a driver with a licensed passenger of that crew;
- see the exact service validation reason instead of a generic form error.

Every write uses the stage-2 transactional services. The preview never reads
or writes legacy `TransportCrew` data, and the legacy workspace remains the
default URL and fallback. Russian, English, Polish and Ukrainian interface copy
is present in the isolated template context.

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

## Stage 6: controlled cutover (in progress)

The project-first project list now links to an organization-specific read-only
reset report. It uses the same shared planning service as the management
command, so the browser report and the command cannot drift into different
deletion scopes. Opening the report never enables or applies the reset.

Before changing the default employer navigation:

1. review the organization-specific reset command in dry-run mode;
2. obtain explicit owner approval for that exact report;
3. temporarily enable `SUPPORT_PROJECT_FIRST_RESET_ALLOWED`;
4. apply the one-time reset with the exact confirmation token;
5. verify preserved workers, housing, vehicles and factual time entries;
6. switch the employer's project navigation to the project-first workspace.

The legacy workspace remains the default until all six cutover checks pass.
Check 1 is ready for staging review; checks 2–6 have not been executed.
