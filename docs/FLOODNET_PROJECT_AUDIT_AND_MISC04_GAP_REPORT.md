# FloodNet — Project Audit and HackMatrix 5.0 MISC-04 Gap Report

**Audit date:** 2026-10-11 (IST) / 2026-10-10 UTC · **Audited by:** code inspection, test runs and live API checks on the
repository as it stands (folder `FloodNet_SIH26085_with_env`). **No code was changed during this audit.**

**Evidence labels used throughout**

| Label | Meaning |
|---|---|
| ✅ **VERIFIED** | Observed in this audit: a test run, a live request, or a command whose output is quoted |
| ☑ **CODE** | Implemented in the code and covered by existing tests, but not re-demonstrated end to end in this audit |
| ◐ **PARTIAL** | Some of the requirement is implemented; the gap is named |
| ✗ **MISSING** | Not implemented |
| ⛔ **BLOCKED** | Needs data, access or a decision we do not have |
| **SIMULATED / ASSUMED / ESTIMATED** | The data-provenance tags the project itself uses (`backend/floodnet/provenance.py`) |

---

## 1. Executive summary

FloodNet is a working prototype for **one district (the Hindmata / Dadar–Parel–Matunga pilot in Mumbai, ~4.7 km²)
and one hazard (urban pluvial flooding)**. It has two halves:

1. **A physics-based flood nowcasting engine** (not AI/ML): rainfall → runoff → 2D surface flow on an MCGM-contour
   terrain ⇄ the real MCGM storm-water network → water depth on each of 2,978 OSM road segments every 5 minutes for
   up to 3 hours.
2. **A MISC-04 decision-support layer** built on it: road status *at the expected arrival time* (Phase 1), a
   resource-constrained response planner with history-preserving replanning (Phase 2), and a scripted replay that
   compares arrival-aware routing with a current-status-only baseline against an independent outcome run (Phase 3).

**IMD status changed during this audit: the IMD key now works.** ✅ A live `current_wx` request returned HTTP 200 at
2026-10-10 19:19:38 UTC, a FloodNet run driven by it completed through the dashboard path with source label
`IMD LIVE` and `fallback_active: false`, and the opt-in live smoke tests passed (5/5). The integration is narrow: one
station's **24-hour cumulative rainfall** is converted to a constant 3-hour intensity (a labelled persistence
assumption). It is not a nowcast.

**MISC-04 coverage:** the planning and arrival-accessibility requirements are largely implemented and tested.
The clear gaps are: **(1) no handling of conflicting or stale reports** (explicitly required by MISC-04 §C);
**(2) a new forecast run wipes the response ledger**, so "replan after a changed hazard report" cannot be shown while
preserving commitments; **(3) uncertainty is categorical only** (no probabilities, so no calibration metric);
**(4) evaluation uses one scripted sequence**, not a set of held-out sequences; **(5) team capabilities are not matched
to settlement needs**.

**What may not be claimed:** no real-world flood-prediction accuracy has been measured (`docs/VALIDATION.md`:
"FloodNet's accuracy has not been measured, because no dataset exists to measure it against"); routes are "lower
modelled flood exposure", never "safe"; alerts are drafts or TEST messages, never issued warnings; all headcounts,
capacities, team sizes, speeds and on-site times are SIMULATED or ASSUMED.

---

## 2. Architecture and end-to-end data flow

```
 External inputs                       Backend (Python 3.14 local, FastAPI)                      Frontend (React 19 + Vite)
 ───────────────                       ─────────────────────────────────────                     ──────────────────────────
 IMD API (current_wx)  ─┐
 ECMWF via Open-Meteo  ─┼─► rainfall/provider.py ─► rainfall/source_manager.py (ordered failover, never silent)
 IMD SRI radar image   ─┤        │
 Design storms, 2005   ─┘        ▼
                          api/state.run_scenario ─► simulation/engine.run_simulation ◄── terrain/ (runoff, surface)
 MCGM GIS snapshot ──► data/build_pilot ──► data/processed/pilot/*   ◄── drainage/hydraulics (graph solver)
 OSM (Overpass)    ──┘                                                ◄── streets/aggregate (grid → road depth)
                                 │ SimulationResult (frames every 5 min)
                                 ▼
                          api/bundle.py ─► POST /api/simulate response (self-contained "run bundle")  ─►  lib/runBundle.js
                                                                                                           state/FloodNetContext.jsx
                          access/  (road status at arrival, closures, arrival-aware router)  ◄─ POST /api/access/route
                          response/ (planner, ledger, replay, evaluation)                    ◄─ POST /api/response/plan|evaluate
                          alerts/  (CAP 1.2 drafts, WhatsApp/Telegram TEST alerts)            ◄─ /api/simulation/{id}/alert, /api/alerts/phone/*
                                                                                                           ▼
                                                                                 MapView (Leaflet), Terrain3D (three.js),
                                                                                 tabs: Overview · Alerts · Why flooded ·
                                                                                 Route · Response · Sources
```

