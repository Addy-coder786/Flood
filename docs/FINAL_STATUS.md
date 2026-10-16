# FloodNet — final status (2026-09-21)

FloodNet Urban Flood Nowcasting System · pilot: Mumbai, Hindmata / Dadar (MCGM F/North + F/South).
This page is the current truth. Older documents are history; where they differ, this page and the dated
"SUPERSEDED" notes inside them win.

## Current system
A physics-based urban flood forecasting and decision-support prototype: rainfall → runoff → 2D storage-cell
surface routing ⇄ MCGM drainage graph (capacity, surcharge) → street-level depth (cm), 37 frames at 5 min over
0–180 min → alerts, "why flooded" diagnostics, flood-aware routing. FastAPI + NumPy backend; React + Vite + Leaflet
frontend. It is **not** an AI/ML predictor.

## Current data sources
| Input | Source | Class |
|---|---|---|
| Drainage geometry, ground levels | MCGM stormwater network | REAL (roughness, capacity, inverts, inlets ESTIMATED) |
| Terrain | MCGM 20 cm contours → ~10 m model grid | REAL |
| Roads, buildings | OpenStreetMap | REAL (runoff coefficients ESTIMATED) |
| Known flood spots | MCGM | REAL |
| Rain — IMD live observation | api.imd.gov.in `current_wx` (Santacruz); nowcast/district-rainfall endpoints also reachable | VERIFIED live; run = REAL observation + ESTIMATED 3-hour persistence (`MIXED`) |
| Rain — ECMWF NWP | Open-Meteo | VERIFIED; NWP forecast, never called a nowcast |
| Rain — IMD Mumbai-Veravali DWR | public SRI image, decoded | `RADAR_IMAGE_DERIVED_ESTIMATE` (ESTIMATED) |
| Rain — 26 July 2005 | Santacruz gauge record | HISTORICAL REAL |
| Rain — design storms | FloodNet | SCENARIO (synthetic) |

Operator-facing names/badges come from one mapping, `frontend-react/src/lib/sources.js`; internal enums are not shown.

## What is live, derived, experimental, cached
| Class | What | Label shown |
|---|---|---|
| Live | IMD station observation (authenticated API; token expires ~hourly, rotated by hand — `DEPLOYMENT.md` §4) | LIVE |
| Derived | 3-hour persistence continuation of that observation; hotspot / onset / peak summaries of a run | (part of LIVE run; "model output") |
| Experimental | IMD Mumbai-Veravali SRI image → rainfall estimate | RADAR-DERIVED |
| Experimental, **off by default** | advection nowcast on decoded radar frames (`FLOODNET_RADAR_NOWCAST=1`) | "Experimental radar-image nowcast" |
| Forecast | ECMWF NWP via Open-Meteo | FORECAST |
| Cached | last good rainfall field (≤ 6 h), with its original source and age | CACHED |
| Demo | deterministic golden scenario (`demo` = cloudburst design storm) | DEMO |
| Not validated | flood depths, hotspot ranking, radar-derived rain, nowcast — no accuracy is claimed | — |

**Failover (`auto` = "Best available source"):** LIVE → RADAR-DERIVED → FORECAST → CACHED → DEMO. Every attempt and the
reason it failed is recorded with the run and in `GET /api/data-status`; a fallback is never relabelled as live.
Observed 2026-09-21 against the real services: IMD token expired + radar frame stale → the run completed on FORECAST.

## Radar status
Experimental. The public SRI **image** is decoded into legend-band rainfall (47 bands; ~23.7–26.2 mm/h band excluded;
capped ≈ 49 mm/h), georeferenced approximately from the image axes, resampled to `[T, ny, nx]`. One frame is
retrievable at a time; frames older than 90 min are refused (the Veravali feed was stale on 2026-09-21). A gated
advection step ("experimental radar-image nowcast", 5–30 min) exists but has only been exercised on generated test
frames — **no verified radar-motion nowcast is claimed**. After 30 min the run uses ECMWF or persistence, labelled per
period. It is not IMD QPE, not an IMD gridded product, not an IMD nowcast. Public-image reuse/automation permission is
unresolved. IMD's API portal offers no radar-product API. SR-01 remains **PARTIAL**.
Detail: `RADAR_SRI_IMAGE.md`, `RADAR_NOWCAST_STATUS.md`.

## Forecast status
0–180 min, 5-min frames, mass balance reported per run. Not validated against independent street-level depth
observations; no accuracy figure is claimed (`VALIDATION.md`). Blockage is a what-if scenario, not telemetry.
Drain-to-street causality is not established and is never stated. Tide is not modelled (`TECHNOLOGY_EXPLAINER.md`, D-22).

