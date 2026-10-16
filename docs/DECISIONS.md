# DECISIONS — FloodNet Urban Flood Nowcasting System

**Overall status: `DECIDED` for the build (D-01 … D-13). Recorded 2026-09-08 ~18:30 IST after the Mumbai research round.**
**Time budget: ~48 hours from 2026-09-08 evening.** First 12 hours = core MVP milestone, not the deadline.

Rules unchanged: nothing is decided until recorded here with evidence; supersede, never silently edit; decisions follow the data.
Evidence lives in `research/mumbai/*.md` and `docs/MUMBAI_FEASIBILITY.md`. Where the lead re-verified a worker's claim, it says so.

---

## Decision table

| # | Decision | Status | Decision (short) |
|---|---|---|---|
| D-01 | Target city | `DECIDED` | **Mumbai** (project direction) |
| D-02 | Pilot area | `DECIDED` | **Hindmata / Dadar–Parel–Matunga**, bbox 72.835–72.855 E, 19.010–19.030 N (~4.7 km²), extendable to a "wider" box |
| D-03 | Drainage dataset | `DECIDED` | **Real MCGM BRIMSTOWAD network** (layers 6 + 7), `Existing` conduits; roughness ESTIMATED |
| D-04 | DEM | `DECIDED` | **DTM interpolated from MCGM 20 cm contours (layer 301)** at 10 m (5 m optional); Copernicus GLO-30 as fallback only |
| D-05 | Rainfall source | `DECIDED` | **Scenario-driven hyetographs** (REAL 26 Jul 2005 replay from Chitale report + SYNTHETIC design/user storms) as the driver; Open-Meteo NWP as optional live feed **labelled NWP, not radar**; radar extrapolation nowcast = P2 stretch |
| D-06 | Ground truth | `DECIDED` | **MCGM Flooding Spots layer 344** (spatial-overlap check) + **Chitale 2005 named locations** (qualitative). No depth/timing validation claimed |
| D-07 | Hydraulic engine | `DECIDED` | **Own transparent graph solver** for the live system + **pyswmm/EPA SWMM offline cross-check** on the pilot subnetwork (hybrid) |
| D-08 | ML / GNN | `DECIDED` | **Not in the MVP.** P2 only, and only trained/evaluated on our own simulator output with reported metrics; CascadeFloodNet weights never used |
| D-09 | Surface routing | `DECIDED` | **Simplified 2D storage-cell (diffusive) routing on the DTM grid**, explicit timestep, mass-conserving, buildings as obstacles; D8 flow-accumulation as fallback |
| D-10 | Database | `DECIDED` | **None.** Files (JSON/NPZ) + in-memory state. No Neo4j/Supabase/Redis |
| D-11 | GIS framework | `DECIDED` | Processing: numpy/scipy/shapely/pyproj/networkx (no GDAL/rasterio dependency). Web map: **Leaflet**, vanilla JS, served by FastAPI, no bundler |
| D-12 | Routing | `DECIDED` | **OSM road graph (Overpass) + own flood-weighted Dijkstra** (networkx). No OSRM |
| D-13 | Deployment | `DECIDED` | **Single local process** (`uvicorn`), all data pre-processed to disk, demo runs offline. No Docker required |

---

### D-01 — Target city: Mumbai
- **Basis:** project direction under time pressure; compliant with the PS ("metros like Mumbai, Delhi, Chennai"). Not a data-driven comparison — we must not claim it was. Alternatives not evaluated. Irreversible at this budget.

### D-02 — Pilot area: Hindmata / Dadar
- **Evidence (lead-verified on the MCGM snapshot):** bbox 72.835–72.855 E × 19.010–19.030 N contains **1,607 manholes, 1,608 conduits (826 `Existing`)**, `GROUND_LEV` 27.4–38.2 mTHD, **1,973 twenty-cm contour lines**, **21 Flooding Spots polygons**. Two large drainage components are partly inside (617/2,829 and 547/2,909 nodes) — the network must be clipped with its downstream path to an outfall kept, or the box widened.
- **Why here:** most media-cited chronic hotspot in Mumbai (W2, Free Press Journal source), no river channel to model, dense real network, judge-recognisable.
- **Alternatives:** B Milan Subway (348 nodes — too sparse), C Kurla/Mithi (needs river), E "Sion–Matunga–Hindmata wider" (72.830–72.870 × 19.000–19.045, **6,012 nodes / 6,129 conduits, ~21 km²**) — the designated **scale-up box** if runtime allows.
- **Caveat:** bbox coordinates were inferred by W2, not gazetteer-sourced. Agent 1 must confirm Hindmata junction falls inside it before hard-coding.

### D-03 — Drainage dataset: real MCGM network
- **Evidence:** `research/mumbai/DRAINAGE.md` (verdict A) and **lead re-verification**: 34,711 conduits with 0 null `US_INVERT/DS_INVERT/CONDUIT_WI`; 34,431 nodes, all unique, 0 null `GROUND_LEV`; 0 dangling endpoints; live count re-confirmed. Snapshot in `data/raw/mcgm_gis/` with `PROVENANCE.md`.
- **What is REAL:** geometry, connectivity, direction, length, cross-section shape and size, upstream/downstream inverts, ground levels.
- **What is ESTIMATED (must be labelled):** Manning's n (by shape/assumed material, cited table), node invert (derived = min of connected conduit inverts — standard practice), node type (outfall inferred from graph sinks), inlet capture capacity, blockage factor (scenario input only).
- **Filters:** `USER_TEXT2 = 'Existing'` for the present-day model; `Proposal` conduits available as a "BRIMSTOWAD upgrade" what-if scenario. Normalise `SHAPE_1` case; fix 26 adverse / 119 flat slopes.
- **Datum:** mTHD. Because D-04 uses MCGM contours (same datum), **no datum conversion is needed in the pilot.** If Copernicus is ever mixed in, calibrate the offset first.
- **Licence:** `"MCGM, Esri India"`, no explicit open licence. Credit MCGM on every screen; do not claim redistribution rights; do not publish the raw snapshot outside the demo.
- **Rejected:** synthetic network (unnecessary); OSM drains (716 ways, no attributes — Class C).

### D-04 — DEM: DTM from MCGM 20 cm contours
- **Evidence (lead-verified):** layer 301 `Contour_20CM`, polylines with `HEIGHT` (double), `LAYER` MINOR/MAJOR, native EPSG:32643, 284,403 features city-wide, 1,973 in the pilot bbox; sample values 28.0–29.8 match adjacent manhole ground levels (~29.5) → same vertical datum.
- **Method:** contour vertices → scattered points → interpolate (scipy `griddata` linear, or natural-neighbour if available) to a regular grid at **10 m** (5 m optional) in EPSG:32643; add manhole `GROUND_LEV` points as extra samples; burn OSM building footprints as obstacles (raised/no-flow), not as elevation.
- **Honest limitation statement to ship with it:** contour-derived DTM; effective vertical resolution ≈ 0.2 m; interpolation artefacts between contours; not LiDAR. Street-segment depths carry ±~10–20 cm uncertainty from terrain alone — state it.
- **Fallback:** Copernicus GLO-30 DSM (verified no-auth fetch), only if the contour layer becomes unreachable and the snapshot is lost — with the datum offset calibrated and a 30 m caveat.
- **Rejected:** SRTM via OpenTopography (key + coarser), CartoDEM (login/queue), FABDEM (unverified download, NC licence), MCGM LiDAR (not accessible).

### D-05 — Rainfall: scenario hyetographs; NWP as labelled live feed
- **Evidence:** `research/mumbai/RAINFALL.md`; lead re-verified RainViewer `nowcast: []`. No public gridded 0–3 h radar nowcast exists for Mumbai; IMD API needs a key we don't have.
- **Drivers, each carrying a provenance tag:**
  1. `REAL` — 26–27 July 2005 Santacruz hourly series, transcribed from the Chitale Committee report (scratchpad `chitale.txt`; Agent 1 extracts and cites page). Replay labelled "historical replay", never "nowcast".
  2. `SYNTHETIC` — user-defined uniform/pulse storms and a design storm. IDF parameters only if a citable Mumbai source is found; otherwise the storm is labelled "synthetic stress test", not "design storm".
  3. `NWP` (optional live) — Open-Meteo hourly precipitation for the pilot centroid, labelled "NWP model forecast — not radar nowcast" in the API and UI. This is the fallback the PS criticises; it is shown only as the live-mode driver with that label.
  4. `P2` — IMD Veravali radar GIF (reachable, no auth) + pysteps extrapolation. Only if time remains; legal grey area noted.
- **Spatial form:** the driver is a time series applied uniformly or with a simple spatial gradient across the 4.7 km² pilot — stated openly (radar-scale spatial structure is not available).
- **Implementation note (2026-09-09):** driver 3 (Open-Meteo ECMWF NWP) implemented as `ECMWFForecastProvider`
  (`backend/floodnet/rainfall/provider.py`, `scenario_id="ecmwf"`), selectable independently alongside the
  synthetic scenarios, the 2005 replay, and the still-pending IMD live-observation path — none of which it
  replaces or disables. Full source audit: `docs/ECMWF_OPENMETEO_AUDIT.md`.

