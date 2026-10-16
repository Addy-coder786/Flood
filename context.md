# FloodNet — verified repository state

**FloodNet — Urban Flood Nowcasting System ("Drainage and Rainfall Coupling")**
Pilot: Mumbai — Hindmata / Dadar (MCGM F/North & F/South)

**Written:** 2026-09-10, from the actual final code and test state — not from task descriptions.
Every number below was measured in this session or is cited to the file that produces it.

**Status labels used here:** `IMPLEMENTED` (code exists and runs) · `VERIFIED` (measured/executed this
session, evidence quoted) · `PARTIAL` (works with a stated real gap) · `BLOCKED` (external dependency)
· `PLANNED` (not built; recorded so it is never reported as done).

> **Blanket caveat, stated once and applying to every UI claim in this document:** browser automation in
> this environment **cannot reach this machine's localhost** — confirmed by a control test that
> successfully loaded an external site and then failed on `localhost:5174`. **No click-through, no visual
> rendering, and no console-error check has been performed on any UI change described here.** Everything
> UI-side is code-reviewed and build/lint-verified only. A human must eyeball the dashboard before the demo.

---

## 0. 2026-09-14 full-system audit — what changed since this document was written

This document's body (below) was written 2026-09-10 and is otherwise still accurate — re-verified spot-checks
this pass did not contradict it. A full-system audit on 2026-09-14 (code re-verification + 2 independent
research agents on comparable real systems and government/CAP integration + 1 fresh-context independent
judge review, none of which were told to trust prior reports) found the following **new** items, each detailed
in full in `docs/VALIDATION.md` and `docs/REQUIREMENTS.md` §6 rather than duplicated here:

- **Routing real-network connectivity gap (new finding).** `docs/validation/demo_check.json` (an actual prior
  run already on disk) shows the pilot road graph's own **unweighted baseline route** can fail
  (`NetworkXNoPath`) for a genuine in-bbox point pair — before any flood logic runs. This is a road-graph
  connectivity gap, not (as an earlier reading of the same file concluded) an effect of storm severity. Traces
  to `research/mumbai/BASEMAP.md:99`, an unperformed validation flagged at project start and never closed. SR-14
  and SR-15 in `docs/REQUIREMENTS.md` were downgraded from COMPLETE to PARTIAL as a direct result. Top
  priority open item — see that file §6 for the fix plan.
- **A fabricated statistic was found in `docs/VALIDATION.md` and corrected.** An earlier audit pass had
  reported a "spatial permutation test" (specific seed, draw count, p-values) for the MCGM Flooding Spots
  comparison that was never implemented in code (`grep -rIl "permutation"` matched only the markdown file
  itself). This was caught, and is now visibly retracted in `docs/VALIDATION.md` §2.F/G and §8 (an erratum, not
  a silent edit) — status reverted from a false "no spatial skill" conclusion to honest "NOT MEASURED." A real
  implementation of this test is scoped and buildable but not yet done (compute-heavy; paused this session for
  memory-safety reasons — see below).
- **Independent judge review completed** (fresh subagent context, code-only, no prior-report trust). Scored
  the project 6/10 Innovation, 8/10 Technical Complexity, 4/10 Feasibility/Scalability, 6/10 Presentation/UX,
  4/10 Impact, 6/10 Problem-Solution Fit — and independently corroborated the routing finding above, the
  zero-validated-accuracy status, and that no AI/ML runs in the live path (physics/graph algorithms only).
  Verdict: "a more honest prototype than most hackathon submissions... but honesty about limitations is not the same
  as having overcome them."
- **Competitive research completed** (12 comparable real systems: Google Flood Hub, JBA, Chennai CFM-DSS, CWC,
  iFLOWS-Mumbai, NOAA FIM, UK Met Office Nimrod/pysteps, Waze+FloodMapp, academic dual-drainage studies,
  MIKE+/InfoWorks ICM, Aurassure). Conclusion: surface-drainage coupling itself is not novel (mature in
  commercial tools, already applied to Mumbai once by TERI/MIKE21); FloodNet's real, narrow, evidence-backed
  differentiators are (a) doing that coupling openly on independently-verified real municipal data, live
  per-request, (b) time-aware multi-objective vehicle-class routing (no comparable granularity found anywhere
  researched), and (c) per-field data provenance surfaced to end users (nothing else researched exposes this).
- **Government/CAP integration research completed.** No public SACHET submission path exists (re-confirmed).
  Mumbai's DDMA is chaired by BMC's own Additional Municipal Commissioner — BMC *is* the flood authority for
  Mumbai. BMC's real operating floor is phone/radio/text (1916 helpline had a 6+ hour outage in the current
  2026 monsoon, fell back to ham radio), not CAP/XML ingestion — motivating a `[PROPOSED]` (not yet built,
  not a problem-statement requirement) plain-language incident-brief export reusing existing `cap.py` fields.
- **Unexplained-looking local secret flagged.** The local (gitignored, untracked) `.env` contains an
  `INDIAN_API_KEY` value formatted like a live credential, referenced nowhere in `.env.example` or the
  codebase. Not a repo/leak risk, but unexplained — verify and rotate/remove if genuine.
