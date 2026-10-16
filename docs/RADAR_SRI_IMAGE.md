# IMD Mumbai-Veravali SRI image → FloodNet rainfall (EXPERIMENTAL)

Status: **RADAR_IMAGE_DERIVED_ESTIMATE**. Not IMD numerical QPE, not an IMD gridded product, not validated, not a nowcast.
Code: `backend/floodnet/rainfall/imd_sri_image.py`, provider `IMDVeravaliSRIImageProvider` (scenario id `imd_sri`).
Tests: `backend/tests/test_imd_sri_image.py`.

## Source
- Page: https://mausam.imd.gov.in/responsive/radar.php?id=Mumbai-Veravali
- Image: https://mausam.imd.gov.in/Radar/sri_vrv.gif (one current frame, overwritten in place; no archive at this URL)
- api.imd.gov.in has no radar-product API (confirmed by IMD).
- Reuse/automated-retrieval terms: **not confirmed** (site shows © IMD and a liability disclaimer only). The image is therefore not committed; tests use a local copy in `backend/tests/fixtures/local/` (gitignored). Fetches are single, low-frequency, cached 5 min.

## Evidence (measured on the official image observed 2026-09-18 03:51:28 UTC; re-checked on 04:29:29 UTC)
| Item | Finding |
|---|---|
| File | GIF89a, 3090×2488, 1 frame, 256-colour palette re-quantised per image |
| Time | GIF comment `2026-09-18T09:21:28` = IST; matches printed "03:51:28 UTC / 09:21:28 IST" |
| Legend | Colour bar x=2700, y=715–2430; 10 ticks y=859…2433 labelled 45.9…0.1 mm/hr, evenly spaced (174–175 px). 47 colour bands → bins 0.18–2.53 mm/h wide; top band open-ended (≥ 49.0 mm/h, capped). Band positions identical across frames; colours drift ≤ 7 RGB units, so each frame's own legend is used and checked against `imd_vrv_sri_legend.json` by position (±12 RGB). |
| Mask | 0 legend-coloured pixels among ~948 000 basemap-only pixels (beyond the 250 km ring) in both frames. White (fefefe) and a grey sliver (d9e2e6) are also drawn by the basemap inside the ring → always excluded, so ~23.7–26.2 mm/h echoes are missed. Dark text/lines, blended edges and out-of-range pixels are UNKNOWN (never filled). |
| Geometry | Axis ticks lon 71–75 E at x=297…2286 (497.25 px/°) and lat 21–17 N at y=236…2364 (532.0 px/°), evenly spaced → linear lon(x), lat(y); tick-fit RMS 0.17 px. Crosshair maps to 72.876 E, 19.133 N = image-stated site 72.8762 E, 19.1342 N (0.23 km). radar.php map marker (72.8672 E) is ~1 km west and not used. |
| Resolution | Displayed pixel ≈ 0.21 × 0.21 km. Native radar resolution: not published. Pilot box ≈ 10 × 11 image pixels. |
| Latency | One sample: observed 03:51 UTC, published 04:30 UTC (~39 min). Refresh interval not established. |

## Transformation
SRI GIF → legend bins (bin midpoint; capped top bin uses its lower bound) → ECHO / NO_ECHO / UNKNOWN mask →
tick-fitted lon/lat → pilot window → valid-weighted bilinear resample onto the model grid (existing
`gridded.resample_to_model_grid`) → `[T, ny, nx]`. t=0 is the observed frame. Since 2026-09-21 the later
steps follow a source-aware horizon (gated advection nowcast 5–30 min when two distinct frames exist, then
ECMWF or persistence) — see `docs/RADAR_NOWCAST_STATUS.md`. With a single frame and no ECMWF it is still the
3-hour persistence estimate described here.

Refusals (ProviderUnavailable, no substitution): image size/format/legend/tick layout differs from the verified
product; timestamp missing; frame older than 90 min; <50 % decodable pixels over the pilot; fetch failure.

A validation sheet (source | mask | decoded rainfall | model grid) is written by `validation_panels()` to
`data/interim/radar_validation/` (gitignored). No accuracy figures exist or are claimed.

## Related facts (2026-09-18)
- **PAC** (`https://mausam.imd.gov.in/Radar/pac_vrv.gif`) is the same kind of rendered image: 24-hour
  accumulation in mm, timestamped ~20 h before download when inspected. It is not decoded — a 24 h total cannot
  drive a 5-minute model.
- **No numerical radar via the IMD API portal**: api.imd.gov.in lists "Radar Image" only as a dead index entry and
  IMD confirmed the portal provides no radar products. A native numerical product would have to come via IMD's
  Data Supply Portal under a licence.
- **Tests:** decoder logic runs everywhere on a format-faithful generated stand-in (`tests/sri_fixture.py`,
  `tests/test_imd_sri_decoder_logic.py`); behaviour on the genuine image is checked by
  `tests/test_imd_sri_image.py`, which skips when the local image is absent.