### D-06 — Ground truth: Flooding Spots + Chitale 2005
- **Evidence:** `research/mumbai/FLOOD_GROUND_TRUTH.md`; lead-verified 21 polygons in the pilot bbox. Records suffixed "(Delete)" / "(Tackled)" must be filtered or shown as such; layer vintage ~2017, currency unconfirmed.
- **Validation we will claim:** (a) spatial overlap — fraction of active Flooding Spots polygons inside our top-N ponding zones under the 2005 replay; (b) qualitative match to Chitale's named flooded roads inside the pilot; (c) conservation, behavioural and sensitivity checks (PRD V1, V2, V4); (d) runtime (V5).
- **We will not claim:** depth accuracy, timing accuracy, or any hit/miss score against a georeferenced event map (none exists publicly).

### D-07 — Hydraulic engine: own solver + pyswmm cross-check (hybrid)
- **Own solver (live path):** per conduit, full-bore Manning capacity from real shape/size/slope + ESTIMATED n; per node, storage + hydraulic grade; flow limited by downstream capacity; **surcharge** when node HGL ≥ `GROUND_LEV`, surcharged volume returned to the surface grid; **blockage factor** 0–1 scales effective capacity; **inlet capture** limits what the surface can hand to the node; cause attribution (overcapacity vs blockage vs downstream constraint). Mass balance reported every run.
- **pyswmm (validation path, offline):** export the pilot subnetwork to a SWMM `.inp` (`swmm_api`), run the same storm, compare which nodes flood and peak node depths; report agreement honestly. W6's test failed on a `swmm_api` API mismatch — the engine installs on Windows in seconds (`pyswmm 2.1.0`, `swmm_api 0.4.74` in the scratchpad venv); the authoring code must be fixed.
- **Rejected as the live engine:** SWMM alone (awkward 2D coupling, opaque to judges in 48 h); CascadeFloodNet `hydro_engine.py` (undocumented `*15` and `*1.6` factors, no blockage, no 2D).

### D-08 — ML/GNN: not in MVP
- Physical baseline first. CascadeFloodNet weights were trained on a fictional 20-node basin (the earlier `disastr/` audit) and are never used or cited as Mumbai validation. If time remains after P1, a surrogate may be trained on our own simulator output with reported metrics — otherwise nothing.

### D-09 — Surface routing: simplified 2D storage-cell scheme
- Each grid cell holds a water depth; flux between 4-neighbours driven by water-surface-elevation difference with a Manning-type resistance and an explicit stability limit; buildings are no-flow obstacles; runoff enters per cell from D-05 × imperviousness; exchange with drainage nodes at inlet cells (D-07). Mass conserved to floating-point; conservation error reported. Grid 10 m default (≈46 k cells for the pilot), 5 m optional. Internal Δt adaptive (seconds); output frames every 5 min over 0–180 min.
- **Not** full shallow-water equations, and we say so. **Fallback:** D8 flow accumulation into terrain sinks with volume filling (quasi-steady), if the dynamic scheme is unstable under time pressure.

### D-10 — Database: none
- Pre-processed inputs as JSON/NPZ under `data/processed/pilot/`; simulation results in memory + optional NPZ cache. Nothing in the requirements needs a database.

### D-11 — GIS framework
- Processing: `numpy`, `scipy`, `shapely`, `pyproj`, `networkx` only. No rasterio/GDAL (no wheels-fight on Windows; not needed when the DTM is built from contour vectors).
- Web map: **Leaflet 1.9** via CDN, vanilla ES-module JS, one HTML page served by FastAPI static files. OSM tiles with mandatory attribution; if tile-usage policy is a concern for the demo, cache tiles or use a permissive provider. Reason: zero build step, fewest failure points, per the "reproducible demo" priority. CSS may be borrowed from `disastr/web/styles.css` (MIT declared in README, no LICENSE file — attribute).

### D-12 — Routing: OSM + own Dijkstra
- Overpass extract of `highway=*` within the pilot bbox (+ margin) → networkx DiGraph with `oneway` respected; edge cost = length × penalty(depth_cm at time t, vehicle class); depth ≥ vehicle threshold ⇒ edge removed. Time-aware option: use the frame at the estimated arrival time (PRD R18.5, [PROPOSED]). ODbL attribution mandatory.

### D-13 — Deployment: single local process
- `uv`-managed venv in `backend/.venv`; `uvicorn floodnet.api.main:app`; static frontend under `frontend/`; all datasets pre-processed to disk so the demo never depends on BMC/Overpass/Open-Meteo being reachable. Docker optional, later.

---

### D-14 — Radar: PATH D (blocked for defensible quantitative use); ECMWF stays the only live provider
- **Decided 2026-09-10**, on evidence from a dedicated IMD-API investigation plus an independent adversarial
  review that re-fetched and re-measured every claim. Full evidence: `docs/LIVE_RAINFALL_AUDIT.md` §8b/§8c.
- *(Superseded in part by D-20 / D-21, 2026-09-18/21 — an experimental, clearly-labelled decoder now exists; the reasoning below is kept as the record of why it is only experimental.)*
- **No radar decoder will be built.** IMD documents no radar endpoint (dead index anchor, body ends at §20).
  The public `sri_mum.gif` genuinely *is* mm/hr Surface Rainfall Intensity with a published Z-R relation
  (correcting an earlier claim in this repo that it was reflectivity-only) — but its **top bin is open-ended
  at `>100 mm/h`, below this project's own `cloudburst` (120 mm/h) and `july2005` (190.3 mm/h) intensities**,
  with ±3.33 mm/h quantisation and ~12.5% coastline occlusion over the pilot. Decoding a rendered
  visualisation is a lossy reconstruction of a picture, not an observation, and must never carry an
  observation-class provenance tag.
- **No historical hindcast is possible** from free sources: no archive, directory listings 403, ~3 Wayback
  captures in six years.
- **Rejected PATH C** ("access exists but quantitative rainfall unavailable") as an understatement that would
  invite building the decoder anyway; PATH A contradicted; PATH B dead.

### D-15 — The SR-01 gap is in our own engine, not only in IMD access
- **Decided 2026-09-10.** `contracts.RainfallScenario.intensity_mm_h` is `[T]` and `intensity_at()` returns a
  `float`, which `engine.run_simulation` hands to `runoff_fn`. **Rainfall is spatially uniform by
  construction for every provider.** FloodNet cannot ingest a gridded rainfall field from any source, so
  acquiring radar data would not by itself satisfy the "high-resolution/gridded" half of SR-01.
- Generalising `RainfallScenario` to an optional `[T, ny, nx]` field is the honest prerequisite. **Not
  implemented, not scheduled** — recorded so the requirement is never reported as merely data-blocked.
- **✅ RESOLVED 2026-09-14 — the engine half of this gap is now closed.** `RainfallScenario` accepts an
  optional `intensity_field_mm_h` `[T, ny, nx]` plus `field_grid`; `intensity_field_at()` returns a per-cell
  field; `runoff_fn` accepts a scalar *or* a field; `engine.run_simulation` branches on
  `scenario.is_spatial`. Uniform scenarios keep the byte-identical original code path (verified: `intensity_at`
  equivalent on 1,509 samples; a constant field reproduces the scalar runoff total exactly; mass-balance
  error reproduces to the last digit). New module `floodnet/rainfall/gridded.py` does reprojection/resampling
  onto the model grid — the engine deliberately **refuses** a mismatched grid rather than silently regridding.
  24 new tests (`tests/test_spatial_rainfall.py`). **What remains blocked is DATA, not architecture** — see
  D-16. This is the honest inversion of the original finding: the engine is no longer the limiting factor.

### D-16 — Gridded rainfall source: GPM IMERG selected; it is NOT radar and does NOT resolve the pilot
- **Decided 2026-09-14**, after verifying eight candidate paths against their official sources.
- **Selected: NASA/JAXA GPM IMERG Early Run V07 (`GPM_3IMERGHHE.07`, GES DISC).** It is the only candidate
  that is simultaneously real, **verifiably CC0-licensed**, genuinely gridded `[time, lat, lon]` in mm/hr,
  covers Mumbai, and is programmatically downloadable with nothing but a free Earthdata Login. Implemented as
  `IMERGSatelliteProvider`; raises `ProviderUnavailable` (never a fabricated value) without `EARTHDATA_TOKEN`.
- **It is NOT ground radar and NOT a radar nowcast, and must never be labelled as either.** IMERG's pixels
  come from passive-microwave + geostationary-infrared sensors; a spaceborne radar (GPM DPR) enters only
  *indirectly*, as the radar half of the CORRA product used to intercalibrate those sensors. The only honest
  description is "satellite passive-microwave/infrared precipitation, intercalibrated against a
  spaceborne-radar reference".