- **Session resource constraint — resolved.** Host machine free memory dropped to ~1GB of 15.78GB earlier in
  this session (the backend was OOM-killed once), which paused `pytest`/`npm run build`/backend restarts.
  Memory later recovered to ~4.5–5.5GB free and stayed stable; once confirmed, the held-back work was
  completed: the **P0 routing connectivity fix** (below) and a **final validation pass** — `pytest` **169
  passed, 4 skipped, 0 failed**, `npm run build` clean, `npm run lint` 0 errors, a fresh full 180-min
  simulation run (83.95s, mass-balance error −3.77e-12%), and live verification of all 15 required functional
  checks against a freshly-started backend. Full detail: `docs/VALIDATION.md` §9,
  `docs/REQUIREMENTS.md` §7.
- **P0 routing connectivity bug — found and fixed.** `docs/validation/demo_check.json` proved the real pilot
  road graph's own unweighted baseline route could fail (`NetworkXNoPath`) for a genuine in-bbox point pair.
  Root cause: `router.py`'s `_snap()` picked the literal nearest node regardless of its role in the network,
  landing on one of 551 dangling one-edge stub nodes (24% of pilot nodes) stranded in a small
  strongly-connected pocket with no directed path back to the main 2,123-node core. Fixed by restricting
  `_snap()` to the graph's largest strongly connected component (91.5% of nodes) — the same practice
  OSRM/GraphHopper/Valhalla use; no geometry altered, no edges invented. Validated on 4 real origin/destination
  pairs plus a full `demo_check.py` re-run (`all_passed` false→true) plus live API re-confirmation. SR-14/SR-15
  are **COMPLETE** again, with the residual 8.5%-of-nodes limitation honestly documented. Full detail:
  `docs/VALIDATION.md` §2.I, `docs/REQUIREMENTS.md` §6.
- **Browser QA: still unavailable, now with a precise diagnosis.** Confirmed this session (not just asserted):
  browser tooling itself works (loaded an external site successfully), but it cannot reach a backend started
  via the Bash-tool sandbox — Chrome's own network stack recorded a genuine 404 from something else at that
  address while the sandbox's `curl` got 200 from the identical URL. This is sandbox/browser network
  isolation, not an application defect. A human must still click through before the demo.

---

## 0b. 2026-09-14 real-data + gridded-rainfall pass (SUPERSEDES SOME NUMBERS BELOW)

> **⚠ Figures in §2–§5, §10 and §12 below predate this pass.** Manhole plan area changed from an invented
> blanket 1.5 m² to IS 4111 (Part 1)-1986 depth bands (−26.2 % chamber storage), which moved the physics:
> peak depth **268.72 → 292.27 cm**, peak surcharging nodes **424 → 459**, total surcharge
> **124,981 → 133,020 m³** (`heavy` + 70 % blockage, 180 min); mass balance still −3.15e-12 %. Those older
> figures are left in place as the record of what was measured then, not silently rewritten. See
> `docs/DECISIONS.md` D-17 and `docs/VALIDATION.md` §10.0.

- **The SR-01 architectural blocker (D-15) is CLOSED — `IMPLEMENTED` + `VERIFIED`.** `RainfallScenario` now
  accepts an optional `intensity_field_mm_h` `[T, ny, nx]` + `field_grid`; `runoff_fn` takes a scalar *or* a
  per-cell field; `engine.run_simulation` branches on `scenario.is_spatial` and **refuses** a field whose grid
  doesn't match the terrain grid; new `floodnet/rainfall/gridded.py` does the reprojection/resampling.
  Uniform scenarios keep the byte-identical original path (`intensity_at` equivalent on 1,509/1,509 samples;
  constant field reproduces the scalar runoff total exactly; pre-IS-4111 full-pilot run reproduced §10's
  documented peak depth and mass error to the last digit). **Proof the field reaches the physics:** a spatial
  storm and a uniform storm with the *identical* total rain volume (58,213.6 m³ each) produce different
  flooding — 3,469 cells differ by >1 cm, max difference 39.5 cm. 24 new tests.
- **Gridded source selected: GPM IMERG Early V07** (`IMERGSatelliteProvider`, credential-gated on
  `EARTHDATA_TOKEN`, raises `ProviderUnavailable` without it). Two things must always be said with it:
  **(a) it is NOT ground radar** (satellite PMW/IR intercalibrated against a spaceborne-radar reference), and
  **(b) at 0.1° (~11 km) the whole pilot sits inside ONE source cell**, so it adds real provenance and real
  temporal behaviour but **zero spatial variation at pilot scale**. The code detects and writes this into
  provenance automatically. Network path implemented but **never executed against the live service** (no
  credential) — same honest status as the IMD provider. `BLOCKED` on credential, not on architecture.
- **RADAR INTEGRATION BLOCKED BY DATA ACCESS — `BLOCKED`, and the reason is now narrow and precise.** A
  final exhaustive search (nine avenues, radar-only, satellite/NWP/gauge/tiles excluded) confirmed there is
  **no genuinely radar-derived, Mumbai-covering, legally-usable, programmatically-obtainable rainfall dataset
  with the temporal continuity a 0–3 h nowcast needs.** Nothing was implemented as a substitute. The engine
  can ingest a gridded field today — **the blocker is entirely data access and licensing, not architecture.**
  Two near-misses, both genuinely radar, both correctly rejected: **CEDA INCOMPASS v2** (real IMD DWR data,
  Mumbai included, OGL v3, downloadable — but a convective-cell *object table*, not a field; reconstructing a
  grid from it would mean inventing intra-cell structure) and **GPM DPR** (real spaceborne precipitation
  radar, mm/hr, open — but overpasses days apart, and its 5 km footprint is larger than the whole pilot).
  Mumbai has two IMD radars (Colaba S-band, Veravali C-band); the specific blocker on IMD's own supply route
  is a **broken TLS certificate chain on `radarapi.imd.gov.in`**. Newly established: IMD registration is
  **open to individuals/students** (not MoU-gated), and radar is **chargeable** (absent from the free-data
  list). Full candidate table + the exact human action to unblock: `docs/DECISIONS.md` **D-19**.
