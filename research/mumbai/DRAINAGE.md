# Mumbai Stormwater Drainage Data — Research Report

**Worker:** Research Worker 3 · **Date probed:** 2026-09-08 · **City:** Mumbai (locked)
**All findings below were obtained by actually fetching the endpoints listed.** Anything not fetched is explicitly marked UNVERIFIED.

---

## VERDICT: **A — directly usable hydraulic network**

MCGM publishes, anonymously and without a token, a **complete directed stormwater network with full hydraulic attribution** through a public ArcGIS REST MapServer. Layer 7 (`Storm Water Drains`, 34,711 polylines) carries `US_NODE_ID`, `DS_NODE_ID`, `CONDUIT_LE` (m), `SHAPE_1` (CIRC/OREC/RECT/ARCH), `CONDUIT_WI` (mm), `CONDUIT_HE` (mm), **`US_INVERT` and `DS_INVERT` in mTHD** — and layer 6 (`Storm Water Manholes`, 34,431 points) carries `NODE_ID` + `GROUND_LEV`. I verified by full download and analysis that **every one of these fields is populated in 100% of records — zero nulls and zero zeros** — that **all 34,711 conduits resolve both endpoints against the 34,431 unique, non-duplicated node IDs (100.00% referential integrity)**, that 5,000/5,000 sampled conduit geometries have both ends within 2 m of their declared node points, that 99.6% of computed slopes are positive with a median of 0.00279, and that `GROUND_LEV − US_INVERT` is positive for **all 34,711** conduits (median cover 2.33 m). This is not a cartographic drain line layer: it is an engineering hydraulic model — almost certainly the BRIMSTOWAD network exported from InfoWorks/MIKE URBAN (the `!`/`!!`/`!!!` node-name suffixes and the `US_INVERT`/`DS_INVERT`/`CONDUIT_*` schema are that software's signature) — and it is directly loadable into SWMM or into our own graph solver. **We have real invert elevations.** The genuine remaining work is not synthesis of the network but four bounded engineering tasks: resolving the mTHD vertical datum offset before mixing with any DEM, deriving node inverts from connected conduit inverts, assigning roughness, and filtering the 11,246 `Proposal` conduits out of the 23,465 `Existing` ones.

---

## What I probed and what responded

| # | URL | Result |
|---|---|---|
| 1 | `https://prsrvgisapp.mcgm.gov.in/server/rest/services?f=json` | **HTTP 200**, 198 B. Folders: `Admin, CEMETERY_MANAGEMENT, esri, ESTATE, mcgm, projmap, SOMobileApp, TRENCHING, Utilities`. ArcGIS Server 10.91 |
| 2 | `.../mcgm/MCGMGIS_Departments_Master_All_Layers/MapServer?f=json` | **HTTP 200**, 23,872 B. 121 layers. serviceDescription: `"All Department GIS Layers for SAP PS GIS usage, (202001091) Added DP Ward layer"` |
| 3 | `.../MapServer/layers?f=json` (primary lead) | **HTTP 200**, 895,578 B. Full field definitions for all 121 layers |
| 4 | `.../MapServer/{6,7,3,4,9,153,344,345,226}/query?where=1=1&returnCountOnly=true&f=json` | **HTTP 200** all. Counts in inventory below |
| 5 | `.../MapServer/7/query?where=1=1&outFields=*&resultRecordCount=5&outSR=4326&f=json` | **HTTP 200**. Real populated attribute values (reproduced verbatim below) |
| 6 | `.../MapServer/6/query?...` | **HTTP 200**. Real node IDs + ground levels |
| 7 | `.../MapServer/7/query?...&resultOffset={0,8000,16000,24000,32000}&resultRecordCount=8000` | **HTTP 200** ×5. **Full download of all 34,711 conduits with geometry succeeded** |
| 8 | `.../MapServer/6/query?...&resultOffset={0..32000}` | **HTTP 200** ×5. **Full download of all 34,431 nodes with geometry succeeded** |
| 9 | 12 × `.../MapServer/7/query?where=<field> IS NULL / =0&returnCountOnly=true` | **HTTP 200** all. Every count `0` (see null audit) |
| 10 | `.../MapServer/{344,345,9,217,301,0}/query?...outFields=*` | **HTTP 200**. Sample values captured |
| 11 | `.../MapServer/7/query?...&f=geojson` | **HTTP 200**. **Native GeoJSON output supported** — ingestion is trivial |
| 12 | `.../services/{mcgm,Utilities,Admin,projmap,SOMobileApp,TRENCHING,ESTATE}?f=json` | **HTTP 200** all. Service lists captured |
| 13 | `.../mcgm/External_Utilities/MapServer?f=json` | **HTTP 200**. Telecom/power utilities only — no drainage |
| 14 | `.../SOMobileApp/MCGMGIS_SODashboard_N/MapServer?f=json` | **HTTP 200**. Sewerage-operations layers only — no stormwater |
| 15 | `https://overpass-api.de/api/interpreter` (POST, Mumbai bbox) | **HTTP 200**. 716 ways + 23 nodes total |

**No TLS problems, no auth, no token, no rate limiting encountered.** Plain `curl -s` worked throughout; `-k` was never needed. The service is fully anonymous and supports pagination (`supportsPagination: true`), statistics, order-by, and SQL expressions.

**Reproducibility caveat:** this is a government server that has historically been intermittent. Because a full download succeeded today, **we should snapshot the data to disk immediately and not depend on live availability during the hackathon demo.**

---

## Service / layer inventory

Service: `mcgm/MCGMGIS_Departments_Master_All_Layers/MapServer` — 121 layers. Native CRS **EPSG:32643 (WGS84 / UTM zone 43N)**; server reprojects on request via `outSR=4326`. `maxRecordCount` = 8000 on all queried layers.

### Core drainage layers

| Layer id | Name | Geometry | Feature count | VERIFIED? | Class |
|---|---|---|---|---|---|
| **7** | **Storm Water Drains** | Polyline | **34,711** | ✅ fetched, fully downloaded + analysed | **A** |
| **6** | **Storm Water Manholes** | Point | **34,431** | ✅ fetched, fully downloaded + analysed | **A** |
| 229 | Storm Water Drainage | Group | — | ✅ (container for 6, 7) | — |
| 3 | Manhole (sewerage) | Point | 97,208 | ✅ count + fields fetched | B/A* |
| 4 | Sewer Line (sewerage) | Polyline | 97,955 | ✅ count + fields fetched | B/A* |
| 9 | Pumping Stn (sewerage) | Point | 58 | ✅ fetched w/ samples | C |
| 153 | Nala and Others | Polyline | 106 | ✅ count + fields fetched | **C** |
| 226 | Road Culvert | Polyline | 5,101 | ✅ count + fields fetched | **C** |
| 293 | SWD / Water (IPVS) | Point | not counted | ⚠️ fields only | C |

\* Layers 3/4 are the **foul sewerage** network, not stormwater. They carry `DINVT_LVL` (Pipe Invert Level m THD), `DPIPE_DIM1/2`, `DPIPE_MAT_CD`, `NODE_NO`/`DN_NODE`, `COV_LVL`, `MH_DP` — so they are potentially Class A too, but **null rates were not audited** and they are out of scope for a stormwater nowcast (relevant only if we later model combined-system interaction).

### Supporting layers relevant to the wider project

| Layer id | Name | Geometry | Count | VERIFIED? | Note |
|---|---|---|---|---|---|
| **344** | **Flooding Spots** | Polygon | **937** | ✅ fetched w/ samples | **Chronic waterlogging inventory — validation gold** |
| **345** | **Flow Level Sensor** | Point | **5** | ✅ fetched w/ samples | Mithi + Poisar river level sensors |
| 346 | Vulnerable Settlements | Polygon | — | ⚠️ listed only | Exposure layer |
| **301** | **Contour_20CM** | Polyline | **284,403** | ✅ count + sample | **20 cm contours — DEM source** |
| 217 | Mumbai Contour | Polyline | 284,403 | ✅ count + sample | Same geometry, fewer fields |
| **0** | **Ward_Boundary** | Polygon | **26** | ✅ fetched w/ samples | Has `SUM_POPULA`, `ZONE`, `DIVISION` |
| 238 | Wards | Polygon | 24 | ✅ count | 24 admin wards |
| 237 / 341 | Wards / Wards_DP | Polygon | — | ⚠️ listed only | Duplicates |
| 156 | Road Centerline | Polyline | — | ⚠️ fields only | |
| 239 | Water Bodies | Polygon | — | ⚠️ listed only | |
| 213/214/221/232 | Fire Stations / Hospitals / Police / Shelters | Point | — | ⚠️ listed only | Disaster-management group (210) |

**There is NO dedicated outfall layer, and NO stormwater pumping-station layer**, in any of the 121 layers or any of the other services. I keyword-scanned all 121 layer names for `outfall, nala, nalla, drain, storm, swd, creek, river, water, culvert, catch, gully, pump, flood, sewer, manhole, chamber, pit, contour`. Layer 9 `Pumping Stn` sits under the **Sewerage Operations** group, and its samples (`TANK BUNDER`, `VERSOVA WWTF`) are sewerage/WWTF assets with `MIN_LVL`/`MAX_LVL` **null**. Mumbai's SWD pumping stations (Haji Ali, Irla, Love Grove, Cleveland Bunder, Britannia, Gazdarbandh, Mogra) are **UNVERIFIED — not found as a distinct layer.**

---

## Drainage layers in detail

### Layer 7 — `Storm Water Drains` (EDGES) — 34,711 features

Complete field list as returned by `/7?f=json`:

| Field | Type | Alias (verbatim from server) |
|---|---|---|
| `OBJECTID` | OID | FID |
| `US_NODE_ID` | String(30) | Up Stream Node Id |
| `DS_NODE_ID` | String(30) | Down Stream Node Id |
| `CONDUIT_LE` | Double | Conduit Length (m) |
| `SHAPE_1` | String(32) | Conduit Shape |
| `CONDUIT_WI` | Double | Conduit Width (mm) |
| `CONDUIT_HE` | Double | Conduit Height (mm) |
| `US_INVERT` | Double | Up Stream Invert Level (mTHD) |
| `DS_INVERT` | Double | Down Stream Invert Lveel (mTHD) *(sic — typo is the server's)* |
| `USER_TEXT2` | String(100) | Type |
| `SHAPE` | Geometry | SHAPE |
| `SHAPE.LEN` | Double | SHAPE.LEN |

**Sample records, verbatim from the server (`outSR=4326`):**

```json
{"OBJECTID":1,"US_NODE_ID":"1!!!","DS_NODE_ID":"2!!!","CONDUIT_LE":8.2,
 "SHAPE_1":"RECT","CONDUIT_WI":2500.0,"CONDUIT_HE":1500.0,
 "US_INVERT":26.55,"DS_INVERT":26.534,"USER_TEXT2":"Existing",
 "geometry":{"paths":[[[72.83909123398863,19.06393970003224],
                       [72.83916917643138,19.063936097133496]]]}}

{"OBJECTID":2,"US_NODE_ID":"1!!","DS_NODE_ID":"2171104704","CONDUIT_LE":24.5,
 "SHAPE_1":"RECT","CONDUIT_WI":2100.0,"CONDUIT_HE":2850.0,
 "US_INVERT":25.559,"DS_INVERT":25.519,"USER_TEXT2":"Existing"}

{"OBJECTID":3,"US_NODE_ID":"1!","DS_NODE_ID":"2!","CONDUIT_LE":191.0,
 "SHAPE_1":"RECT","CONDUIT_WI":1500.0,"CONDUIT_HE":1500.0,
 "US_INVERT":26.015,"DS_INVERT":25.812,"USER_TEXT2":"Existing"}

{"OBJECTID":4,"US_NODE_ID":"1","DS_NODE_ID":"2","CONDUIT_LE":15.8,
 "SHAPE_1":"RECT","CONDUIT_WI":2000.0,"CONDUIT_HE":2000.0,
 "US_INVERT":25.245,"DS_INVERT":25.225,"USER_TEXT2":"Existing"}
```

**Null / zero audit — server-side counts, all 12 queries returned `{"count":0}` against a population of 34,711:**

`US_INVERT IS NULL` → 0 · `DS_INVERT IS NULL` → 0 · `US_INVERT=0` → 0 · `DS_INVERT=0` → 0 · `CONDUIT_WI IS NULL` → 0 · `CONDUIT_WI=0` → 0 · `CONDUIT_HE IS NULL` → 0 · `US_NODE_ID IS NULL` → 0 · `DS_NODE_ID IS NULL` → 0 · `CONDUIT_LE IS NULL` → 0 · `CONDUIT_LE=0` → 0 · `SHAPE_1 IS NULL` → 0

**Value distributions (computed over the full 34,711-record download):**

- `SHAPE_1`: `CIRC` 15,557 · `OREC` 10,777 · `RECT` 7,138 · `ARCH` 1,237 · `circ` 1 · `rect` 1 *(two case-inconsistent records — trivial cleanup)*
- `USER_TEXT2`: **`Existing` 23,465 (684.8 km)** · **`Proposal` 11,246 (565.5 km)**
- `CONDUIT_LE`: min 1.0 m, max 1,345.7 m, total 1,250.3 km
- `CONDUIT_WI`: 230 – 160,000 mm; `CONDUIT_HE`: 230 – 12,000 mm
- Existing CIRC diameters: min 230, p25 600, **p50 600**, p75 900, max 12,000 mm
- Existing box sections (W×H mm) e.g. 2500×1500, 2100×2850, 1500×1500, 7500×2423, 3000×2000
- `US_INVERT` 17.08 – 176.60 mTHD; `DS_INVERT` 17.00 – 167.00 mTHD

### Layer 6 — `Storm Water Manholes` (NODES) — 34,431 features

| Field | Type | Alias |
|---|---|---|
| `OBJECTID` | OID | FID |
| `NODE_ID` | String | Node Id |
| `GROUND_LEV` | Double | Ground Level |
| `SHAPE` | Geometry | SHAPE |

**Sample records, verbatim (`outSR=4326`):**

```
NODE_ID '1'     GROUND_LEV 28.6   (72.85677260087633, 19.063947337680972)
NODE_ID '1!'    GROUND_LEV 28.1   (72.83364740634491, 19.080649179536596)
NODE_ID '1!!'   GROUND_LEV 28.3   (72.82826542824886, 19.076277336766658)
NODE_ID '1!!!'  GROUND_LEV 28.6   (72.83909123398863, 19.063939700032240)
NODE_ID '1!!!!' GROUND_LEV 30.0   (72.84795305594275, 19.045406407524370)
NODE_ID '10'    GROUND_LEV 28.85  (72.83544830543882, 19.088571974157320)
NODE_ID '10!'   GROUND_LEV 28.6   (72.83893561747165, 19.063339922642086)
NODE_ID '100!'  GROUND_LEV 29.3   (72.84190902099114, 19.062619276863366)
```

Note node `1!!!` at (72.83909123398863, 19.06393970003224) is **bit-identical** to the first vertex of conduit OBJECTID 1, whose `US_NODE_ID` is `1!!!`.

**Null audit (population 34,431):** `GROUND_LEV IS NULL` → 0 · `GROUND_LEV=0` → 0 · `NODE_ID IS NULL` → 0
**`GROUND_LEV` range:** 24.18 – 177.06 mTHD, mean 32.03

### Verified network integrity (computed over the complete downloads)

| Check | Result |
|---|---|
| Unique `NODE_ID` values | **34,431 / 34,431 — zero duplicate collisions** |
| Conduits whose `US_NODE_ID` resolves to a node | **34,711 / 34,711 = 100.00%** |
| Conduits whose `DS_NODE_ID` resolves to a node | **34,711 / 34,711 = 100.00%** |
| Conduits with **both** ends resolving | **34,711 / 34,711 = 100.00%** |
| Geometry vs node coords: both endpoints within 2 m of declared node (first 5,000 conduits) | **5,000 / 5,000** |
| Slope `(US_INVERT−DS_INVERT)/CONDUIT_LE` positive | 34,566 (99.6%) · flat 119 (0.3%) · **adverse 26 (0.1%)** |
| Slope percentiles | p5 0.00035 · **p50 0.00279** · p95 0.02323 |
| Cover depth `GROUND_LEV − US_INVERT` **negative** | **0 out of 34,711 (0.0%)** · p5 1.19 m · p50 2.33 m · p95 4.60 m |
| Directed graph, all conduits | 34,345 nodes touched · **399 sinks (candidate outfalls)** · 2,807 sources · max out-degree 9 · max in-degree 6 |
| Directed graph, `Existing` only | 24,035 nodes touched · 963 sinks · 2,122 sources |
| Undirected connected components (all 34,431 nodes) | **412 components**; largest five 5,490 / 2,909 / 2,829 / 2,631 / 2,213; **86 isolated nodes** |
| Spatial extent (nodes) | lon **72.7762 – 72.9719** · lat **18.8947 – 19.2658** — full Greater Mumbai |
| North/south distribution | 25,012 nodes south of 19.10 N (island city) · 9,419 north (suburbs) |

The zero-negative-cover result is the strongest single piece of evidence: `GROUND_LEV` lives on layer 6 and `US_INVERT` on layer 7, two separately-served tables, and they are **physically consistent in 100% of 34,711 independent comparisons**. Random or placeholder values cannot do that.

---

## Attribute gap analysis vs. requirements

### NODES (required: id, x/y, rim/ground elevation, invert elevation, node type)

| Requirement | Status | Source |
|---|---|---|
| Node ID | ✅ **HAVE** | `NODE_ID`, 34,431 unique, 100% join coverage |
| Location x/y | ✅ **HAVE** | SHAPE point, EPSG:32643 → 4326 on request |
| Ground / rim elevation | ✅ **HAVE** | `GROUND_LEV` mTHD, 0% null |
| **Invert elevation** | ⚠️ **DERIVE** | Not a node field. Derive `node_invert = min(US_INVERT of outgoing, DS_INVERT of incoming)`. Standard SWMM practice, fully supported here because conduit inverts are 100% populated and 100% joined. |
| Node type (junction/outfall/storage) | ❌ **INFER** | No type field. Infer outfalls from the **399 graph sinks** (963 for `Existing`-only), filtered by coastline/creek proximity. |

### EDGES (required: start/end node, geometry, length, size, slope/inverts, material, roughness, direction)

| Requirement | Status | Source |
|---|---|---|
| Start node | ✅ **HAVE** | `US_NODE_ID`, 100% resolves |
| End node | ✅ **HAVE** | `DS_NODE_ID`, 100% resolves |
| Direction / connectivity | ✅ **HAVE** | US→DS is explicit; graph is a coherent DAG-like drainage tree with 399 sinks |
| Geometry | ✅ **HAVE** | SHAPE polyline; endpoints verified against node points |
| Length | ✅ **HAVE** | `CONDUIT_LE` (m), 0% null; agrees with `SHAPE.LEN` |
| Diameter / W×H | ✅ **HAVE** | `CONDUIT_WI`, `CONDUIT_HE` (mm), 0% null, plus `SHAPE_1` cross-section code |
| **Slope / US+DS inverts** | ✅ **HAVE** | `US_INVERT`, `DS_INVERT` (mTHD), 0% null, 99.6% physically sane |
| Material | ❌ **ASSUME** | Not present on layer 7. (Layer 4 *sewer* has `DPIPE_MAT_CD`, but that is the foul network.) |
| Roughness (Manning's n) | ❌ **ASSUME** | Not present. Must assign by `SHAPE_1` + assumed material and **document the assumption**. |

**Scorecard: 9 of 9 make-or-break attributes are present or cleanly derivable. Only material and roughness must be assumed — and roughness is a parameter that is calibrated in real practice anyway.**

### Known data issues to handle (all bounded, all quantified)

1. **Vertical datum — the single biggest integration risk.** Elevations are **mTHD (Town Hall Datum)**, not MSL and not EGM96. Minimum ground level across the entire network is 24.18 mTHD. The THD→MSL offset is **UNVERIFIED**; my search returned only historical context (THD is a benchmark 100 ft below a mark on the Town Hall steps) and **no authoritative numeric offset**. Since coastal Mumbai ground is a few metres above MSL, the offset is *plausibly* ~20–24 m, but **that is an inference, not a verified figure.** It **must be calibrated empirically** (regress `GROUND_LEV` against a known-datum DEM over the 34,431 node points) before any DEM, tide, or sea-level data is mixed in. Note layer 344 has a `HEIGHT_TO_MSL` field (null in both samples seen), implying MCGM itself treats MSL as a separate quantity.
2. **`Proposal` conduits.** 11,246 of 34,711 (565.5 km) are `USER_TEXT2 = 'Proposal'` — BRIMSTOWAD proposed works whose construction status is unknown. **Filter to `Existing` (23,465 conduits, 684.8 km) for a present-day model**; the `Proposal` set is a bonus "what-if upgrade" scenario.
3. **412 disconnected components, 86 isolated nodes.** Partly genuine (Mumbai drains to sea/creek through many independent catchments), partly data gaps. Keep the large components; treat small orphans as noise.
4. **26 adverse-slope and 119 flat conduits** (0.4% combined) need a small fix-up pass for solver stability.
5. **Two case-inconsistent `SHAPE_1` values** (`circ`, `rect`) — normalise to uppercase.
6. **No outfall boundary conditions.** The 399 sinks have no tide/tailwater attribute. Mumbai's flooding is driven by high-tide-plus-heavy-rain coincidence, so **tide level must come from a separate source** and be imposed at outfall nodes.
7. **`CONDUIT_WI` max 160,000 mm (160 m)** — this is a wide open nallah cross-section, plausible for a major nallah but worth spot-checking a handful of extreme records.
8. **Currency unknown.** No `description`, no `copyrightText`, no date field on layers 6/7. Vintage is **UNVERIFIED**.

---

## Other sources checked

| Source | What I did | Result |
|---|---|---|
| **OpenStreetMap** (Overpass API, POST, Mumbai bbox 18.89–19.28 N, 72.77–72.99 E) | Counted `waterway~drain\|ditch\|canal`, `man_made=manhole`, `manhole=*`, `tunnel=culvert` | **716 ways + 23 nodes total.** No inverts, no diameters, no US/DS connectivity. **Class C at best** — two orders of magnitude worse than MCGM's 34,711 attributed conduits. Not worth using except possibly as a visual cross-check. |
| **Other MCGM ArcGIS services** — `External_Utilities`, `SODashboard_N`, `RoadInfo`, `Admin_Boundaries`, `3D_Road_Network`, `Trenching_Application`, `SAP_RE_GIS_Integration`, `Geometry`, `RasterUtilities` | Enumerated all 7 non-esri folders; opened `External_Utilities` and `SODashboard_N` | `External_Utilities` = Jio/Airtel/Tata/Adani/Excel telecom + power lines + a `Ward_Boundary`. `SODashboard_N` = sewerage ops (`NetJunctions`, `Street Connection`, `Lateral Line`, `SewerLine Gradewise`, `Manhole RWTS`…). **Neither adds stormwater data.** The `MCGMGIS_Departments_Master_All_Layers` service is the complete story. |
| **data.gov.in / MCGM open data** | Web search | **No downloadable SWD shapefile found.** Search surfaced MCGM SWD tender PDFs and the SWD RTI manual (which states the system is >100 years old and ~480 km long — consistent with our 684.8 km of `Existing` including minor drains). **Irrelevant now** — the REST service supersedes any portal download. |
| **BRIMSTOWAD literature** | Web search | Confirms BRIMSTOWAD began 1985, studied to 1993, and that MCGM tenders "survey, hydraulic modelling and analysis of the storm water drain network". **Corroborates the provenance** of layers 6/7 as a real hydraulic model, but I found **no published network file** — we don't need one. |
| **GitHub** | Web search | `sanjanakrishnan/mumbai_spatial_data` — MCGM-region **boundary** GeoJSONs only, no drainage network. **UNVERIFIED (not fetched)** — superseded anyway. |
| **iFLOWS-Mumbai** | Search result only (`nccr.gov.in` PDF) | India's official Mumbai flood-warning system. **UNVERIFIED — not fetched.** Worth a look as a prior-art/benchmark reference, not as a data source. |

---

## Synthetic-network fallback requirements

**Not required.** The verdict is A, so we do **not** need to synthesise a network. Recorded only as contingency should the server become unreachable and our snapshot be lost:

- **Inputs that would be needed:** road centrelines (layer 156) as the alignment proxy; a DEM — buildable from **layer 301 `Contour_20CM`, 284,403 twenty-centimetre contours**; outfall locations (would have to be hand-digitised from the coastline — no outfall layer exists); ward boundaries (layer 0) for catchment subdivision; assumed pipe sizes from a design-storm rational-method calculation.
- **What such a model could claim:** *relative* flood susceptibility ranking between locations; sensitivity to rainfall intensity; plausible flow directions following topography.
- **What it could NOT claim, and must never be presented as claiming:** absolute water depths, actual surcharge timing, real capacity exceedance, or any statement about a specific named street's drain. A synthetic network's inverts are invented, so the hydraulic grade line is fictional.
- **Honesty constraint:** every synthetic attribute would have to be flagged as such in the data model (e.g. an `is_synthetic` column per attribute) and stated on-screen in the demo.

**Given verdict A, the honesty posture is the opposite and much stronger: we can state that our hydraulic network is MCGM's own published BRIMSTOWAD data — real inverts, real sizes, real connectivity — with only roughness and material assumed, and the vertical datum offset calibrated.**

---

## What I could not verify

1. **THD → MSL/EGM96 datum offset.** No authoritative numeric value found. Must be calibrated against a known-datum DEM. **This is the top open engineering question.**
2. **Data vintage / currency of layers 6 and 7.** No date, description, or copyright metadata on either layer.
3. **Whether `Proposal` conduits have since been built.** No status or completion-date field.
4. **Null rates on the sewerage layers 3 and 4** (97,208 manholes / 97,955 sewer lines). Fields look Class A (`DINVT_LVL`, `COV_LVL`, `MH_DP`, `DPIPE_MAT_CD`, `NODE_NO`/`DN_NODE`) but I did not audit them — out of scope for stormwater.
5. **Existence of a stormwater pumping-station dataset.** Not found in 121 layers or any other service; layer 9 is sewerage. Mumbai's SWD pumping stations are **not represented** in what I could reach.
6. **Layer 344 `Flooding Spots` completeness.** 937 polygons confirmed and two samples read (one is named `"Chittrajan Nagar (Delete) "` — the layer contains soft-deleted records needing filtering); I did not audit `DEPTH`/`AFFECT_POPULATION` null rates.
7. **Licensing / terms of use.** `copyrightText` on the service root is `"MCGM, Esri India"`; layer-level copyright is empty. **No explicit open licence was found.** For a hackathon this is normal, but we should credit MCGM and not claim redistribution rights.
8. **Whether the server is reliably available.** It responded fast today (0.25–0.9 s). Historical reliability unknown — **snapshot the data now.**

---

## Recommended immediate action (for the lead to decide, not me)

Snapshot layers **7** (34,711 conduits) and **6** (34,431 nodes) via `f=geojson` with `outSR=4326` and `resultOffset` paging at 8,000/page — 5 requests each, already proven to work — plus **344** (Flooding Spots), **0** (Ward_Boundary), **345** (Flow Level Sensor), and, if a DEM is wanted, **301** (Contour_20CM, 284,403 features ≈ 36 paged requests). Working copies from this investigation are in the scratchpad at
`C:\Users\MOHAMM~1\AppData\Local\Temp\claude\<project>\9b0f9430-0cd1-465b-adc3-0fd4e749df3e\scratchpad` as `c7_*.json` and `n6_*.json` (Esri JSON with geometry, EPSG:4326).