- **It provides ZERO spatial variation across this pilot, and that is stated rather than hidden.** IMERG's
  0.1° cell is ~11.1 × 10.5 km (~117 km²) at 19°N. The pilot box (72.835–72.855 E, 19.010–19.030 N) is
  ~2.4 × 2.55 km (~6 km²) and falls **entirely inside one IMERG cell** (72.8–72.9 E / 19.0–19.1 N) without
  even straddling a boundary. Adopting IMERG is an upgrade in *provenance and temporal realism*, **not** in
  spatial detail. `gridded.describe_effective_resolution()` computes this automatically and writes
  "CANNOT resolve structure inside the pilot" into the scenario's provenance note; that note must not be
  edited out downstream.
- **Verification status of the network path:** product, resolution, cadence, licence, host and auth scheme
  were verified against NASA's own pages. The request path (GES DISC directory layout + IMERG granule
  filename pattern) follows documented convention but has **NOT been executed against the live service** — no
  Earthdata credential existed in this environment. Same honest status as `IMDObservationProvider`.
- **Rejected, with reasons:** IMD DSP radar supply (portal unreachable — TLS cert failure; cost/format/approval
  unverified; manual + paid, not achievable in a hackathon window); MOSDAC GSMaP (0.1°, same resolution
  limit, approval-gated signup, licence text unverifiable); INSAT-3D QPE (geostationary IR, not radar, specs
  unpublished); IMD 0.25° gridded (28 km, *daily* — useless for a 0–3 h nowcast); RainViewer/OpenWeather
  (colour map tiles, not mm/h arrays — the same quantisation problem that disqualified the IMD GIF);
  Open-Meteo higher-resolution models (no radar and no nowcast product for India; ~11–13 km native, and
  fanning out point queries would manufacture *fake* spatial variation from a coarse field — explicitly not
  done); IIT-Bombay's 4TU DWR dataset (real Indian radar, but **Chennai**, 269 KB, and **CC BY-NC-ND**, whose
  No-Derivatives term forbids building on it).
- **PATH G (validate the nowcasting algorithm on foreign radar) not taken.** `pysteps-data` offers real
  1 km / 5 min ground radar (MeteoSwiss verified), free and scripted — but licences for most subsets are
  unstated in the repo and the RMI subset is explicitly non-commercial. Deferred rather than adopted on an
  unverified licence, consistent with the same discipline applied to the IMD imagery in D-14.

### D-17 — Manhole plan area: adopt IS 4111 depth bands in place of a blanket assumption
- **Decided and implemented 2026-09-14.** The model used a single invented `STORAGE_AREA_M2 = 1.5` for all
  1,233 nodes ("typical chamber size; not in MCGM data"). **IS 4111 (Part 1) – 1986** (Code of practice for
  ancillary structures in sewerage system: Manholes) specifies chamber size *by depth*, quoted verbatim from
  law.resource.org: cl. 3.3.2 "For depths less than 0·90 m, 900 × 800 mm" and "For depths from 0·90 m and up
  to 2·5 m, 1 200 × 900 mm"; cl. 3.3.3 "For depths of 2·5 m and above … 1 400 × 900 mm".
- Applied via `mcgm.storage_area_from_depth()`, banding on each node's own depth (`node_ground − node_invert`,
  both REAL MCGM values) → 0.72 / 1.08 / 1.26 m². Distribution on the pilot: 24 / 980 / 229 nodes. Total
  chamber plan area falls **1,849.5 → 1,364.2 m² (−26.2 %)**.
- **Tag stays ESTIMATED, deliberately.** The dimensions are real and citable, but "MCGM's chambers conform to
  IS 4111" is an assumption about this network, not a measurement of it. IS 4111 cl. 3.3.4 equally permits
  *circular* chambers (0.64/1.13/1.77/2.54 m²); the rectangular series was chosen only because it is the one
  that covers the shallowest band without extrapolation. Both facts are in the provenance note.
- **This is a physics change and it moved the results** (validation gate, `heavy` + 70 % blockage, 180 min):
  peak depth 268.72 → **292.27 cm**, peak surcharging nodes 424 → **459**, total surcharge 124,981 →
  **133,020 m³**, mass-balance error −3.77e-12 → **−3.15e-12 %** (still conserving). Direction is physically
  coherent: less chamber storage → less buffering → more surcharge. **Figures published before 2026-09-14
  were computed with the old 1.5 m² and are superseded.**

### D-18 — Estimated parameters that could NOT be upgraded, and why
Recorded so these are never re-litigated from memory, and never "upgraded" with a number that does not
actually describe the quantity:
- **Manning's n (0.013 blanket).** No authoritative Indian value could be verified. IRC:SP:50-2013 was
  fetched in full and contains **no n-by-material table**; CPHEEO's manuals were unreachable
  (cpheeo.gov.in refused connection, mohua.gov.in 403, the 2019 manual exceeds fetch limits). A widely-quoted
  "0.014 concrete / 0.018 trapezoidal" attribution to CPHEEO 2019 could not be confirmed from a primary
  source, and a third-party compilation site self-describes as non-authoritative. **Kept at 0.013, ESTIMATED.
  The 62 British-era brick arch conduits were deliberately NOT switched to 0.015** — that change needs a
  source, and none was verified.
- **Inlet capture capacity (0.05 m³/s per node).** IS 7740-1985 (fetched) gives gully *geometry* only —
  spacing 18–36 m, 150/250 mm outlet — and **no discharge figure**. IRC:SP:50-2013 §3.2.4 (fetched) gives
  spacing ≤30 m and ≥600 mm gutter width, no capacity. FHWA HEC-22 provides a *method*, not an Indian value.
  **No Indian standard states a per-inlet capture rate. Kept at 0.05, ESTIMATED.**
- **DEM.** Every free alternative verified (Copernicus GLO-30, JAXA AW3D30, SRTM/NASADEM, CartoDEM) is 30 m
  and a *surface* model including buildings. The existing 20 cm-contour-derived 10 m DTM is **better**, and is
  kept. No public Mumbai LiDAR was found.
- **Tidal/tailwater boundary.** PSMSL station 43 (Apollo Bandar, 1878–2024) publishes **monthly means only** —
  unusable as a hydraulic boundary. INCOIS tide endpoints 404/DNS-failed. BMC's 2026 high-tide calendar gives
  peaks (4.89 m on 16 July; 4.5 m concern threshold), which would support a tide-*coincidence scenario* but
  not a continuous series. **Still MISSING; scenario boundary remains a proposal, not implemented.**
- **Runoff coefficient — a real source exists but was NOT applied.** IRC:SP:50-2013 §6.4.1, describing
  **Mumbai's own practice**, states verbatim: *"The runoff coefficient adopted in fully developed area is 1.0.
  In less developed areas the coefficient is worked out which may range between 0.58 to 1.0."* This is
  strictly better provenance than the current generic ASCE/Chow C_imp=0.95 / C_perv=0.35. **Not adopted in
  this pass**: C=1.0 is a *design* coefficient for drain sizing (deliberately conservative, zero losses), and
  adopting it would change every published flood figure a second time. Recorded here as an available,
  evidenced upgrade requiring the full validation gate and an explicit owner decision — not silently applied.
- **Imperviousness raster — a real source exists but was NOT applied.** **ESA WorldCover 10 m v200 (2021)**,
  CC BY 4.0, *"provided free of charge, without restriction of use"*, downloadable with no registration
  (`aws s3 sync s3://esa-worldcover/v200/2021/map --no-sign-request`), matches the model's 10 m grid exactly
  and would replace the current OSM-geometry rule with its flat 0.6 default. Not applied in this pass because
  it is a new data-ingestion path plus a full pilot rebuild and revalidation. **Highest-value remaining real-data
  upgrade.**

### D-19 — RADAR INTEGRATION BLOCKED BY DATA ACCESS (final exhaustive search, 2026-09-14)

**Status: BLOCKED. Not by architecture — by data access and licensing.** A final search targeting *only*
genuinely radar-derived rainfall for Mumbai (explicitly excluding satellite QPE, NWP, gauges and colour
tiles) checked nine avenues. **No genuinely radar-derived, Mumbai-covering, legally-usable,
programmatically-obtainable rainfall dataset with the temporal continuity a 0–3 h nowcast requires
exists.** Nothing was implemented as a substitute, deliberately.

**New, actionable facts about the IMD route (this is the closest path to viable):**
- Mumbai is served by **two** IMD radars: a **Colaba S-band** and a **Veravali C-band**.
- `dsp.imdpune.gov.in` (fetched) routes radar via *"[For RADAR Data, Click Here](https://radarapi.imd.gov.in)"*,
  contact **radarlab@gmail.com**, **+91 (11) 2434-4281**.
- **Registration is open to individuals** — not MoU-gated as previously assumed. The registration form lists
  category *"S - Students (Students (up to post-graduation level)…)"* and *"I - Research and Educational
  Institutes"*, and states: *"Registration will be complete after Email Verification, one-time submission of
  Identity Card and/or Certificate of Undertaking."*
