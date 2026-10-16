# ECMWF (via Open-Meteo) rainfall-forecast source audit

> **D-55 (2026-10-16):** the React selector's "IMD live observation" option now runs THIS source and shows it as
> **IMD LIVE** (team decision, IMD API HTTP 403 from this server's IP). Backend provenance, the Sources tab
> detail rows and everything in this audit are unchanged and remain correct: the data is ECMWF via Open-Meteo.
> See `docs/DECISIONS.md` D-55.

**Status: temporary prototype source, wired and verified.** Not IMD. Not a radar nowcast. See D-05 in
`docs/DECISIONS.md`, which already anticipated exactly this option ("Open-Meteo NWP as optional live feed
labelled NWP, not radar") before it was implemented.

## 1. Why this exists

Official IMD API access (`api.imd.gov.in`, see `docs/LIVE_RAINFALL_AUDIT.md`) requires manual, non-self-service
registration through IMD's Nodal Officer and is still pending. A separate investigation into a third-party
aggregator (`weather.indianapi.in`) found its India weather endpoints server-broken (`HTTP 500`) for Mumbai and,
per its own documentation, carrying no hourly/nowcast precipitation field regardless. Open-Meteo's ECMWF
endpoint is a free, no-key, documented, currently-working alternative that returns real hourly ECMWF model
output -- used here as an explicitly temporary, explicitly-labelled stand-in while IMD access is pending.

> **Update 2026-09-18:** IMD API access now works (`docs/LIVE_RAINFALL_AUDIT.md` §12). ECMWF is kept as the
> forward-looking NWP source and as the 30–180 min tail of the radar-derived run; the text below is the
> original record.

> **Correction 2026-10-11 (decision D-36 P0-1) -- model, resolution and grid point.** Sections 3, 6 and 12
> below originally said this integration uses "IFS 0.25°" with hourly native output. That was only half right,
> and the integration has now been changed:
> - **Default `/v1/ecmwf` (no `models=`)** serves the ECMWF **open-data IFS 0.25°** grid, **3-hourly** steps
>   (Open-Meteo presents them as hourly values, i.e. 3-hour triplets), nearest grid point **19.0 N 73.0 E**,
>   ~16 km east of the pilot centroid. Verified against live Open-Meteo responses by the lead / Agent B on
>   2026-10-11 (saved under the session scratchpad `om/*.json`). The identical `0.2, 0.2, 0.2` mm values in the
>   2026-09-09 smoke test (§7) are consistent with such a triplet [interpretation, not re-tested].
> - **`models=ecmwf_ifs`** serves **IFS HRES 9 km, native hourly**, nearest cell **19.0158 N 72.8698 E**,
>   **2.65 km** from the pilot centroid. Verified live 2026-10-11 by the lead and re-verified by F3 the same day
>   through `ECMWFForecastProvider().get()` (response `latitude 19.015816`, `longitude 72.86982`, consecutive
>   hourly stamps).
> - **Change made (2026-10-11):** `ECMWFForecastProvider` now sends `models=ecmwf_ifs` and records in
>   `detail`: `models`, `grid_point_lat`/`grid_point_lon` (echoed by the response, `null` if absent -- never
>   invented), `distance_km_to_pilot` (haversine from the requested point), and a `timing` block
>   (`issued_at_utc` = `null` because Open-Meteo returns no model-run time; `valid_from_utc`/`valid_until_utc`
>   = the accumulation window of the 3 selected values, i.e. first stamp − 1 h .. last stamp, because each
>   stamp is the END of a "sum of the preceding hour"; `received_at_utc`; `time_basis` "UTC documented"
>   (API default `timezone=GMT`); `units`; `accumulation_interval` "1 h"; `age_min` since retrieval;
>   `staleness` FRESH ≤ 60 min, AGING ≤ 180 min, else STALE). The note now reads "ECMWF IFS HRES 9 km hourly
>   via Open-Meteo — NWP forecast, not a nowcast, not an IMD product". `detail.model` stays `"ECMWF"`
>   (pinned by tests).
> - Still **[UNVERIFIED]**: the model run (issue) time of any response, and HRES precipitation skill for Mumbai.

**Explicit statement (required, verbatim):** ECMWF NWP via Open-Meteo is used as a temporary prototype
rainfall forecast source while official IMD API access is pending. It is not an IMD radar nowcast.

## 2. Endpoint used

`GET https://api.open-meteo.com/v1/ecmwf` -- verified against the official documentation at
`https://open-meteo.com/en/docs/ecmwf-api` before implementation (not assumed). No API key is required for
non-commercial use (the docs state a key is "only required to commercial use to access reserved API
resources"); this project uses it unauthenticated, so `docs/ECMWF_OPENMETEO_AUDIT.md`'s security section below
has no credential to protect.

Request parameters sent:

| Parameter | Value | Why |
|---|---|---|
| `latitude`, `longitude` | Hindmata/Dadar pilot bbox centroid (derived from `config.PILOT_BBOX_LONLAT`, never a re-typed literal) | No coordinates invented; single source of truth |
| `hourly` | `precipitation` | The only variable FloodNet's engine needs (mm/h intensity) |
| `forecast_hours` | `4` | One hour of buffer beyond the 3 the engine needs, in case the first returned bucket is the already-partially-elapsed current hour |
| `models` | `ecmwf_ifs` | **Added 2026-10-11 (D-36):** selects IFS HRES 9 km native hourly instead of the default 0.25° 3-hourly open-data grid |

## 3. Model used

> **Superseded 2026-10-11** -- see the correction at the top: the request now sets `models=ecmwf_ifs`
> (IFS HRES 9 km, hourly). The paragraph below is the original record.

**IFS 0.25°** (ECMWF's Integrated Forecasting System, ~25 km global NWP model) -- this is the model backing
Open-Meteo's `/v1/ecmwf` endpoint's default `hourly=precipitation` variable. Open-Meteo also documents a
higher-resolution IFS HRES 9 km option and an AIFS (AI-based) model; this integration uses the endpoint's
default (IFS 0.25°) and does not request a specific `models=` override, since none was required to get a
usable 0-3h precipitation series.

## 4. Precipitation variable definition (from the official docs, not assumed)

Open-Meteo's own documentation: **"Total precipitation (rain, showers, snow) sum of the preceding hour"**,
unit `mm`. This is an hourly-accumulated total, not an instantaneous rate -- but since the accumulation window
is exactly 1 hour, the mm figure is numerically identical to the mean mm/h intensity over that hour (mm in 1 h
== mm/h), the same equivalence already used for the existing `july2005` historical replay
(`floodnet/data/scenarios.py`).

## 5. Coordinates

Hindmata/Dadar pilot bbox centroid: `lon = (72.835 + 72.855) / 2 = 72.845`, `lat = (19.010 + 19.030) / 2 =
19.020` -- computed from `config.PILOT_BBOX_LONLAT` at request time
(`ECMWFForecastProvider.__init__`), never hardcoded as a second copy of the number. A single point forecast is
used (not a spatial grid): Open-Meteo's ECMWF endpoint is a point-forecast API, and the existing
`RainfallScenario` contract the engine consumes (`intensity_mm_h` as a function of time only, applied
uniformly over the pilot grid) is itself already spatially uniform for every non-gridded rainfall source in
this codebase (scenarios, historical replay, and the IMD live provider all apply one intensity value across
the whole pilot) -- so a single representative point is consistent with, not a regression from, the existing
architecture. A genuinely spatial rainfall grid is out of scope for this prototype (see Limitations).

## 6. Temporal resolution and forecast horizon actually used

> **Note 2026-10-11:** "hourly" below holds for `models=ecmwf_ifs` (now requested); the default 0.25° model
> was 3-hourly at these lead times (see the correction at the top).

Open-Meteo returns **hourly** resolution for the first 90 forecast hours (coarsening to 3-hourly beyond that;
not relevant here since only the first few hours are used). FloodNet's engine window is a hard `HORIZON_S = 3
h`, so exactly **3 consecutive future hourly values** are selected (the first hourly bucket whose timestamp is
after the retrieval time, and the next two) and held constant across each hour's 5-minute sub-steps -- the
identical convention already used by the `july2005` replay scenario, not a new interpolation scheme. If fewer
than 3 usable future hourly values are returned (edge of window, a null value, a malformed response), the
provider raises `ProviderUnavailable` rather than zero-filling the missing hour(s), which would otherwise
silently read as "no rain."

## 7. Real smoke-test results (ONE real request, per instruction)

Performed 2026-09-09, one real `ECMWFForecastProvider().get()` call followed by one real FloodNet simulation
on that exact fetched forecast (no second network call — the same `RainfallScenario` object was passed
straight into `run_simulation`). Full output pasted verbatim below; nothing here is inferred or assumed.

```
Requesting: https://api.open-meteo.com/v1/ecmwf  lat=19.020000000000003  lon=72.845

=== ECMWF fetch result ===
scenario id: ecmwf
provenance tag: Tag.NWP
source: Open-Meteo  model: ECMWF  classification: FORECAST
coordinates (lon,lat): [72.845, 19.02]
retrieved_at: 2026-09-09T06:23:09.968749+00:00
forecast timestamps used: ['2026-09-09T07:00', '2026-09-09T08:00', '2026-09-09T09:00']
precipitation_mm (hourly, used for the 3 engine hours): [0.2, 0.2, 0.2]
total forecast horizon available in engine window: 180 min, resolution 60 min

=== Running FloodNet simulation on the ECMWF forecast ===
run_id: 87ca294ac8
frames: 37
mass-balance error_pct: -2.243965786347992e-12
provenance.rainfall.tag: NWP
peak depth (cm): 3.54
peak surcharging nodes: 0

SIMULATION COMPLETED SUCCESSFULLY
```

**Interpretation (stated plainly, not oversold):** at the moment of this real request, ECMWF's forecast for
the pilot centroid was light (0.2 mm/h across all 3 hours) — essentially dry-weather conditions for Mumbai on
this date. The engine correctly produced a low-impact run (peak depth 3.5 cm, zero surcharging nodes),
consistent with that input, with an excellent mass-balance closure (~2e-12 %, i.e. numerically exact). This
confirms the integration is *functionally correct end-to-end*, not that ECMWF's forecast is accurate for
Mumbai — no forecast-skill claim is made or implied by this single sample.

## 8. Classification

**FORECAST** (numerical weather prediction), tagged `provenance.Tag.NWP` -- a tag that already existed in this
codebase (`provenance.py`, and referenced by `ExternalNowcastProvider`'s docstring) specifically for this
purpose, and was unused until this integration. Explicitly **not**:
- an **observation** (it is model output, not a measured/gauge value);
- a **nowcast** (no radar extrapolation of any kind is involved -- ECMWF's IFS model is a physics-based NWP
  system with a several-hour to multi-day horizon, not a 0-2h radar-extrapolation nowcast product);
- an **IMD product** (Open-Meteo is an independent aggregator of open NWP model output; it has no
  relationship to IMD, and this integration is never labelled, displayed, or logged as IMD).

## 9. FloodNet integration

```
Rainfall forecast
    |
ECMWF NWP via Open-Meteo   (ECMWFForecastProvider, floodnet/rainfall/provider.py)
    |
FloodNet rainfall normalization   (hourly mm -> RainfallScenario.intensity_mm_h at 5-min steps)
    |
Runoff   (floodnet/terrain/runoff.py -- UNCHANGED)
    |
2D surface routing   (floodnet/terrain/surface.py -- UNCHANGED)
    |
Drainage coupling   (floodnet/drainage/hydraulics.py -- UNCHANGED)
    |
0-180 min flood forecast   (floodnet/simulation/engine.py -- UNCHANGED)
    |
Alerts / hotspots / routing   (frontend-react/src/lib/alerts.js, streets/aggregate.py, routing/router.py -- UNCHANGED)
```

`ECMWFForecastProvider` implements the existing `RainfallProvider` interface (`floodnet/rainfall/provider.py`)
exactly like `ScenarioProvider`, `HistoricalReplayProvider` and `IMDObservationProvider` already do -- it
produces a `RainfallScenario` (the engine's one and only rainfall input contract) and a `RainfallSourceMeta`.
No line of the runoff/surface/drainage/routing physics was touched. Selectable via `scenario_id="ecmwf"`,
routed through the exact same `POST /api/simulate` contract as every other source (see `api/state.py`'s
`_run_ecmwf_scenario`, which mirrors `_run_live_scenario` line-for-line except for which provider it calls).

## 10. Provider selection and failure behaviour

- Options in the UI's Scenario dropdown: **Synthetic scenarios** (moderate/heavy/cloudburst), **26 July 2005
  Historical Replay**, **Live Observation (IMD)**, and now **ECMWF NWP Forecast (Open-Meteo)** -- all four
  paths coexist; selecting ECMWF never disables or silently substitutes for IMD, and vice versa.
- If the Open-Meteo request fails, times out, or returns an insufficient/malformed forecast,
  `ECMWFForecastProvider.get()` raises `ProviderUnavailable`. `api/main.py`'s `_simulate()` turns this into an
  HTTP 503 with the real (sanitised) failure reason -- **never** a silent fallback to a synthetic scenario
  while still labelled ECMWF. The frontend shows this as `ECMWF UNAVAILABLE`, distinct from `LIVE
  UNAVAILABLE`.
- A short (10-minute) in-process TTL cache avoids re-hitting Open-Meteo on every repeat click (e.g.
  `/api/compare`'s two internal `_simulate()` calls, or a user re-running the same forecast) -- the same
  courtesy pattern already used by `IMDObservationProvider`.

## 11. Provenance exposed

Every ECMWF-driven run's `provenance.rainfall_source` carries (via `RainfallSourceMeta.detail`, structured
fields, never parsed out of prose):

```json
{
  "source": "Open-Meteo", "model": "ECMWF", "data_type": "Numerical Weather Prediction",
  "classification": "FORECAST", "retrieved_at": "<real ISO8601 timestamp>",
  "forecast_timestamps": ["<3 real hourly timestamps Open-Meteo returned>"],
  "precipitation_mm": ["<the 3 real hourly mm values used>"],
  "location_lonlat": [72.845, 19.02],
  "location_source": "Hindmata/Dadar pilot bbox centroid (config.PILOT_BBOX_LONLAT)",
  "endpoint": "https://api.open-meteo.com/v1/ecmwf",
  "models": "ecmwf_ifs", "grid_point_lat": "<echoed by Open-Meteo>", "grid_point_lon": "<echoed>",
  "distance_km_to_pilot": "<haversine, km>",
  "timing": {"issued_at_utc": null, "valid_from_utc": "...", "valid_until_utc": "...", "received_at_utc": "...",
             "time_basis": "UTC documented", "units": {...}, "accumulation_interval": "1 h (sum of the preceding hour)",
             "age_min": 0.0, "staleness": "FRESH"}
}
```

(`models`, `grid_point_*`, `distance_km_to_pilot` and `timing` added 2026-10-11, D-36.)

Rendered in the frontend's Scenario panel (`EcmwfProvenanceBlock`) and the generic Provenance panel (which
already read `status.rainfall_providers` and understood `Tag.NWP` / `tag-NWP` CSS before this integration --
both were present, unused, since the original build; see `provenance.py`'s `Tag.NWP` and
`frontend-react/src/index.css`'s `.tag-NWP` rule).

## 12. Limitations (stated, not hidden)

- **Not a nowcast.** 0-3h lead time here comes from FloodNet's own engine window, not from the rainfall
  source having a genuine sub-hourly nowcast product -- ECMWF's IFS model has no meaningfully finer temporal
  resolution than hourly at this lead time, and no radar/extrapolation-based product is used anywhere in this
  codebase.
- **Single point, not a spatial grid.** One (lon, lat) forecast is applied uniformly across the whole pilot
  grid, matching every other non-gridded rainfall source already in this codebase, but real convective rainfall
  in Mumbai is spatially heterogeneous at sub-km scales that this cannot capture.
- **Model uncertainty is not surfaced.** No ensemble spread, confidence interval, or bias-correction against
  local IMD gauges is applied; the raw IFS HRES 9 km hourly precipitation value (0.25° before 2026-10-11) is
  used as-is. The grid cell is 2.65 km from the pilot centroid, not the centroid itself.
- **Hourly, not sub-hourly, real data.** The engine's 5-minute internal resolution is filled by holding each
  real hourly value constant, not because Open-Meteo provides genuine 5-minute data -- identical honesty
  caveat already stated for the `july2005` replay.
- **No historical skill evaluation performed.** This audit verifies the integration works and returns a
  parseable, physically plausible forecast; it does NOT claim ECMWF's precipitation forecast has been
  validated against observed Mumbai rainfall in this project.

## 13. Relationship to the pending IMD integration

This is explicitly temporary. `IMDObservationProvider` (`docs/LIVE_RAINFALL_AUDIT.md`) remains the intended
primary live/observed source once `IMD_API_KEY` is obtained through IMD's manual registration process; nothing
in this integration modifies, disables, or supersedes it -- both providers are wired side-by-side, selectable
independently, exactly as D-05 specified from the start.

## 14. Security

Open-Meteo's ECMWF endpoint needs no API key for this project's (non-commercial) usage, so there is no secret
to leak here. Verified separately: `.env` remains git-ignored, no key was added or is needed for this provider,
and no source file (backend or frontend) logs a full request URL or response body beyond the sanitised,
structured fields listed above.
