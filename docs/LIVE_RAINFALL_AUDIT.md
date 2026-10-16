# Live rainfall / nowcast data access audit

**Date:** 2026-09-09. **Scope:** determine whether an official India Meteorological Department (IMD) source
can honestly replace `ExternalNowcastProvider`'s inert stub with real, live rainfall input for the Mumbai
Hindmata/Dadar pilot. Every claim below is either a direct quote/citation from an IMD-operated page, or an
empirical result of an actual HTTP request made from this machine (never both assumed) — sources and dates
are recorded so this can be re-verified independently.

## 1. Sources investigated

| # | Source | URL | What it is |
|---|---|---|---|
| 1 | IMD API Management Platform | `https://api.imd.gov.in/` | Official IMD "unified gateway" — the only genuinely *documented* IMD API surface found |
| 2 | IMD API reference | `https://api.imd.gov.in/public/api_reference.html` | Lists 28 documented endpoints (city forecast, current weather, district/station nowcast, AWS/ARG, rainfall, warnings, radar, cyclone, marine, sun/moon) |
| 3 | Current Weather API | `.../api/v1/current_wx` | Station-level current observation incl. "Last 24 hrs Rainfall" (mm) |
| 4 | District-wise Rainfall API | `.../api/v1/districtrainfall` | Daily/weekly/cumulative/monthly actual-vs-normal rainfall per district |
| 5 | AWS/ARG Data API | `.../api/v1/aws_data` | Automatic Weather Station obs — **no rainfall field documented** |
| 6 | District-wise Nowcast API | `.../api/v1/districtnowcast` | 0–3h categorical hazard warning (Cat1–Cat19 bands, colour-coded), **not quantitative mm** |
| 7 | Station-wise Nowcast API | `.../api/v1/stationnowcast` | Same categorical scheme, per station |
| 8 | Mumbai radar page | `https://mausam.imd.gov.in/responsive/radar.php?id=Mumbai` | Public webpage showing composite reflectivity **images**, no documented data API |
| 9 | IMD radar API (gated) | `.../api/v1/radar` (listed in ref. doc) | Documented response format is **"Image"**, not a gridded QPE product |
| 10 | IMD district nowcast GIS viz | `mausam.imd.gov.in/mumbaiums/district_nowcast_mumums.php`, `.../responsive/districtWiseNowcastGIS.php` | Public visualisation portal pages, no documented API |
| 11 | IITM Mumbai MESONET | `https://mumbairain.tropmet.res.in/` | IITM/MoES + MCGM + IMD dense rain-gauge network, **139 stations, 15-min updates** — the highest-resolution real Mumbai rainfall network found |
| 12 | Open Government Data Platform | `data.gov.in` rainfall catalogue | IMD-sourced datasets, generally daily/normal statistics, not real-time |
| 13 | IMD's own PDF on API access | `mausam.imd.gov.in/Forecast/marquee_data/API_doc.pdf` | States the actual registration process (see §3) |
| 14 | API Setu (govt. API directory) | `directory.apisetu.gov.in/api-collection/mausam` | Listing page found; no usable additional detail beyond what's above |
| 15 | IMD Radar Data Supply Portal (found 2026-09-10, follow-up pass) | `radarapi.imd.gov.in` → redirects (HTTP 302, verified live) to `radarapi.imd.gov.in/dsp/frontend/login` | Distinct from the `api.imd.gov.in` nodal-officer process — has a **"Sign up"** link (self-service or semi-self-service account creation, unlike `api.imd.gov.in`). Sibling portal `dsp.imdpune.gov.in` describes supplying **historical** meteorological/climate data including "radar data," operational since Mar 2019 (v5.0 since Oct 2024), enrollment/login required (introduced Aug 2021), **not free** for most data (cost estimates before purchase; some free data exists), commercial reproduction requires permission. **Not yet classified** — whether it offers a real-time/near-real-time gridded rainfall *nowcast* (vs. only a historical archive of radar imagery/volumes), whether Mumbai is covered, exact pricing, and approval turnaround are all genuinely **UNVERIFIED** (would require creating an account, out of scope for this audit without separate authorisation). Recorded here as an unexplored lead, not a solved gap — does not change §4's classification or the SR-01 conclusion below. |

## 2. Empirical verification (not assumed)

Real, unauthenticated HTTP GETs were made from this machine on 2026-09-09 against the documented endpoints:

```
GET https://api.imd.gov.in/api/v1/districtrainfall?id=164   -> HTTP 401  {"error":"API key missing"}
GET https://api.imd.gov.in/api/v1/districtnowcast?id=1      -> HTTP 401  {"error":"API key missing"}
GET https://api.imd.gov.in/api/v1/cityforecast              -> HTTP 401  {"error":"API key missing"}
GET https://api.imd.gov.in/api/v1/current_wx?id=43003&apikey=<fake>  -> HTTP 401 Unauthorized (confirmed again
        through this project's own IMDObservationProvider code, not just curl — see §7)
```