**Key design facts (verified in code)**

- **One coupling point:** `backend/floodnet/simulation/engine.py` (107 lines) is the only place water is exchanged
  between components; components implement interfaces in `backend/floodnet/contracts.py`.
- **No database** (decision D-10). State lives in files (pilot data), process memory (runs), and **in the browser**: the
  run bundle, the road-event log, the response ledger and the resource-event log are all held client-side and sent
  with each request (`api/bundle.py`, `ResponsePlanRequest`). This makes the serverless deployment work, and means a
  page reload loses the ledger.
- **The frontend never calls IMD or any external weather API**; it only calls the FastAPI backend (verified:
  `grep imd.gov.in frontend-react/src` returns nothing). Secrets stay in the backend `.env`.
- **37 HTTP endpoints** in `backend/floodnet/api/main.py` (1,323 lines), listed in §5.

---

## 3. IMD API status and integration details

| Item | Finding | Evidence |
|---|---|---|
| Endpoint used by the product | `GET https://api.imd.gov.in/api/v1/current_wx?id=43003` (Mumbai-Santacruz; `IMD_STATION_ID`, default 43003) | `rainfall/provider.py::IMDObservationProvider._fetch_current_wx`; matches IMD's public `api_reference.html` |
| Authentication | Header `X-API-Key: <IMD_API_KEY>` **and** `Authorization: Bearer <JWT>`. The JWT is generated and renewed automatically from `IMD_EMAIL` / `IMD_PASSWORD` via `POST https://api.imd.gov.in/api/oauth/token.php` | `imd_token_issuer.py`, `imd_auth.py`. IMD's public reference does **not** document auth; the scheme is established from IMD's own error replies ("Authorization header missing or invalid", "Invalid API key") |
| Live verification | ✅ HTTP 200, `application/json`, list with `Station`, `Date of Observation`, `Time`, `Last 24 hrs Rainfall`, … at **2026-10-10 19:19:38 UTC**, egress IP `103.115.201.202` | independent diagnostic from the backend's Python runtime (no secret printed) |
| Through the product | ✅ `POST /api/simulate {scenario_id: "live"}` via the dashboard proxy → HTTP 200 in 5.9 s; `scenario_name: "Live (IMD-observed, Mumbai-Santacruz)"`, `active_source.source_label: "IMD LIVE"`, `fallback_active: false`; `/api/data-status`: `imd_auth VALID`, `imd_live.ok true` | live request during audit |
| Opt-in live tests | ✅ `RUN_LIVE_IMD=1 pytest tests/test_imd_live_smoke.py -m live` → **5 passed** | covers `current_wx`, a full live-driven FloodNet run, and `stationnowcast`, `districtnowcast`, `districtrainfall` returning Mumbai rows |
| What the data is | **One station's observed 24-hour cumulative rainfall.** FloodNet divides it by 24 and holds that intensity constant for 3 hours, uniformly over the pilot. Tagged `ESTIMATED`, and the note says: *"Forecast intensity is a PERSISTENCE assumption … this is NOT a radar or NWP nowcast"* | `provider.py::_normalize` (lines ~370–425); run provenance |
| Value at audit time | 0.0 mm in the last 24 h at 19 UTC → a dry live run | run provenance |
| Timestamps | IMD observation time (`Date of Observation` + `Time`, UTC) is carried into the source metadata; readings are cached for **10 min** (`CACHE_TTL_S = 600`), so a run can use a reading a few minutes old. The run reports its age (`age_seconds: 465.7` in the audit run) | `active_source` block of the run |
| Other IMD endpoints | `stationnowcast`, `districtnowcast`, `districtrainfall` respond live (tests) but are **not used by product code** | ◐ reachable, not integrated |
| Fix made before this audit | the backend now re-reads `IMD_API_KEY` from `.env` on every request (it used to require a restart); 403 wording names both possible causes | `imd_auth.py::api_key`, `tests/test_imd_key_reload.py` (10 tests) |
| Fragility | The key is bound to one public IP. Any change of network (hotspot, college Wi-Fi, Vercel static IP) breaks live IMD until a key exists for that IP | observed twice during the project (`117.198.136.1`, `103.115.201.202`) |

**Other sources (for contrast)**