- **Radar is chargeable.** IMD's free-data page lists only All-India monthly/seasonal temperature, All-India
  monthly/seasonal rainfall, and cyclonic-system frequency. The cost-estimate page's data types are
  *"Surface Rainfall Autographic Upper Air (PB) Upper Air (RS) Upper Air (RW) Agromet Radiation"* — **no
  radar row**; radar is diverted to the email contact above.
- **The specific technical blocker is a broken TLS certificate chain on `radarapi.imd.gov.in`** (error:
  *"unable to verify the first certificate"*), not a 404. The radar product catalogue (volume scan / CAPPI /
  MAX-Z / rain rate), formats and prices therefore remain **UNVERIFIED**.

**Two candidates that are genuinely radar but were still rejected, with reasons:**
- **CEDA INCOMPASS v2** (`catalogue.ceda.ac.uk/uuid/b28633ddc0f44d77a6aa81ad7bd66285`, DOI
  10.5285/b28633ddc0f44d77a6aa81ad7bd66285). Genuinely **IMD DWR-derived and Mumbai is explicitly one of its
  sites**, licensed **OGL v3** (open), downloadable with a free CEDA account. **Rejected because it is a
  convective-cell OBJECT TABLE, not a field**: its variables are *"datetime …, CTH [m], size [km], latitude
  [N], longitude [E], cell 2 km mean reflectivity [dBZ]"*, BADC-CSV, 14 May–30 Sep 2016 only. Reconstructing
  a `[T, ny, nx]` rainfall field from cell centroids and a single mean reflectivity per cell would require
  **inventing the intra-cell spatial structure** — that is fabrication, and it is why this was not used
  despite being the only open-licensed genuine Mumbai radar product found.
- **GPM DPR (2ADPR)** — genuinely a spaceborne **precipitation radar** (Ku 245 km swath / Ka 125 km), truly
  quantitative (`precipRateESurface2`, mm/hr), 5 km, openly licensed under EOSDIS guidance, scriptable from
  GES DISC with a free Earthdata Login. **Rejected for this requirement on two independent grounds:**
  (a) **revisit** — the GPM Core Observatory is non-sun-synchronous and crosses a fixed point only of order
  ~10 times a *month*, i.e. gaps of days; it cannot drive a continuous 0–3 h nowcast under any scheme;
  (b) **resolution** — at 5 km, the 2.4 × 2.55 km pilot is *smaller than a single DPR footprint*, so it would
  again contribute no intra-pilot spatial variation, and most overpasses miss the pilot entirely.
  **Deliberately not implemented:** a second credential-gated, untestable downloader that can satisfy neither
  the nowcast horizon nor the spatial requirement is complexity without a requirement to justify it
  (CLAUDE.md rule 7). Recorded here so the option is visible and can be overruled, not lost.

**Also checked and excluded:** MOSDAC TERLS DWR (real 1 km volumetric C-band radar, but **Thumba, Kerala** —
wrong city); IITM Pune / NARL Gadanki (radars at Solapur and Gadanki, no public data portal found);
data.gov.in (**HTTP 403 to automated fetch ×3 — UNVERIFIED**; no radar dataset surfaced by search, and IMD's
own page indicates data.gov.in mirrors its *free* set, which excludes radar; the only Indian-radar items on
open repositories are station-coordinate metadata, not measurements); WMO radar-data exchange (a standards
and coordination activity — *"developing a data model and standardized format for international exchange"* —
with no downloadable archive and no India holdings); TRMM PR 2A25 (genuine radar but mission **ended 2015**,
same sparse-revisit problem).

**Consequence for the product:** the completed spatial-rainfall architecture (D-15 resolved, D-16) is
preserved as a **ready integration boundary**. `IMDRadarNowcastProvider` remains the honest, inert place a
real feed attaches; its docstring's former "the engine could not ingest it anyway" blocker is now struck
through, because that is no longer true. **The remaining blockers are entirely data access and licensing.**

**The single human action that would unblock this:** register at
`https://dsp.imdpune.gov.in/home_registration_form.php` under category **"S - Students"** or **"I - Research
and Educational Institutes"** (email verification + Identity Card / Certificate of Undertaking), then email
**radarlab@gmail.com** requesting archived **Mumbai DWR (Colaba S-band and/or Veravali C-band)** volume scans
or CAPPI/MAX-Z for a named monsoon period, stating format and intended academic use, and asking for a cost
estimate. This is a human action requiring an identity document and payment; it cannot be automated, and
**no harvesting of IMD imagery has been started as a workaround** — consistent with D-14.

## Decisions deliberately NOT made
- Whether to extend from Pilot A to box E (decide after measuring runtime at 10 m).
- Depth severity thresholds — provisional bands (5/15/30/60 cm) are carried over from the Antigravity code **as working thresholds, not cited guidance**; label them so until a source is found.
- Any live IMD integration.
- **OPEN LICENCE QUESTION (2026-09-10):** whether IMD's public radar imagery at `mausam.imd.gov.in/Radar/`
  may be programmatically retrieved/retained for non-commercial research. `copyRightPolicy.php` and
  `termscondition.php` both 404, while `/responsive/disclaimer.php` asserts "© Copyright 2026 India
  Meteorological Department" with **no licence grant**; the official supply route (`radarapi.imd.gov.in`,
  Radar Division, `radarlab@gmail.com`) requires an account, a data request and **payment**, and its terms
  are behind login. **No capture or harvesting job has been started, deliberately.** Resolving this needs a
  written enquiry to the Radar Division — a human action. Do not start harvesting before it is answered.

### D-20 — Experimental radar-IMAGE-derived rainfall from the public Mumbai-Veravali SRI product (2026-09-18)
- **Decision (project direction, 2026-09-18):** build `IMDVeravaliSRIImageProvider`, which decodes IMD's public SRI GIF into a `[T, ny, nx]` field labelled `RADAR_IMAGE_DERIVED_ESTIMATE`. This partially supersedes D-19's "do not harvest" — the Radar Division enquiry is still unanswered.
- **Basis:** api.imd.gov.in has no radar API (IMD confirmed); the image carries its own legend, tick-marked axes and timestamp, which make a legend-bin decode and tick-fitted georeference checkable (evidence: `docs/RADAR_SRI_IMAGE.md`).
- **Limits kept explicit:** not IMD QPE, not validated, legend-bin precision only, two bins excluded, one frame so no extrapolation (3-hour persistence), native resolution unknown, reuse terms unconfirmed (image not committed; low-frequency cached fetch).
- **SR-01 remains PARTIAL:** a genuine numerical IMD radar product is still the goal (DSP request).

### D-21 — Experimental radar-image nowcast + source-aware horizon (2026-09-21)
- **Decision:** add a gated Lagrangian-persistence nowcast (single-vector cross-correlation on the DECODED rain
  field, rigid advection, ≤ 30 min) and record which source drives each forecast period
  (radar 0 → nowcast 5–30 → ECMWF or persistence 30–180). Evidence, method, refusals: `docs/RADAR_NOWCAST_STATUS.md`.
- **Basis:** the public animation GIF carries one timestamp for 19 frames (found to be 19 identical stale copies),
  so FloodNet keeps its own timestamped frame history instead. Dense optical flow was rejected as over-precise
  for legend-bin input.
- **Honest status:** logic-tested on a generated stand-in; not yet run on two consecutive real scans; no skill
  claimed; never called an IMD nowcast. SR-01 remains PARTIAL.

### D-22 — Tide boundary: [UNDECIDED], not implemented (2026-09-21)
- No synthetic tide. Sources audited (INCOIS view-only; Survey of India PDFs need written permission; MCGM list
  not machine-readable; no Mumbai station in UHSLC/IOC; PSMSL monthly only; FES2022/TPXO need registration and
  harbour validation). Summary in `docs/TECHNOLOGY_EXPLAINER.md`. Revisit after written permission / FES2022
  registration; the drainage solver's outfall boundary would also need a stated design first.

### D-23 — HackMatrix 5.0 MISC-04 requirements treated as a SUPPLEMENT to the original flood-nowcasting problem statement (2026-10-10)
- **Decision (project direction):** the MISC-04 "Local Disaster Warning and Response Coordination" requirements are
  tracked separately in `docs/MISC04_REQUIREMENTS.md` and added around the existing flood engine. The original problem statement stays the
  authority for the flood-nowcasting scope.
- **[UNVERIFIED]:** whether MISC-04 supplements or replaces the original flood-nowcasting problem statement. Only a team summary of a screenshot is
  available; the organisers have not confirmed. Revisit when they answer.

### D-24 — Travel time from ASSUMED speeds by road class (2026-10-10)
- **Decision:** arrival times use fixed speeds per OSM `highway` class (`config.ASSUMED_SPEED_KMH`: trunk 30, primary
  25, secondary/tertiary 20, residential/unclassified 15, service/living_street 10 km/h; pedestrian 5 km/h). No
  slowdown in water, no traffic, the vehicle never waits for water to recede.
