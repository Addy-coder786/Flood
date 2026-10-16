# Emergency response and resource planner (MISC-04 Phase 2)

**Status (2026-10-10): implemented and tested on simulated runs.** Decision support for a demonstration; not an
operational dispatch system. Decisions: `DECISIONS.md` D-27 … D-29. Traceability: `MISC04_REQUIREMENTS.md` §B.
Builds on the arrival-time road access of Phase 1 (`ROAD_ACCESS.md`).

## Data: real places, simulated numbers

`backend/floodnet/response/fixtures.py`. Every location is REAL; every count is SIMULATED and labelled so in the API,
the UI and the map, with one exception noted.

| Record | Location source | Simulated fields |
|---|---|---|
| 4 settlements S1–S4 | active MCGM Flooding Spots (layer 344): Dadar TT / Khodad Circle, Vaccharaj Lane, Shakkar Panchayat, Dadar station area | people needing help (60 / 90 / 40 / 25), priority (critical / high / medium / low). S2's 90 is MCGM's own `AFFECT_POPULATION` attribute (a planning figure, not a live headcount) |
| 2 shelters SH1–SH2 | OSM: Sharda Mangal Karyalaya (hall), IES V N Sule Guruji (school) | capacity 80 (20 occupied) and 150 (empty): 210 free places for 215 people |
| 2 teams T1–T2 | OSM: Dadar Fire Brigade, Dadar Police Station | members, capabilities |
| 2 vehicles V1–V2 | same bases | truck 25 seats, truck 20 seats (60 cm clearance, `VEHICLE_LIMIT_CM`) |

No real shelter designation is implied. On-site times are ASSUMED: 2 min team pickup, 10 min loading, 5 min unloading.

## A mission and how it is routed

`[vehicle → team pickup, if apart] → settlement (load) → shelter (unload)`. Each leg is routed by the Phase 1
arrival-aware router at the minute it starts, so the shelter leg is checked against the forecast 15–30 min after
dispatch, not against the map at dispatch. Confirmed road closures received by the decision time are enforced on
every leg. `mode: "current"` routes every leg with the current-status-only baseline instead (for Phase 3 evaluation).

## Allocation rule (transparent greedy, not an optimiser)

1. Take the settlement with the highest declared priority and unmet need (`PRIORITY_RULE`: declared priority, then
   larger remaining need; never flood depth alone).
2. For every free (team, vehicle) pair and every open shelter with room, build the mission from where the pair will
   be when it is next free. People moved = min(remaining need, vehicle seats, shelter room).
3. Keep the mission that **finishes first**; reserve the seats and the shelter places; the pair becomes free at the
   shelter when it finishes (shuttle trips follow naturally).
4. Repeat until nothing feasible is left, then record, per settlement, how many people are unserved and why:
   *no available vehicle · shelter capacity exhausted · no feasible route: confirmed closures / forecast flooding / both ·
   travel time too long: mission runs past the forecast horizon*. A settlement is never dropped from the list.

Invariants checked on every plan (`ledger.check_invariants`): no team or vehicle has overlapping missions; no shelter
is reserved beyond its free places. The API returns any violation in `invariant_violations` (expected: `[]`).

## Ledger, states and history

States: `PROPOSED → ACCEPTED → DISPATCHED → IN_PROGRESS → COMPLETED`, any non-final → `CANCELLED`. Each change appends
`{status, t_min, reason, by: operator | planner}`; history is never edited. The ledger, the road-event log and the
resource-event log live in the browser and are sent with every request (no database, D-10), like the run bundle.
Every response carries `sim_t_min` (simulation minutes from T0) and `processed_at` (clock time), kept apart.

## Replanning rules (what is preserved)

| State | On replan at time `now` |
|---|---|
| COMPLETED | history; counts as served; the pair is where the mission ended |
| DISPATCHED / IN_PROGRESS | never re-allocated. Remaining legs re-routed from the estimated position (the next node the vehicle reaches) with the closures and forecast known now. If the destination shelter has no room, the shelter leg is **redirected** to another shelter with room; if none, the mission is kept and **flagged for an operator decision**. If no route exists at all, it is **cancelled by the planner** with the reason, the pair freed at that position, and the people return to the unmet pool. A mission past its planned completion is flagged "confirm completion or cancel" |
| ACCEPTED | a commitment: kept if still feasible from max(now, planned departure), else cancelled with the reason |
| PROPOSED | recomputed; an unchanged proposal keeps its id, so `changes` lists only real differences (proposed / withdrawn / re-routed / redirected / cancelled / attention) |

Resource events (`vehicle_unavailable`, `vehicle_available`, `shelter_update`, `settlement_update`) and road events
are applied only if received by `now`; later ones are returned as pending. A reported shelter occupancy is the
operator's figure for people **not** delivered by FloodNet missions; FloodNet adds its own completed deliveries.