**Every documented api.imd.gov.in endpoint requires a key. There is no anonymous/public tier.** This alone
rules out an "A" classification (directly usable now, no credentials) for anything on that platform.

## 3. What obtaining credentials actually requires

IMD's own PDF (`API_doc.pdf`) states verbatim: *"If any organization is interested to use these APIs
following IMD's terms and condition then they can contact to the Nodal officer Dr. Sankar Nath, Sc-E, IMD,
New Delhi (email: sankar.nath@imd.gov.in, mobile +91-9821832587)."* The portal itself (`api.imd.gov.in`)
additionally states **"IP Whitelisting required"** for access. There is no self-service signup, no published
approval SLA, and no published fee schedule. This is a manual, organisation-level, individually-negotiated
process — realistically not completable within a hackathon's timeframe, and incompatible by default with a
dev machine's dynamic IP (a static, pre-registered IP would be needed).

## 4. Classification

**A = usable now, no credentials. B = usable after registration/credentials. C = publicly visible but not a
reliable programmatic source. D = unavailable/unsuitable.**

| Source | Class | Why |
|---|---|---|
| api.imd.gov.in (all 28 documented endpoints) | **B** | Documented, structured, real API — but every endpoint requires a key (empirically confirmed), the key requires a manual nodal-officer approval process with no published SLA, and the portal states IP whitelisting is required |
| Mumbai radar imagery (`mausam.imd.gov.in/responsive/radar.php`) | **C** | Public webpage, rendered images, not a documented data API. **Two claims in the original wording of this row were corrected on 2026-09-10 (see §8c):** (i) the Mumbai `sri_*` product is *not* only reflectivity — it is Surface Rainfall Intensity in **mm/hr** with the Z-R relation printed in-band; (ii) this audit previously stated the gated API's radar endpoint "is documented as returning an Image" — that is **unsupported**: there is no radar section in the API reference at all, only a dead index anchor. The class **C** verdict nonetheless stands, for the reasons in §8c. |
| District-wise/Station-wise Nowcast (`districtnowcast`/`stationnowcast`) | **B, but not usable as intended** | Real, documented, gated API — but the nowcast is **categorical** (colour/severity bands, e.g. "Cat12: Heavy rain: > 15 mm/hr"), never a quantitative mm/h value. Even with a key, this cannot honestly become a rainfall intensity time series without inventing precision IMD never published |
| AWS/ARG Data (`aws_data`) | **D** for rainfall purposes | Documented fields are temperature/humidity/wind/pressure — **no precipitation field at all** |
| IITM Mumbai MESONET (`mumbairain.tropmet.res.in`) | **C** | Real, dense (139 stations, 15-min), genuinely the best-resolution Mumbai rain network found — but no documented API; the page states data is available on request via email to the curator, not programmatically. Reverse-engineering its dashboard's network calls would be scraping an undocumented endpoint, which this audit was explicitly told not to do |
| data.gov.in rainfall catalogue | **C** | Real IMD-sourced datasets exist, but the ones found are daily/normal statistical products, not a real-time or sub-daily feed suitable for nowcasting |
| **No source in this audit reached class A.** | | |

## 5. Critical physics question: what does FloodNet's engine actually require?

`floodnet/simulation/engine.py::run_simulation()` consumes exactly one rainfall input type:
`contracts.RainfallScenario(t_s, intensity_mm_h)` — a **piecewise-constant intensity time series**, one
value per `config.RAIN_DT_S` = 300 s (5-min) step, covering the full `config.HORIZON_S` = 10 800 s (3 h)
window (37 points). It needs a **series**, not a scalar, and needs it at **5-minute** native resolution. The
engine itself was not touched by this work (nor does it need to be — this is exactly the contract every
other provider, including the synthetic scenarios, already satisfies).

None of the classified sources provide that directly:
- `current_wx`'s "Last 24 hrs Rainfall" is a **single cumulative total**, current as of the station's last
  report — not a series.
- `districtrainfall` is **daily** (plus weekly/cumulative/monthly) — also not a sub-hourly series.
- `districtnowcast`/`stationnowcast` are **categorical**, not numeric.
- `aws_data` has no rainfall field.

**Normalisation layer implemented** (`floodnet/rainfall/provider.py::IMDObservationProvider._normalize`):
the real observed 24h total is divided by 24 to get a mean mm/h rate, then **held constant** across every
5-min step of the 3h window — a persistence assumption, the simplest and most conservative real nowcasting
baseline (literally the skill floor real nowcast systems are benchmarked against), not a radar extrapolation
and not represented as one. Because a real observation is being reshaped by a stated assumption, the
resulting `RainfallScenario` is tagged `Tag.ESTIMATED` (not `Tag.REAL`) — the same "real input + a stated
derivation rule → ESTIMATED" pattern already used elsewhere in this codebase (e.g. Manning's n). The full
numeric derivation (raw 24h total, station, observation time, resulting mm/h) is written into the
provenance `note` field on every run — nothing about the transformation is hidden.

