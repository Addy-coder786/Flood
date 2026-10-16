# Mumbai Rainfall & Rainfall-Nowcast Availability — Evidence Report

Research worker: RESEARCH WORKER 1 | Topic: FloodNet Urban Flood Nowcasting, target city Mumbai
Compiled: 2026-09-08. Budget: ~28 tool calls (web search / web fetch / direct HTTP probes). Breadth-first, not exhaustive.

**Definitional discipline used throughout:** a *nowcast* = a forecast of FUTURE (0–3h) rainfall/reflectivity. Real-time or historical *observations* of rainfall are not nowcasts, no matter how fresh. Every item below is labeled OBSERVATION or FORECAST/NOWCAST accordingly.

---

## 1. Summary Verdict

**Is a true 0–3h rainfall nowcast for Mumbai obtainable by us within a few hours of work? → NO (as a ready-made product), PARTIAL (if we build a minimal extrapolation nowcast ourselves).**

Evidence:
- IMD does operate a "district/station nowcast" for Mumbai, and it has a real, reachable API endpoint (`api.imd.gov.in/api/v1/districtnowcast`, `.../stationnowcast`) — **but it is CATEGORICAL, not quantitative-gridded**. It returns weather-category codes (1–33), color codes, and text messages with 3-hour validity, and per IMD's own operational description this product is **manually issued by forecasters every 3 hours**, not an automated gridded rainfall-rate raster. Source (secondhand, academic): "A method for automatic verification of thunderstorm nowcasts," Journal of Earth System Science / Springer — https://link.springer.com/article/10.1007/s12040-024-02471-4 (not independently fetched in full, found via search summary). The API endpoint itself was **directly probed** and is real but returns `401 {"error":"API key missing"}` — so it requires a key we do not have, and no self-service registration process was found in the time available (UNVERIFIED whether one exists / how long approval takes).
- IMD's live Mumbai radar imagery **IS directly, publicly reachable with no authentication** — this is a directly-verified, load-bearing finding (see §2). But it is a **rendered GIF picture** (reflectivity/rainfall-intensity PPI display with a baked-in color legend), not a georeferenced/calibrated data array, and it is an **observation**, not a forecast.
- No global consumer nowcast API we checked (RainViewer) is currently delivering populated forecast frames when directly probed (see §3) — its "nowcast" array was empty at probe time.

