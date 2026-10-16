# Competitive analysis — reference video and public repository (2026-09-21)

**Evidence classes.** VIDEO-SHOWN = visible in the reference video. CODE-VERIFIED = read in the public
repository. CLAIMED ONLY = stated in a README / narration with nothing behind it. UNKNOWN = not determined.
Only VIDEO-SHOWN and CODE-VERIFIED count as comparison evidence.

**How this was established.**
- Video (https://www.youtube.com/watch?v=BB9RKvk9oHs, 4:10): **not watched by the analyst** — the tooling cannot
  play video. The VIDEO-SHOWN column is the project lead's own viewing notes, recorded as given. Verbal claims in
  the video are CLAIMED ONLY.
- Repository (another team's public GitHub repository on the same problem statement): cloned read-only outside
  this project and read file by file. Nothing was built or executed. 21 commits, 23–28 Aug 2026, one author.
  It is not confirmed that the video and this repository are the same project.

## 1. What the repository actually contains

| # | Area | README claim | Code finding | Class |
|---|---|---|---|---|
| 1 | Radar ingestion | Live DWR | No radar pixel is ever fetched or decoded. `radar_provider.py::prewarm_radar_buffer()` invents a 42 dBZ Gaussian blob tagged "Live Doppler Radar Composite / VERIFIED". RainViewer client fetches only an index JSON. Go "ingestion daemon" serves hard-coded telemetry, rain = `15+10*sin(phase)`. A real dBZ→rain converter exists for in-memory arrays. | PARTIAL (mock input) |
| 2 | Radar motion nowcast | Optical flow | `cpp_core/optical_flow.cpp` (68 lines): single-level windowed Lucas-Kanade (pyramid args ignored). `services/nowcast/advection.py`: gradient flow + real semi-Lagrangian advection. Input is synthetic/fixture frames only; the realtime endpoint returns a canned scenario. | PARTIAL |
| 3 | DEM | CartoDEM / SRTM | Only a seeded synthetic 134×134 DEM is committed (honestly labelled). City DEMs are gitignored; download script pulls Copernicus 30 m. Missing file → silent zeros DEM. | PARTIAL |
| 4 | Grid | — | 30 m. City grid not reproducible from the repo. (The video's 100 m grid is VIDEO-SHOWN only.) | PARTIAL |
| 5 | Surface hydraulics | 2D Saint-Venant FV | `solver_2d.cpp::advance_step` is a real first-order FV shallow-water step (Audusse, Rusanov, CFL, Manning, OpenMP). But `solve_inundation_full` sets depth from a closed-form terrain index, then runs only 15 s of SWE "relaxation". NumPy fallback hard-codes `mass_closure_error_pct: 0.012`. | PARTIAL |
| 6 | Drainage | 1D EPA-SWMM | pyswmm genuinely coupled (orifice exchange, mass ledger) — but only on a synthetic 4-junction / 3-conduit network. Real OSM drains are committed (Mumbai 988 features) and never run; C++ path sets exchange to 0. | PARTIAL |
| 7 | Tide | Tidal backflow | Real Open-Meteo Marine calls; value is telemetry only, never a hydraulic boundary. Their own scenario code says tide is "out of scope". | CLAIMED ONLY (as backflow) |
| 8 | Routing | Sub-ms A* | Real Python Dijkstra with flood penalties. The C++ "A*" only nudges waypoints sideways. City graph gitignored; demo graph synthetic (85 segments). | PARTIAL |
| 9 | Alerts | CAP | `cap.py::to_xml` emits CAP 1.2. Dispatcher is self-described simulated, always "DELIVERED". | CODE-VERIFIED (CAP) |
| 10 | UI | React/Canvas tactical GIS | ~5.9k lines React 18 + TS + Canvas. The "Doppler radar layer" is drawn range rings + rotating beam, no imagery. | CODE-VERIFIED |
| 11 | Storytelling / demo mode | — | None in the frontend (a CLI script only). Demo-story controls are VIDEO-SHOWN, not in this repo. | not in repo |
| 12 | 3D / tech visualisation | 3D digital twin | No three.js / deck.gl / maplibre; 2D canvas. 3D terrain is VIDEO-SHOWN only. | not in repo |
| 13 | Performance | 54 ms, 22.5 M cells/s | Docs only; no stored results. Plausible only because 15 s of physics is run. | CLAIMED ONLY |
| 14 | Deployment | — | No Dockerfile / compose / CI. A Windows DLL is committed. | absent |
| 15 | Testing | — | ~726 test functions; real coverage of Z-R, advection, ledger, CAP, SWMM coupling — all on synthetic fixtures; real-data tests skip. | CODE-VERIFIED (synthetic) |

Other: NASA IMERG/SMAP is CLAIMED ONLY (client returns hard-coded values with status "AUTHENTICATED"). For
non-demo cities the API returns a procedural moving Gaussian labelled `REAL_OBSERVED / CALIBRATED_RADAR /
DWR_MOSAIC`. Secrets are committed (an OpenTopography key, a CARTO key) and TLS verification is disabled.

## 2. Gap matrix

| Feature | Competitor (evidence) | FloodNet | Real gap? | Action |
|---|---|---|---|---|
| Real radar input | Mock blob labelled live (CODE-VERIFIED) | Real IMD Mumbai-Veravali SRI image decoded, labelled RADAR_IMAGE_DERIVED_ESTIMATE | No — FloodNet is ahead | Keep labels honest |
| Radar motion nowcast | LK/advection code on synthetic frames (PARTIAL) | Was: single frame + persistence | **Yes (temporal)** | Built gated advection nowcast — `docs/RADAR_NOWCAST_STATUS.md` |
| Source-aware 0–3 h driver | none | Was: one source per run | Yes | Built: radar 0 → nowcast 5–30 → ECMWF/persistence, each labelled |
| DEM | synthetic committed; 30 m download | MCGM-derived ~10 m pilot terrain | No | Do **not** coarsen to 100 m |
| Surface hydraulics | FV SWE step exists, city output is a heuristic | 2D storage-cell, time-integrated, mass balance reported per run | No | No solver swap (Part 19) |
| Drainage | SWMM on 4 synthetic junctions | Real MCGM network, capacity-limited graph solver | No (theirs is richer physics on toy data) | None |
| Tide | display only | not modelled, stated | Shared gap | Documented; no usable licensed source yet |
| Routing | Dijkstra + penalties | Dijkstra + penalties, multi-candidate, time-aware, vehicle classes | No | Presentation only (A vs B + why) |
| Alerts | CAP 1.2 XML | CAP 1.2 draft, explicitly not issued | No | None |
| Technology story before the dashboard | VIDEO-SHOWN | Landing had a static, stale section | **Yes (UX)** | 7-stage interactive chain, shared data |
| Demo story mode | VIDEO-SHOWN | Guided Briefing (situational) only | **Yes (UX)** | "Demo Story" on the same engine |
| Pipeline visible on the dashboard | VIDEO-SHOWN (dark tactical HUD) | none | Yes (UX) | Story strip RAINFALL → FLOOD → DRAINAGE → ACTION |
| Elevation / junction / surcharge popovers | VIDEO-SHOWN | Already present (node HGL, freeboard, fill, spill; drain capacity; street depth) | No | None |
| Explicit origin → destination route | VIDEO-SHOWN | Search + map pick existed; result lacked an A/B view | Partly | OD header + direct vs lower flood-risk + why |
| 3D terrain | VIDEO-SHOWN | 2D Leaflet | Yes, cosmetic | Not built — no requirement; effort better spent on evidence |
| C++/OpenMP performance | CLAIMED ONLY | Python/NumPy, measured per run | Unproven | Measure first; no rewrite |
| Multi-city | partial data | one evidenced pilot | By design | None |

## 3. Decisions
- Adopt: technology storytelling, demo story, visible pipeline, clearer route comparison, a temporal radar step.
- Do not adopt: mock data under "live/verified" labels, closed-form depth presented as simulation, 100 m grid,
  performance or tide claims without evidence, 3D for its own sake.
- Borrowable ideas for later, with proper validation: pyswmm↔surface coupling with a mass ledger; semi-Lagrangian
  advection with a spatially varying motion field.

## 4. Note for the project lead
A public repository (`the team's earlier repository`) has a layout that appears identical to
this project (`backend/floodnet/...`, `CLAUDE.md`, `data/mcgm.py`). Only its file tree was looked at. Confirm it
is an intended team copy.