| Source | What it is | Status | Label |
|---|---|---|---|
| ECMWF via Open-Meteo | hourly NWP **forecast** for the pilot point, resampled to 5 min | ✅ live run succeeded earlier this session; also the automatic fallback | "FORECAST", never called a nowcast or an IMD product |
| IMD Mumbai-Veravali SRI image | **experimental, image-derived** rain estimate (legend-band decode of a public GIF) | ◐ last attempt failed honestly: *"legend band layout differs from the verified reference legend; refusing to guess"* | `RADAR_IMAGE_DERIVED_ESTIMATE`, never official radar QPE |
| Radar-image nowcast | advection of decoded frames, ≤ 30 min | off by default (`FLOODNET_RADAR_NOWCAST=1`), logic-tested only | experimental |
| GPM IMERG | gridded satellite rain | code path present, optional `h5py` extra, needs `EARTHDATA_TOKEN` | not enabled |
| Design storms, 26 July 2005 replay | scenario hyetographs | ✅ | SYNTHETIC / REAL (transcribed) |
| Failover | `source_manager.py`: LIVE → RADAR-DERIVED → FORECAST → CACHED → DEMO, always reporting the active source | ✅ verified earlier: "auto" ran on ECMWF with `fallback_active: true` while IMD was refused | never silently relabelled |

---

## 4. Technology stack (as found)

| Layer | Component | Role | Notes / risks |
|---|---|---|---|
| Backend language | **Python 3.14.7** (local venv) | everything server-side | ⚠ the Vercel manifest (`pyproject.toml`) pins `>=3.12,<3.13`; the code has only been tested here on 3.14 |
| Web framework | FastAPI 0.143, Starlette 1.7, Uvicorn 0.54, Pydantic 2.14 | REST API, validation | `api/main.py` is 1,323 lines (all routes in one file) |
| Numerics / geo | numpy 2.5.3, scipy 1.18.1, shapely 2.2, pyproj 3.8, networkx 3.7, Pillow 12.3 | solver, terrain interpolation, CRS, road graph & routing, radar-image decode | no GDAL/rasterio (D-11) |
| HTTP client | httpx 0.28.1 | IMD, Open-Meteo, Twilio, Telegram | |
| Routing / optimisation | own code on networkx: flood-weighted Dijkstra (`routing/router.py`), time-dependent label-setting search (`access/arrival_router.py`), greedy priority allocator (`response/planner.py`) | | no OSRM, no MILP solver |
| Storage | JSON/NPZ files + process memory + browser-held bundles | | no database; ledger lost on page reload |
| Frontend | React 19.2, Vite 8.2, Leaflet 1.9.4, three.js 0.170, oxlint 1.79, Node 24.21 | dashboard, map, 3D terrain | main JS chunk 603 kB (bundle-size warning) |
| Tests | pytest 9.1 (backend), `node --test` (frontend `lib/` units) | | no browser/E2E UI tests |
| Deployment | `vercel.json` + root `pyproject.toml` + `app.py` (one Vercel Python function, region bom1), `Dockerfile`, `.vercelignore` | | not re-verified in this audit; two dependency manifests (root and `backend/`) can drift |
| Notifications | Twilio WhatsApp (blocked on a Trial account: error 21654), Telegram (code done, not configured) | | |
| Duplication | `frontend/` (legacy vanilla-JS dashboard) is still served as a fallback when `frontend-react/dist` is missing (`config.FRONTEND_DIR`) | | dead weight / confusion risk |
| Docs | 20+ documents in `docs/`, plus `context.md` (43 KB, partly stale) | | `docs/STATUS.md` is explicitly superseded |

---

## 5. Implemented features with evidence

| Feature | Status | Evidence (files · endpoints · tests) |
|---|---|---|
| Pilot data build (MCGM drains, contour DTM, OSM roads/buildings, flooding spots) | ☑ CODE | `data/build_pilot.py`, `data/processed/pilot/*`, `tests/test_data_pilot.py` |
| Coupled flood simulation, 5-min frames, mass balance | ✅ | `simulation/engine.py`; live runs this session; mass-balance error ~1e-12 % (`VALIDATION.md`); `tests/test_e2e.py`, `test_scientific_regression.py` |
| Per-road depth time series | ✅ | `streets/aggregate.py`; run bundle `streets` |
| Flood-aware routing (direct vs lower-risk, alternatives) | ☑ CODE | `/api/route`, `/api/route/alternatives`; `tests/test_routing_router.py` |
| Road status at arrival (5 classes), closures, no-future-information | ✅ | `access/*`, `/api/access/route`; `tests/test_road_access.py` (15) |
| Response planner (allocation, invariants, unmet reasons, replanning) | ✅ | `response/planner.py`, `ledger.py`, `/api/response/plan`; `tests/test_response_planner.py` |
| Scripted replay + matched baseline evaluation | ✅ | `response/evaluate.py`, `/api/response/evaluate`; `tests/test_response_eval.py`; `docs/validation/response_eval_*.json` |
| CAP 1.2 alert **drafts** | ☑ CODE | `alerts/cap.py`, `/api/simulation/{id}/alert`; `tests/test_alerts_cap.py` |
| Phone TEST alerts (WhatsApp/Telegram) | ◐ | code + tests (`test_alerts_whatsapp.py`, `test_alerts_telegram.py`); no real message delivered yet (Twilio Trial blocks; Telegram unconfigured) |
| 3D terrain, guided briefing (en/hi/mr), TTS | ☑ CODE | `Terrain3D`, `GuidedBriefing`, `/api/tts`; `test_tts_endpoint.py` |
| Exports (ledger, dispatch log CSV, plan versions, events, evaluation) | ☑ CODE | `lib/exports.js` (unit-tested); buttons in Response tab |
| Offline validation tooling (flooding-spot overlap, extreme depth, SWMM adapter) | ☑ CODE | `validation/*`; `docs/validation/*.md` |