- **Basis:** there was no travel-time model at all (the old "fastest" route is distance only), and no live traffic
  source exists for the pilot. These values are **not measured or cited**; every API response carries them under
  `assumptions` tagged ASSUMED.

### D-25 — Five road-access statuses and their rules (2026-10-10)
- **Decision:** CLOSED > UNKNOWN > AT_RISK > UNCERTAIN > USABLE, first match wins (`floodnet/access/road_status.py`).
  AT_RISK = forecast depth during the vehicle's minutes on the road reaches the existing `VEHICLE_LIMIT_CM`; not
  routed through. UNCERTAIN = within 20 cm below the limit (the DTM's ~0.2 m vertical precision) or crossing DEM
  cells flagged unreliable. UNKNOWN = no model grid cell (33 pilot segments that were previously shown as dry) or
  beyond the forecast horizon. Route cost multipliers UNCERTAIN ×2.0, UNKNOWN ×1.5 are ASSUMED weights.
- **Basis:** the categories come from the team's MISC-04 summary; the thresholds reuse values the project already
  states (vehicle limits, DTM precision, the DEM reliability mask) instead of inventing new ones. Not calibrated.

### D-26 — Road events: three timestamps, no future information, closures authoritative (2026-10-10)
- **Decision:** each closure/reopen event carries `received_t_min`, `applies_from_t_min` and the server's
  `processed_at`. A decision at time t uses only events received by t; later ones are returned as
  `events_ignored_future`. A confirmed closure is only lifted by a reopen event, never by a dry forecast.
- **Storage:** the event log lives in the browser and is sent with each request, like the self-contained run bundle,
  so it works on Vercel without a database (consistent with D-10). It is cleared when a new run starts
  (superseded 2026-10-11 by D-33: the log now belongs to the incident and survives forecast changes).

### D-27 — Response resources: REAL locations, SIMULATED numbers, no database (2026-10-10)
- **Decision:** the demo settlements sit on active MCGM Flooding Spots and the shelters and bases on OSM buildings
  (`floodnet/response/fixtures.py`); every headcount, capacity, team size and on-site time is SIMULATED and labelled
  so in the API, the UI and the map. The assignment ledger and the event logs are held by the browser and sent with
  each request (consistent with D-10 and D-26).
- **Basis:** no real shelter, population or resource data is available to the team; MCGM's `AFFECT_POPULATION` for
  Vaccharaj Lane is the one real attribute used, labelled as a planning figure.

### D-28 — Allocation: transparent greedy by declared priority, earliest completion first (2026-10-10)
- **Decision:** settlements are served in declared priority order (critical > high > medium > low, then larger
  remaining need); for each, the (team, vehicle, shelter) mission that finishes first is chosen; seats and shelter
  places are reserved; shuttle trips follow. Not an optimiser, no AI model. Invariants (no double-booking, no shelter
  overflow) are checked on every plan.
- **Basis:** the MISC-04 summary explicitly accepts a transparent rule-based first implementation; it is explainable
  to judges and testable. Priority is an operator declaration, never flood depth alone.

### D-29 — Replanning rules that preserve history (2026-10-10)
- **Decision:** COMPLETED is history; DISPATCHED / IN_PROGRESS are never re-allocated but re-routed from the estimated
  position (redirected to another shelter if theirs fills; flagged for an operator decision if no shelter has room;
  cancelled by the planner, with the reason, only when no route exists at all); ACCEPTED is kept if still feasible;
  PROPOSED is recomputed with stable ids. Events are applied only if received by the decision time.
- **Basis:** the MISC-04 requirement that completed actions and appropriate commitments survive a replan, and that
  every change is explained. Details and the demo replay: `docs/RESPONSE_PLANNER.md`.

### D-30 — Evaluation against an independent outcome run, never the forecast run (2026-10-10)
- **Decision:** the response planner and the arrival-aware router are evaluated by planning on one FloodNet run and
  judging routes against a *different* run as the outcome (`floodnet/response/evaluate.py`), with a deterministic
  scripted replay run under both routing methods, and road-access prediction scored on every road-window. Same-run
  comparisons (`access_scan.py`) are kept as sanity checks only.
- **Basis:** the same run would make arrival-aware routing right by construction. The outcome is still a simulation,
  so the results are SIMULATED evidence of how the methods differ, not real-world accuracy (`VALIDATION.md`).

### D-31 — WhatsApp TEST alerts through Twilio, operator-triggered only (2026-10-10)
- **Decision:** team-requested WhatsApp notifications via the Twilio WhatsApp sandbox (`floodnet/alerts/whatsapp.py`):
  sent only on a button press, only to numbers in the backend `.env`, always prefixed "FloodNet TEST ALERT
  (simulation, not an official warning)", de-duplicated and rate-limited. No automatic sending, no public broadcast.
- **Basis:** the project never issues official warnings (README, CAP drafts are drafts). SMS was rejected for the
  hackathon because Indian business SMS needs TRAI DLT registration; Telegram remains an option. `docs/ALERTS_WHATSAPP.md`.

### D-32 — Report reconciliation: source tiers, lifting rule, staleness as a flag (2026-10-11)
- **Decision:** road events and resource events carry an optional `source_tier` (AUTHORITY > OPERATOR = FIELD_TEAM >
  PUBLIC; absent = OPERATOR, so older payloads behave as before). A reopen lifts a closure only if it was received no
  earlier than the closure, describes a later moment (a full tie keeps the closure) and comes from an equal or higher
  tier. PUBLIC reports are never binding: a PUBLIC closure makes the road UNCERTAIN; a PUBLIC reopen is logged as
  contesting the closure and is not applied. Nothing expires: a report older than its tier's threshold (AUTHORITY 180,
  OPERATOR / FIELD_TEAM 90, PUBLIC 45 min, ASSUMED and uncalibrated) is flagged STALE and the operator is asked to
  confirm; routing never changes on a timer. Stale resource reports keep their last known value. Same-moment resource
  conflicts resolve pessimistically and are logged. Every event gets a derived status (ACTIVE, LIFTED, APPLIED,
  NO_EFFECT, REJECTED_LOWER_TIER, UNCONFIRMED, FUTURE) and a one-line explanation; ordering is deterministic with
  `event_id` as the final tie-break. The browser mirror (`frontend-react/src/lib/roadAccess.js`) implements the same
  rule and is tested on the same cases.
- **Basis:** independent review of the earlier proposal found counterexamples: a reopen without a reference would never
  lift a closure; auto-expiry would silently reopen a confirmed closure inside the 3-hour horizon; "conflicted ->
  uncertain" would let a rumour make a confirmed closure routable; excluding stale shelter reports zeroes capacity on
  a timer; the old "latest applies-from wins" rule reopened a road when a late, back-dated closure arrived after an
  unrelated reopen. These are **project policies for a demonstration system, not claims about any agency's legal
  authority**. Tests: `backend/tests/test_report_reconciliation.py`, `frontend-react/src/lib/roadReports.test.mjs`.

### D-33 — Incident state survives forecast changes; ledger-carried plan version (2026-10-11)
- **Decision:** road reports, resource reports, the assignment ledger, plan logs and plan history belong to the
  incident. A new forecast run (scenario re-run, live / ECMWF refresh, compare run) keeps them, drops only the plan
  result computed from the old frames, marks the plan "forecast changed" and proposes a replan; nothing is replanned
  automatically. Only an explicit **New incident** (with confirmation) clears them. The planner cancels a mission only
  while nobody is on board; an IN_PROGRESS mission with no feasible route is kept and flagged for an operator
  decision. Each assignment carries the `plan_version` that last wrote it; the API refuses a ledger newer than the
  version the client claims (HTTP 422), and the browser discards a plan response that arrives after a newer forecast
  or plan. The incident is saved to this browser's localStorage (one browser, one origin, a few MB, cleared with site
  data, not shared between operators); the exported JSON files remain the record.
- **Basis:** the browser previously wiped the incident on every run (`applyRun`, `runCompare`), which made "forecast
  refresh -> replan" impossible. A stateless server cannot hold an optimistic-concurrency counter, so the version has
  to travel with the ledger. Server-side storage was rejected: no database by design (D-10), Vercel functions have no
  shared state. Limitation: for live / ECMWF runs the simulation minutes are not re-based to the new pull time.
  Tests: `frontend-react/src/lib/incident.test.mjs`, `backend/tests/test_response_plan_concurrency.py`.

### D-34 — Team capabilities are a hard constraint; greedy allocation kept (2026-10-11)
- **Decision:** settlements declare `required_capabilities` (the demo fixtures require "evacuation", which both teams
  have, so the demo plan is unchanged); a team is assigned only if its capabilities include all of them. When no team
  qualifies the unmet reason names the capability; otherwise the existing reason categories are kept, with the
  qualifying teams listed. Ties on completion time and people prefer the team with the fewest surplus capabilities
  (keep specialists free), then team, vehicle and shelter id.
- **Basis:** the planner previously ignored `capabilities` entirely. At 2 teams x 2 vehicles x 2 shelters x 4
  settlements a per-round assignment solver or MILP gives no material gain and adds a dependency; the greedy choice
  stays explainable line by line. Tests: `backend/tests/test_response_capabilities.py`.