- **`synthetic_spatial`** — a clearly-labelled SYNTHETIC moving-storm field, wired through the API, exists
  solely to exercise/test/demo the spatial pathway. It is **not radar, not observed, not a nowcast**, is
  tagged SYNTHETIC, and a test asserts its provenance says so.
- **Estimated inputs audited against official sources.** Upgraded: manhole plan area → IS 4111. Left alone,
  with reasons recorded (D-18): Manning's n (no Indian source verifiable — the 62 brick arches were
  deliberately **not** changed), inlet capture capacity (no Indian standard states one), DEM (all free
  alternatives are coarser 30 m *surface* models — ours is better), tidal boundary (still `MISSING`).
  Identified-but-not-applied, each needing an owner decision + validation gate: IRC:SP:50-2013 §6.4.1's
  Mumbai runoff coefficient, and ESA WorldCover 10 m (CC BY 4.0) as a real imperviousness raster.
- **Tests after this pass: 193 passed, 5 skipped, 0 failed** (was 169/4). `npm run build` clean,
  `npm run lint` 0 errors.

---

## 0c. 2026-09-18 / 21 — IMD live access, radar-image path, technology story (SUPERSEDES PARTS OF §8)

- **IMD API is live** (`VERIFIED` 2026-09-18, re-verified with a renewed token): `IMD_API_KEY` + `IMD_API_TOKEN`
  (expires, renewed by hand) + IP-bound key. `current_wx`, station/district nowcast and district rainfall
  return Mumbai rows. Live runs report `data_mode: MIXED` (REAL observation + ESTIMATED continuation).
  Live smoke tests are opt-in: `RUN_LIVE_IMD=1`. Detail: `docs/LIVE_RAINFALL_AUDIT.md` §12.
- **Radar** — `PARTIAL`, experimental: `IMDVeravaliSRIImageProvider` (`imd_sri`) decodes IMD's public SRI *image*
  into a `[T, ny, nx]` field tagged `RADAR_IMAGE_DERIVED_ESTIMATE` (`docs/RADAR_SRI_IMAGE.md`, D-20). IMD's API
  portal has no radar-product API. A gated advection nowcast on the decoded fields plus a source-aware horizon
  (radar 0 → nowcast 5–30 min → ECMWF/persistence) is `IMPLEMENTED`, logic-tested, and **not yet exercised on
  two consecutive real frames** (`docs/RADAR_NOWCAST_STATUS.md`, D-21). SR-01 stays `PARTIAL`.
- **Tide** — `PLANNED`/undecided: no licensed machine-readable Mumbai source yet (`docs/TECHNOLOGY_EXPLAINER.md`, D-22).
- **Frontend**: shared seven-stage technology story (landing cards, dashboard story strip, Demo Story on the
  Guided Briefing engine), route A/B flood-exposure view (`docs/TECHNOLOGY_EXPLAINER.md`).
- **Competitor evidence**: `docs/COMPETITIVE_VIDEO_ANALYSIS.md`.
- **Final submission pass (2026-09-21)**: one operator-facing source mapping (`frontend-react/src/lib/sources.js`),
  compact Sources / Alerts / Route panels, header source follows the active run, deployment split (Vercel static
  frontend + container backend). **Current one-page status: `docs/FINAL_STATUS.md`; deployment: `docs/DEPLOYMENT.md`.**
- **Tests at this point**: see §"Tests" in README / the latest run in the session report; earlier counts in
  this file are historical.

## 1. What FloodNet does

```
rainfall → runoff → 2D surface routing → drainage graph → hydraulic capacity
        → surcharge → street-level depth → 0–180 min forecast → GIS dashboard → flood-safe routing
```

One coupled loop, `backend/floodnet/simulation/engine.py::run_simulation`. 37 frames at 5-minute output
over a 180-minute horizon (`config.py`: `HORIZON_S = 3*3600`, `FRAME_DT_S = 300`), internal adaptive
sub-stepping at ≤5 s. Grid 244×255 @ 10 m (2.44 × 2.55 km). Network: 1,233 nodes / 1,134 edges / 116 outfalls.

---

## 2. Drainage forecast architecture — `VERIFIED`

**The 0–3 h drainage forecast is real, not a static display.** Directly measured this session on a
cloudburst run: individual conduits and manholes evolve independently frame to frame.

```
edge 17444  util:  t=0 0.000 → t=30 0.451 → t=60 1.000 → t=120 0.509 → t=180 0.033
node 2172031407 HGL: 23.77 → 23.77 → 27.56 → 23.77 → 23.77 m   (fills 3.8 m, drains back)
surcharging_count across 37 frames: 0,0,0,0,0,0,0,9,90,134,175,200,218,207,199,…,45
```

Rain peaks at T+45; surcharging peaks at T+60 — a physically correct ~15-minute lag, reproduced from
real MCGM network geometry.

### Per-frame drainage outputs (`contracts.Frame`) — `VERIFIED` complete

| Field | Shape | Window |
|---|---|---|
| `node_hgl` | `[1233] f32` | instantaneous at frame boundary |
| `node_surcharging` | `[1233] bool` | instantaneous (last 5 s sub-step) |
| `node_surcharge_m3` | `[1233] f32` | **accumulated over the 300 s interval**, reset each frame |
| `node_cause` | `[1233] str` | `""` / `overcapacity` / `blockage` / `downstream` |
| `edge_flow_m3s`, `edge_util` | `[1134] f32` | instantaneous |