**Not a feature despite its name:** `GET /api/nowcast` returns a summary of the latest simulation run; it is not an
IMD or radar nowcast.

---

## 6. MISC-04 compliance matrix

Classification: **1** implemented & verified · **2** implemented, not verified · **3** partial · **4** not implemented ·
**5** blocked.

### A. Planning resource-constrained response

| Requirement | Class | Evidence / gap |
|---|---|---|
| Assign teams, transport and shelter capacity to settlements | **1** | `planner.plan`; `test_first_plan_serves_by_priority_and_is_sound`; demo replay `docs/validation/response_demo_replay.json` |
| Respect declared priorities | **1** | `fixtures.PRIORITY_RULE`, priority-ordered loop; test above |
| Respect travel times | **1** | every leg routed with ASSUMED speeds (`config.ASSUMED_SPEED_KMH`) at its start minute |
| Respect resource requirements | **3** | vehicle seats and clearance (`VEHICLE_LIMIT_CM`) are enforced; **team `capabilities` and settlement `need` exist in the data but are never matched** (`planner.py` has no capability check) |
| Respect confirmed road status | **1** | closures enforced on every leg; `test_confirmed_closure_is_never_used…` |
| Track assignments over time | **1** | `ledger.py` states + append-only history; dispatch log |
| Prevent conflicting team use | **1** | busy windows; `check_invariants`; `test_later_commitment_is_pushed…`; 0 violations in all replays |
| Prevent shelter overflow | **1** | reservations + redirect/flag rule; `test_underway_mission_is_redirected_when_its_shelter_fills` |
| Display unmet needs and why | **1** | `unmet` with reason categories; Response tab |
| Replan after road closures | **1** | `_reroute_underway`; `test_replan_keeps_completed_and_cancels_blocked_underway` |
| Replan after changed hazard reports | **3** | backend `settlement_update` event works; **no UI for it**; **a new forecast run clears the ledger and closure log** (`FloodNetContext.jsx` lines ~296–297, ~380–381), so a changed *flood forecast* cannot be replanned against while keeping commitments |
| Replan after resource loss | **1** | `vehicle_unavailable`, `shelter_update`; tests |
| Preserve completed / committed actions | **1** | replanning rules table in `docs/RESPONSE_PLANNER.md`; tests |
| Label historical replay timestamps | **3** | sim time (`sim_t_min`) and clock time (`processed_at`) are kept apart; **sim time is not mapped to the historical date/time** of the 26 July 2005 replay |

### B. Predicting road accessibility at arrival

| Requirement | Class | Evidence / gap |
|---|---|---|
| Predict usability at expected arrival time | **1** | `access/road_status.classify` over each vehicle window; `test_road_open_now_but_flooded_at_arrival_is_avoided` |
| Use time-stamped conditions, terrain and reports available at decision time | **3** | reports: only those received by the decision time (`ClosureBook`, tested); terrain: DEM reliability mask; **the flood forecast is one run issued at T0 and is not re-issued at later decision times**, and a forecast built from a historical replay knows the whole storm (perfect foresight) — acceptable for design storms, not for a historical held-out test |
| Represent predictive uncertainty | **3** | categorical `UNCERTAIN` (within 20 cm of the limit, or unreliable DEM) and `UNKNOWN`; **no probabilities** |
| Compare feasible routes and plans using that uncertainty | **3** | routes: UNCERTAIN ×2.0 / UNKNOWN ×1.5 cost (ASSUMED weights) and both routes shown side by side; **plans are not compared under uncertainty** (no ensemble, no risk-weighted plan choice) |
| Confirmed closures binding until valid updated evidence | **1** | only a `reopen` event lifts a closure; a dry forecast never does (tested) |
| Compare arrival-aware vs current-status on identical held-out event sequences | **3** | identical scripted sequence run with both methods against an **independent** outcome run (✅); but **one** sequence, hand-written, not a held-out set |
| Report accessibility prediction quality | **1** | per road-window confusion (accuracy, POD, FAR) on 2,978 roads × leads 5/15/30 min (`evaluate.prediction_quality`) |
| Report invalid routes | **1** | `legs_invalid_by_outcome`, `missions_invalid_by_outcome`, `known_closure_violations` |
| Report response delays | **1** | `first_service_t_min`, `response_delay_min`, `last_completion_t_min` |