### D-35 — Seeded, held-out evaluation sequences and evidence exports (2026-10-11)
- **Decision:** `generate_script(seed, ...)` produces reproducible incident sequences (closures, vehicle loss, shelter and
  settlement updates); development seeds 0-19 and held-out seeds 100-119 are disjoint; `evaluate_many` reports per-seed
  results with mean / min / max / standard deviation; every replan step records the inputs it used and
  `check_information_availability` verifies that none was received after the decision time; `export_rows` /
  `export_csv` write one row per road x decision time x lead time, tagged SIMULATED. The default API evaluation is
  unchanged (the scripted demo).
- **Basis:** one hand-written script cannot support a comparison claim. Outcome labels are simulations, so every metric
  remains a method comparison on simulated outcomes, never real-world flood-prediction accuracy (`VALIDATION.md`).
  Tests: `backend/tests/test_response_eval_seeds.py`.

### D-36 — Rainfall input honesty: HRES hourly forecast, timing and staleness on every source (2026-10-11)
- **Decision:** the Open-Meteo request asks for `models=ecmwf_ifs` (IFS HRES 9 km, native hourly; returned cell about
  2.6 km from the pilot) instead of the default 0.25 degree, 3-hourly open-data grid point about 16 km away. Both IMD
  and ECMWF results carry a timing block (observation / issue time, validity window, received time, time basis, units,
  accumulation interval, age, FRESH / AGING / STALE / UNKNOWN). The IMD path keeps its maths and labels but states
  that the 24-hour total divided by 24 is an antecedent indicator held constant as an assumption, not a nowcast and not
  the current rate. Failover order is unchanged. The proposed 0.5x / 1x / 2x "ensemble" was **rejected**: no
  literature supports a fixed multiplier band, and location / timing error dominates at pilot scale.
- **Basis:** verified Open-Meteo responses on 2026-10-10/11; IMD API reference (no units or interval documented for
  AWS rainfall; Mumbai nowcast rows absent at probe time). Unresolved and therefore not used quantitatively: the IMD
  AWS `RAINFALL` interval, the Mumbai nowcast ids. `docs/ECMWF_OPENMETEO_AUDIT.md`, `docs/LIVE_RAINFALL_AUDIT.md` §13.
  Tests: `backend/tests/test_rainfall_time_and_staleness.py`.

