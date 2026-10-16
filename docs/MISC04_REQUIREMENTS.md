# HackMatrix 5.0 MISC-04 — requirement traceability

**Source:** the team's written summary of a screenshot of MISC-04 "Local Disaster Warning and Response Coordination".
The screenshot itself is not in this repository. **[UNVERIFIED]** whether MISC-04 supplements or replaces the original flood-nowcasting problem statement:
until the organisers confirm, it is treated as a supplement (`DECISIONS.md` D-23). Original problem-statement requirements stay in
`REQUIREMENTS.md`; nothing here is a problem-statement requirement.

Status values: COMPLETE (built + tested), PARTIAL, NOT STARTED. Phase plan: 1 road access, 2 response planner,
3 replay + evaluation + dashboard.

## A. Arrival-time road accessibility (Phase 1)

| # | Requirement (from the summary) | Status | Evidence |
|---|---|---|---|
| A1 | Link forecast frames to road segments | COMPLETE | existing per-segment depth series; unlinked roads now UNKNOWN, not dry (`access/road_status.py`) |
| A2 | Estimate arrival time along a route | COMPLETE (ASSUMED speeds) | `access/arrival_router.py`, D-24 |
| A3 | Check each segment at its expected arrival time, not one current map | COMPLETE | `test_road_open_now_but_flooded_at_arrival_is_avoided` |
| A4 | Statuses Usable / At Risk / Uncertain / Closed / Unknown | COMPLETE | D-25, `test_status_categories` |
| A5 | Confirmed closures always respected; forecast never reopens them | COMPLETE | `test_confirmed_closure_is_never_used_and_dry_forecast_does_not_reopen_it` |
| A6 | Closure changes recorded with timestamps | COMPLETE | event log (`access/events.py`), reopen is a new event |
| A7 | Received / applies-to / processed times kept apart; no future information | COMPLETE | D-26, `test_closure_received_after_the_decision_time_is_ignored` |
| A8 | Compare routes on time, flood risk at arrival, closures, uncertainty, vehicle | COMPLETE | `comparison` block of `POST /api/access/route` |
| A9 | Baseline: current-status-only routing | COMPLETE | `current_status` route |
| A10 | Never claim a route is safe | COMPLETE | `disclaimer` in every response and in the UI |
| A11 | Measure arrival-aware vs baseline (validity, closure violations, delay) | COMPLETE (SIMULATED) | scripted replay judged against an independent outcome run, `RESPONSE_EVALUATION.md` |
| A12 | Rainfall observations / road reports as live inputs | PARTIAL | forecast from any run source; road reports are operator-entered SIMULATED events |

## B. Emergency response and resource planner (Phase 2) — see `RESPONSE_PLANNER.md`

| # | Requirement (from the summary) | Status | Evidence |
|---|---|---|---|
| B1 | Settlement records (id, name, location, people, priority, need, served, remaining, status) | COMPLETE (SIMULATED numbers, REAL locations) | `response/fixtures.py`, `state.settlements` in the plan |
| B2 | Shelter records; available = total − occupancy; never over-assign | COMPLETE | `check_invariants`, `test_no_double_booking_and_no_overflow_are_checked` |
| B3 | Team and vehicle records (location, capability, type, capacity, availability, commitments, next free time) | COMPLETE | `state.teams` / `state.vehicles` (`free_at_t_min`, `position_lonlat`); capabilities enforced as a hard constraint since 2026-10-11 (D-34, `test_response_capabilities.py`) |
| B4 | Documented, configurable priority rule; not flood depth alone | COMPLETE | `PRIORITY_RULE`, D-28 |
| B5 | Assign teams, vehicles and shelter places by urgency, travel time, capacity | COMPLETE | `planner.plan`, `test_first_plan_serves_by_priority_and_is_sound` |
| B6 | No double-booking; track available / assigned / travelling / working / done | COMPLETE | busy windows per resource; states PROPOSED … COMPLETED |
| B7 | Show unmet needs with the reason and never hide a settlement | COMPLETE | `unmet` with reason categories and detail; all settlements always listed |
| B8 | Replan on road closure: record, re-check, alternative, recalculate, explain, preserve | COMPLETE | `_reroute_underway`, demo step T+10; `test_replan_keeps_completed_and_cancels_blocked_underway` |
| B9 | Replan on vehicle loss or shelter full | COMPLETE | `test_vehicle_unavailable_removes_it_and_explains`, `test_underway_mission_is_redirected_when_its_shelter_fills` |
| B10 | Preserve history: what was planned, dispatched, routed, completed, changed and why | COMPLETE | append-only `history`, `changes`, `log`, dispatch log in the UI |
| B11 | Keep simulated time apart from clock time | COMPLETE | `sim_t_min` vs `processed_at` |
| B12 | Assignment states with timestamps and reasons; underway changes follow explicit rules | COMPLETE | `ledger.TRANSITIONS`, replanning table in `RESPONSE_PLANNER.md` |
| B13 | Vehicle compatibility with the route | PARTIAL | clearance limit per vehicle type only; no width / access restrictions |
| B14 | Real population, shelter and resource data | NOT AVAILABLE | all counts SIMULATED; one MCGM planning attribute used and labelled |

## C. Replay, evaluation and dashboard (Phase 3) — see `RESPONSE_EVALUATION.md`