Results already produced (SIMULATED, from `docs/validation/response_eval_*.json`): forecast `heavy` → outcome
`cloudburst`: blocked road-windows detected **92.4 %** arrival-aware vs **70.6 %** current-status (similar false-alarm
ratio); missions tie (0 invalid legs each). Forecast `heavy` → outcome `july2005`: **both** methods dispatch 5 missions
judged invalid by the outcome; detection 10.5 % vs 7.9 %.

### C. Scope and demonstration

| Requirement | Class | Evidence / gap |
|---|---|---|
| One district, one hazard | **1** | Hindmata/Dadar pilot, urban flooding |
| Bounded road graph with settlements and shelters | **1** | 2,978 OSM segments; 4 settlements on MCGM flooding spots, 2 shelters on OSM buildings (REAL locations, SIMULATED numbers) |
| Public geography; documented simulated disruptions | **1** | `fixtures.py` provenance; `DEMO_SCRIPT` in `evaluate.py` |
| Demonstrate deteriorating access | **1** | forecast-driven AT_RISK on arrival (e.g. N C Kelkar Marg at 35 cm, T+51.9, `ROAD_ACCESS.md`) |
| Demonstrate confirmed closure | **1** | Route tab "Close a road"; scripted closure at T+10 |
| Insufficient shelter or transport capacity | **1** | 215 people vs 210 shelter places; V2 loss at T+30 |
| **Conflicting or stale reports** | **4** | **not implemented.** The event model has only `closure`/`reopen`; the latest by "applies-from" time wins silently; no report source credibility, validity window, staleness flag or conflict surfacing (`grep -i "conflict|stale|expire" access/ response/` finds nothing) |
| Show unserved settlements and replanning | **1** | Response tab, change list, dispatch log |

### D. Required evidence

| Evidence | Class | Where |
|---|---|---|
| Road and event replays | **1** | `/api/response/evaluate` steps; `docs/validation/response_demo_replay.json`, `response_eval_*.json`; event-log export |
| Resource ledgers | **1** | Ledger (JSON) export; `assignments` in every plan |
| Forecast features and labels | **3** | computed inside `prediction_quality` but **not exported as a per-road-window table** |
| Dispatch logs | **1** | Dispatch log (CSV) export |
| Route checks | **1** | per-leg outcome checks, `blocked_examples` |
| Matched baseline response outcomes | **1** | both modes on the same script, same outcome run (`comparison` block) |

---

## 7. Tests run and actual results (this audit)

| Check | Command | Result |
|---|---|---|
| Backend, full suite | `cd backend && .venv/Scripts/python.exe -m pytest tests -q -rs` | ✅ **452 passed, 10 skipped** in 219 s. Skips: 8 opt-in live tests (IMD ×6, ECMWF ×2), `h5py` not installed (IMERG), `pyswmm` not installed (SWMM run mode) |
| IMD live smoke (opt-in, real API) | `RUN_LIVE_IMD=1 .venv/Scripts/python.exe -m pytest tests/test_imd_live_smoke.py -m live` | ✅ **5 passed** |
| IMD + rainfall unit tests (after the key fix, 2026-10-10) | `pytest tests/test_imd_*.py tests/test_rainfall_provider.py tests/test_run_data_mode.py` | ✅ 127 passed, 6 skipped (opt-in live) |
| Frontend unit tests | `cd frontend-react && node --test "src/**/*.test.mjs"` | ✅ **51 passed, 0 failed** |
| Frontend lint | `npx oxlint src` | **0 errors, 7 warnings** (all in files predating MISC-04 work: `FloodNetContext.jsx` ×2, `kinetic-grid.jsx`, `WhyFloodedPanel.jsx`, `RoutePlanner.jsx`, `NoticeBanner.jsx`, `TourOverlay.jsx`) |
| Frontend build | `npm run build` | ✅ built; ⚠ chunks > 500 kB (`index` 603 kB, `Terrain3D` 719 kB) |
| API health | `curl http://127.0.0.1:8000/health` | ✅ `{"ok":true}`; dashboard proxy `/api/scenarios` 200 |
| Live IMD through the product | `POST /api/simulate {"scenario_id":"live","horizon_min":60}` | ✅ HTTP 200, `IMD LIVE`, no fallback |
| Deployment | — | **not run** in this audit (no Vercel deploy attempted) |
| UI in a browser | — | **not run**: no browser automation available in this audit; §8 is a code-based review |

---

## 8. Dashboard UX audit (code-based; not visually inspected in this audit)

**What a first-time user understands easily**
- The landing page → dashboard split, the flood map with a timeline, the severity colours, and the Overview tab.
- The header always names the active rainfall source (`lib/sources.js`), which is good for honesty.

**What is confusing or overloaded**
1. **The Route tab holds two planners stacked**: `RoutePlanner` (direct vs lower-risk) and `ArrivalAccess`
   (arrival-aware vs current-status). The second silently depends on the first's FROM/TO and vehicle.