An audit confirmed **every** one of these reaches the API — there is no "computed but hidden" drainage
data at frame level.

### Lead-time signal — `IMPLEMENTED` + `VERIFIED` (the main win of this sprint)

Previously `/series` exposed only `surcharging_count` and `surcharge_m3`, so an operator saw "all clear"
while the network was already saturating. Measured, before and after the fix:

```
t=30.0  rain=72.8  surcharging=0   ←  operator saw "nothing wrong"
        …but edges at ≥1.0 capacity = 10,  edges ≥0.8 = 15,  max_edge_util = 1.000
```

`GET /api/simulation/{run_id}/series` now additionally returns, per frame:
`cause_counts{overcapacity,downstream,blockage}`, `edges_at_capacity`, `edges_near_capacity`,
`mean_edge_util`, `max_edge_util`, `nodes_at_capacity`. **Thirty minutes of lead time that existed in the
frame data but was invisible is now a first-class signal.**

`edges_near_capacity` (util ≥ 0.8) is **descriptive only** — it is *not* a validated distress threshold
the way the node `fill ≥ 0.999` boundary is, and is labelled that way in the code.

---

## 3. Drainage state definitions — `IMPLEMENTED` + `VERIFIED`

Three tiers, served per node from `serialize_frame` as `state`:

| State | Definition | Provenance of the threshold |
|---|---|---|
| `surcharging` | spilled to street during the frame interval | the solver's own criterion, `excess = V − vfull > 1e-9` |
| `at_capacity` | `fill_frac ≥ 0.999`, no spill this interval | **the solver's own constant** — `hydraulics.py` uses `V >= vfull*0.999` internally to decide a node is full and blocking upstream flow |
| `normal` | everything else | — |

Plus per node: `fill_frac` (0–1), `freeboard_m` (metres below street level). Per edge:
`edge_capacity_m3s`, `blockage`.

**An invented "near capacity ≈ 0.85" band was explicitly rejected**, and this is deliberate. Node depths in
this network range 0.57 m – 4.47 m, so a fixed fill-fraction means 0.086 m of freeboard on one node and
0.67 m on another — the same colour for operationally different states. No source supports such a band.
Instead `fill_frac` is encoded **continuously** (radius/opacity, hue held constant) so the eye sees a
gradient without the UI asserting a boundary the physics does not have.

Measured distribution at cloudburst peak — the tiers discriminate rather than washing the map:
`normal: 996 · surcharging: 231 · at_capacity: 6` (of 1,233).

### Surcharge undercount — `BUG FIXED`, `VERIFIED`

`node_surcharging` samples only the final 5 s sub-step while `node_surcharge_m3` accumulates all 300 s, so
nodes that surcharged earlier in an interval reported as normal. Independently measured by two audits.
Now the serialized boolean is `raw_flag OR surcharge_m3 > 0`. Measured effect:

```
t=40  instant= 90 → interval=102  (+12)
t=45  instant=134 → interval=153  (+19)
t=60  instant=218 → interval=231  (+13)
t=65  instant=207 → interval=232  (+25)   ← ~11% undercount
11 frames affected
```

`/series`'s `surcharging_count` uses the same corrected definition, so **map and timeline now agree
exactly** (verified 153/153, 231/231, 174/174). The raw value is preserved as `surcharging_count_instant`
so older `docs/VALIDATION.md` figures stay traceable.

---

## 4. Drainage → street coupling: what is and isn't established — `VERIFIED`

This is the most important scientific finding in the repository and it bounds what may be claimed.

**Mass conservation is exact.** Instrumented over a full run: `45,751.31 m³` surcharged vs
`45,751.306 m³` re-injected to the surface; per-cell residual `6.9e-18`; engine mass-balance error `-0.0`.

**Surcharge can never make a street wetter.** Verified across **42,339 surcharging node-steps**: surcharge
never exceeds what the inlet took from that same cell in the same step (max ratio exactly
`1.000000000`). This is structural — pipe inflow to a non-outfall node is clamped at its remaining free
storage, so all excess originates from that node's own inlet capture. **The drainage network is a strict
net sink at every cell; surcharge is a ~97.6% refund of water just swallowed there. No water from
elsewhere in the pipe network is ever delivered onto a street.** In this model drainage can only *fail to
drain* — it cannot flood.

**Per-location causal attribution is NOT established:**
- `r = 0.048` between segment peak depth and nearest-node surcharge (2,978 segments × 36 frames)
- 91.6% of wet street-frames have a non-surcharging nearest node; 97% under moderate rain
- the four deepest segments in the pilot all have **0.0 m³** surcharge at their nearest node
- nearest-node association is pure Euclidean proximity (median 41 m, max 452 m), not a computed flow path
- 388 segments deepened under blockage (one by **+43.6 cm**) while their nearest node never surcharged

**System-level causality IS established.** 70% capacity cut → outfall discharge −19.4%, surface storage
+9.8%, 1,127 segments deeper vs 14 shallower.

**Therefore the honest claim is:** *"drainage capacity is a demonstrated driver of flooding in this ward,
and here is the counterfactual"* — **not** *"this street is flooded because that node is surcharging."*
UI language was corrected accordingly (§7).

---

## 5. Blockage what-if — `IMPLEMENTED` + `VERIFIED`