**This is the smallest technically honest combination available**, matching the task's own suggested
pattern: *IMD observation + FloodNet hydraulic forecast* — explicitly **not** *IMD observation + IMD
nowcast*, because no quantitative IMD nowcast product exists to add.

## 6. problem-statement requirement alignment

- **SR-01 (rainfall nowcast input):** partially strengthened. A real, live, quantitative IMD observation can
  now genuinely drive a run (when credentials are configured) — but it is an **observed-rainfall-driven
  persistence forecast**, not a Doppler-radar-derived nowcast. This is stated explicitly in the UI-facing
  provenance, not implied otherwise.
- **SR-02 (0–3h horizon):** satisfied structurally — the engine still produces the full 3h forecast from
  whatever `RainfallScenario` it's given, live included. The *forcing* for that 3h window is a stated
  assumption (§5), not a genuine 3h nowcast product, because IMD does not publish one via this API.
- No other rainfall-related SR is affected.

## 7. What was actually built

`floodnet/rainfall/provider.py` gains **one** new class, `IMDObservationProvider` (plus a `ProviderUnavailable`
exception) — not a second "IMDNowcastProvider", because there is no genuine quantitative nowcast product to
wrap (§4); building one would mean inventing a mapping from categorical warning bands to fabricated mm/h
values, which this project's rules forbid.

- Reads `IMD_API_KEY` / `IMD_STATION_ID` from the environment (`.env`, git-ignored; see `.env.example`),
  loaded by a ~15-line dependency-free loader in `floodnet/config.py` (no new package — `httpx`, used for
  the actual request, was already a dependency, used the same way as the existing OSM/Overpass fetcher).
- `IMD_STATION_ID` defaults to `43003` (Mumbai-Santacruz), verified via IMD's own `city.imd.gov.in` linking
  convention (`city.imd.gov.in/citywx/...?id=43003` is IMD's own URL for "Mumbai-Santacruz").
- Not configured → `ProviderUnavailable` (not a fabricated value); `/api/simulate` with `scenario_id="live"`
  then returns **HTTP 503** with a clear message, never a silent fallback to scenario/replay data while
  still labelled live (empirically verified — see §8).
- Configured and the call fails (network error, unexpected schema, missing field) → same `ProviderUnavailable`
  outcome, same 503 — confirmed for real by actually calling the real IMD endpoint with an invalid key from
  this project's own code (§8), not just by curl.
- Configured and the call succeeds → real observation parsed using IMD's own documented field names,
  persistence-normalised (§5), tagged ESTIMATED, cached in-process for `CACHE_TTL_S=600s` (10 min) per
  IMD's own portal guidance — *"use client-side caching to optimise performance during peak weather
  events"* — so a demo repeatedly requesting "live" doesn't hammer the API.
- **One genuinely unverified detail, stated plainly rather than guessed silently as fact:** IMD's materials
  confirm a key is *required* but never document *how* it's transmitted (header vs query param). The
  implementation sends it three ways at once (`Authorization: Bearer`, a configurable custom header default
  `X-API-Key`, and an `apikey` query parameter) so whichever convention IMD actually uses is covered; this
  is the one thing to confirm against IMD's real onboarding documentation once a key is issued.

**`scenario_id="live"` wired into the existing `/api/simulate` contract** (no new endpoint): `floodnet/api/state.py`
special-cases it to bypass the deterministic `lru_cache` used for the static scenarios (a live run must
reflect *current* conditions, not be frozen forever at whatever it returned once) and instead relies on
`IMDObservationProvider`'s own short-TTL cache. `GET /api/status`'s `rainfall_providers` list now includes a
`live` entry with `available`/`reason`, computed without making a network call (so status polling stays
cheap) — `ProvenancePanel` in the existing React frontend already renders whatever this list contains
generically, so it will show the live entry's state automatically.

**Deliberately not done this pass, per the explicit "do not redesign the frontend" instruction:** the
`ScenarioPanel` scenario dropdown is populated from `GET /api/scenarios`, which only lists the pilot's
*static* scenarios (moderate/heavy/cloudburst/july2005) — "live" is not a static pilot scenario, so it does
not appear there. Selecting and running "live" therefore currently requires calling `POST /api/simulate
{"scenario_id":"live",...}` directly (curl/Postman/the API docs at `/docs`), not via the dashboard's
dropdown. Adding a "Live" option to that dropdown is a small, well-defined follow-up (the backend contract
is already correct and tested for it) intentionally left out of this pass's scope.

## 8. Verification performed

- **Unit tests, mocked** (`backend/tests/test_rainfall_provider.py`): `IMDObservationProvider` parsing,
  persistence-normalisation arithmetic, ESTIMATED tagging, correct 503-worthy failure on missing key /
  HTTP error / missing field, TTL caching and `clear_cache()`, key transmitted correctly (all three
  mechanisms). Response fixtures use IMD's own documented field names verbatim (`"Last 24 hrs Rainfall"`
  etc.), not invented ones.