2. **"Dispatch time" is implicit**: it is whatever the timeline slider shows. Nothing says "decision time = T+45".
3. **Road closures are entered in the Route tab but used by the Response tab**: the link is invisible.
4. **The Response tab is long**: plan controls, tiles, settlements, shelters, vehicles, assignments, change list,
   exports and the evaluation all on one scroll.
5. **A new run silently clears the ledger and the closure log** (by design today; see gap A).
6. **"Run evaluation" quietly runs a second ~1-minute simulation**; only a loading line explains it.
7. **The guided tour skips the new tabs**: `lib/tour/tabSteps.js` `TAB_KEYS` = overview, alerts, why, routing,
   provenance. No Response step, and no briefing narration for the MISC-04 features.
8. **Phone alerts panel** shows WhatsApp "ready" although every send fails on the Trial account.

**Functional vs decorative**
- The landing-page hero (`ui/gateway-flow.jsx`, `ui/kinetic-grid.jsx`) is decorative animation only.
- No "coming soon" placeholders or dead buttons were found in `src/` (scan for TODO/placeholder/lorem).

**Prioritised UI plan (proposal only; nothing changed)**

| # | Change | Why |
|---|---|---|
| U1 | A visible **"Decision time T+X"** chip in the header, shared by Route and Response | removes the biggest implicit coupling |
| U2 | Merge the two Route planners into one card: one FROM/TO, two result columns | one mental model |
| U3 | A **Reports** strip (closures, reopenings, hazard updates, conflicts) shared by Route and Response | makes events first-class |
| U4 | Split Response into sub-tabs: *Plan* · *Resources* · *Log & exports* · *Evaluation* | reduces the scroll |
| U5 | Add Response and Evaluation steps to the guided tour and briefing | judges follow the story |
| U6 | Show WhatsApp as "blocked by Trial account" instead of "ready" | honest status |

---

## 9. Missing features and technical risks

**Missing (MISC-04)**
- Conflicting and stale report handling (C) — ✗.
- Replanning across a changed flood forecast while preserving the ledger (A) — ◐.
- Capability matching between teams and needs (A) — ◐.
- Probabilistic uncertainty and plan comparison under it (B) — ◐.
- A set of held-out event sequences, not one script (B) — ◐.
- Exported forecast features and labels (D) — ◐.
- Historical-date labelling for replays (A) — ◐.

**Technical risks**

| Risk | Impact | Mitigation |
|---|---|---|
| IMD key bound to one IP | live IMD breaks on any network change (seen twice) | demo on the registered network, or Vercel static IP + a key for it; keep ECMWF fallback |
| Ledger only in the browser | page reload loses the plan and history | export before demo; optional localStorage or server persistence later |
| Large modules (`api/main.py` 1,323 lines, `rainfall/provider.py` 1,293, `FloodNetContext.jsx` 773) | harder changes, merge conflicts | split only when touched |
| Python 3.14 locally vs 3.12 pinned for Vercel | untested deploy runtime | run the suite once on 3.12 before deploying |
| Evaluation runtime on serverless | two replays plus an outcome simulation may approach function limits | send outcome series from the client (already supported); measure on Vercel |
| No API authentication | on a public deployment anyone can trigger runs or TEST alerts | alerts are rate-limited and go only to `.env` recipients; add an admin token before a public demo |
| Secrets pasted in chat (Twilio) and plaintext `.env` | credential exposure | rotate Twilio auth token after the event; never commit `.env` |
| Legacy `frontend/` fallback | confusion about which UI is served | remove or document |

**Scientific limits (unchanged, must be stated)**
- Accuracy is unmeasured.
- The flooding-spot check is uninformative for the heavy scenario: *"the model floods most of the pilot at the
  scoring depth"* (`docs/validation/FLOODING_SPOTS.md`).
- The SWMM cross-check had convergence problems.
- Depth precision is bounded by the ~0.2 m contour-derived terrain model.

---

## 10. Prioritised implementation roadmap

### P0 — Critical (MISC-04 requirement missing)

**P0-1 · Conflicting and stale reports**
- **Why:** MISC-04 §C requires demonstrating it, and nothing exists today.
- **Reuse:** `access/events.py` (`RoadEvent`, `ClosureBook`, the three-timestamp model).
- **Change:**
  - Add report kinds: `field_report_flooded`, `field_report_passable`, `closure`, `reopen`.
  - Add fields: `source` with an authority rank (authority > field > forecast), and `valid_until_t_min`.
  - Rule: a confirmed authority closure stays binding; a field report older than its validity is **STALE** (shown, not used); disagreeing reports inside the same window are a **CONFLICT**, surfaced with the rule that resolved it.
  - Files: `access/events.py`, `access/road_status.py` (status reason), `response/planner.py` (change list), UI Reports strip.