## API

- `GET /api/response/resources` — the fixtures with provenance tags.
- `POST /api/response/plan` — `now_t_min`, forecast (`run_id` or `series_t_min` + `streets_cm`), `road_events`,
  `response_events`, `ledger`, `plan_version`, `mode`. Returns `assignments` (the new ledger, with legs, routes and
  per-road statuses), `unmet`, `changes`, `log`, `invariant_violations`, `summary` (served / committed / proposed /
  unmet, utilisation, replan time) and `state` (settlements, shelters, teams, vehicles as known now). 422 on unknown
  ids, illegal status histories, or a missing forecast.

## Dashboard

**Response plan** page: build / replan at the decision time (explicit action, never automatic), summary tiles, settlements
with unmet reasons, shelters (*Report full*), vehicles (*Report unavailable / available*), assignments with their next
operator action (Accept → Dispatch → Arrived → Complete, or Cancel), attention flags, what changed, and the dispatch
log. The map shows settlements (colour = priority), shelters, bases, vehicle positions and every live mission's legs
(dashed = proposed).

**Incident state (D-33, 2026-10-11).** Road reports, resource reports, the ledger, plan logs and plan history belong
to the incident and survive a new forecast run: the plan result is dropped and marked "forecast changed", a replan
proposal states how many underway / accepted / proposed missions it will re-check, and nothing changes until the
operator replans. Only **New incident** (Overview page, with confirmation) clears them. The incident is saved to this
browser's localStorage so a reload does not lose it. Limits: one browser on one origin, a few MB, cleared with the
site data, not shared between operators, not a system of record (the JSON exports are). For live / ECMWF runs the
simulation minutes are not re-based to the new pull time. The API refuses a ledger stamped with a newer
`plan_version` than the request claims (422), and the browser discards a plan that arrives after a newer forecast.

**Capabilities (D-34).** A settlement's `required_capabilities` must all be held by the assigned team; otherwise the
unmet reason names the missing capability. An IN_PROGRESS mission whose remaining route becomes infeasible is kept
and flagged for an operator decision (people are on board); the planner cancels only missions with nobody on board.

## Demo replay (SIMULATED, cloudburst run, `docs/validation/response_demo_replay.json`)

| Step | Result |
|---|---|
| T+0 first plan | 10 missions over 112 min cover 210 of 215 people; 5 at S4 unmet: shelter capacity exhausted. 359 ms |
| A1, A2 dispatched; T+10 closure on Tilak Road (A2 would enter it at T+15.6) | both underway missions re-routed from their estimated positions, +2 min each; all proposals keep their ids |
| T+30: A1, A2 completed; V2 breaks down; SH1 reported full | 45 served (history kept); 6 proposals withdrawn, S1's last 15 people redirected to SH2; one vehicle now serves everything; 20 unmet: 15 at S3 "mission runs past the forecast horizon", 5 at S4 "shelter capacity exhausted". 119 ms |

At T+30 the current-status baseline produced the same missions: in this run the flood exposure of the mission roads
does not differ by method at those times. The method comparison on an independent outcome run is Phase 3.

Tests: `backend/tests/test_response_planner.py` (12), `frontend-react/src/lib/response.test.mjs` (2).

## Live operations (D-38 to D-43, 2026-10-11)

- **Resource registry** (`floodnet/ops/registry.py`): teams, vehicles and shelters with AVAILABLE / ASSIGNED /
  OUT_OF_SERVICE (MECHANICAL, REST, PERSONNEL), updated only by the dispatcher or a registered responder within its
  scope; every update keeps observed and received time, out-of-order updates are stored but not applied, staleness is a
  flag (60 min, ASSUMED). `POST /api/response/plan` with `resources_source: "registry"` plans with this state; a resource
  that is not AVAILABLE reaches the planner as unavailable and the unmet reason names it. The demo fixtures stay the
  default. Status words follow the NIMS idea; this is not NIMS compliance.
- **Rescue trips** (`floodnet/ops/trips.py`, `impact.py`): a trip is a dispatched assignment. Its stage moves the ledger
  forward (`lib/ops.js applyTripsToLedger`, every intermediate status recorded). After a road report, a forecast change
  or a GPS fix, the remaining route is re-checked from the latest credible position; a blocked route gives a REQUIRED
  proposal or, with no feasible route, an escalation to a person. Design and rules: `docs/LIVE_OPERATIONS.md`.

## Limits

- Greedy by strict priority: a critical settlement is served completely before a high one even when a short detour
  would serve both sooner; no optimisation across settlements, no partial-priority trade-offs.
- Two teams, two vehicles, one need type (evacuation to a shelter); no medical, rescue-only or multi-vehicle missions.
- Travel speeds and on-site times are assumptions (D-24); no traffic, no waiting for water to recede.
- The planner proposes; every status change is an operator action in the UI. Nothing here issues orders.
