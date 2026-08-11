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

## Next stage

Run visual/manual testing of the isolated preview, refine the compact calendar
and project page, then prepare the explicit staging reset/cutover command. The
reset must remain a separate operator action and must preserve workers, housing
and the vehicle registry.