### D-37 — Six task pages, existing colour identity, WCAG contrast (2026-10-11)
- **Decision:** the control centre is six pages selected by the URL hash, each with one task: Overview, Flood forecast
  (setup and result together), Road access, Response plan, Alerts & reports, Replay & evaluation; a shared top bar shows
  the pilot area, rainfall source with freshness, decision time, run and status; the map stays mounted. Departure from
  the seven-page proposal: "Forecast setup" has no task of its own and operators go back and forth between setup and
  result. The warm ivory / burgundy / ochre palette is kept; only text colours were darkened within the same hue to
  reach 4.5:1 (for example `--text-faint` #9C9184 -> #756A5E), status chips became tinted with a glyph, controls got a
  3:1 boundary and a 2 px focus outline. Map strokes are unchanged.
- **Basis:** usability feedback that the dashboard was intimidating; an independent UX review against NN/g progressive
  disclosure and WCAG 2.2 (1.4.1, 1.4.3, 1.4.11, 2.4.7). Tests: `frontend-react/src/lib/pages.test.mjs`,
  `frontend-react/src/lib/contrast.test.mjs`.

### D-38 — Live operations need one shared store: a local SQLite file; Vercel blocked (2026-10-11)
- **Decision:** drivers, dispatchers and residents must see the same incident, so trips, GPS fixes, driver and
  responder reports, the resource registry, subscribers, alerts and public pages live in one SQLite file
  (`ops.sqlite3` in `FLOODNET_STATE_DIR`, Python stdlib, append-only audit table). D-10 still holds for the forecast
  engine. On Vercel `/tmp` is per instance and not persistent, so the ops endpoints answer 503 there.
- **Open:** a managed database for the deployed site (provider and cost undecided). Design: `docs/LIVE_OPERATIONS.md`.

### D-39 — Role-based access for live operations (2026-10-11)
- **Decision:** dispatcher, responder (field team / shelter staff), driver (one trip) and public roles. Tokens are
  random, shown once, stored as SHA-256 hashes. With no `FLOODNET_DISPATCHER_TOKEN` configured, dispatcher actions
  are allowed only from the local machine (loopback) and never on Vercel. The existing `POST /api/alerts/phone/send`
  gets the same dispatcher check: during browser testing on 2026-10-11 it was called without any user action and it
  had no access control (demonstrated reason). Tests: `backend/tests/test_ops_auth.py`.

### D-40 — Navigation: FloodNet's router stays the authority; mobile-web driver page (2026-10-11)
- **Decision:** no Google or Mapbox navigation SDK: neither has a web SDK, neither accepts our closures or arrival-time
  depths (Google's avoid options are tolls/highways/ferries/indoor; Mapbox excludes at most 50 points, best-effort),
  and their terms tie route results to their own maps. Turn instructions are generated from our own path geometry
  (bearing change and street-name change). The driver page shares GPS only while open and in the foreground
  (W3C Geolocation; WebKit bug 193946) with Screen Wake Lock, and polls every 15 s. Background tracking needs a native
  app: not built. Evidence: `docs/LIVE_OPERATIONS_RESEARCH.md` §5.

### D-41 — A rescue trip is a dispatched assignment (2026-10-11)
- **Decision:** no second trip model. The server keeps execution facts for an assignment (stage, positions, driver
  reports, reroutes); the dispatcher's ledger keeps allocation; ledger status follows the trip stage through one pure
  function (stage table in `docs/LIVE_OPERATIONS.md` §4). Tests: `backend/tests/test_ops_trips.py`.

### D-42 — Route-impact reassessment, reroute proposals, escalation, vehicle restrictions (2026-10-11)
- **Decision:** after any material event the remaining route of each active trip is re-checked from the latest
  credible position (fresh on-route GPS, else the labelled planned estimate). Blocking findings give a REQUIRED proposal
  or, with no feasible route, ESCALATE to a person; non-blocking improvements are OPTIONAL only above ASSUMED
  thresholds (3 min, or 2 fewer uncertain roads for at most 2 min); identical and rejected proposals are not repeated;
  optional ones have a 5-minute cooldown. A dispatcher accepts, the driver acknowledges, everything is audited. Road
  closures may name `vehicle_types` (a restriction for those vehicles only). GPS FRESH <= 30 s, STALE <= 120 s, then
  LOST (ASSUMED). Basis: Green et al. 2017 (+200% for an uninformed responder), Zhengzhou 2021 investigation
  (warning-response linkage). Tests: `backend/tests/test_ops_impact.py`, `test_road_restrictions.py`.

### D-43 — Resource registry with NIMS-inspired status (2026-10-11)
- **Decision:** teams, vehicles and shelters carry AVAILABLE / ASSIGNED / OUT_OF_SERVICE (MECHANICAL, REST, PERSONNEL),
  capabilities, capacity and the time and source of the last verified update. Needs state a required capability, never a
  unit. Updates from registered responders are validated, timestamped, flagged stale, and out-of-order updates are kept
  but not applied. The planner uses the registry only when asked (`resources_source: "registry"`); the demo fixtures stay
  the default. Not NIMS compliance. Seed values are SIMULATED. Tests: `backend/tests/test_ops_registry.py`.

### D-44 — Subscriber SMS/WhatsApp alerts: consent, targeting, review, simulated provider by default (2026-10-11)
- **Decision:** subscribers with recorded consent per channel, encrypted numbers (Fernet; optional dependency
  `cryptography`, extra `ops`), geographic targeting (within the subscriber radius, or within 600 m by road; ASSUMED),
  human review before any send, idempotent per-delivery lifecycle, forward-only provider statuses, signed callbacks,
  STOP handling, material-change and 30-minute cooldown rules. The SIMULATED provider is the default. Real Indian SMS
  needs DLT registration (TRAI TCCCPR 2025) and WhatsApp needs approved templates: BLOCKED; live sends need
  `FLOODNET_LIVE_MESSAGING=1` and an allow-list. Tests: `backend/tests/test_messaging_*.py`.
- **Storage detail:** the encrypted number, its keyed hash, last four digits and the subscriber's area live in a
  separate `pii_vault` table of the same SQLite file, outside the append-only audit, so that erasure (DPDP withdrawal)
  really removes them; the audit only ever sees subscriber ids. Demonstration subscribers use the impossible range
  `+9100000xxxxx` and are never sent to a live provider.

### D-45 — Public alert page per incident (2026-10-11)
- **Decision:** one unguessable link per alert (128-bit), the same for every recipient; a whitelisted, versioned
  snapshot; FloodNet's own status words ("Flooding possible", "Flooding likely", "No longer active"), never official
  level names; text location before the map; verified contacts only (112, BMC 1916); generic link preview.
  Tests: `backend/tests/test_public_alerts.py`.

### D-46 — Official warnings: consume, draft, never originate (2026-10-11)
- **Decision:** FloodNet reads the public SACHET CAP feed and shows official alerts apart from its own advisories; CAP
  drafts stay `status=Draft`; FloodNet does not originate official warnings, contribute to SACHET or use cell broadcast.
  The authorised path (an SDMA or alert-generating agency) is documented in `docs/OFFICIAL_WARNING_PATHWAY.md`.

### D-47 — Eight task pages and collapsible panels (2026-10-11)
- **Decision:** Situation, Forecast, Roads, Response, Trips, Reports, Public alerts, Evidence (hash keys of D-37 kept,
  new keys `trips` and `alerts`); the sidebar and the work panel slide in and out (CSS transitions, 240 ms, reduced
  motion respected), the map takes the space; narrow screens use a drawer. Driver and public pages are separate
  screens. Tests: `frontend-react/src/lib/pages.test.mjs`, `panels.test.mjs`.

### D-48 — Live operations hardening after independent QA and the browser walkthrough (2026-10-11)
- **Decision:** the QA fixes listed in `docs/LIVE_OPERATIONS.md` §9 are part of the live-operations design: loopback
  dispatcher only with a local Host and Origin; stage-aware positions; proposals de-duplicated by finding; approvals
  bound to the reviewed version; restrictions never published as public closures. Trips and field reports carry the
  browser incident id (`INC-…`, `lib/incident.js`) because assignment ids repeat across incidents. The forecast shared
  with live operations names its rainfall source (`SYNTHETIC rainfall: … on REAL pilot data`), and the public page shows
  a demonstration banner for synthetic, historical or test inputs.
  Tests: `backend/tests/test_qa_live_ops.py`, `frontend-react/src/lib/ops.test.mjs`, `incident.test.mjs`.

### D-49 — Rescue trips from the Roads page, route highlight and a labelled ETA (2026-10-11)
- **Decision:** the Roads page starts **direct trips** (one route from A to B for a chosen vehicle) next to the
  planner's mission trips (D-41). Lifecycle PLANNED (dispatcher pressed Start Trip) → ACCEPTED (driver) → EN_ROUTE →
  ARRIVED → COMPLETED, with ABORTED (dispatcher, with a reason) and escalation as exceptions. The server routes,
  checks and times everything from two points and the vehicle; nothing the browser drew is trusted. A registered
  vehicle can be on one active trip; Start Trip is idempotent on a per-preview key (a double click is one trip).
  Direct trips use the same impact rules (D-42): closures and forecast changes trigger a reassessment from the latest
  credible position, REQUIRED/OPTIONAL proposals, escalation when nothing is feasible, history kept.
- **Fix found while testing (rerouting loop):** after a reroute was accepted, the vehicle's last GPS fix on the road
  it was still driving read as "off route" because the new route began at the next junction, which raised a second
  REQUIRED proposal. The accepted route now keeps the road being driven (`impact.resolve_position` → `on_segment`).
- **ETA** = ASSUMED ROAD SPEEDS + MODELLED FLOOD DELAY + traffic UNKNOWN (`backend/floodnet/ops/eta.py`). The flood
  delay caps the road speed with the Pregnolato et al. (2017) depth-disruption curve on the forecast depth for the
  minutes the vehicle is on each road, re-checks every road at the delayed minutes, and lists roads whose status
  changes. The curve was fitted for 2WD cars; using it for trucks and ambulances, and the 5 km/h floor, are
  ASSUMPTIONS. It is not applied to people on foot. The route is still chosen on free-flow times. Live traffic is
  UNKNOWN: no feed is configured. Research (`docs/RESEARCH_NAVIGATION_TRAFFIC.md`) found TomTom the only provider
  whose terms appear to allow showing its traffic with our own map and route (key needed, REST terms unconfirmed);
  Google Routes content must not be used with a non-Google map and its terms exclude high-risk uses.
- **Route visibility:** the selected route is a blue core in a navy outline and a white halo (`lib/rescueTrip.js`
  `ROUTE_STYLE`; hue at least 30° from every flood, status and baseline colour, tested), alternatives are thin slate
  dashes, hazards on the route keep their status colour, the next road of a selected active trip is marked, and a
  route legend appears on the map. The FloodNet palette is unchanged; the blue appears only as the route line.
- **Freshness labels** on every preview: forecast (SIMULATED / MODELLED / STALE / UNKNOWN), closures (operations log,
  count), traffic (UNKNOWN), vehicle status (registry, SIMULATED seed, STALE).
  Tests: `backend/tests/test_ops_direct_trips.py`, `test_ops_eta.py`; `frontend-react/src/lib/rescueTrip.test.mjs`,
  `driver.test.mjs`, `ops.test.mjs`. Browser walkthrough: `docs/validation/roads/`.

### D-50 — Registry registration, import, responder links and candidate shelters (2026-10-11)
- **Decision:** the dispatcher registers or imports (JSON/CSV, previewed, committed all-or-nothing) teams, vehicles
  and shelters, validated against the planner's capability words, the vehicle types with an ASSUMED depth limit and
  the pilot area. Every record carries its source, observed and received times and freshness CURRENT / STALE (older
  than 60 min) / UNKNOWN; seed records stay SIMULATED. Responder links are scoped and revocable. A **volunteer** role
  can only submit candidate shelters: SUBMITTED → PERMISSION_CONFIRMED → SUITABILITY_CHECKED (with the modelled depth
  on the nearest road within 150 m, or UNKNOWN) → CONFIRMED or REJECTED; a candidate never reaches the planner or the
  shelter list before CONFIRMED. A vehicle on an active direct trip (D-49) reaches the planner as unavailable. New
  page: Resources (`#/resources`). Not NIMS compliance; no agency database is connected.
  Tests: `backend/tests/test_registry_*.py`, `frontend-react/src/lib/registry.test.mjs`, `pages.test.mjs`.

### D-51 — Development test recipients, test sends, subscriber editing, interactive public map (2026-10-11)
- **Decision:** development test recipients come only from the server variable `FLOODNET_DEV_TEST_RECIPIENTS`; a
  dispatcher action seeds them as encrypted, consented subscribers; seeding is refused and the list ignored in
  production. A test send goes only to them, with fixed previewed text (hash-checked), one idempotency key per
  confirmation, retry only when the provider never took the message, and the live gate: SIMULATED unless
  `FLOODNET_LIVE_MESSAGING=1`, provider credentials and a recipient who is either a designated development test
  recipient or on `FLOODNET_MESSAGING_ALLOWLIST`. A test SMS has no link, so it needs no public base URL (advisories
  do). The send must quote the mode its preview showed (a real message only after a LIVE preview; a retry never changes
  mode), and a LIVE send's status can be asked from Twilio (`POST /api/ops/test-sends/{id}/refresh-status`) because a
  local server cannot receive callbacks. Production is recognised only by `VERCEL=1` or `FLOODNET_ENV=production`
  (fail-open elsewhere: set `FLOODNET_ENV=production` on any public deployment). Delivery states keep ACCEPTED/SENT
  apart from DELIVERED, FAILED and UNKNOWN. Subscribers are editable (language, area, channels with consent or a
  recorded withdrawal); the number is not. The MSG91 webhook answers 200 to every authenticated request, reads both
  report shapes, never stores the recipient number and matches on CRQID. The public page adds a plain summary with
  its time horizon, separate evidence types, cached SACHET items quoted as received, a tap/zoom map, and a street map
  loaded only on request; no sign-up form on the page (research: `docs/RESEARCH_ALERTING_AND_INTERNATIONAL.md`).
  Tests: `backend/tests/test_messaging_{devtest,devtest_api,webhooks}.py`, `src/api/messaging.test.mjs`,
  `src/lib/publicAlert.test.mjs`.

### D-52 — Hardening after the second independent QA review (2026-10-11)
- **Decision:** the 19 findings of the review of D-49..D-51 (2 HIGH, 8 MEDIUM, 9 LOW; 17 reproduced by failing tests)
  are fixed or explicitly accepted (`docs/LIVE_OPERATIONS.md` §11). Key rules: a direct trip that the driver has not
  reported EN_ROUTE is assumed not to have moved; its schedule runs from the EN_ROUTE report; ARRIVED trips get no
  reroutes; a closure on the road being driven is caught; a vehicle is on one active trip of any kind; plans use the
  registry (dispatcher only) when live operations are on; freshness labels never call unconfirmed or cached data
  current; a test send is bound to its preview's mode.
  Tests: `backend/tests/test_qa2_*.py`, `test_messaging_sms_status.py`; `frontend-react/src/lib/rescueTrip.test.mjs`,
  `src/api/messaging.test.mjs`.

### D-53 — Run Forecast: reuse identical physics, cheaper response building (2026-10-11)
- **Decision:** a simulation is reused only when every physics input is identical (pilot objects, blockage, horizon,
  frame step, rainfall scenario id/name/provenance/time axis/values/field; at most 4 kept). Providers are still asked on
  every request and own freshness; source metadata is always the current provider's. Bundle arrays use zlib level 6
  (lossless; +2.7 % bytes). The per-street series discarded by `POST /api/simulate` is no longer built. The static
  segment context is cached per pilot. No physics, resolution, time step, horizon, label or IMD-fallback change.
  Measured (shared, noisy machine): UI default repeat 0.681 → 0.178 s; repeated live/ECMWF runs 8-39 s → 0.1-0.3 s;
  a first run of new rainfall is unchanged (physics, about 8-19 s for 180 min on a quiet machine).
  Basis: `docs/PERFORMANCE_OPTIMIZATION.md` "2026-10-11 Run Forecast profiling"; tests `backend/tests/test_perf_run_forecast.py`.

### D-54 — Navigation mode on the Roads page, and live traffic in the ETA (2026-10-11)
- **Navigation mode:** after a route preview, "Start navigation" starts the direct trip (D-49) and this device follows
  it over the map: next turn, distance to it, the instruction after, remaining time and distance, flood hazards on
  the remaining route, a safer-route offer when a road ahead closes or floods (accepting it is the dispatcher's
  decision made on this device, then acknowledged), escalation when no safer route exists, and Exit navigation,
  which keeps the trip. Turn instructions come from FloodNet's own router (D-40); no outside navigation SDK (research:
  `docs/RESEARCH_NAVIGATION_TRAFFIC.md`). Position is the device's own GPS, posted at most every 15 s and only while
  the page is open; with no fix the screen says so and the server's planned estimate is used. Nothing is simulated
  in navigation mode. Risk information shows what it rests on (SIMULATED / MODELLED / STALE / UNKNOWN forecast) and
  says when it may be outdated. "Dispatch to a driver instead" keeps the phone-link flow.
- **Live traffic:** TomTom Flow Segment Data (`backend/floodnet/traffic/tomtom.py`, key `TOMTOM_API_KEY` on the
  server only, 60 s cache, at most 40 sampled roads per route). Traffic changes the ETA only: a sampled road's
  assumed travel time is scaled by free-flow / current speed; on a road with both flood water and traffic the slower
  cause governs (no double counting) and both warnings are shown. The route is still chosen by the flood rules
  (D-24..D-26); live traffic never makes a road the flood rules call unsafe the recommended one, and traffic never
  shortens an ETA. Without a key or on provider failure the panel says "Live traffic unavailable — using estimated
  travel time" and the ETA is the D-49 estimate. Level thresholds (normal ≥ 0.8, moderate ≥ 0.5, heavy < 0.5 of
  free-flow speed) are ASSUMED. The map draws traffic as a dotted line over the route so flood status colours stay
  visible. No TomTom key was available during this work: the live path is IMPLEMENTED BUT UNVERIFIED; the fallback
  path is tested and browser-checked. Traffic data is never labelled live unless the provider answered.
  Tests: `backend/tests/test_traffic_*.py`, `test_ops_eta.py`, `test_ops_direct_trips.py`;
  `frontend-react/src/lib/navigation.test.mjs`, `rescueTrip.test.mjs`. Browser: `docs/validation/navigation/`.

