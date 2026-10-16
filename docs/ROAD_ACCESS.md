# Arrival-time road access (MISC-04 Phase 1)

**Status (2026-10-10): implemented and tested on simulated runs.** Not validated against observed road conditions.
Decisions: `DECISIONS.md` D-23 … D-26. Requirement traceability: `MISC04_REQUIREMENTS.md`.

## What it answers

"If a vehicle leaves A at time T, is each road on the way still usable at the minute it gets there?" It compares two
routes for the same trip, vehicle, closures and speeds:

| Route | Each road is checked… |
|---|---|
| **Current status only** (baseline) | as it is at the dispatch time: the flood frame "now" and the closures in force now |
| **Arrival-aware** | over the minutes the vehicle is expected to be on it: dispatch time + travel time accumulated along the route |

The baseline route is then also checked at its real arrival times. This shows whether "open now" would still be open
when the vehicle got there.

## Road statuses (first match wins)

| Status | Rule | Routing |
|---|---|---|
| CLOSED | a confirmed closure is in force during the vehicle's window | never used |
| UNKNOWN | road overlaps no model grid cell (33 of 2,978 pilot segments), or window beyond the last forecast frame | used, cost ×1.5 |
| AT_RISK | forecast depth reaches the vehicle limit (`VEHICLE_LIMIT_CM`: car 30, ambulance 40, truck 60 cm) | never used |
| UNCERTAIN | depth within 20 cm below the limit, or road crosses DEM cells flagged unreliable (25 segments) | used, cost ×2.0 |
| USABLE | none of the above: no *known* reason it is impassable, not a safety guarantee | used |

Window depth = maximum over the 5-minute frames bracketing the window, so a rise between frames is not missed.

## Inputs and assumptions

- **Forecast:** the per-segment depth series of a FloodNet simulation run (SIMULATED, physics model, not observed).
- **Speeds (ASSUMED, D-24):** by OSM road class, 10–30 km/h; pedestrians 5 km/h. No slowdown in water, no traffic,
  no waiting. Every response returns them under `assumptions`.
- **Road events (D-26, D-32):** `closure` / `reopen`, each with `received_t_min`, `applies_from_t_min` (simulation
  minutes from T0), the server's `processed_at` and an optional `source_tier` (AUTHORITY > OPERATOR = FIELD_TEAM >
  PUBLIC; absent = OPERATOR). Only events received by the decision time are used. A reopen lifts a closure only if it
  was received no earlier than the closure, describes a later moment (ties keep the closure) and has an equal or
  higher tier. A PUBLIC closure makes the road UNCERTAIN, never CLOSED; a PUBLIC reopen is shown as contesting the
  closure. Nothing expires; reports older than their tier's threshold are flagged stale (a request to confirm, never a
  routing change). `events_used` returns every event with its derived status and a one-line explanation. These are
  project policies for a demonstration system, not statements about any agency's authority.
- **Search:** Dijkstra over (cost, arrival time). With time-dependent statuses this is a heuristic, not an optimality
  proof; the chosen route is re-checked road by road and that check is what is reported.

## API

`POST /api/access/route`. Body: `origin`, `dest` ([lon, lat]), `vehicle`, `dispatch_t_min`, optional `decision_t_min`
(≤ dispatch), the forecast as `run_id` or `series_t_min` + `streets_cm`, and `events`. Returns `arrival_aware`,
`current_status` (route, travel time, arrival time, per-road timeline with status and reason, roads closed or at risk
on arrival, or `unreachable_reason`), `comparison.summary`, `events_used`, `events_ignored_future`, `assumptions`,
`disclaimer`. 422 without a forecast, for unknown segment ids, or for a decision time after dispatch.

## Vehicle restrictions (D-42, 2026-10-11)

A confirmed closure may name `vehicle_types` (for example `["truck"]` for a weak bridge). It then binds only those
vehicles: the router passes the vehicle type to `ClosureBook.closed_during(..., vehicle=...)`, so a truck is routed
round the bridge while a car may use it (`tests/test_road_restrictions.py`). On the map (no vehicle chosen) a restriction
is shown like a closure and its explanation says which vehicles it binds. A reopen always applies to every vehicle and
lifts the restriction under the usual D-32 rule. A driver's "water too deep" report becomes a FIELD_TEAM closure for
every vehicle type whose ASSUMED wading limit is no higher than the reporting vehicle's (`ops/trips.py`).
Height, width and weight limits are not modelled: there is no data for the pilot roads.

## Dashboard

Route tab → **Arrival-time road access**. Uses FROM/TO and vehicle from the Route planner and the timeline time as
dispatch time. Re-checks automatically when the timeline moves or the event log changes. **Close a road** then click
the map to record a closure at the timeline time. **Reopen** records a reopen event; the log is never edited.

## Evidence (SIMULATED)