- **Acceptance (tests):**
  1. a stale "passable" report never reopens a closure;
  2. a newer authority reopen beats an older field "flooded" report and the conflict is listed;
  3. a report received after the decision time is ignored (already tested).

**P0-2 · Replan across a changed flood forecast without losing the ledger**
- **Why:** MISC-04 "replan after changed hazard reports"; today a new run wipes everything.
- **Change:**
  - Add a "Use as updated forecast" action that swaps the forecast series and keeps the ledger, the event logs and the sim clock.
  - Add a UI for `settlement_update` (people, priority).
  - Files: `FloodNetContext.jsx` (stop clearing on forecast update; keep clearing on a genuinely new scenario), `ResponsePanel.jsx`.
- **Acceptance:** dispatch at T+10 on forecast A, swap to forecast B at T+20, replan → completed and underway missions keep their ids and history, and the change list shows re-routes caused by B.

### P1 — Core MISC-04 compliance

**P1-1 · Capability matching**
- **Change:** `need` → `required_capability`; filter teams in `planner.py`; reason "no team with capability X".
- **Acceptance:** a settlement needing `water rescue` is never assigned T2; when T1 is busy the unmet reason says so.

**P1-2 · Probabilistic accessibility from a forecast ensemble**
- **Reuse:** existing scenarios as ensemble members (e.g. moderate/heavy/cloudburst, or ±20 % rainfall perturbations).
- **Change:**
  - `Forecast` → `ForecastEnsemble`; P(blocked) per road-window = share of members at or over the limit.
  - Routing cost uses P; the plan reports expected invalid legs.
  - Files: `access/road_status.py`, `arrival_router.py`, `planner.py`.
- **Acceptance:** with one member flooding a road, P = 1/N is shown; the evaluation adds a Brier score and a reliability table (makes "uncertainty quality" measurable).

**P1-3 · Held-out event sequences**
- **Change:** turn `DEMO_SCRIPT` into a seeded generator (closure time and road, vehicle loss, shelter update, report conflicts); run N ≥ 20 sequences × both methods × ≥ 2 outcome runs; report mean ± spread.
- **File:** `response/evaluate.py`.
- **Acceptance:** same seed gives identical output; the report lists per-sequence and aggregate metrics.

**P1-4 · Historical timestamp labelling**
- **Change:** map T+min to the 26 July 2005 clock time for the replay scenario in API output, UI and exports.
- **Acceptance:** exports show both `sim_t_min` and `historical_time`.

### P2 — Evidence and reliability

**P2-1 · Forecast features and labels export**
- **Change:** one row per road × decision time × lead (depth, trend, DEM flag, linked, lead → outcome label); CSV from `/api/response/evaluate`.
- **Acceptance:** row count = roads × decisions × leads; labels match the confusion counts.

**P2-2 · One-command evidence bundle**
- **Change:** `scripts/evidence_bundle.py` writes the replays, ledgers, dispatch logs, route checks and baseline comparison into `docs/validation/`.
- **Acceptance:** two runs give byte-identical JSON (apart from timestamps).

**P2-3 · Rolling forecast issue times**
- **Change:** at each decision time, use the forecast issued then (persistence of the outcome-so-far, or a re-run from the current state).
- **Acceptance:** no forecast value uses information after its issue time (test).

**P2-4 · IMD robustness**
- **Change:** surface `districtnowcast` warnings as context; startup check of key/IP with a clear banner.
- **Acceptance:** the dashboard shows the IMD state accurately on a changed network.

**P2-5 · Deployment check**
- **Change:** run the suite on Python 3.12; one Vercel preview deploy; measure the evaluate endpoint's time.

### P3 — UX and presentation
- U1–U6 from §8.
- Guided tour steps for Response and Evaluation.
- Split the Terrain3D/three.js bundle.
- Remove the legacy `frontend/` fallback.

---

## 11. Reproducible demonstration scenario (deterministic, SIMULATED events)

**Setup:** cloudburst forecast, 180 min; the 4 settlements, 2 shelters, 2 teams, 2 trucks from `response/fixtures.py`;
outcome run `july2005` for the fair comparison. Every event is scripted with `received_t_min` and
`applies_from_t_min`, and each decision uses only events received by its time.