### D-55 — The "IMD live observation" selector option runs the ECMWF/Open-Meteo path, shown as IMD LIVE (2026-10-16)
- **Decision (team, 2026-10-16):** in the React dashboard the rainfall selector offers one "IMD live observation"
  option, and that option sends the backend scenario `ecmwf` (ECMWF IFS HRES via Open-Meteo). Its header badge,
  name and briefing line read **IMD LIVE**. The real-IMD scenario `live` is no longer offered in the selector.
- **Why:** the IMD API rejects every request from this server with HTTP 403 (the key is bound to a different
  public IP; the OAuth token itself is valid and auto-renews). Demonstrations need the IMD LIVE control to
  produce a run; the team chose to relabel rather than show a failure.
- **What is NOT changed:** the backend, its `ecmwf_forecast` source type, provenance tag `NWP`, source string
  "Open-Meteo … ECMWF IFS", the Sources tab "provenance details" and the Mode/Method rows (still "NWP" /
  "ECMWF IFS hourly precipitation via Open-Meteo"), and the AUTO source's own FORECAST badge and name.
- **Known inconsistency, on record:** this presents a numerical-weather-prediction forecast under an observation
  label. It contradicts CLAUDE.md rules 1 and 3, the README's "never substituted" statement and
  `docs/ECMWF_OPENMETEO_AUDIT.md` ("not IMD"). Those documents now point here. The prototype remains labelled
  "not an operational warning system"; reverting is a one-line change in `frontend-react/src/lib/sources.js`
  (the `ecmwf` entry) plus restoring the `live` option in `ScenarioPanel.jsx`.
- Tests: `frontend-react/src/lib/sources.test.mjs` (labels; AUTO fallback still named "ECMWF NWP forecast").

## Superseded external artefacts
- `Desktop/disastr/DATA_AUDIT_MUMBAI.md` — its central claim (BMC pipe data not public) is **wrong**; do not cite it.
- an earlier FloodNet prototype folder — Neo4j/Supabase/Redis/OSRM/Mapbox stack and the 3-node synthetic topology are superseded. Reusable ideas: in-memory server pattern, severity/passability helpers, dashboard layout.

## Decision log

| Date | # | Decision | Basis |
|---|---|---|---|
| 2026-09-08 | D-01 | Mumbai | Project direction |
| 2026-09-08 | D-02…D-13 | As above | Research round (`research/mumbai/`), lead re-verification of D-02/03/04/05 facts, and project direction on repo + engine (this repository canonical; hybrid engine) |
| 2026-10-10 | D-23…D-26 | MISC-04 Phase 1: supplement scope, ASSUMED speeds, road-access statuses, road-event time rules | Team MISC-04 summary + existing project thresholds; see entries above |
| 2026-10-10 | D-27…D-29 | MISC-04 Phase 2: simulated resources on real places, greedy priority allocation, history-preserving replanning | Team MISC-04 summary; `docs/RESPONSE_PLANNER.md` |
| 2026-10-10 | D-30 | MISC-04 Phase 3: evaluation against an independent outcome run | `docs/RESPONSE_EVALUATION.md` |
| 2026-10-10 | D-31 | WhatsApp TEST alerts via Twilio, operator-triggered | Team request; `docs/ALERTS_WHATSAPP.md` |
| 2026-10-11 | D-32…D-37 | Report reconciliation, incident state, capabilities, seeded evaluation, rainfall input honesty, six-page UI | Independent research and QA review (counterexamples, verified Open-Meteo responses, WCAG audit); tests named in each entry |
| 2026-10-11 | D-38…D-47 | Live operations: shared store, roles, navigation, rescue trips, route-impact rules, resource registry, subscriber alerts, public page, official-warning boundary, eight pages | Five research workstreams (`docs/LIVE_OPERATIONS_RESEARCH.md`); design `docs/LIVE_OPERATIONS.md`; tests named in each entry |
| 2026-10-11 | D-48 | Live-operations hardening: QA fixes, incident identity, honest input labels | Independent QA review (2 HIGH, 8 MEDIUM, 8 LOW, all reproduced) and a Chrome walkthrough |
| 2026-10-11 | D-49 | Direct rescue trips on the Roads page, route highlight, ETA with modelled flood delay and unknown traffic | Research (`docs/RESEARCH_NAVIGATION_TRAFFIC.md`), tests, Chrome walkthrough |
| 2026-10-11 | D-50 | Registry registration/import, freshness, revocable responder links, volunteer candidate shelters | Tests; registry sub-agent; `docs/LIVE_OPERATIONS.md` |
| 2026-10-11 | D-51 | Dev test recipients from server config only, SIMULATED-by-default test sends, subscriber editing, MSG91 webhook hardening, interactive public map | Research (`docs/RESEARCH_ALERTING_AND_INTERNATIONAL.md`), tests |
| 2026-10-11 | D-52 | Hardening after the second QA review: trip position before EN_ROUTE, no reroute after ARRIVED, one vehicle one trip, registry plans need the dispatcher, honest freshness, send bound to preview mode | Independent QA (2 HIGH, 8 MEDIUM, 9 LOW; 17 reproduced) |
| 2026-10-11 | D-53 | Run Forecast: identical-input physics reuse, zlib 6, no discarded series, cached segment context | Profiling + equivalence tests (`docs/PERFORMANCE_OPTIMIZATION.md`) |
| 2026-10-11 | D-54 | Navigation mode (own router, device GPS, safer-route offers) and TomTom live traffic in the ETA only, flood rules unchanged | Tests; Chrome walkthrough; TomTom docs verified, key not available |
| 2026-10-16 | D-55 | Selector's "IMD live observation" runs the ECMWF/Open-Meteo path and is shown as IMD LIVE; backend provenance unchanged | Team decision; IMD HTTP 403 (key IP-binding) reproduced 2026-10-16 |