`backend/scripts/access_scan.py`: 150 random trips × 7 dispatch times (T+0 … T+120) on the cloudburst run, no closures.
Forecast and outcome are the **same** simulated run, so this measures where the two methods disagree, not real-world
accuracy.

| Vehicle | Baseline route invalid on arrival | …and arrival-aware found a valid route | Output |
|---|---|---|---|
| car (30 cm) | 148 / 1050 | 96 | `docs/validation/access_scan_cloudburst_car.json` |
| ambulance (40 cm) | 91 / 1050 | 79 | `docs/validation/access_scan_cloudburst_ambulance.json` |

Demo case from the scan: cloudburst, car, A = [72.855425, 19.024207] → B = [72.843045, 19.02427], dispatch T+45. The
baseline route meets N C Kelkar Marg at 35 cm at T+51.9; the arrival-aware route avoids it for +0.2 min.

Tests: `backend/tests/test_road_access.py` (15), `frontend-react/src/lib/roadAccess.test.mjs` (2).

## Limits

- The forecast "knows" the whole rainfall series of its scenario; the honest comparison against a *different*
  outcome run is Phase 3.
- The pilot is small (~4.7 km²): most trips take under 10 minutes, so differences grow with later dispatch times and
  longer missions (Phase 2).
- Speeds, thresholds and cost weights are assumptions; depth precision is bounded by the ~0.2 m DTM.
- A recommended route is a candidate with lower modelled flood exposure, never a guaranteed safe route.

## Live traffic (TomTom, 2026-10-11)

Module `backend/floodnet/traffic/tomtom.py`, routes `GET /api/traffic/status` and `POST /api/traffic/segments`
(`{"seg_ids": [...], "refresh": false}`, at most 40 known road segment ids; unknown ids are a 422). No auth: road ids
are public map data and the key never leaves the server.

- **What is fetched.** One TomTom Traffic Flow *Flow Segment Data v4* call per road segment, at the segment's midpoint
  (middle vertex; for an even vertex count the mean of the two middle vertices). Endpoint, parameters and response
  fields verified on 2026-10-11 against
  https://docs.tomtom.com/traffic-api/documentation/tomtom-maps/v1/traffic-flow/flow-segment-data (service version 4,
  last edit 2025-04-17): `currentSpeed`, `freeFlowSpeed`, `confidence` (0..1), `roadClosure`, `frc`. Per segment we
  return `current_kmh`, `free_flow_kmh`, `ratio`, `level`, `confidence`, `road_closure`, `sampled_at`,
  `source: "tomtom"`, `from_cache`.
- **Cache.** 60 s per segment, in process memory only (`TRAFFIC_CACHE_TTL_S`); `refresh: true` bypasses it. Nothing is
  persisted (the REST terms on storage are unread, see below).
- **Levels (ASSUMED thresholds, not a TomTom definition).** `ratio = currentSpeed / freeFlowSpeed`: `normal` >= 0.8,
  `moderate` 0.5..0.8, `heavy` < 0.5, `unknown` when either speed is missing or zero. A failed, timed-out, non-200 or
  invalid response skips that segment and increments `failures`; no speed is ever estimated by this module.
- **Fallback wording.** `mode: "UNAVAILABLE"` with the note *"Live traffic unavailable — TOMTOM_API_KEY not configured;
  using estimated travel time"* when the key is absent, and *"Live traffic unavailable — every TomTom request failed or
  timed out; using estimated travel time"* when nothing could be fetched. Partial failures stay `LIVE` and say
  "`n` of `m` segments unavailable". Every LIVE note says *live traffic is not a flood forecast*.
- **Key.** Set `TOMTOM_API_KEY` in the backend environment (TomTom Developer Portal, https://developer.tomtom.com,
  create an app and copy its key). Free tier: the research doc recorded "2.5k non-tile requests/day"; the pricing page
  fetched on 2026-10-11 (https://docs.tomtom.com/pricing) instead states "Free monthly requests per API, no credit card
  needed" and lists Traffic Flow API Segment Data as "Free 20K monthly". The two readings differ; treat the current
  allowance as **[UNVERIFIED]** until checked in the portal account itself. The key is never logged, returned or put in
  a URL.
- **Terms.** The TomTom REST API terms (caching, storage, safety-critical use, use with OSM-based routing) were **not
  confirmed by us**: the terms page is JavaScript-rendered and could not be read (docs/RESEARCH_NAVIGATION_TRAFFIC.md).
  TomTom `roadClosure` is at most a PUBLIC-tier signal under D-32, never a CLOSED status.
- **Rule.** FloodNet never recommends a traffic-faster route that its flood rules (road statuses above) consider
  unsafe. Live traffic may change an ETA or re-order safe candidates; it never overrides UNSAFE/CLOSED/UNCERTAIN.
  The ETA/route integration is the lead's; this module only reports speeds.