- **API integration tests, mocked** (`backend/tests/test_api_contracts.py`): `POST /api/simulate
  {"scenario_id":"live"}` without a key → real HTTP 503 with `IMD_API_KEY` named in the message, no
  traceback; with a mocked successful provider → a full, real engine run on the real pilot (mass balance
  closes to <0.1%, 37 real frames, provenance tag ESTIMATED) proving FloodNet genuinely consumes live input
  end to end; `GET /api/status` correctly reports `live: {available: false, reason: "...IMD_API_KEY..."}`.
- **Real, unmocked verification against the actual IMD server** (this session, no key available): calling
  `IMDObservationProvider(api_key="demo-unregistered-key-not-real").get()` — this project's own code, not
  curl — produced a genuine `HTTP 401 Unauthorized` from `api.imd.gov.in` and correctly raised
  `ProviderUnavailable`, proving the request construction (URL, params, headers) actually reaches IMD's real
  server and the failure path is handled correctly, not merely assumed to work.
- **Opt-in live smoke test** (`backend/tests/test_imd_live_smoke.py`, `pytest.mark.live`, skipped unless
  `IMD_API_KEY` is set): **not executed against a real key in this session** — no credentials were available
  (see §3) — stated here plainly rather than faked. It will run automatically once real credentials are
  configured (`.env` or environment) and asserts a physically-plausible 24h total (0–500 mm) plus a full
  real FloodNet run.
- Full backend suite: **101 passed, 2 skipped** (the two opt-in live tests) — up from 90 passed before this
  pass; zero regressions.

## 8b. Follow-up: historical radar nowcasting feasibility (2026-09-10)

A deeper, dedicated pass investigated whether genuine HISTORICAL (not live) Mumbai Doppler radar data could
support a defensible motion-estimation nowcast demo, without creating any external account (out of scope for
that pass) and without treating the `radarapi.imd.gov.in` Radar Data Supply Portal lead from §1 row 15 as
solved. Findings:

- `dsp.imdpune.gov.in`'s own "Data Formats & Cost Estimation" tool — the closest thing IMD publishes to a
  product/pricing catalogue — lists Surface, Rainfall, Autographic, Upper Air, Agromet, and Radiation as its
  data types. **Radar is not among them**; radar is administered separately, by the Radar Division (Delhi,
  contact `radarlab@gmail.com` / Dr. Soma Sen Roy, per `radarapi.imd.gov.in/dsp/frontend/contact`), reachable
  only after account creation + approval, with pricing not published anywhere found.