So: a genuine 0–3h *quantitative* rainfall nowcast, ready to consume via API, was **not found to exist publicly for Mumbai**. What is achievable within hours is a **do-it-yourself extrapolation nowcast** built from the openly-reachable IMD radar GIF sequence (or from RainViewer's past-radar tiles) using an optical-flow tool such as pysteps — this converts observations into a real (if crude) nowcast, which is scientifically defensible if labeled honestly.

---

## 2. Sources Investigated — Summary Table

| Source | What it provides | Resolution | Latency/cadence | Access method | Licence/registration | Verified? |
|---|---|---|---|---|---|---|
| IMD DWR network, Mumbai (S-band Colaba, C-band Veravali, 4× X-band suburbs) | Radar reflectivity/rainfall PPI imagery | Radar-native (not confirmed in km); X-band nodes have 60 km range each | Unknown cadence (not stated on page) | Public webpage + directly-linked GIF images | No terms found permitting bulk/API reuse; disclaimer is liability-only | PARTIAL — radar existence via 1 press article (secondhand) + IMD's own radar.php page (fetched); image reachability directly curl-tested |
| IMD radar GIF images (`mausam.imd.gov.in/Radar/*_vrv.gif`) | Live CAZ/PPI/rainfall-intensity image for Veravali radar | Rendered image, unknown native grid resolution | Image `Last-Modified` matched fetch time (updates same-day); exact cadence unstated | Direct HTTP GET, **no auth** | Ambiguous — govt disclaimer covers liability only, no explicit reuse/scraping permission, no robots.txt found (404) | **VERIFIED directly** — `curl -sI` returned `HTTP/1.1 200 OK`, `Content-Type: image/gif`, 1.2 MB, fresh `Last-Modified` |
| IMD API — District/Station Nowcast (`api.imd.gov.in/api/v1/districtnowcast`, `stationnowcast`) | Categorical thunderstorm/rain-intensity nowcast, 3h validity, manually issued every 3h | Not gridded — per-district/per-station categorical codes | 3-hourly issuance (secondhand) | REST JSON, requires API key | Key required; no public self-registration process found | **VERIFIED endpoint is real** (curl → `401 {"error":"API key missing"}`); nature of product (categorical, manual) is SECONDHAND via academic paper summary, not fetched in full |
| IMD API — District/State Rainfall (`districtrainfall`, `staterainfall`) | Observed daily/weekly/monthly rainfall totals vs. normal | District/state level, not a grid | Not real-time (daily aggregates) | REST JSON, requires API key | Same key requirement | **VERIFIED endpoint real** (curl → 401, key missing); this is OBSERVATION/climatology, not a nowcast |
| IMD API — Basin QPF (`basinqpf`) | River-basin Quantitative Precipitation Forecast, deterministic+probabilistic, 5-day | Basin/sub-basin, not fine grid | 5-day range, not 0–3h nowcast | REST JSON, requires API key | Same key requirement | UNVERIFIED in detail — documented via WebFetch of api_reference.html only, endpoint itself not separately probed |
| Mumbai Rainfall portal (mumbairain.tropmet.res.in, IITM/MoES/IMD/BMC/NMMC) | Live rainfall at ~139 sites, radar image, satellite imagery | Point/station-based (139 sites), radar "up to 150 km range" (as described on page) | Real-time station updates; radar imagery refresh interval not confirmed independently | Web portal + mobile app; page loads jQuery/`api.js` but this looks like a Google-Maps loader, not a confirmed public rainfall-data API | Unclear; "MESONET high-resolution rain gauge data is freely accessible... for research/academic purpose" per page | PARTIAL — page fetched (200 OK) and summarized by WebFetch; **no working public JSON data API found/confirmed** in the time available |
| BMC "Disaster Management" app (MCGM), ~60 AWS stations | Ward-level real-time rainfall, temp, humidity, wind; rolling 15min/1h/3h windows | ~60 stations across Mumbai | Refreshed every 15 min (per secondhand source) | Mobile app (Android/iOS); no public API found | Unclear | SECONDHAND ONLY — via Citizen Matters article (citizenmatters.in) and Soft112 app-store listing found in search results; **not independently fetched or probed** |
| MOSDAC (ISRO) Open Data | Satellite-derived rainfall (GSMap-ISRO-Rain), SHAR S-band polarimetric DWR data (Sriharikota, not Mumbai) | GSMap ~gridded satellite estimate (resolution not confirmed here) | Not confirmed | Web portal; "SSO" (Single Sign-On) credentials required for the DWR product per MOSDAC's own FAQ | Free for non-commercial use per site's own open-data page, but **login required** for actual download | PARTIAL — `open-data` landing page attempt returned **HTTP 403 Forbidden** when directly fetched; findings otherwise from WebSearch snippets only (secondhand). No Mumbai-specific radar product confirmed on MOSDAC — SHAR radar is near Chennai/Andhra coast, not Mumbai |
| GPM IMERG (NASA) | Global satellite precipitation estimate | 0.1°×0.1° (~10 km) grid | 30-min steps; Early-Run NRT latency ~4h | Documented via NASA GES DISC / Earthdata (typically requires a free Earthdata login) | Open/free, non-commercial-friendly (well-established), but registration requirement for download **not itself re-verified today** | SECONDHAND (WebSearch of NASA GPM pages); **this is an OBSERVATION/estimate, not a forecast**, and 4h latency makes it unsuitable as a "current state" input for a true nowcast without further work |
| RainViewer public Weather Maps API (`api.rainviewer.com/public/weather-maps.json`) | Radar tile mosaic (past ~2h, 10-min steps) + advertised "nowcast" tile frames | Radar tile pyramid (not a stated km figure in the JSON itself) | Past frames at 10-min steps; "nowcast" advertised as 2h look-ahead, refreshed ~10 min (per RainViewer's own docs/blog, secondhand) | Direct HTTP GET to public JSON index, **no API key required** for this index | "Free for personal or educational use," attribution requested; commercial/free-tier limits (1000 req/day) reported secondhand | **VERIFIED directly** — curl returned valid JSON with 13 past-radar frame paths, **but `"nowcast":[]` — the nowcast array was EMPTY at probe time** (2026-09-08, ~07:40 UTC). Cannot confirm whether this is a persistent gap, a time-of-day artifact, or specific to the public unauthenticated tier — flagged as a finding to re-check, not a permanent conclusion |
| Tomorrow.io Timeline API | Claims by-the-minute forecast globally; proprietary "radar-satellite constellation" | Global; India-specific radar page (`weather.tomorrow.io/IN/radar`) states **hourly** update for India | Minute-resolution claimed globally, but India radar itself updates hourly per their own site (secondhand) | REST API, requires registration/API key (freemium) | Freemium, registration required | SECONDHAND ONLY (WebSearch), not fetched/probed directly; the India-specific hourly radar cadence contradicts the "by-minute" global marketing claim and should be treated with caution |
| OpenWeatherMap "minutely" precipitation (One Call API) | Minute-by-minute precipitation nowcast, ~1–2h look-ahead, ML model blending radar/satellite/stations | Point-based (per lat/lon query) | Minutely, ~1–2h | REST API, requires free API key | Freemium/registration | SECONDHAND ONLY; **India-specific coverage/accuracy of the minutely layer was not confirmed** — historically this style of product (Dark-Sky-derived) had strongest skill in radar-dense regions (US/EU); not verified for India |
| pysteps (open-source library) | NOT a data source — a tool: optical-flow/Lagrangian extrapolation nowcasting from a radar image time series | Whatever resolution the input radar data has | Whatever cadence the input has | Python library, BSD license, pip-installable | Fully open source | VERIFIED to exist as a real, published, peer-reviewed tool (GMD journal + GitHub, via WebSearch): https://gmd.copernicus.org/articles/12/4185/2019/, https://github.com/pySTEPS/pysteps — not run/tested by us |

---

## 3. Detailed Findings Per Source

### 3.1 Doppler Weather Radar coverage over Mumbai
- A 2024-era news article (mumbailive.com) states Mumbai is covered by **6 radars**: an S-band radar at IMD Colaba, a C-band radar at Veravali (near Andheri), and 4 new X-band radars (DJ Sanghvi College Vile Parle; Amity University Panvel; Vidyavardhini College Vasai; Netivali Water Treatment Plant, Kalyan-Dombivali), each X-band with **60 km range**, combined coverage claimed at **~50,000 km²**. Source: https://www.mumbailive.com/amp/en/environment/imd-launches-4-new-radars-in-mumbai-for-accurate-and-real-time-forecasts-85772 — **this is a press article, read secondhand, not IMD's own technical spec sheet.** Treat radar count/type/range as plausible but not IMD-primary-sourced.
- IMD's own radar page for Mumbai (`https://mausam.imd.gov.in/responsive/radar.php?id=Mumbai`) was fetched and **confirms Mumbai has an active radar display page** showing multiple products: Max reflectivity (MAX Z), PPI (Z and V), Surface Rainfall Intensity, Precipitation Accumulation, Volume Velocity Processing, sourced from images named `*_vrv.gif` (consistent with "Veravali" naming). **Conclusion: Mumbai is within usable IMD radar coverage** — confirmed via IMD's own site, though exact range/resolution figures are not stated on the page itself.

### 3.2 Real-time radar access (direct probe results)
- `curl -sI https://mausam.imd.gov.in/Radar/caz_vrv.gif` → **HTTP/1.1 200 OK**, `Content-Type: image/gif`, `Content-Length: 1231094`, `Last-Modified` timestamped at fetch time. **No authentication, no API key, no cookie required** for this GET. Same result for the page `radar.php?id=Mumbai` itself (200 OK).
- This means: **a live Mumbai radar image is genuinely, programmatically fetchable right now with a plain HTTP GET.** This is the single most useful concrete finding for this project.
- Caveats (must not be glossed over):
  - It is a **rendered raster image with a baked-in color legend**, not a machine-readable reflectivity/rain-rate grid (e.g., not NetCDF/HDF5/GeoTIFF). To use it quantitatively we would need to (a) know the color→dBZ or color→mm/h legend mapping, (b) know the image's exact geographic extent/projection to georeference pixels, and (c) decide whether the "Surface Rainfall Intensity" product (if separately available) already gives rain-rate colors rather than dBZ. **None of this was verified today** — we only confirmed the image is reachable, not its legend/georeferencing metadata.
  - `robots.txt` on `mausam.imd.gov.in` returns 404 (not present) — so there is no machine-readable crawl policy either permitting or forbidding automated fetches.
  - The site's disclaimer page (fetched) only covers liability ("information... liable to change without notice," no liability for damages from use/misuse) and says nothing explicit about bulk/automated reuse, commercial use, or redistribution. **This is a legal gray area, not a green light** — recommend treating this as "reachable but not clearly licensed for reuse," polling conservatively, and attributing IMD as the source.
  - Update cadence of the GIF was **not established** — we saw one fresh timestamp, not a time series, so we do not know if it refreshes every 5, 10, or 15 minutes. Would need repeated polling to confirm.
  - A "3 Hrs Animation RADAR" link (`radar_animation.php`) exists on the same page, suggesting IMD itself provides an animated loop — not fetched/tested today (time-boxed out).

### 3.3 Nowcast products — what's actually accessible
- `curl` to `https://api.imd.gov.in/api/v1/districtnowcast` and `.../districtrainfall` both returned **HTTP 401, `{"error":"API key missing"}`** — confirming these are real, live endpoints, gated by a key we do not have.
- A WebFetch of IMD's own API reference page (`https://api.imd.gov.in/public/api_reference.html`) enumerated ~22 documented endpoints (city forecast, current weather, district/station nowcast, district/state rainfall, subdivision rainfall forecast, basin QPF, district/subdivision warnings, AWS data, port/sea/coastal bulletins, cyclone track/wind/cone). **None of the documentation text itself specified an API-key acquisition process** — no visible "sign up here" flow was found in the time available. **UNVERIFIED: whether/how a project team could obtain a key within our timeframe.**
- Per a secondhand academic source (Springer/JESS paper on thunderstorm nowcast verification, found via search, not fully read), IMD's district-nowcast has been issued **manually by forecasters since 2018, at 3-hour intervals, for thunderstorms and rainfall-intensity categories** — i.e., it is a **categorical/qualitative warning product**, not an automated quantitative gridded rainfall-rate nowcast raster. This matches the API reference's own description of the districtnowcast/stationnowcast payload shape: "category codes 1–33, color codes, messages, validity times" — consistent with a warning-class product, not mm/h grids.
- **No evidence was found of a publicly/programmatically retrievable gridded quantitative 0–3h rainfall-RATE nowcast for Mumbai from any Indian agency.** IMD's Basin QPF endpoint is 5-day range and basin-scale, not 0–3h fine-grid.

### 3.4 Historical radar/rainfall data for past Mumbai floods
- Not substantively investigated within the time budget beyond noting that `mumbairain.tropmet.res.in` and the BMC Disaster Management app appear to show **rolling short windows only (last 15 min / 1h / 3h)** per a secondhand Citizen Matters article (https://citizenmatters.in/disaster-management-app-bmc-mumbai-monsoon/, not independently fetched) — i.e., likely **not** a queryable historical archive going back to specific past flood dates (e.g., 26 July 2005, 29 Aug 2017) via API.
- MOSDAC's own open-data landing page could not be fetched directly (see below) so its historical archive depth for the west coast/Mumbai region is **UNVERIFIED**.
- **This is a gap we ran out of time on** — historical event reconstruction (e.g., for model validation against a known flood day) would need further digging into IMD's Pune-based historical data portals, Kaggle-hosted India rainfall datasets (one was seen in search results: https://www.kaggle.com/datasets/vijayveersingh/indias-rainfall-data, not vetted), or a direct data request to IMD/IITM — none of which was verified today.

### 3.5 MOSDAC / ISRO
- `WebFetch` to `https://www.mosdac.gov.in/open-data` returned **HTTP 403 Forbidden** — could not directly inspect the open-data catalog ourselves.
- Per WebSearch snippets (secondhand, from mosdac.gov.in pages we did not fetch), MOSDAC provides free/open access to *derived* products for non-commercial use, but the specific Doppler radar product mentioned (SHAR-DWR) is located at **Sriharikota** (Andhra Pradesh coast) — **not Mumbai** — and even that reportedly requires **MOSDAC Single Sign-On credentials** to actually download. No Mumbai/west-coast-specific radar product on MOSDAC was identified. GSMap-ISRO-Rain (satellite rainfall estimate) exists as a product name but its resolution/cadence/access requirements for a Mumbai bounding box were **not verified**.

### 3.6 Global open alternatives — verified vs. not
- **GPM IMERG (NASA):** 0.1°×0.1° (~10 km) grid, 30-min steps, Early-Run near-real-time latency ~4 hours. This is a well-documented, credible OBSERVATION/estimate product (secondhand via NASA GPM pages), but (a) it is not a forecast, (b) 4-hour latency is far too stale to represent "now" for a 0–3h nowcast pipeline without additional extrapolation, and (c) ~10 km grid is coarse for urban (ward-level) flood nowcasting in a city the size of Mumbai. Access mechanics (Earthdata login requirement) were not re-verified today.
- **RainViewer:** directly probed — public JSON index reachable with **no key**, returns real past-radar tile paths (13 frames, 10-min spacing ≈ last ~2h of observed radar mosaic), **but the "nowcast" array was empty at the moment of testing.** RainViewer's own marketing/docs (read secondhand via WebSearch, not the full api.html page which a WebFetch could only partially summarize) describe a "2-hour rain forecast updated every 10 minutes" — **this claim is NOT currently corroborated by what the public API actually returned to us.** This is an important, directly-observed discrepancy and should be re-tested (e.g., during active rain, or via the paid/keyed tier) before relying on it.
- **Tomorrow.io / OpenWeatherMap minutely:** both are proprietary, both require registration/API keys, and neither's India-specific performance or true "radar-based" character for Mumbai was verified firsthand. Tomorrow.io's own India radar page reportedly states **hourly** updates for India (secondhand), which would undercut a "by-the-minute nowcast" claim for this specific city even if the global API nominally supports minutely queries. **Do not claim minutely fidelity for Mumbai from these without direct testing.**
- **pysteps:** a legitimate, peer-reviewed, open-source (BSD) Python library that performs optical-flow extrapolation nowcasting (0–6h) from a radar (or radar-like) image time series (Lucas-Kanade, DARTS, STEPS ensemble methods). Confirmed to exist via its GMD paper and GitHub repo — this is a **tool**, not a data source; it needs input imagery (e.g., the IMD GIFs from §3.2, once georeferenced/decoded, or another radar mosaic) to produce an actual nowcast.

### 3.7 Gauge networks (BMC / IMD / IITM)
- BMC reportedly operates **~60 Automatic Weather Stations (AWS)** across Mumbai, feeding a "Disaster Management" app with rainfall/temp/humidity/wind refreshed **every 15 minutes**, showing rolling 15-min/1h/3h totals. Source: secondhand, via WebSearch summaries of Citizen Matters and the Soft112 app listing — **not independently fetched or probed**; no public JSON/REST API for this was located.
- A separate, larger collation exists at `mumbairain.tropmet.res.in` (IITM/MoES + IMD + BMC + Navi Mumbai Municipal Corporation + Central/Western Railways), covering **~139 sites**. The page was fetched (200 OK) and appears to be a live map-based portal; page source shows a Google-Maps-style `api.js`/jQuery loader but **no confirmed public rainfall-data JSON endpoint was found** in the HTML in the time available.
- **Conclusion:** gauge data likely exists at useful density (60–139 points) and short (15-min) latency, which would be valuable for calibration/bias-correction of a radar-based nowcast or as a nowcast-independent fallback signal — but **no working programmatic access path was confirmed today.** This is a concrete follow-up: someone should inspect the network requests made by `mumbairain.tropmet.res.in` or the BMC app (e.g., via browser dev tools) to see if an underlying JSON API exists, which WebFetch/curl alone could not reveal.

---

## 4. Nowcast vs. Observation — explicit scorecard

| Product | Forecast or Observation? | Basis for this call |
|---|---|---|
| IMD radar GIF (`*_vrv.gif`) | **Observation** | It is a live PPI/reflectivity snapshot, not a predicted future field |
| IMD district/station "nowcast" API | **Nominally a forecast (0–3h validity)**, but categorical/qualitative (warning class), not a rainfall-rate grid, and manually issued | Per secondhand academic source + API reference's payload shape |
| IMD Basin QPF | **Forecast**, but 5-day range, basin-scale — not 0–3h, not fine-grid | Per API reference doc summary |
| GPM IMERG | **Observation/estimate** (satellite-retrieval), ~4h-stale even at "near-real-time" | Well-established NASA product description |
| RainViewer "past radar" | **Observation** (confirmed via direct probe: 13 past frames) | Direct curl of public JSON index |
| RainViewer "nowcast" | **Advertised as forecast, but empty when directly probed** | Direct curl: `"nowcast":[]` |
| Tomorrow.io / OpenWeatherMap minutely | **Advertised as forecast** (ML-blended); India fidelity/radar-basis unverified | Secondhand only |
| A pysteps extrapolation run on IMD or RainViewer imagery | **Would be a genuine forecast** if we build it | Not yet built; requires engineering work described in §5 |

---

## 5. Ranked, honest fallbacks (if a ready-made true radar nowcast is not obtainable)

Ranked by (a) scientific defensibility and (b) estimated hours of work, both stated explicitly.

### Rank 1 — Build our own advection/extrapolation nowcast from the IMD radar GIF sequence (or RainViewer past-radar tiles)
- **What we could claim:** "A 0–1h (extrapolation skill degrades fast beyond that) rainfall/reflectivity nowcast for Mumbai, generated in-house via optical-flow extrapolation (pysteps) of the last N observed radar frames from IMD's public Veravali radar imagery." This is real, honest, and matches exactly what the original problem statement gestures at (DWR-driven nowcasting), just built by us rather than consumed as a finished IMD product.
- **What we could NOT claim:** that it is IMD's own official nowcast product; that it has been validated against ground truth; skill beyond ~60–90 minutes lead time (extrapolation nowcasts degrade quickly, well-documented in nowcasting literature, e.g., pysteps paper); precise mm/h values unless we correctly decode the GIF's color legend and Z-R relationship (unverified today).
- **Effort estimate:** Non-trivial. Requires: (1) repeatedly polling and archiving the GIF over time to build a sequence (needs the actual refresh cadence, unconfirmed — assume worst case poll every 5 min for a few hours to get enough frames), (2) decoding pixel-color → dBZ/mm-h legend (legend mapping not yet found — would need to inspect the image itself or find IMD's published color scale), (3) georeferencing the image to lat/lon (extent not yet confirmed), (4) running pysteps optical flow + extrapolation, (5) validating outputs look physically sane. Realistically **several hours minimum**, and carries legal-gray-area risk (§3.2 caveats on IMD's reuse terms).

### Rank 2 — Consume RainViewer's radar tiles (past frames, confirmed reachable, no key) and run our own extrapolation on those instead of IMD's GIFs
- **What we could claim:** Same as Rank 1, but sourced from RainViewer's radar mosaic instead of IMD's page. RainViewer explicitly offers this as a **free-for-personal/educational** tile service, which is a clearer licence footing than IMD's ambiguous disclaimer.
- **What we could NOT claim:** RainViewer's underlying "nowcast" feature works for us (it returned empty when tested); that RainViewer's tiles are calibrated to IMD's own instrument (unknown provenance for India — RainViewer aggregates 1200+ radars worldwide, but which one(s) cover Mumbai and at what native resolution was **not verified**).
- **Effort estimate:** Similar to Rank 1 (still need to build the pysteps pipeline), but tile format (standard XYZ map tiles) may be easier to grab and georeference than IMD's raw GIF. Roughly comparable or slightly less effort — a few hours.

### Rank 3 — Drive the model with a historical observed Mumbai flood event, replayed and clearly labeled as a "replayed historical event," NOT as a live nowcast
- **What we could claim:** "We demonstrate the flood model's response using observed rainfall from [a specific, dated, sourced historical event], replayed as a synthetic real-time feed for demo purposes." This is honest and still scientifically useful for demonstrating the downstream flood model.
- **What we could NOT claim:** that this is a nowcast, that it generalizes to an arbitrary future storm, or that it demonstrates real-time predictive skill.
- **Effort estimate:** Low-to-moderate — mainly gated on actually finding a retrievable historical Mumbai rainfall time series at usable resolution, which **was not confirmed today** (§3.4 gap). If a Kaggle dataset or IMD historical portal pans out, this could be **1–2 hours**; if not, could balloon.

### Rank 4 — Drive the model with a synthetic design storm (e.g., a standard IDF-curve-based hyetograph for Mumbai)
- **What we could claim:** "We stress-test the flood model using a synthetic design storm consistent with published Mumbai rainfall intensity-duration-frequency statistics," clearly labeled as synthetic, not observed or forecast.
- **What we could NOT claim:** any real-world predictive or nowcasting capability at all.
- **Effort estimate:** Lowest — could be done in under an hour if IDF parameters for Mumbai are found in literature (not investigated today, but highly likely to exist given Mumbai's flood-study history — CWPRS/IITB papers, etc.). This is the fastest fallback but the weakest scientifically — it demonstrates the flood model, not a nowcasting system.

**Not recommended as a primary claim, but worth a mention as future work:** using IMD's `districtnowcast`/`stationnowcast` categorical API (Rank "N/A" — not viable within our timeframe since it needs an API key with no confirmed self-service path, and even if obtained, its payload is categorical/qualitative, not a rainfall-rate grid usable to drive a hydrologic-hydraulic model directly).

---

## 6. What we must NOT claim

- Do **not** claim IMD (or anyone) provides a publicly, programmatically retrievable **gridded quantitative 0–3h rainfall-rate nowcast** for Mumbai — this was not found to exist in accessible form. IMD's real nowcast product is categorical/warning-class and gated behind an unobtained API key.
- Do **not** claim the IMD radar GIF is a calibrated, georeferenced data product — it is a rendered image; we do not yet know its legend mapping or exact geographic extent.
- Do **not** claim RainViewer (or any third-party "nowcast" API) is currently delivering a working forecast for Mumbai — our direct probe of RainViewer's public endpoint showed an **empty nowcast array** at test time.
- Do **not** claim GPM IMERG, satellite estimates, or any ~4-hour-latency product is "real-time" or a "nowcast" — they are stale observations relative to a 0–3h nowcast requirement.
- Do **not** claim Tomorrow.io/OpenWeatherMap "minutely" precipitation is validated, radar-grade, or even necessarily radar-based for Mumbai specifically — this was not verified and their own India-specific radar cadence (Tomorrow.io) is reportedly hourly, not minutely.
- Do **not** claim any BMC/IITM gauge data is available via a public API — only a web/app UI was confirmed; a machine-readable feed was not found.
- Do **not** present a self-built pysteps extrapolation nowcast (if we build one) as an "IMD nowcast" or as validated — it must be labeled as an in-house, unvalidated, short-lead-time (≤~1h realistic skill) extrapolation product with clearly stated data provenance and legal caveats about IMD's reuse terms.
- Do **not** assert IMD's radar imagery reuse is definitely legally clear for a public hackathon demo — the disclaimer found only covers liability, not reuse rights, and this remains a gray area.

---

## 7. What I could not verify / ran out of time on

- Exact **update cadence** of the IMD Mumbai radar GIF (only one snapshot's freshness confirmed, not a time series).
- The **color-legend / Z-R mapping** needed to convert the IMD GIF's colors into dBZ or mm/h.
- The **geographic extent/projection** of the IMD radar image (needed to georeference pixels).
- Whether a **self-service API key registration** process exists for `api.imd.gov.in` (searched, not found; may require direct email/contact to IMD as hinted by named contacts found in search results, not verified).
- Full content of IMD's radar animation page (`radar_animation.php`) — link found, not fetched.
- Whether `mumbairain.tropmet.res.in` or the BMC Disaster Management app has any underlying JSON/REST API (would require browser dev-tools network inspection, not possible with curl/WebFetch alone in the time available).
- MOSDAC's actual open-data catalog contents (blocked by a 403 on direct fetch); whether any Mumbai/west-coast-specific radar or high-res rainfall product exists there beyond the Sriharikota DWR and GSMap-ISRO-Rain names surfaced in search.
- Historical Mumbai flood-event rainfall/radar archives — no specific, verified, retrievable historical dataset was pinned down (only an unvetted Kaggle dataset name surfaced).
- GPM IMERG's actual registration/login requirement (Earthdata account) — stated from general knowledge/secondhand search, not re-confirmed today.
- Whether RainViewer's "nowcast" array being empty is a persistent condition or specific to this moment/tier — needs re-testing, ideally during active regional rainfall and/or with a registered API key.
- Mumbai-specific IDF (intensity-duration-frequency) curve literature for a synthetic design-storm fallback (Rank 4) — not searched due to time-box.
- Tomorrow.io and OpenWeatherMap were evaluated only via WebSearch summaries, never directly probed with curl/WebFetch — their actual India response payloads, resolution, and coverage remain unverified firsthand.