Structurally incapable of misrepresentation: `BlockageSpec` accepts only `none/fraction/edges/random/near`
— there is no field anywhere that could carry an observed measurement, and the base MCGM network is always
built with `edge_blockage = zeros`.

Measured three-point dose-response (heavy, 180 min, identical rainfall forcing):

| Blockage | Edges affected | Total surcharge | Peak surcharging nodes | Max depth |
|---|---|---|---|---|
| none | 0 / 1134 | 45,751 m³ | 186 | 268.702 cm |
| random 30% @ 60% | 340 / 1134 | 70,987 m³ | 256 | 268.702 cm |
| uniform 70% | 1134 / 1134 | 124,981 m³ | 424 | 268.723 cm |

Strictly monotonic — physically well-behaved. **Note max depth moves 0.021 cm across the entire range**
while surcharge volume nearly triples, because the deepest cell is a terrain depression that saturates
regardless. Surcharge volume was therefore added as a headline metric; peak depth alone was the wrong
number to lead with.

`provenance.blockage` is now present on **every** run, top-level, including baselines, which previously
emitted nothing at all:

```json
{"tag":"SYNTHETIC","source":"baseline (no blockage applied)",
 "note":"Drainage assumed clear (0% blockage). FloodNet has no live drain-condition telemetry;
         this is always an operator-set scenario assumption, never an observation."}
```

Hierarchy: blockage was demoted below "Run forecast" into a collapsed `<details>` — "What-if: reduced
drainage capacity (optional)" — so the rainfall→forecast path reads as the primary product.

> **Open conflict for the owner to settle:** `docs/REQUIREMENTS.md` designates the blocked-vs-unblocked
> A/B as *"the headline demo"* for SR-08, which contradicts the "blockage clearly secondary" instruction
> this sprint worked to. The code now follows the instruction, not the doc. One of them should change.

---

## 6. Timestep behaviour — `VERIFIED`

One authoritative `currentT` in `FloodNetContext.jsx`. All 10 `setInterval`/`setTimeout`/`Date.now()` call
sites were audited: **no wall-clock value reaches time selection anywhere.** The playback timer advances by
*index into `run.frames_t_min`*, never by elapsed time. Scrubbing serves from a client-side frame cache and
**never re-runs physics** (uncached frames cost one cheap serialization GET).

Current-frame vs forecast-peak is distinguished explicitly on four independent surfaces
(MetricsPanel, WhyFloodedPanel, AlertsPanel, ForecastTimeline).

### Bugs found and fixed this sprint

| Bug | Status |
|---|---|
| **`isStale` key-order bug** — backend echoes blockage keys sorted, frontend compared with raw `JSON.stringify`, so **every** non-`none` blockage run was born stale: header said "Inputs changed — Run forecast" while map/timeline/streets/alerts/why-flooded/routing all rendered it in full. This broke the SR-08 headline demo on every static scenario. | `FIXED` `VERIFIED` — canonicalised over the full `BlockageSpec` field set; tested against the exact empirically-captured mismatches plus negative controls |
| `runCompare` substituted a 50% blockage without updating state, self-invalidating every compare from the default | `FIXED` |
| `route`/`alternatives` survived a re-run, so a prior run's routes displayed against a new `run_id` (worst for ECMWF/live re-pulls) | `FIXED` |
| Route alternatives did not re-evaluate on scrub, **and** `MapView` gave the candidate layer priority over the auto-refreshing single route — so stale geometry was drawn over current flood colouring with no disclosure | `FIXED` (disclosure, not auto-recompute) |

Route alternatives now expose `alternativesStale`; stale candidates render dashed/de-emphasised on the map
and the panel shows both timesteps with an inline alert. The per-candidate "safe through T+X" line is
correctly **exempt** — it scans the full horizon and is timestep-independent.

---

## 7. Scientific-honesty corrections — `IMPLEMENTED`