| # | Requirement (from the summary) | Status | Evidence |
|---|---|---|---|
| C1 | Deterministic historical / simulated event replay | COMPLETE | `evaluate.py::DEMO_SCRIPT`; seeded sequences with disjoint development / held-out seeds (D-35, `test_response_eval_seeds.py`) |
| C2 | Baseline comparison: current-status-only vs arrival-aware | COMPLETE | `POST /api/response/evaluate`, both modes on the same script |
| C3 | Route validity and invalid-route count against the replay outcome | COMPLETE | `legs_invalid_by_outcome`, `missions_invalid_by_outcome` |
| C4 | Closure violations | COMPLETE | `known_closure_violations` (0 in all runs), `closures_encountered` |
| C5 | Road-access prediction quality vs outcome | COMPLETE | per-road-window confusion: accuracy, POD, false-alarm ratio |
| C6 | Response delay, arrival time | COMPLETE | `first_service_t_min`, `response_delay_min`, `last_completion_t_min` |
| C7 | Unmet needs, shelter overflow, double-booking, replanning time, utilisation | COMPLETE | `summary` per plan; `invariant_violations_total`; `replan_ms`; `utilisation` |
| C8 | Uncertainty quality (if probabilities are implemented) | NOT APPLICABLE | no numerical probabilities; UNCERTAIN is a category, scored as "flagged" |
| C9 | Route/event replays, resource ledgers, forecast features, dispatch logs as evidence | COMPLETE | exports in the Response tab; `docs/validation/response_eval_*.json`, `response_demo_replay.json` |
| C10 | Dashboard: settlements, shelters, teams, assignments, unmet needs, evaluation | COMPLETE | Response tab + map layer |
| C11 | Do not use future information in a replay | COMPLETE | events applied only if received by the decision time; `events_ignored_future` / `events_pending` |
| C12 | No invented performance numbers | COMPLETE | every figure in the docs is produced by `backend/scripts/response_eval.py` or `access_scan.py` |
| C13 | Evaluation against observed outcomes | NOT AVAILABLE | no observed road-status data; the outcome is a second simulation, labelled so |

## D. Additions 2026-10-11 (independent review and QA; DECISIONS D-32 to D-37)

| # | Requirement | Status | Evidence |
|---|---|---|---|
| D1 | Conflicting, stale and uncertain reports handled with explicit, reproducible rules | COMPLETE (project policy, SIMULATED reports) | source tiers, lifting rule, PUBLIC never binding, staleness flag; `test_report_reconciliation.py`, browser parity `roadReports.test.mjs` + `reconciliation_parity.json` |
| D2 | Explain why a report did or did not influence a decision | COMPLETE | per-event status + one-line explanation in `events_used`; Alerts & reports page |
| D3 | Forecast refresh does not destroy the response plan; new incident is explicit | COMPLETE | `lib/incident.js`, `incident.test.mjs`; browser run: refresh keeps reports, ledger and history; reload restores them |
| D4 | Stale plan versions cannot overwrite a newer ledger | COMPLETE | ledger-carried `plan_version` → 422; client in-flight guard; `test_response_plan_concurrency.py` |
| D5 | Ongoing missions with people on board are never cancelled by the planner | COMPLETE | IN_PROGRESS no-route → attention; `test_response_plan_concurrency.py` |
| D6 | Team capabilities respected; unmet reason names the missing capability | COMPLETE | D-34, `test_response_capabilities.py` |
| D7 | Reproducible held-out evaluation and evidence exports (road-window rows, CSV) | COMPLETE | D-35, `export_rows` / `export_csv`, `response_eval_*_seeds.json` |
| D8 | Rainfall inputs: timing, units, validity, staleness; no nowcast claim from a 24-hour total | COMPLETE | D-36, `test_rainfall_time_and_staleness.py` |
| D9 | Calibrated rainfall uncertainty | NOT STARTED | rejected the 0.5x/2x multiplier band (no evidence); options in `LIVE_RAINFALL_AUDIT.md` §13 |
| D10 | Simpler operator workflow; accessible contrast and keyboard order | COMPLETE | D-37, six pages, `pages.test.mjs`, `contrast.test.mjs`; browser checks at 1440, 1366 and 1024 px |

## E. Live operations 2026-10-11 (DECISIONS D-38 to D-47) — [PROPOSED] extensions, see `LIVE_OPERATIONS.md`

| # | Capability | Status | Evidence |
|---|---|---|---|
| E1 | Rescue trip from a dispatched assignment; driver link; stages move the ledger | IMPLEMENTED AND TESTED (local) | `test_ops_trips.py`, `test_ops_api.py`, `ops.test.mjs` |
| E2 | Re-check active trips after closures, forecast changes, GPS; required / optional proposals; escalation; no loops | IMPLEMENTED AND TESTED | `test_ops_impact.py` |
| E3 | Vehicle-specific road restrictions | IMPLEMENTED AND TESTED | `test_road_restrictions.py`, `roadReports.test.mjs` |
| E4 | Verified resource registry connected to the planner | IMPLEMENTED AND TESTED; data SIMULATED | `test_ops_registry.py` |
| E5 | Subscriber advisories: consent, targeting, review, delivery lifecycle | SIMULATED provider tested; live providers UNVERIFIED; Indian SMS BLOCKED (DLT) | `test_messaging_*.py` |
| E6 | Public alert page per advisory | IMPLEMENTED AND TESTED (API) | `test_public_alerts.py` |
| E7 | Official warnings consumed, not originated | Feed reader tested on a hand-written fixture; live feed UNVERIFIED | `OFFICIAL_WARNING_PATHWAY.md` |
| E8 | Shared store on the deployed site | BLOCKED (needs a managed database, D-38) | `test_ops_api.py::test_store_unavailable_on_vercel_is_a_503` |

