# Mumbai DEM Evidence — Research Worker 2

Scope: FloodNet Urban Flood Nowcasting, target city Mumbai. Evidence-gathering only — no project decisions made here. All claims below are tagged VERIFIED (I fetched/tested the endpoint myself in this session), DOCUMENTED (I read it in official/vendor documentation but did not myself download/test it), or UNVERIFIED (could not confirm, stated explicitly).

---

## Summary verdict

**Pragmatic choice for the 12-hour prototype: Copernicus GLO-30 DEM (ESA/AWS), used as a DSM, with an explicit caveat that it cannot resolve individual streets, or FABDEM (Copernicus GLO-30 with buildings/forest statistically removed) if a closer-to-bare-earth surface is preferred and the non-commercial licence is acceptable.**

Reasoning: it is the only candidate I could **directly, anonymously, and instantly** fetch a real Mumbai tile from in this session (see "Verified access" below) — no login, no approval queue, no API key. Its documented vertical accuracy (≤4 m LE90 globally, ≤3 m LE90 for 95% of EEA cells; independent LiDAR validation shows LE90 ≈ 7.7 m) is 30 m-class, same tier as every other freely obtainable global DEM. Nothing freely and immediately obtainable for Mumbai gets below 30 m horizontal resolution with a documented, independently validated vertical accuracy. **No candidate in this list can distinguish flooding between two adjacent streets in central Mumbai's dense low-rise fabric** (typical street width 6–15 m, i.e. sub-pixel at 30 m). The honest limitation statement that must accompany any demo: *"This prototype uses a 30 m-resolution, building/tree-inclusive global DEM. It can show which local drainage basin/pocket floods and roughly how deep, but it cannot resolve street-by-street differences within a basin, and cannot be presented as a true micro-topographic model."*

---

## DEM candidate comparison table