| Step | Sim time | Event | Expected system behaviour | Exists today? |
|---|---|---|---|---|
| 1 | T+0 | S1 Dadar TT declared **critical** (60 people) | first plan serves S1 first | ✅ |
| 2 | T+0 | build plan | 10 missions; 5 people at S4 unmet: *shelter capacity exhausted* | ✅ |
| 3 | T+0→T+50 | forecast deepens on N C Kelkar Marg (35 cm at T+51.9 for a car) | Route tab: current-status route **valid now, AT_RISK on arrival**; arrival-aware detour +0.2 min | ✅ |
| 4 | T+10 | **confirmed closure** on the next road of underway mission A2 | A2 re-routed from its estimated position; change list explains | ✅ |
| 5 | T+30 | **V2 breaks down**, **SH1 reported full** | proposals withdrawn; people redirected to SH2; unmet reasons updated | ✅ |
| 6 | T+35 | **conflicting report**: a field team says the closed road is passable (received T+35, applies T+20); authority closure still in force | closure stays binding; conflict listed with the rule that resolved it | **✗ needs P0-1** |
| 7 | T+40 | **stale report**: "Tilak Road flooded" from T+5 with 15-min validity | marked STALE, not used | **✗ needs P0-1** |
| 8 | T+45 | changed hazard: S3 people 40 → 70 | replan keeps completed/underway missions; S3 gets more trips or an unmet reason | ◐ backend only (needs UI, P0-2) |
| 9 | — | dashboard shows plan v-history, unmet needs and reasons, dispatch log | ✅ |
| 10 | — | replay steps 1–8 with arrival-aware and with current-status routing against outcome `july2005`; compare | ✅ for steps 1–5 (`/api/response/evaluate`); extend the script once P0-1 exists |

---

## 12. Evaluation metrics and matched-baseline method

**Matching**
- Identical event script, identical resources, identical forecast run; only the routing method differs.
- Both are judged against the same **outcome run, which must differ from the forecast run**: a same-run comparison makes arrival-aware routing right by construction.

**Metrics already computed (`response/evaluate.py`)**
- **Route validity:** legs and missions invalid by the outcome; known-closure violations (target 0); closures met that were reported later.
- **Response:** people served by the horizon, unmet at the end, first-service time per settlement, response delay (baseline − arrival-aware), last completion time.
- **Soundness:** double-booking + shelter overflow (target 0); replan time (ms).
- **Accessibility prediction** on every road-window (2,978 roads × decision times × leads 5/15/30 min): hits, misses, false alarms, accuracy, probability of detection, false-alarm ratio, and the share of blocked windows flagged including UNCERTAIN.

**Metrics to add**
- Brier score and reliability once probabilities exist (P1-2).
- Mean ± spread across held-out sequences (P1-3).
- Resource utilisation is already in each plan's `summary.utilisation` but not aggregated by the evaluation.

**Honesty rule:** every figure is SIMULATED (two FloodNet runs). It shows how the methods differ under an
imperfect forecast; it is not real-world accuracy.

---

## 13. Evidence and artifacts for the judges

| Artifact | Location / how to produce |
|---|---|
| Road-access scan (same-run sanity check) | `docs/validation/access_scan_cloudburst_{car,ambulance}.json` (`backend/scripts/access_scan.py`) |
| Planner demo replay (T+0, T+10, T+30) | `docs/validation/response_demo_replay.json` |
| Matched baseline evaluations | `docs/validation/response_eval_heavy_vs_cloudburst.json`, `…_vs_july2005.json` (`backend/scripts/response_eval.py`) |
| Resource ledger, dispatch log, plan versions, event logs, evaluation | Response tab → Export buttons |
| Requirement traceability | `docs/MISC04_REQUIREMENTS.md`, this report §6 |
| Design and decisions | `docs/ROAD_ACCESS.md`, `RESPONSE_PLANNER.md`, `RESPONSE_EVALUATION.md`, `DECISIONS.md` D-23…D-31 |
| Flood-model validation status | `docs/VALIDATION.md`, `docs/validation/*.md` |
| Live IMD proof | re-run the live smoke test on demo day (`RUN_LIVE_IMD=1 … -m live`) |

---

## 14. Exact next steps

1. **Implement P0-1 (conflicting/stale reports)** with its three acceptance tests, then add steps 6–7 to `DEMO_SCRIPT`.
2. **Implement P0-2 (forecast update without ledger loss + hazard-update UI).**
3. **Implement P1-1 (capability matching)**: small change, closes an explicit requirement line.
4. **Implement P1-3 (seeded held-out sequences)** and regenerate `docs/validation/response_eval_*.json`.
5. **Implement P1-2 (ensemble probabilities + Brier score)** if time allows; otherwise state "categorical uncertainty" plainly to the judges.
6. **Demo-day checklist:**
   - confirm the public IP matches the IMD key and run the live smoke test;
   - export the ledger and evaluation;
   - rotate the Twilio token afterwards.

### The five most important things to implement next
1. **Conflicting and stale report handling** (P0-1): the only MISC-04 demonstration item with no implementation.
2. **Forecast update with the ledger preserved, plus a hazard-report UI** (P0-2): makes "replan after changed hazard" demonstrable.
3. **Seeded held-out event sequences for the matched baseline** (P1-3): turns one anecdote into an evaluation.
4. **Capability matching** (P1-1): a small change that closes "respect resource requirements".
5. **Decision-time chip + Reports strip + guided-tour step for Response** (U1, U3, U5): lets judges follow the
   story without explanation.
