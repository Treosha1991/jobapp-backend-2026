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

## Next stage

Build the transactional service layer for:

- creating a project crew with its first driver and vehicle;
- publishing/replacing selected calendar days;
- applying or removing passengers for future or selected days;
- permanent driver replacement with vehicle transfer/release rules;
- exact conflict reporting and append-only audit events.