| Source | Native h-res | Vertical accuracy (documented) | DSM or DTM | Licence | Registration needed | Access verified? |
|---|---|---|---|---|---|---|
| **Copernicus GLO-30** (ESA, via AWS S3) | 30 m (1 arcsec) | ≤4 m LE90 spec; ≤3 m LE90 (95% of EEA cells); independent LiDAR validation ≈7.7 m LE90 globally [dataspace.copernicus.eu handbook](https://dataspace.copernicus.eu/sites/default/files/media/files/2024-06/geo1988-copernicusdem-spe-002_producthandbook_i5.0.pdf) | **DSM** (includes buildings, canopy — derived from TanDEM-X radar) | Copernicus DEM licence, free, permissive, attribution requested | **None** for the public AWS S3 bucket | **VERIFIED** — fetched tile listing and actual GeoTIFF header for Mumbai tile with plain `curl`, HTTP 200, no auth |
| **Copernicus GLO-30** (via OpenTopography) | 30 m | same as above | DSM | Copernicus licence | Free account + API key (my.opentopography.org) | **VERIFIED (partially)** — hit the API without a key and got a clean `401`, confirming the key requirement is real and enforced; account creation is self-service (email activation), not an approval queue — DOCUMENTED, not tested end-to-end |
| **FABDEM** (Bristol Univ., Fathom) | 30 m (1 arcsec), derived from GLO-30 | Mean absolute error reduced to 1.12 m in built-up areas vs 1.61 m for raw GLO-30 (per Fathom's own validation) [fathom.global](https://www.fathom.global/academic-papers/a-30-m-global-map-of-elevation-with-forests-and-buildings-removed/) — this is a *bias-correction* metric, not an independent LE90; treat as DOCUMENTED not independently confirmed | **Near-DTM** — ML-based removal of building and forest height bias from GLO-30, explicitly built for hydrology | **CC BY-NC-SA 4.0 — non-commercial only** | data.bris.ac.uk portal; Google Earth Engine community catalog (`projects/sat-io/open-datasets/FABDEM`) | **UNVERIFIED** — attempted to fetch the data.bris dataset page twice; one attempt returned a connection error, one timed out. Could not confirm whether direct download requires an account. GEE route needs a (free) Google/GEE account, not independently tested here |
| **FathomDEM(+)** (Fathom/Bristol) | ~30 m | Documented as improving on FABDEM using LiDAR-trained ML; exact LE90/RMSE not located in the time available | Near-DTM (bare-earth, successor to FABDEM) | Free v1.0 tiles on Zenodo (Eurasia/Africa) per search results; FathomDEM+ appears to be a newer/commercial product | Zenodo appears open | **UNVERIFIED** — did not fetch Zenodo page directly; could not confirm Mumbai/India tile coverage or licence terms of the "+" product in the time budget |
| **SRTM (SRTMGL1, 30 m)** (NASA/USGS) | 30 m (1 arcsec) | Mission spec: 16 m LE90 absolute / ~9.7 m RMSE equivalent [ias.ac.in](https://www.ias.ac.in/article/fulltext/jess/124/06/1343-1357) | **DSM** (C-band radar, partial canopy penetration but still surface-biased) | Public domain (USGS) | Free USGS EarthExplorer login, or OpenTopography key, or public AWS bucket (not tested this session) | DOCUMENTED — not fetched directly in this session |
| **NASADEM** (NASA JPL, reprocessed SRTM) | 30 m | "Typically better than 16 m absolute / 10 m relative" per search summary; only slight improvement over SRTM in comparative studies | DSM | Public domain (NASA) | Via OpenTopography (API key) or NASA Earthdata login | DOCUMENTED — not fetched directly |
| **ALOS World 3D-30m (AW3D30)** (JAXA) | 30 m (source 5 m DSM resampled) | Target 5 m RMSE (design goal, per JAXA product spec) [eorc.jaxa.jp](https://www.eorc.jaxa.jp/ALOS/en/dataset/aw3d30/data/aw3d30v4.1_product_e_1.0.pdf) | **DSM** (optical stereo, PRISM) | Free, JAXA terms of use, requires agreeing to terms via JAXA portal or GEE (`JAXA/ALOS/AW3D30`) | Likely a lightweight registration/terms click on JAXA's own site — NOT tested this session | UNVERIFIED (access route) — DOCUMENTED (specs) |
| **CartoDEM (Cartosat-1)** (ISRO/NRSC) | 30 m public (10 m reportedly used internally, not public) | Overall RMSE ≈4.7 m (CartoDEM30) / LE90 ≈7.3 m per NRSC's own 2014 evaluation report [bhuvan-app3.nrsc.gov.in PDF](https://bhuvan-app3.nrsc.gov.in/data/download/tools/document/Evaluation%20of%20Indian%20National%20DEM%20Version_2%20using%20Cartosat-1%20data%20Dec%202014.pdf); design target 8 m LE90 | **DSM** ("surface model of elevation" per NRSC's own description) | Indian government data — free but not open-licence in the OSI sense | **Registration + login required** on Bhoonidhi (bhoonidhi.nrsc.gov.in) — confirmed a dedicated registration page exists; NRSC's own docs state some products are "delayed download" (queued, notified by email/SMS), i.e. **not instant** | **VERIFIED that login/registration is required** (found live registration and login pages); did **not** attempt registration or download — a 12-hour build cannot risk an approval queue |
| **Mumbai MCGM city-wide LiDAR/3D City Model** (Genesys International + Innowave, ₹65 crore project) | Unknown — LiDAR-typical (likely sub-metre to 1 m class), not documented publicly | Not documented publicly | Documented deliverables include **both** DSM and DTM ("Generation of Digital Surface Model & Digital Terrain Models" per MCGM tender doc) | **Proprietary / government-owned**, produced under a paid tender to a private consortium; no evidence of public/open release | Not applicable — no public access path found | **NOT ACCESSIBLE** — confirmed the project exists via an MCGM tender PDF [mcgm.gov.in tender](https://www.mcgm.gov.in/irj/go/km/docs/documents/Tenders/ETH/ETH_8000054009_231023.pdf) but found **no open-data portal, API, or download path**. This is the best-quality DEM for Mumbai in principle but is realistically unobtainable in 12 hours. |

---

## Detailed findings

### 1. Copernicus GLO-30 — the verified, no-registration route
- I ran `curl -sI https://copernicus-dem-30m.s3.amazonaws.com/` → **HTTP 200**, bucket exists, publicly listable, hosted `eu-central-1`, part of the AWS Open Data Sponsorship Program. Source: [registry.opendata.aws/copernicus-dem](https://registry.opendata.aws/copernicus-dem/)
- Tile naming: `Copernicus_DSM_COG_10_N{lat}_00_E{lon}_00`. Mumbai (≈19°N, 72–73°E) is fully inside tile `N19_00_E072_00`.
- I listed that tile's contents directly: `curl "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/?list-type=2&prefix=Copernicus_DSM_COG_10_N19_00_E072_00"` → returned a real S3 XML listing including `Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif` (13,336,459 bytes, i.e. ~12.7 MB for the whole 1°×1° tile, Cloud-Optimized GeoTIFF, EPSG:4326).
- I then did `curl -sI` directly on that `.tif` URL → **HTTP 200**, `Content-Type: image/tiff`, `Accept-Ranges: bytes` confirming it is a genuine Cloud-Optimized GeoTIFF servable by byte-range (so a tool like `rasterio`/GDAL can windowed-read just the Mumbai bounding box without downloading the full 12.7 MB tile).
- **This means: no OpenTopography account, no API key, no NRSC login — a script can `gdalwarp`/`rasterio` a Mumbai bbox straight off this public bucket right now.** This is the single most time-efficient DEM access path found in this investigation.
- Format: GeoTIFF (COG), CRS EPSG:4326 (WGS84 lat/lon — **must be reprojected** to a metric CRS before any hydrology, see Preprocessing below).
- One 1°×1° tile (~12.7 MB) fully covers all of Mumbai city + suburbs; a clipped Mumbai-sized subset would be a few MB at most.

### 2. FABDEM — best DSM→DTM correction, licence caveat
- Built by statistically subtracting building/forest height bias from GLO-30 using ML, published by University of Bristol / Fathom. [fathom.global paper](https://www.fathom.global/academic-papers/a-30-m-global-map-of-elevation-with-forests-and-buildings-removed/), [gee-community-catalog.org/projects/fabdem](https://gee-community-catalog.org/projects/fabdem/)
- Licence is **CC BY-NC-SA 4.0 — non-commercial only**. For a hackathon prototype (not sold/commercialised) this is very likely fine, but it is a real legal constraint that must be logged for the team, not silently absorbed.
- I could **not** verify the download mechanics of data.bris.ac.uk directly (connection failures both attempts — one `ECONNRESET`, one full timeout, so this is UNVERIFIED, not "confirmed open"). The Google Earth Engine route (`projects/sat-io/open-datasets/FABDEM`) is documented by a third-party community catalog, not Bristol/Fathom themselves, and requires a GEE account (free, but another registration step, not tested here).

### 3. Cartosat/CartoDEM — India's own DEM, but gated
- Confirmed live pages: `bhoonidhi.nrsc.gov.in/bhoonidhi/registration.html` and `.../login.html` both resolve and are dedicated registration/login flows (found via search, not fetched with curl in this session — DOCUMENTED that they exist, VERIFIED only that the URLs were indexed and titled as registration/login pages).
- NRSC's own Bhoonidhi documentation states some products download instantly if pre-staged online, others are "delayed download" — user is notified by email/SMS once ready. Source: [Bhoonidhi user manual](https://bhoonidhi.nrsc.gov.in/bhoonidhi_resources/help/docs/Bhoonidhi_ISROEOHub_UserManual_V2.0.pdf). **For a 12-hour build this delay risk alone rules it out as a primary path**, even though its accuracy (RMSE ≈4.7 m) is nominally better than SRTM's spec.
- CartoDEM is explicitly documented as a **surface model** (DSM), not bare earth.

### 4. Mumbai's own LiDAR (MCGM/Genesys/Innowave) — best possible quality, not obtainable
- Confirmed the project is real: Mumbai's municipal corporation (MCGM) commissioned a ₹65 crore LiDAR + terrestrial mobile mapping + bathymetric survey project, producing DSM, DTM, orthophoto, and a 3D city model. Source: [Smart Cities Council article](https://www.smartcitiescouncil.com/article/mumbai-starts-property-mapping-first-city-enable-lidar-project), [MCGM tender PDF](https://www.mcgm.gov.in/irj/go/km/docs/documents/Tenders/ETH/ETH_8000054009_231023.pdf) (this PDF appears to be a *further* tender referencing/extending 3D city model work, confirming the underlying data exists in MCGM's systems).
- No public portal, API, WMS, or bulk-download path was found for this data. It is almost certainly restricted to MCGM's property-tax/planning use. **This is the single biggest "if only we had it" gap** — true LiDAR DTM+DSM at Mumbai city scale would solve nearly every problem in this brief — but it is NOT realistically obtainable in 12 hours (or probably at all without an official request to MCGM).

---

## DSM vs DTM — summary

| Candidate | DSM or DTM |
|---|---|
| SRTM / NASADEM | DSM (radar surface return, partial canopy penetration) |
| Copernicus GLO-30 | DSM (explicit — TanDEM-X radar surface) |
| ALOS AW3D30 | DSM (optical stereo surface) |
| CartoDEM | DSM (NRSC's own description: "surface model") |
| FABDEM | Near-DTM (ML bias-correction of GLO-30 to approximate bare earth + building removal) |
| FathomDEM | Near-DTM (successor method to FABDEM, LiDAR-trained) |
| MCGM LiDAR | Both DSM and DTM documented as deliverables — but inaccessible |

**Implication for Mumbai:** every readily obtainable candidate except FABDEM/FathomDEM will treat rooftops as ground, meaning naive flow-routing will show water "flowing over" buildings or pooling on roofs instead of in streets. This is a first-order correctness problem for a flood model, not a cosmetic one.

---

## Practical retrieval path — top 3 candidates

**1. Copernicus GLO-30 via public AWS S3 (recommended, verified working):**
```
Tile: Copernicus_DSM_COG_10_N19_00_E072_00_DEM/Copernicus_DSM_COG_10_N19_00_E072_00_DEM.tif
Bucket: https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/
```
A developer can `gdalwarp -te <west> <south> <east> <north>` directly against the `/vsicurl/https://copernicus-dem-30m.s3.amazonaws.com/.../DEM.tif` path (COG + HTTP range requests, verified via `Accept-Ranges: bytes` header) to pull only the Mumbai pilot bbox in seconds, no auth, no download of the full tile. This is the fastest path found.

**2. FABDEM via Google Earth Engine (if bare-earth-ish surface is wanted):**
`ee.ImageCollection("projects/sat-io/open-datasets/FABDEM")` per the community catalog — requires a free GEE account (not tested end-to-end here) and is licensed non-commercial only.

**3. OpenTopography Global DEM API (fallback, verified the auth wall is real):**
`https://portal.opentopography.org/API/globaldem?demtype=COP30&south=18.89&north=19.30&west=72.75&east=72.99&outputFormat=GTiff&API_Key=...` — confirmed this endpoint is live and enforces auth (got a clean `401` without a key). Registration is self-service/instant per documentation (not approval-gated), but adds a step the S3 route skips entirely.

---

## Preprocessing requirements (Mumbai-specific)

Regardless of which candidate is chosen, the following are necessary before any hydrologically meaningful use, based on general DEM-hydrology practice and Mumbai's specific geography (coastal, low-lying reclaimed land, dense mid-rise construction, numerous flyovers/rail viaducts):

1. **Reprojection**: source data is EPSG:4326 (lat/lon degrees) — must reproject to a metric CRS. For Mumbai the standard local choice is **UTM Zone 43N (EPSG:32643)**, which covers 72°E–78°E and is the conventional UTM zone for the west coast of India at this longitude — this is a standard UTM-zone fact, not something I found in a Mumbai-specific source; verify zone number before use.
2. **Sink/depression filling**: any DSM will contain spurious pits (radar/optical noise, building shadow artefacts). Standard fill (e.g. Wang & Liu, or Planchon-Darboux) is required before flow accumulation, but over-aggressive filling will erase genuine low-lying flood-prone pockets (e.g. Hindmata is a *real* topographic low, not an artefact) — fill parameters need to be tuned conservatively, not applied blindly.
3. **Building/flyover false-dam removal**: Mumbai has extensive elevated infrastructure (Santacruz-Chembur Link Road, Eastern/Western Express Highway flyovers, multiple rail viaducts crossing the Mithi and local nullahs). A DSM will show these as continuous walls blocking cross-drainage that in reality passes underneath. This needs explicit correction (e.g. burning in culvert/bridge locations, or using FABDEM-style building removal plus manual flyover masking) — I found no ready-made Mumbai flyover/culvert vector dataset in this session; this would need separate sourcing (e.g. OpenStreetMap bridge/tunnel tags) and is a gap.
4. **Coastal/tidal boundary handling**: Mumbai's DEM edge meets the Arabian Sea and tidal creeks (Mahim Creek, Thane Creek). GLO-30's `WBM.tif` auxiliary file (water body mask, confirmed present in the S3 tile listing above) can flag sea/water pixels, but tidal variation itself (Mumbai's tidal range is several metres) is not encoded in a static DEM — any flood model needs a separate tide/boundary-condition input, which is out of scope for "DEM" but a dependency the modelling team must know about.
5. **No-data / void handling**: SRTM in particular has known radar-shadow voids; NASADEM improves but doesn't eliminate this. Copernicus GLO-30 is a merged product (TanDEM-X, SRTM fill, ALOS fill per its own product handbook) and is documented to have far fewer voids — this is DOCUMENTED from the product handbook, not independently void-checked over Mumbai in this session.
6. **Vertical datum**: global DEMs are referenced to EGM (geoid), not a local Mumbai/India datum. Any comparison to India's own survey benchmarks would need a datum shift — flagged as a gap, not resolved here.

---

## Suitability for street-level modelling — honest assessment

- **30 m-class candidates (SRTM, NASADEM, GLO-30, AW3D30, CartoDEM, FABDEM)**: a single 30 m pixel is larger than the width of most Mumbai residential streets (typically 6–15 m) and comparable to or larger than an entire city block in the denser wards. **These cannot distinguish flooding on one street from its parallel neighbour.** What they *can* honestly support is basin/pocket-level differentiation — i.e., "this low-lying cluster around Hindmata floods, this higher area 500 m away does not" — at the neighbourhood scale, not the street scale.
- **Vertical accuracy** of 3–8 m LE90 (the documented range across these products) is **larger than the entire depth range we are trying to report in centimetres.** A DEM with several metres of vertical uncertainty cannot defensibly support a "cm-precision" depth claim in absolute terms. The defensible framing is **relative/ordinal**: "this pocket is lower than that one, so it will pond first and deepest," not "this street will see 23 cm of water."
- **No candidate investigated here — including FABDEM — is documented as good enough to make the problem-statement-required street-level, centimetre-depth claim honestly.** The one asset that plausibly could (MCGM's own LiDAR) is not accessible. This is the central honest limitation the team must decide how to handle: either narrow the pilot's claimed precision (ordinal/relative risk, not absolute cm depth), or explicitly scope the demo as "conceptual/what depth would look like if we had LiDAR" using a synthetically sharpened DEM, clearly labelled as such and not as ground truth.

---

## Proposed pilot areas (candidate, not decided)

All three are grounded in the same evidence: multiple 2024–2025 news reports confirm BMC's own list of chronic waterlogging spots, and the Mithi River is repeatedly documented as Mumbai's principal urban flood corridor. Bounding boxes below are **my own approximate estimates from general geographic knowledge of these named landmarks — I did NOT fetch a gazetteer or GIS boundary file for these coordinates, so they are INFERRED, not sourced, and must be checked against OpenStreetMap/Google Maps before use in any code.**

### Pilot A — Hindmata / Dadar (chronic ponding, most-cited single hotspot)
- Grounding: Hindmata is repeatedly named as a chronic flood point where BMC has installed underground storage tanks. Source: [Free Press Journal, BMC dewatering plan](https://www.freepressjournal.in/mumbai/mumbai-monsoon-preparedness-bmc-proposes-110-crore-dewatering-pump-plan-to-tackle-flooding-at-key-waterlogging-hotspots)
- Approx bbox (INFERRED, unverified coordinates): 19.010°N–19.030°N, 72.835°E–72.855°E
- Approx size: 0.02° lat ≈ 2.22 km; 0.02° lon at 19°N ≈ 0.02 × 111 × cos(19°) ≈ 2.10 km → **≈ 4.7 km²**
- Judge-recognisability: high — "Hindmata" is one of the most media-covered flood names in Mumbai.

### Pilot B — Milan Subway / Khar-Santacruz (western suburb subway flooding)
- Grounding: Milan Subway named explicitly among BMC's current-year dewatering-pump priority list. Same source as above.
- Approx bbox (INFERRED): 19.075°N–19.095°N, 72.835°E–72.855°E
- Approx size: same arithmetic as Pilot A → **≈ 4.7 km²**

### Pilot C — Kurla / Chunabhatti / LBS Marg (Mithi River basin)
- Grounding: Kurla and Chunabhatti both explicitly named as chronic hotspots; Kurla additionally documented as being flooded to ~5 ft from Mithi River overflow with evacuations in past events. Sources: [Free Press Journal](https://www.freepressjournal.in/mumbai/mumbai-monsoon-preparedness-bmc-proposes-110-crore-dewatering-pump-plan-to-tackle-flooding-at-key-waterlogging-hotspots), [FloodList — Mithi River](https://floodlist.com/asia/mumbai-floods-mithi-river)
- Approx bbox (INFERRED): 19.060°N–19.085°N, 72.865°E–72.895°E
- Approx size: 0.025° lat ≈ 2.78 km; 0.03° lon ≈ 0.03 × 111 × cos(19°) ≈ 3.15 km → **≈ 8.75 km²**
- Note: larger and more complex (river channel + built-up), riskier for a 12-hour build than Pilot A or B.

**My recommendation among these three (research input only, not a decision): Pilot A (Hindmata/Dadar) is the smallest, most judge-recognisable, and does not require modelling an actual river channel — best fit for a 12-hour scope.**

---

## Cell-count arithmetic (for runtime feasibility judgement)

Formula: cells = area_km² × (1000 m / resolution_m)²

| Pilot | Area (km²) | 30 m cells | 10 m cells | 5 m cells | 2 m cells |
|---|---|---|---|---|---|
| A — Hindmata/Dadar | 4.7 | 4.7 × 1,111 ≈ **5,200** | 4.7 × 10,000 ≈ **47,000** | 4.7 × 40,000 ≈ **188,000** | 4.7 × 250,000 ≈ **1,175,000** |
| B — Milan Subway | 4.7 | ≈ **5,200** | ≈ **47,000** | ≈ **188,000** | ≈ **1,175,000** |
| C — Kurla/Mithi | 8.75 | 8.75 × 1,111 ≈ **9,700** | 8.75 × 10,000 ≈ **87,500** | 8.75 × 40,000 ≈ **350,000** | 8.75 × 250,000 ≈ **2,187,500** |

Interpretation: at 30 m (the only resolution actually obtainable from any verified-accessible source), Pilot A/B are a trivial ~5,000-cell grid — fast for any routing algorithm, but this is the same resolution that cannot resolve individual streets (see Suitability section). The 5 m/2 m columns are shown only to illustrate what *would* be computationally feasible (well under 2M cells even at 2 m for the smaller pilots) **if** a finer DEM existed — they do not correspond to any data source verified as obtainable in this investigation. If the team pursues any resampling/sharpening/super-resolution of the 30 m DEM to visualise at 5 m or 2 m, that must be labelled as interpolation/synthetic detail, not real measured elevation.

---

## What I could not verify (ran out of time / access on)

1. **FABDEM's actual download mechanics** at data.bris.ac.uk — two fetch attempts both failed at the network level (not a content/permission answer, a connectivity failure in my environment). Registration requirement is UNCONFIRMED either way.
2. **FathomDEM/FathomDEM+ Mumbai coverage and exact licence terms** — found via search only, did not reach Zenodo or Fathom's product page directly.
3. **AW3D30's actual JAXA registration flow** — documented to exist, not walked through.
4. **SRTM/NASADEM direct-download endpoints** — did not curl-test USGS EarthExplorer, NASA Earthdata, or the SRTM public AWS bucket (if one exists) directly; relied on search-summarized documentation only. Given GLO-30's clearly superior verified-access path, I judged this lower priority within the time budget and did not test it.
5. **Exact pilot-area bounding boxes** — all three proposed boxes are my own coordinate estimates from general knowledge of these named Mumbai locations, not pulled from a gazetteer, OSM Nominatim, or official ward-boundary shapefile. These must be checked in QGIS/Google Maps before being hard-coded anywhere.
6. **Void/no-data extent of GLO-30 specifically over Mumbai** — did not open the actual GeoTIFF pixel data (only confirmed the file is fetchable and is a valid, byte-range-servable COG), so could not independently confirm the water-body mask or void pattern over the exact Mumbai coastline in this session.
7. **CartoDEM's newer/finer product (10 m)** — search results mention a 10 m version used at "IMGEOS" but state the public Bhuvan product is only 30 m; I did not chase down what IMGEOS is or whether the 10 m product has any public route (likely commercial/internal — treat as not obtainable).
8. Did not exhaustively check MERIT-Hydro or other hydrologically-conditioned derivative DEMs (derived from SRTM/multi-error-removed) — these exist per literature but were out of my time budget; flagging as a possible follow-up for whoever picks up preprocessing.

Tool-call count used: ~19, within the 25-35 budget.