- Independent, credible evidence (open-source tooling — `xradar`, `PyScanCf`, part of the Py-ART/"Open Radar
  Science" ecosystem — built specifically to read IMD's raw sweep files) confirms IMD's actual internal radar
  product genuinely is quantitative (NetCDF4, IRIS-inspired, 2-10 files per volume) — i.e. the underlying data
  is real and usable in principle — but no public catalogue, sample file, pricing table, or download path was
  found reachable without the gated account/approval process above.
- No open, DOI-linked, or `data.gov.in`-hosted historical Mumbai/Veravali radar dataset was found.
- The linked official radar-portal User Guide PDF 404s on direct fetch; an IMD radar-applications training PDF
  is reachable but is image-only (no OCR tooling available in this environment) — both genuinely UNVERIFIED,
  not confirmed negative.
- **Conclusion: unchanged from §4/§6 above, now with deeper evidence** — no source reaches class A, and the
  gated class-B path (`radarapi.imd.gov.in`) was investigated as far as possible without account creation.
  **No radar-based feature is being implemented.**
- Scientific feasibility check (grounded in `pysteps`'s own documentation, fetched directly): its worked
  STEPS-nowcast example uses 3 frames at 5-min cadence for a 30-min forecast; the underlying paper (Pulkkinen
  et al. 2019) reports reliable skill up to ~2h given genuine quantitative gridded input. `pysteps` (BSD-3,
  not currently a dependency — confirmed via grep, only appears in docstrings/docs) would be an appropriate
  tool *if* quantitative radar/precipitation fields were ever obtained — the blocker is data access, not
  technique.
- One adjacent, clearly-different-class dataset was surfaced and explicitly NOT pursued: NASA/JAXA **GPM
  IMERG** (satellite multi-sensor precipitation estimation, 0.1°/30-min, freely available for historical dates
  via a NASA Earthdata account). This is **not radar** and must never be labeled as such if a future pass ever
  decides to use it — recorded here only so it isn't silently rediscovered and mislabeled later.

## 8c. IMD API + DWR deep investigation, and the PATH D decision (2026-09-10)

A dedicated IMD-API investigation, followed by an **independent adversarial review** that re-fetched and
re-measured every claim, resolved the radar question. Both agents worked from IMD's own pages; neither
registered an account. Findings below are labelled by the reviewer's verdict.

**VERIFIED — there is no documented radar API.** `api.imd.gov.in/public/api_reference.html` indexes 28 APIs
(`#api-1`…`#api-28`) but its DOM contains only `id="api-1"`…`id="api-20"`; the body ends after "20) Cyclone
Cone of Uncertainty". "Radar Image" (`#api-25`) and "Lightning Data" (`#api-26`) are **dead anchors** — no
endpoint, parameters, response format, or resolution is published for radar anywhere on that page.

**VERIFIED — endpoint existence cannot be probed.** Auth is evaluated *before* routing: a deliberately
nonsensical path (`/api/v1/definitely_not_a_real_endpoint_xyz`) returns byte-identical 401s to a real one.
Auth requires two headers — `X-API-Key` and `Authorization: Bearer <JWT>` — evidenced by three distinct 401
bodies. Note this proves two required *headers*, not necessarily two separately-issued credentials; a
placeholder key passes the first gate, so key *validity* is untestable without a real credential.

**VERIFIED — the public `sri_mum.gif` product genuinely is rainfall intensity, not reflectivity.** It prints
in-band: `DWR MUMBAI (18.9013N, 72.8075E, 100.0 mts)`, `Method Type: Z-R`, `Constant (a/b): (152.0/1.5)`,
`Display Range: 150 Km`, a UTC timestamp, and a legend headed **`mm/hr`** with 15 bins. This corrects the
original §4 row. A rolling animation (`animation/Converted/MUM_SRI.gif`) holds 11 distinct timestamps
spanning 2 h 53 m at bimodal ~10.2/~20.4 min spacing.

**But the product is NOT usable as a FloodNet rainfall input, for measured reasons:**
- **The top bin is open-ended at `>100 mm/h`.** FloodNet's own `cloudburst` scenario peaks at **120 mm/h** and
  the `july2005` replay reaches **190.3 mm/h**. The product cannot distinguish either from 101 mm/h — it
  censors precisely the intensity regime this system exists to model. This alone is disqualifying.
- **Quantisation is ±3.33 mm/h** (bins 6.65 mm/h wide).
- **~12.5% of the pilot footprint is occluded** by the drawn coastline vector — Dadar sits on the coastline,
  so the loss is systematic and located exactly where the pilot is, not averageable noise.
- **"No Data", sub-threshold, and out-of-domain share one RGB** — no-echo is indistinguishable from off-disc.
- **Rain rate is inferred at 2.0 km height**, not at the surface; publication latency measured at 30–40 min;
  and the GIF is **resampled** (0.4277 km/px rendered, fitted from range rings, vs the 0.4 km/px stated).
- The earlier "96.6% of pixels decode exactly" statistic was **reproduced (96.40%) and shown to be
  misleading**: 96.27 of those points are the No-Data background colour; actual rain-bin pixels were 0.13%.
  The statistic measured empty sky. (The narrower claim that the palette has no anti-aliasing *is* robust —
  only 7 distinct RGB values appear in the whole plot panel — but colour→bin being deterministic does not
  make bin→rainfall defensible.)

**VERIFIED — no free historical archive.** `/Radar/` and its subpaths return 403 (no listing), date-stamped
filename guesses 404, and the Internet Archive holds only ~3 captures of `sri_mum.gif` across six years. A
retrospective radar hindcast of a past Mumbai flood is **not possible** from free sources.

**Licensing is worse than previously recorded.** `copyRightPolicy.php` and `termscondition.php` both 404, but
`/responsive/disclaimer.php` returns 200 with an affirmative **"© Copyright 2026 India Meteorological
Department"** and **no licence grant** — an explicit copyright assertion is worse than silence. The official
route for DWR data is `radarapi.imd.gov.in` (Radar Data Supply Portal): account signup + data request +
**payment**, contact `radarlab@gmail.com`, Radar Division. Its terms sit behind login and are **UNKNOWN** —
no account was created.

**VERIFIED — AWS/ARG documents no precipitation field** (confirming the earlier finding). Separately, the
District/Station Nowcast APIs are **categorical** (`Cat1`…`Cat19`, top rain band open-ended at ">15 mm/hr"),
not a quantitative nowcast. River Basin QPF does carry quantitative areal precipitation but is Day1–Day5
daily — far too coarse for a 0–3 h horizon.

### The structural finding that reframes SR-01

**FloodNet's engine cannot ingest a spatial rainfall field from ANY source.** `contracts.RainfallScenario`
carries `intensity_mm_h` as a **`[T]` array** and `intensity_at(t)` returns a single **`float`**;
`engine.run_simulation` passes that scalar to `runoff_fn`. Rainfall is therefore spatially uniform over the
pilot by construction, regardless of provider. Consequently the radar product's coarse footprint over the
pilot (~5.6 × 5.9 radar pixels) costs nothing — **the binding constraint is FloodNet's own scalar interface,
not IMD data access.** Obtaining perfect radar data tomorrow would not, by itself, move SR-01 one step.

### Decision: PATH D

Radar access is blocked for defensible quantitative use. **ECMWF remains the only live provider**, labelled
exactly as it is today — a real NWP forecast, never a radar nowcast, never an IMD product. No radar decoder
is being built: decoding a rendered visualisation is a lossy reconstruction of a *picture*, not an
observation, and must never be tagged as radar-derived measurement.

**Deliberately rejected: PATH C** ("radar access exists but quantitative rainfall is unavailable"). It
understates the case — mm/hr values *are* nominally present — and would invite someone to build the decoder
anyway. **PATH A** is contradicted (no endpoint, no credential, unusable product); **PATH B** is dead (no
archive).

**Open, unresolved, and logged rather than assumed:** the `mausam` imagery licence question. No capture or
harvesting job has been started. The evidence-gathering step that costs nothing and unblocks the most is a
written enquiry to the Radar Division (`radarlab@gmail.com`) covering both DWR data terms and permission for
programmatic use of the public imagery — a human action, not an agent one.

## 9. Remaining limitations (stated, not hidden)

- No IMD source reached class A; live rainfall requires configuring a real, individually-approved API key
  this project does not have.
- Even with a key, the input is a **persistence forecast from a real observed 24h total**, not a genuine
  radar/NWP nowcast — IMD does not publish a quantitative nowcast product via this API. The UI/provenance
  must keep saying exactly this if a key is ever configured.
- The exact API-key transmission mechanism is unverified against real onboarding docs (§7).
- `current_wx`'s single station (Mumbai-Santacruz, ~10 km from the Hindmata/Dadar pilot, the same caveat
  already recorded for the July 2005 replay in `docs/DECISIONS.md`) is applied uniformly over the pilot grid
  — no spatial variation, same simplification already accepted for every other scenario in this project.

## 10. Configuration quick reference

| Variable | Required | Default | Where |
|---|---|---|---|
| `IMD_API_KEY` | Yes, to enable LIVE at all | unset (LIVE disabled, honestly, not faked) | `.env` at repo root (git-ignored) or a real environment variable |
| `IMD_STATION_ID` | No | `43003` (Mumbai-Santacruz) | same |
| `IMD_API_KEY_HEADER` | No | `X-API-Key` | same — only matters if IMD's real header-name convention turns out to differ (§7) |

Copy `.env.example` to `.env` and fill in `IMD_API_KEY` once a real key is obtained (see §3 for how — it is
not self-service). **No real credential is committed anywhere in this repository**; `.env` is listed in
`.gitignore`. No frontend or other backend code needs to change when a key is added — see §11.

### How to configure IMD live observations (operator steps)

1. **Obtain the credential.** Not self-service — per §3, contact IMD's nodal officer for `api.imd.gov.in`
   (Dr. Sankar Nath, `sankar.nath@imd.gov.in`) and complete IMD's own registration/terms-of-use process.
   This project has not obtained one; no URL, contact, or process beyond what §3 already cites from IMD's
   own materials is asserted here.
2. **Place it.** At the repo root: `cp .env.example .env`, then set `IMD_API_KEY=<the real key>` in `.env`.
   Never place it in any tracked file, command-line argument, or commit message.
3. **Environment variable name:** `IMD_API_KEY` (optionally `IMD_STATION_ID`, `IMD_API_KEY_HEADER` — see the
   table above). Loaded automatically at backend startup by `floodnet/config.py`'s `.env` reader; no other
   setup needed. Restart the backend process after changing `.env` (it's read once at import time).
4. **Verify configuration** (no live network call, so this is always safe/cheap to check):
   `curl http://localhost:8000/api/status` → look for
   `{"id":"live", "available": true, ...}` in `rainfall_providers` (it reads `{"available": false, "reason":
   "IMD_API_KEY not configured"}` until a key is set).
5. **Run the opt-in live smoke test** once configured:
   `cd backend && .venv/Scripts/python.exe -m pytest tests/test_imd_live_smoke.py -v -m live`
   (it is skipped automatically, with an explanatory reason, whenever `IMD_API_KEY` is not set — normal
   `pytest tests` never requires or attempts to reach a real IMD credential).

## 11. Dashboard integration (added after this audit — "LIVE OBSERVATION" UI)

"Live Observation" is now a selectable entry in the existing scenario dropdown (synthesised client-side from
`GET /api/status`'s `rainfall_providers` list, which already reported a `live` entry from the first pass of
this work) — no second data path, no bypass of `IMDObservationProvider`. Running it calls the same
`POST /api/simulate` used by every other scenario.

**Three distinct rainfall concepts are now labelled consistently everywhere** (header, scenario panel,
provenance): `SYNTHETIC SCENARIO`, `HISTORICAL REPLAY`, `LIVE OBSERVATION`. The persistence-derived 3h
continuation is always labelled **"3-HOUR PERSISTENCE ESTIMATE"**, never "IMD nowcast", "radar nowcast",
"official forecast" or "live forecast" — this exact string is emitted by the backend
(`RainfallSourceMeta.detail.forecast_extension_label`) and rendered verbatim by the frontend, not composed
from scratch in JS, so wording can't drift between the two layers.

**No-credentials behaviour (verified live against the real, unmodified running server, not just tests):** a
`POST /api/simulate {"scenario_id":"live"}` against this backend with no `IMD_API_KEY` configured returns a
real `HTTP 503` with detail `"live rainfall unavailable: IMD_API_KEY is not configured -- live IMD data is
disabled, not faked. ..."`, and `GET /api/status` reports `{"id":"live", "available": false, "reason":
"IMD_API_KEY not configured"}`. The dashboard shows "LIVE UNAVAILABLE / IMD API credentials are not
configured." and leaves whatever scenario/replay run was already on screen untouched -- it does not clear
the map, switch scenarios, or otherwise silently substitute another data source while still labelled live.

**Security fix made during this pass:** the original `IMDObservationProvider` sent the API key as a query
parameter (necessary, since IMD's docs never specified the transmission mechanism -- see §7) but on a failed
request, httpx's own exception text embeds the *full request URL, including the key*. That raw exception
text was originally being included in the message raised (and therefore in the HTTP 503 body). Fixed:
failure messages are now built from known-safe components only (station id, HTTP status code / exception
class name); the full original exception is still logged server-side (operator-only) for debugging.
Verified with a real (invalid-key) request against the actual `api.imd.gov.in` server that the key no longer
appears in the raised/returned message, plus a regression test
(`test_imd_provider_never_leaks_the_api_key_in_a_failure_message`).

**Structured provenance (Task 4):** a live run's `SimulationResult.provenance` gained one additive key,
`rainfall_source` (the existing `provenance.rainfall` key, and every other scenario's provenance shape, is
untouched) — `RainfallSourceMeta.detail` carries `source`, `station`, `station_id`, `retrieved_at`,
`observed_at`, `observed_rainfall_mm`, `persistence_intensity_mm_h`, `forecast_extension_label`, and
`forecast_extension_note` as plain fields, so the UI renders SOURCE/MODE/STATION/RETRIEVED/RAINFALL/FORECAST
EXTENSION directly rather than parsing them out of the prose provenance note.

**Not done, still correctly out of scope:** SR-02 is not claimed as newly/fully satisfied by the persistence
estimate (§6 stands unchanged) — the dashboard's own copy says "3-hour persistence estimate", never
"forecast" or "nowcast", exactly to avoid that overclaim.

## 12. 2026-09-18 — authenticated IMD API access verified

- **Auth chain (observed responses, credentials never printed):** no key → 401 "API key missing"; key only →
  401 "Authorization header missing or invalid"; key + bad JWT → 401 "Invalid or expired JWT token"; bad key +
  valid JWT → 403 "Invalid API key"; valid key + JWT from an unbound IP → 403 "IP address … not authorized".
  The provider maps each of these to a specific diagnostic without echoing the IP or any credential.
- **Working endpoints (HTTP 200, Mumbai rows):** `current_wx` (43003 Mumbai-Santacruz, 43057 Mumbai-Colaba),
  `stationnowcast` (only "Navi Mumbai" matches), `districtnowcast` (151 MUMBAI CITY, 157 MUMBAI SUBURBAN),
  `districtrainfall` (151/157, daily mm).
- **Field-name correction:** the live `current_wx` sends the observation hour as `Time`, not the documented
  `Time of Observation`; the provider accepts both and labels the time UTC (per the reference).
- **The JWT expires** (observed: valid, then "Invalid or expired JWT token" within ~2 h). Renew it in the portal;
  live smoke tests are opt-in via `RUN_LIVE_IMD=1` so an expired token never fails the offline suite.
- **Radar:** the portal has no radar-product API (see `docs/RADAR_SRI_IMAGE.md` for the image-derived path).

## 13. Input honesty (2026-10-11, decision D-36)

**What the `live` path is.** `IMDObservationProvider` reads `current_wx` "Last 24 hrs Rainfall" (station 43003
Mumbai-Santacruz), divides it by 24 and holds that value constant for the 3-h engine window. The result is the
**mean rate over the preceding day** -- an *antecedent-rainfall indicator* used as a forcing value by
ASSUMPTION. It is **not** a nowcast, **not** the current rainfall rate, and **not** an IMD forecast. The 24-h
window's exact boundaries are not documented by IMD [UNVERIFIED]. Worked illustration of the failure modes
(Agent B §1): 30 mm in the last hour after a dry day reads as 1.25 mm/h; a 300 mm day that ended at 06 UTC
still forces 12.5 mm/h at 18 UTC.

**What changed in code (no change to auth, headers, endpoint, cache TTL, failover order or the ÷24 maths):**
- `detail.caveat`: "IMD 24-h cumulative rainfall at the station expressed as a 24-h mean rate -- an antecedent
  indicator held constant as an ASSUMPTION; NOT a nowcast and NOT the current rainfall rate". The pinned
  `forecast_extension_label` ("3-HOUR PERSISTENCE ESTIMATE") and the raw `observed_at` string are unchanged;
  the provenance note gains one sentence saying the 24-h mean is not the current rate.
- `detail.timing`: `observed_at_utc` parsed from `Date of Observation` + `Time`/`Time of Observation`
  (`"3"` -> 03:00:00 UTC, `"05:30:00"` as is; anything unparseable or a date without a time -> `null`, never
  guessed), `valid_from_utc` = observation time, `valid_until_utc` = observation + 3 h (the next synoptic report
  is due -- a FloodNet convention, not an IMD validity statement), `received_at_utc`, `time_basis` "UTC
  documented", `units`, `accumulation_interval` "24 h", `age_min` (age of the OBSERVATION) and `staleness`:
  FRESH ≤ 180 min, AGING ≤ 360 min, else STALE; UNKNOWN when the time does not parse. Thresholds are the class
  constants `FRESH_MAX_MIN`/`AGING_MAX_MIN`; the clock is injectable (`clock=`).
- `provider_status()` (`/api/status` `rainfall_providers`) live/ecmwf rows and `/api/data-status`
  `active_source` now carry `staleness` and `age_min`, re-evaluated at request time from the cached fetch's
  timing block (no network call, no credential).
- Failover order LIVE → RADAR-DERIVED → FORECAST → CACHED → DEMO is **unchanged**. Agent B recommends no longer
  using the 24-h mean as the first forcing choice; reordering is an open product decision for the user.

**What the UI may claim (Agent B §8):**
- Allowed: "IMD observation (AWS/synoptic), observed at HH:MM UTC, NN min ago"; "persistence of the last
  observation (assumption)"; "ECMWF IFS HRES 9 km NWP forecast via Open-Meteo — not a nowcast, not an IMD
  product"; "IMD nowcast warning: <category text>, valid until HH:MM IST" (verbatim); "N of M labelled
  scenarios"; "radar-image-derived estimate (experimental)".
- Not allowed: "nowcast" for any NWP or persistence output; "IMD nowcast" for anything derived from numbers;
  "probability"/"P(blocked)" from unvalidated scenario counts; "hourly ECMWF" while on the 0.25° default (the
  provider now requests `models=ecmwf_ifs`, see `docs/ECMWF_OPENMETEO_AUDIT.md`); "live rate" for the 24-h mean.

**Still UNRESOLVED (not implemented, display-only candidates for next steps):**
- **IMD AWS `aws_data` `RAINFALL` / `RAINFALL_SEL`:** neither field appears in the IMD API reference; units
  (presumably mm), accumulation interval, and time zone of `TIME` are [UNVERIFIED]. Most likely `RAINFALL` is the
  hourly accumulation reset at each full hour UTC (the documented ARG convention, WMO TECO-2010) and
  `RAINFALL_SEL` a second accumulation -- inference only. **Probe protocol** (server-side, read-only, during
  rain): poll `aws_data?sid=21` every 15 min for a rainy 24 h; for each Mumbai station (MAHALAXMI, RAM_MANDIR,
  MUMBAI_SANTA_CRUZ) plot both fields vs `TIME`. A drop to 0 at each full hour => hourly accumulation; a reset at
  03:00 UTC => daily cumulative; cross-check the 03 UTC value against `districtrainfall` and `current_wx`. Until
  then any display must read "IMD AWS rainfall — accumulation interval UNVERIFIED".
- **Mumbai nowcast ids:** `stationnowcast`/`districtnowcast` with `?id=Santacruz`, `?id=Mumbai`, `?id=151/157`
  returned HTTP 400 in Agent B's probe, while §12 recorded district rows 151/157 on 2026-09-18 -- the reliable
  filter is [UNRESOLVED]. Protocol: fetch the unfiltered lists, match Mumbai by name substring, discard rows whose
  `Date`+`Vupto` (IST [UNVERIFIED]) is past, and show "No IMD nowcast currently issued for Mumbai" as a real
  state. Exact Mumbai station strings must be copied from IMD's nowcast pages by a team member (mausam.imd.gov.in
  is blocked from the research network); do not guess ids.