## Flood hotspots and street inspector
`GET /api/simulation/{run_id}/hotspots`: max depth, first flooding, peak time, flooded area, affected road length and
intersections, top hotspots (deepest first, one per named street, deterministic). Street inspector: current depth, peak,
peak time, onset, rain rate, DEM elevation, nearest-node state ("Nearby drainage network is …" — never a causal claim).

## Routing
Flood-aware Dijkstra on the real pilot road graph (largest strongly-connected component), vehicle depth limits,
direct vs lower flood-risk comparison with the reason, multi-candidate options with time-aware status. Route results
carry their forecast time; an older set shows "Route snapshot: T+x" with a one-click update. No traffic data.

## Alerts
Client-side forecast alerts plus a CAP 1.2 **draft** export: "DRAFT • NOT ISSUED — requires authorized government
issuance." FloodNet issues nothing.

## 3D terrain
2D | 3D toggle. The 3D view meshes the solver's own DEM (`/api/terrain/dem`, lossless float32; 255 × 244 cells, 10 m,
16.14–40.37 mTHD) with three.js, 10× visual relief (display only), the forecast's depth grid and flooded streets
draped on it, and a DEM elevation inspector. No online terrain service. Visual appearance unreviewed. `TERRAIN_3D.md`.

## Guided Briefing, Demo Story, Explore
Guided Briefing (situation, spoken, en/हिन्दी/मराठी), Demo Story (technical walkthrough on the same engine), Explore
FloodNet (product tour), dashboard story strip + interactive "How FloodNet Works". Every step opens the tab/layer it
describes. Neural voice via backend proxy needs `GOOGLE_TTS_API_KEY`; otherwise a matching browser voice, or a stated
silent mode — never an English voice reading Hindi/Marathi.

## Deployment architecture
Static frontend on Vercel (`vercel.json`, `VITE_API_BASE_URL`) + FastAPI backend as a single container (`Dockerfile`)
on a host with a static outbound IP. Not a Vercel function: runs are held in process memory and IMD keys are
IP-bound. `DEPLOYMENT.md`.

## Known limitations
No validated accuracy · radar is image-derived and experimental · IMD token expires and is renewed by hand ·
single-instance backend (in-memory runs) · no tide, traffic, sensors/SCADA, ML · CAP drafts only · one pilot area ·
a cold 180-min cloudburst run measured 115 s on the development machine.

## Test status (2026-09-21, final pass)
`pytest -q`: **339 passed, 8 skipped, 0 failed** (skips: 5 opt-in live IMD, 2 opt-in live ECMWF, 1 needs optional `h5py`).
`npm run test:unit`: **27/27**. `RUN_LIVE_IMD=1` live smoke last passed 5/5 on 2026-09-18 with a fresh token (the token
has since expired; not re-run). Docker: image builds (672 MB), container started from a CLEAN environment (no `.env`)
is `healthy`, runs as an unprivileged user, and served `/health`, `/api/data-status`, DEM, a demo run, frames, hotspots,
CAP draft, routing and an `auto` run (→ FORECAST) with CORS restricted to the configured origin. No `.env`, credential
value or credential-bearing layer is in the image. The 12 real-IMD-image tests run only where the local image exists.
How to run: `cd backend && .venv/Scripts/python.exe -m pytest -q` · `cd frontend-react && npm run test:unit && npm run lint && npm run build` · `docker build -t floodnet-api .`

## Build status
`npm run build` PASS · `npm run lint` 0 errors (7 long-standing warnings) · Vercel-style build (`VITE_BASE=/`) PASS.

## Vercel status
Configuration is in the repository (not yet committed to git) and statically verified (base path, SPA fallback, API origin, no secrets or localhost in the
bundle, `.env` excluded). **Not verified:** `vercel build`/`vercel pull` (CLI not installed), any deployed (AWS/Vercel) smoke test, and any visual browser check (automation cannot reach localhost here).


## Addendum 2026-10-10 — HackMatrix MISC-04 additions (supplement to the original problem statement, D-23)

Built and tested on top of the flood engine, without changing it: arrival-time road access with confirmed closures
(`ROAD_ACCESS.md`), an emergency response and resource planner with history-preserving replanning
(`RESPONSE_PLANNER.md`), and a deterministic replay evaluated against an independent outcome run
(`RESPONSE_EVALUATION.md`). Requirement-by-requirement status: `MISC04_REQUIREMENTS.md`. All headcounts, capacities,
speeds and on-site times are SIMULATED / ASSUMED and labelled; the evaluation is a comparison between two simulations,
not real-world validation. Whether MISC-04 replaces or supplements the original flood-nowcasting problem statement remains [UNVERIFIED] with the organisers.