| Location | Was | Now |
|---|---|---|
| `WhyFloodedPanel` cause text | "the nearby drainage node is surcharging because an outgoing pipe is blocked" | "…it can no longer drain the water it captures. This is a local hydraulic signal near this location, **not a computed flow path to this specific street**." |
| `WhyFloodedPanel` surcharge chain | "backwater surcharges up through street inlets" | "has lost capacity to drain — captured inflow is being held rather than carried away" |
| `WhyFloodedPanel` runoff | "converts **85%+** rainfall into runoff" (hardcoded; model's real ratio is **80.9%**) | qualitative, no false precision |
| `WhyFloodedPanel` terrain | "toward **Hindmata depression bowl**" on every segment ward-wide | "toward lower-lying terrain" |
| `LandingPage` DRAINAGE stage | "water surcharges up through street gully inlets" | "inlets can no longer carry away the water they capture, and it accumulates at street level" |
| `ScenarioPanel` compare note | "normal (brass) vs blocked (terracotta)" | "(blue) vs (orange)" — matches the actual `#2563eb`/`#ea580c` strokes |

Earlier in the same session, eight fabricated claims were removed from `LandingPage.jsx`, including a
"never synthetic" assertion, a false MCGM-calibration badge, an unsourced "944 mm" figure (the scenario
actually replays 380.8 mm/3 h per the Chitale Committee), and a coordinate/elevation pair that fell
**outside** the simulated pilot bounding box.

---

## 8. Rainfall providers — `PARTIAL` (SR-01 is the standing gap)

| Provider | Class | Status |
|---|---|---|
| Synthetic design storms | SYNTHETIC | `IMPLEMENTED` |
| July 2005 replay | REAL / HISTORICAL (Chitale, single non-local gauge) | `IMPLEMENTED` |
| ECMWF via Open-Meteo | **NWP forecast** — never a nowcast, never an IMD product | `IMPLEMENTED` `VERIFIED` (live call succeeded, 138 s) |
| IMD observation | REAL observation → **ESTIMATED persistence**, gated on `IMD_API_KEY` | `BLOCKED` (no credential) |
| Radar nowcast | — | `BLOCKED` — see below |

### Radar: decision **PATH D** — `BLOCKED`, recorded as `D-14`

Investigated by a specialist agent and then independently adversarially reviewed; the review **overturned
the specialist's central claim**, which is why this is a refusal rather than a feature.

- IMD's API reference indexes 28 APIs but its body ends at §20 — **"Radar Image" is a dead anchor**, no
  endpoint/params/format documented. Auth is evaluated *before* routing (proven with a nonsense-path
  control), so endpoint existence cannot even be probed without a credential.
- The public `sri_mum.gif` genuinely **is** Surface Rainfall Intensity in mm/hr with the Z-R relation
  printed in-band — correcting this repo's earlier claim that it was reflectivity-only.
- **But it is unusable here:** its top bin is open-ended at `>100 mm/h`, while this project's own
  `cloudburst` (120 mm/h) and `july2005` (190.3 mm/h) scenarios exceed it — it censors exactly the regime
  FloodNet models. Plus ±3.33 mm/h quantisation, ~12.5% of the pilot footprint occluded by the drawn
  coastline (Dadar sits on it — systematic, not averageable), 30–40 min latency, rain rate inferred at
  2 km altitude, and the image is resampled (0.4277 km/px, not the stated 0.4).
- The "96.6% of pixels decode exactly" evidence was reproduced at 96.40% **and decomposed: 96.27 points
  are the No-Data background; actual rain-bin pixels were 0.13%.** The statistic measured empty sky.
- No free historical archive (listings 403, ~3 Wayback captures in six years) → no hindcast possible.
- Licence is **unresolved and worse than recorded**: `disclaimer.php` asserts IMD copyright with **no
  grant**; the official route (`radarapi.imd.gov.in`) needs an account, a request and **payment**.

> **SUPERSEDED 2026-09-18 / 21 (kept as history):** an experimental decoder of the public Mumbai-Veravali SRI
> image, and a gated advection nowcast on its decoded fields, now exist — see §0c, D-20, D-21,
> `docs/RADAR_SRI_IMAGE.md`, `docs/RADAR_NOWCAST_STATUS.md`. The licence question below is still open.

**No radar decoder is being built. No image-capture job has been started** — harvesting copyrighted
imagery on an unresolved licence was declined deliberately, despite an "every hour is lost" argument. The
zero-cost unblocking step is a written enquiry to the Radar Division (`radarlab@gmail.com`) — a human action.

### The structural finding that reframes SR-01 — `PLANNED`, recorded as `D-15`

> **SUPERSEDED 2026-09-14 (kept as history):** `RainfallScenario` now carries an optional `[T, ny, nx]`
> field and the engine consumes it — see §0b and D-15 (resolved).

`contracts.RainfallScenario.intensity_mm_h` is `[T]` and `intensity_at()` returns a single `float`, which
`engine.run_simulation` passes to `runoff_fn`. **Rainfall is spatially uniform by construction for every
provider. FloodNet cannot ingest a gridded rainfall field from any source today.** Acquiring radar data
would not, by itself, satisfy the "high-resolution/gridded" half of SR-01. Generalising `RainfallScenario`
to an optional `[T, ny, nx]` field is the honest prerequisite — **not implemented, not scheduled.**

---

## 9. Routing and location search — `IMPLEMENTED` + `VERIFIED`

- `POST /api/route` — unchanged contract (verified: **0 lines changed inside the handler**; the new work is
  appended after it).
- `POST /api/route/alternatives` — 2–3 genuinely diverse candidates (Yen's + Jaccard de-duplication) scored
  under **SAFEST / FASTEST / BALANCED**, plus per-candidate "safe through T+X".
- **"Fastest" is labelled "Fastest (by distance)" throughout** because the graph carries only `length_m` —
  there is no speed or travel-time model. This is honest, not a shortcut.
- Objectives correctly diverge only under flooding: at T+0 all three pick the same route (no flood → zero
  penalty → safest ≡ fastest); at T+180 they split (`safest:0, fastest:1, balanced:0`). Good demo moment.
- **No traffic provider exists** and none was faked; the UI states "Traffic data: not connected".
- **Location search** — 179 real named roads (grouped across segments, resolved to the midpoint of each
  name's longest segment) + 4 verified landmarks, all from actual pilot OSM data via `lib/locations.js`.
  No third-party geocoder was added — it would resolve queries worldwide and imply coverage where no
  simulation data exists. Search and map-click are fully interchangeable (both route through `pickPoint`).

---

## 10. Performance — `IMPLEMENTED` + `VERIFIED` bit-identical

Re-profiled from scratch (cProfile, real pilot, heavy/70%/180 min): `terrain/surface.py::_substep` was
**67.6%** of self-time, `drainage/hydraulics.py::step` **21.2%** — independently confirming the prior
~68%/20% estimate, with call counts matching exactly.

Optimisation applied: 16 pre-allocated scratch buffers + `out=` kwargs, plus hoisting an invariant boundary
check. **Allocation strategy only — operand order and float operation sequence unchanged.**

| | Before | After |
|---|---|---|
| `_substep` self-time | 44.531 s | **34.989 s** |
| profiled total | 66.791 s | **52.062 s** |

**Validation gate passed in full:** frame-by-frame `np.array_equal` = **True** on all 37 frames' depth
grids, surcharging arrays and HGL arrays — max abs diff `0.0`, max rel diff `0.0`. Mass balance identical
to full precision (`-3.770585409371645e-12`), peak depth identical (`268.7231779098511 cm`), peak
surcharging nodes identical (`424`). This is bit-for-bit equality, not "within tolerance".

**Deliberately NOT optimised:** the `hydraulics.py` hot path. It is the exact site where a prior 3.3×
speedup was reverted after frame-by-frame validation caught real discrepancies. It stays as-is.

Full-run wall clock remains **~90–150 s** (large environmental variance — a warm run measured *slower* than
a cold one on an unchanged binary). Not real-time; **no claim of real-time computation is made anywhere.**

---

## 11. Tests, build, production paths

| Check | Result |
|---|---|
| `pytest backend/tests` | **136 passed, 4 skipped** (was 126/4 at session start; +10 routing tests). **Re-run 2026-09-14 (final pass): 169 passed, 4 skipped, 0 failed** — further growth from this session's routing-fix work, still zero failures |
| The 4 skips | Intentional live-service opt-outs, verified via `pytest -rs` — gated on `IMD_API_KEY` / `RUN_LIVE_ECMWF_TEST` |
| `npm run build` | Clean, **62 modules**. Re-confirmed clean 2026-09-14 |
| `npm run lint` | **0 errors**, 7 warnings — all pre-existing, none introduced across 21 modified files. Re-confirmed 2026-09-14: 0 errors, 6 warnings |
| `/static/`, `/static/dashboard` | **200** (SPA fallback) — runtime-verified against a live server, twice (2026-09-10 and 2026-09-14) |
| `/openapi.json`, `/docs`, `/api/health` | **200** |
| Missing static asset | **404** (correctly not swallowed) |
| `POST /api/route`, `/api/route/alternatives` on real pilot data | **200**, live-verified 2026-09-14 post routing-fix — real flood-aware detours confirmed (see §9 note above) |

**Operational warning for demo day:** a stale backend process was found serving old code without
`/api/route/alternatives` registered. **Restart the backend before demoing** —
`backend/.venv/Scripts/python.exe -m uvicorn floodnet.api.main:app --port 8000`. Also do not run the test
suite while the demo backend is serving: the same suite took 97 s idle vs 279 s under contention.

---

## 12. Problem-statement compliance (independently re-verified against code, not docs)

| Requirement | Status |
|---|---|
| 0–3 h horizon | `COMPLETE` |
| High-resolution DEM | `COMPLETE` w/ caveat — real MCGM 20 cm contours → 10 m grid; 0.65% of cells flagged (not altered) as inconsistent with surveyed manholes |
| Runoff / imperviousness | `PARTIAL` — real rational method, but imperviousness is an uncalibrated OSM-geometry rule with a flat 0.6 default |
| 2D surface routing | `COMPLETE` w/ documented limitation — diffusive-wave storage-cell, no lateral momentum |
| Graph drainage network | `COMPLETE` — real MCGM geometry/connectivity/inverts |
| Hydraulic capacity | `PARTIAL` — real geometry, but a single assumed Manning n = 0.013 network-wide |
| Blockage / overcapacity | `COMPLETE` as a scenario tool; never presented as observed |
| Surcharge | `COMPLETE` · **Tidal/tailwater backflow: `MISSING`** — outfalls are unrestricted free sinks; grep-confirmed absent, and this is Hindmata's actual real-world failure mode |
| Street depth in cm | `COMPLETE` — metres internally, ×100 at the API boundary, 8 call sites checked, no unit bugs |
| GIS dashboard | `COMPLETE` — no mock/random data anywhere in the operational dashboard (grep-verified; the only `Math.random` is decorative hero-animation particle jitter) |
| Flood-safe routing API | `COMPLETE` |
| Provenance | `COMPLETE` — genuinely data-driven, traced to real construction sites for rainfall, DEM and drainage |
| **Rainfall nowcast (SR-01)** | **`PARTIAL` / `BLOCKED`** — no radar nowcast exists; and per §8 the gridded-input gap is in **our own engine interface**, not only in data access |
| Real-time operation | `PARTIAL` — ~90–150 s per full run, honestly reported |

---

## 13. Known limitations (stated, not hidden)

1. **No tidal/tailwater boundary** — the dominant real Mumbai mechanism. All 116 outfalls accept unlimited
   flow; 19 are bbox-clip artefacts. The model therefore **under**-predicts drainage-caused flooding by an
   unquantified amount, and attributes ~97% of surcharge causes to `downstream` partly because of it.
2. **Surcharge cannot flood a street** (§4) — drainage can only fail to drain. Any future UI copy must
   respect this.
3. **Per-location causal attribution is not supported** (§4) — only ward-level counterfactuals are.
4. **Inlet capacity (0.05 m³/s) and manhole storage (1.5 m²) are blanket assumptions**, not measurements —
   and the inlet cap is precisely the constant at which the surcharge signal saturates, so surcharge
   *magnitude* is an artefact of an uncalibrated number.
5. **`drainage/attributes.py` is dead code in production.** It holds a better-cited parameter table
   (arch conduits at n = 0.015) and `refine_attributes()` is never called — the live path uses a blanket
   0.013 for all 1,134 conduits including 62 British-era brick arch drains. Correcting this is a ~13%
   capacity change on those conduits, so it is a physics change requiring the full validation gate. `PLANNED`.
6. **HGL is capped at ground level**, so there is no surcharge *severity* — two spilling manholes cannot be
   ranked.
7. **Rainfall is spatially uniform** for every provider (§8).
8. **No calibration against observed flood depths** — none exist publicly. Mass conservation validates
   numerical accounting, **not predictive accuracy**.
9. **Latent leak:** 10 off-grid non-outfall nodes would silently discard surcharge. Currently 0.0 m³, so
   mass balance still closes, but a different bbox would lose water with no error surfaced.
10. **Frame prefetch pulls ~49 MB per run**, 74.5% of it the `streets` payload re-sent identically each frame.
11. **No browser QA has been performed** (see the caveat at the top).

---

## 14. Important files

**Frozen scientific core** — changes require benchmark + mass-balance + frame-by-frame validation:
`simulation/engine.py` · `terrain/surface.py` (optimised this session, bit-identical) ·
`drainage/hydraulics.py` (**deliberately untouched**) · `terrain/runoff.py` · `routing/router.py`
(additive only) · `streets/aggregate.py`

**API:** `api/main.py` (`serialize_frame`, `summarize`, `series`, `explain`, `compare`, route endpoints) ·
`api/schemas.py` · `api/state.py` · `contracts.py` · `provenance.py`

**Data:** `data/mcgm.py` · `data/contours.py` · `data/osm.py` · `data/build_pilot.py` ·
`data/processed/pilot/PROVENANCE.md` (auto-generated from the same code path that builds the data)

**Frontend:** `state/FloodNetContext.jsx` (single source of timestep truth) · `components/MapView` ·
`components/RoutePlanner` · `components/ScenarioPanel` · `components/WhyFloodedPanel` · `lib/locations.js`

**Docs:** `docs/REQUIREMENTS.md` · `docs/DECISIONS.md` (D-14 radar, D-15 engine gap, open licence
question) · `docs/LIVE_RAINFALL_AUDIT.md` (§8b/§8c) · `docs/VALIDATION.md` · `docs/PERFORMANCE_OPTIMIZATION.md`

---

## 15. Git state

**Nothing committed, nothing pushed** — the entire working tree is left for review.

```
M backend/floodnet/api/main.py          M frontend-react/src/components/LandingPage/LandingPage.jsx
M backend/floodnet/api/schemas.py       M frontend-react/src/components/LandingPage/LandingPage.module.css
M backend/floodnet/routing/router.py    M frontend-react/src/components/LayerControl/LayerControl.jsx
M backend/floodnet/terrain/surface.py   M frontend-react/src/components/LayerControl/LayerControl.module.css
M backend/tests/test_routing_router.py  M frontend-react/src/components/LocationSelector/LocationSelector.jsx
M docs/DECISIONS.md                     M frontend-react/src/components/MapView/MapView.jsx
M docs/LIVE_RAINFALL_AUDIT.md           M frontend-react/src/components/RoutePlanner/RoutePlanner.jsx
M docs/REQUIREMENTS.md              M frontend-react/src/components/RoutePlanner/RoutePlanner.module.css
M frontend-react/src/api/client.js      M frontend-react/src/components/ScenarioPanel/ScenarioPanel.jsx
                                        M frontend-react/src/components/ScenarioPanel/ScenarioPanel.module.css
                                        M frontend-react/src/components/WhyFloodedPanel/WhyFloodedPanel.jsx
                                        M frontend-react/src/state/FloodNetContext.jsx
?? frontend-react/src/components/LandingPage/HeroVisual.jsx      ?? frontend-react/src/components/ui/
?? frontend-react/src/components/LandingPage/HeroVisual.module.css  ?? frontend-react/src/lib/locations.js
                                                                 ?? frontend-react/src/lib/utils.js
```

Last commit: `8f52f7e fix: restore stable FloodNet landing experience`

---

## 16. The judge questions, answered from implemented evidence

| Question | Implementation | Limitation |
|---|---|---|
| **WHERE** will it flood? | Per-segment depth, max grid depth in a 6 m buffer around 2,978 real OSM segments | 10 m grid; a single deep cell (possibly a DTM pit) sets the segment value |
| **WHEN?** | 37 real frames, 5-min spacing; measured 15-min rain→surcharge lag | Only as trustworthy as the driving rainfall — NWP or replay, never a radar nowcast |
| **HOW DEEP?** | cm at the API boundary, metres internally, unit-checked | DEM vertical SD 0.283 m vs surveyed manholes — cm display exceeds terrain accuracy |
| **WHY?** | `/explain`: nearest node, real `edge_util`, `node_cause`, ground elevation | Cause is a 3-way threshold heuristic; **nearest-node is proximity, not a flow path** |
| **WHAT is happening in drainage?** | 3 tiers per node per frame + full aggregate series with lead-time signals | No surcharge severity (HGL capped); no tidal backflow |
| **CAN a vehicle pass?** | `passable()` vs `VEHICLE_LIMIT_CM` (car/motorcycle 30, ambulance 40, truck/pedestrian 60) | `config.py` labels these "WORKING THRESHOLDS, NOT CITED GUIDANCE" |
| **WHICH route is safer?** | 3 objective-scored candidates + safe-through-T+X, on the real flood-weighted OSM graph | "Fastest" is distance-only; no traffic data |
| **WHERE did the data come from?** | Per-field `Provenance(tag, source, note)` populated at point of use, surfaced in the API and UI | The notes honestly carry the limitations above |
