"""EXPERIMENTAL rainfall field decoded from IMD's public Mumbai-Veravali DWR Surface Rainfall Intensity image.

WHAT THIS IS -- AND IS NOT
--------------------------
IMD publishes the Mumbai (Veravali) C-band Doppler Weather Radar SRI product only as a rendered map image,
https://mausam.imd.gov.in/Radar/sri_vrv.gif (page: .../responsive/radar.php?id=Mumbai-Veravali). IMD has no
radar-product API (api.imd.gov.in has none). This module reads the colours in that image back into
rainfall-rate BINS. The result is labelled RADAR_IMAGE_DERIVED_ESTIMATE everywhere. It is NOT IMD numerical
QPE, NOT an IMD gridded radar product, NOT validated, and NOT a nowcast. See docs/RADAR_SRI_IMAGE.md for the
evidence behind every constant below; each was measured on the official image of 2026-09-18 03:51:28 UTC.

HOW EACH STEP IS ESTABLISHED (nothing assumed from outside the image)
-------------------------------------------------------------------
Legend   Read from EACH image's own colour bar (column x=2700, y=715..2430) -- IMD re-quantises the palette
         per image, so band colours drift by a few RGB units between frames while band positions do not. Its 10 tick marks sit at
         y=859..2433, 174-175 px apart, labelled 45.9 .. 0.1 mm/hr (labels read visually once; verified to
         be evenly spaced, so value is linear in y). Each colour run on the bar becomes a [lo, hi) bin. The
         top colour also covers the open-ended arrow, so it is ">= lo" and CAPPED. At runtime the bar and
         ticks are re-measured and must match the reference, otherwise decoding refuses.
Mask     A legend colour counts as rain only if it never occurs in the basemap. The basemap is sampled
         from the same image outside the 250 km range ring, where no radar echo can exist (on the reference
         image: 0 of ~948 000 such pixels had a legend colour). Two legend colours were also found drawn by
         the basemap INSIDE the ring (pure white = labels/page background; a grey quantisation sliver along
         coastlines) and are always excluded -- rain in those bins (~23.7-26.2 mm/h) is therefore MISSED
         (false negatives preferred to false positives). Pixel classes: ECHO (legend colour), NO_ECHO (a
         basemap colour: < 0.1 mm/h drawn), UNKNOWN (dark text/lines, blended edges, ambiguous colours,
         outside radar range) -- UNKNOWN is never guessed.
Geometry The axes carry tick marks: longitude 71..75 E at x=297..2286 (497.25 px/deg) and latitude
         21..17 N at y=236..2364 (532.0 px/deg), both evenly spaced, i.e. the plot is linear in lon and in
         lat. A least-squares fit to those ticks is the georeference. Check: the radar crosshair (1230,1229.5)
         maps to 72.876 E, 19.133 N -- the image's own stated site 72.8762 E, 19.1342 N, within ~0.2 km. (The
         radar.php map marker says 72.8672 E, ~1 km west; the product geometry agrees with the image text.)
Time     The GIF comment holds the observation time in IST, e.g. "2026-09-18T09:21:28" -- verified against
         the image's printed "03:51:28 UTC / 09:21:28 IST".

Resolution: one displayed pixel is ~0.21 km. IMD does not state the native radar resolution; it is unknown.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np

SRI_URL = "https://mausam.imd.gov.in/Radar/sri_vrv.gif"
PAGE_URL = "https://mausam.imd.gov.in/responsive/radar.php?id=Mumbai-Veravali"
SITE_NAME = "IMD Mumbai-Veravali DWR (C-band)"
SITE_LON, SITE_LAT = 72.8762, 19.1342          # as printed on the product image (see module docstring)
PAGE_MARKER_LON = 72.8672                        # radar.php map marker; ~1 km west of the product geometry
RANGE_KM = 250.0
MODE = "RADAR_IMAGE_DERIVED_ESTIMATE"
IST = timezone(timedelta(hours=5, minutes=30))

IMAGE_SIZE = (3090, 2488)                        # (width, height) of the reference product
LEGEND_X = 2700
LEGEND_Y0, LEGEND_Y1 = 715, 2430                 # colour-bar rectangle (arrow above LEGEND_Y0 = top colour)
LEGEND_TICK_X = 2736
LEGEND_TICKS_Y = (859, 1034, 1209, 1384, 1559, 1733, 1908, 2083, 2258, 2433)
LEGEND_TICK_VALUES = (45.9, 40.8, 35.7, 30.6, 25.5, 20.5, 15.4, 10.3, 5.2, 0.1)   # mm/hr, top -> bottom
LON_TICKS = ((71.0, 297.0), (72.0, 794.0), (73.0, 1292.0), (74.0, 1789.0), (75.0, 2286.0))  # (deg, x px)
LAT_TICKS = ((21.0, 236.0), (20.0, 768.0), (19.0, 1300.0), (18.0, 1832.0), (17.0, 2364.0))  # (deg, y px)
LON_TICK_ROW, LAT_TICK_COL = 2426, 37
FRAME = (32, 2428, 35, 2431)                     # x0, x1, y0, y1 inside the map frame
# Legend bands (by colour-bar row range) whose colour the basemap also draws inside the ring: pure white
# (labels / page background) and a grey quantisation sliver along coastlines. Identified by POSITION because
# IMD re-quantises the GIF palette for every image, so exact RGB values drift by a few units between frames.
BASEMAP_AMBIGUOUS_LEGEND_Y = ((1532, 1618), (1653, 1658))
LEGEND_RGB_TOL = 12                              # per-channel drift allowed vs the reference legend
DARK_MAX_CHANNEL = 80                            # text / lines / borders: occlude whatever lies beneath
TICK_TOL_PX = 2.0

_REFERENCE_LEGEND = Path(__file__).with_name("imd_vrv_sri_legend.json")

ECHO, NO_ECHO, UNKNOWN = 2, 1, 0                  # confidence classes


class SRIDecodeError(RuntimeError):
    """The image cannot be decoded without guessing (layout, legend, geometry or timestamp mismatch)."""


@dataclass(frozen=True)
class LegendBin:
    rgb: int
    lo: float
    hi: Optional[float]            # None -> open-ended (capped) top bin
    y0: int
    y1: int

    @property
    def value(self) -> float:
        """Representative rate: bin midpoint; the open top bin uses its lower bound (conservative)."""
        return self.lo if self.hi is None else 0.5 * (self.lo + self.hi)


@dataclass
class DecodedSRI:
    observed_at_utc: datetime
    rain_mm_h: np.ndarray          # [H, W] float32, NaN where UNKNOWN
    confidence: np.ndarray         # [H, W] uint8: ECHO / NO_ECHO / UNKNOWN
    lon_of_x: np.ndarray           # [W] longitude of each pixel column centre
    lat_of_y: np.ndarray           # [H] latitude of each pixel row centre
    legend: list[LegendBin]
    georef: dict
    stats: dict = field(default_factory=dict)

    @property
    def valid(self) -> np.ndarray:
        return self.confidence != UNKNOWN


# ------------------------------------------------------------------------------------------------ helpers
def _rgb_keys(img) -> np.ndarray:
    pal = np.array(img.getpalette()[:768], dtype=np.int32).reshape(-1, 3)
    rgb = pal[np.asarray(img)]
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def _dark(keys: np.ndarray) -> np.ndarray:
    r, g, b = (keys >> 16) & 255, (keys >> 8) & 255, keys & 255
    return np.maximum(np.maximum(r, g), b) < DARK_MAX_CHANNEL


def _run_centres(mask_1d: np.ndarray, offset: int = 0) -> list[float]:
    idx = np.flatnonzero(mask_1d)
    if idx.size == 0:
        return []
    breaks = np.flatnonzero(np.diff(idx) > 1)
    starts = np.r_[idx[0], idx[breaks + 1]]
    ends = np.r_[idx[breaks], idx[-1]]
    return [float((s + e) / 2 + offset) for s, e in zip(starts, ends)]


def _has_ticks(found: list[float], expected) -> bool:
    return all(any(abs(f - e) <= TICK_TOL_PX for f in found) for e in expected)


def parse_timestamp(comment: bytes | str | None) -> datetime:
    """GIF comment -> UTC. The comment is the observation time in IST (verified against the printed label)."""
    if not comment:
        raise SRIDecodeError("SRI image carries no GIF comment, so its observation time cannot be established")
    text = comment.decode("ascii", "replace") if isinstance(comment, bytes) else comment
    try:
        local = datetime.strptime(text.strip(), "%Y-%m-%dT%H:%M:%S")
    except ValueError as ex:
        raise SRIDecodeError(f"unrecognised SRI timestamp comment {text!r}") from ex
    return local.replace(tzinfo=IST).astimezone(timezone.utc)


def value_at_legend_y(y: float) -> float:
    """Linear legend scale through the bottom (0.1) and top (45.9) ticks."""
    yb, yt = LEGEND_TICKS_Y[-1], LEGEND_TICKS_Y[0]
    vb, vt = LEGEND_TICK_VALUES[-1], LEGEND_TICK_VALUES[0]
    return vb + (yb - y) * (vt - vb) / (yb - yt)


def extract_legend(keys: np.ndarray) -> list[LegendBin]:
    """Colour runs along the image's own colour bar -> bins. Refuses if ticks or colours disagree with the
    reference legend (the product layout changed; decoding would then be guesswork)."""
    ticks = _run_centres(_dark(keys[800:2480, LEGEND_TICK_X]), 800)
    if len(ticks) != len(LEGEND_TICKS_Y) or not _has_ticks(ticks, LEGEND_TICKS_Y):
        raise SRIDecodeError(f"legend tick marks not where the reference legend has them (found {ticks})")
    col = keys[:, LEGEND_X]
    bins: list[LegendBin] = []
    y = LEGEND_Y0
    while y <= LEGEND_Y1:
        y2 = y
        while y2 + 1 <= LEGEND_Y1 and col[y2 + 1] == col[y]:
            y2 += 1
        top = y == LEGEND_Y0
        lo = LEGEND_TICK_VALUES[-1] if y2 == LEGEND_Y1 else value_at_legend_y(y2 + 0.5)
        hi = None if top else value_at_legend_y(y - 0.5)
        bins.append(LegendBin(int(col[y]), round(lo, 3), None if hi is None else round(hi, 3), y, y2))
        y = y2 + 1
    if len({b.rgb for b in bins}) != len(bins):
        raise SRIDecodeError("a legend colour repeats along the colour bar; colour -> value is not unique")
    ref = json.loads(_REFERENCE_LEGEND.read_text(encoding="utf-8"))["bins"]
    if [[b.y0, b.y1] for b in bins] != [c["legend_y"] for c in ref]:
        raise SRIDecodeError("legend band layout differs from the verified reference legend; refusing to guess")
    for b, c in zip(bins, ref):
        r = int(c["rgb"], 16)
        drift = max(abs(((b.rgb >> k) & 255) - ((r >> k) & 255)) for k in (16, 8, 0))
        if drift > LEGEND_RGB_TOL:
            raise SRIDecodeError(f"legend band at y={b.y0} is #{b.rgb:06x}, reference #{r:06x}; colour scale changed")
    return bins


def fit_georeference(keys: np.ndarray) -> dict:
    """Verify the axis tick marks are where the reference has them, then least-squares lon(x), lat(y)."""
    lon_found = _run_centres(_dark(keys[LON_TICK_ROW, FRAME[0]:FRAME[1]]), FRAME[0])
    lat_found = _run_centres(_dark(keys[FRAME[2]:FRAME[3], LAT_TICK_COL]), FRAME[2])
    if not _has_ticks(lon_found, [x for _, x in LON_TICKS]) or not _has_ticks(lat_found, [y for _, y in LAT_TICKS]):
        raise SRIDecodeError("map axis tick marks are not where the reference geometry has them")
    lx = np.array([x for _, x in LON_TICKS]); lv = np.array([v for v, _ in LON_TICKS])
    ly = np.array([y for _, y in LAT_TICKS]); lt = np.array([v for v, _ in LAT_TICKS])
    a_lon, b_lon = np.polyfit(lx, lv, 1)
    a_lat, b_lat = np.polyfit(ly, lt, 1)
    res_lon_px = (lv - (a_lon * lx + b_lon)) / a_lon
    res_lat_px = (lt - (a_lat * ly + b_lat)) / a_lat
    # independent check: the radar crosshair (the darkest full column / row through the frame centre)
    sub = _dark(keys[FRAME[2] + 5:FRAME[3] - 5, FRAME[0] + 5:FRAME[1] - 5])
    cx = float(np.argmax(sub.sum(axis=0)) + FRAME[0] + 5)
    cy = float(np.argmax(sub.sum(axis=1)) + FRAME[2] + 5)
    km_lon = 111.32 * np.cos(np.radians(SITE_LAT))
    centre_err_km = float(np.hypot((a_lon * cx + b_lon - SITE_LON) * km_lon, (a_lat * cy + b_lat - SITE_LAT) * 110.57))
    return {"method": "linear lon(x), lat(y) least-squares fit to the image's own 5+5 axis tick marks",
            "lon_per_px": float(a_lon), "lon_at_x0": float(b_lon),
            "lat_per_px": float(a_lat), "lat_at_y0": float(b_lat),
            "tick_rms_px": float(np.sqrt(np.mean(np.r_[res_lon_px, res_lat_px] ** 2))),
            "pixel_km": (float(abs(a_lon) * km_lon), float(abs(a_lat) * 110.57)),
            "crosshair_px": (cx, cy), "site_vs_crosshair_km": centre_err_km,
            "site_used": (SITE_LON, SITE_LAT), "page_marker_lon": PAGE_MARKER_LON}


# ------------------------------------------------------------------------------------------------ decoding
def decode_sri(data: bytes) -> DecodedSRI:
    """Official SRI GIF bytes -> per-pixel rain bins + confidence classes + georeference. Raises
    SRIDecodeError rather than guessing whenever any verified property of the product does not hold."""
    try:
        from PIL import Image
    except ImportError as ex:   # optional dependency, see pyproject [radar]
        raise SRIDecodeError("Pillow is not installed; install with: pip install -e .[radar]") from ex
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as ex:  # noqa: BLE001
        raise SRIDecodeError(f"not a readable image ({type(ex).__name__})") from ex
    if img.format != "GIF" or img.mode != "P":
        raise SRIDecodeError(f"expected a palette GIF, got {img.format}/{img.mode}")
    if img.size != IMAGE_SIZE:
        raise SRIDecodeError(f"image is {img.size}, the verified product layout is {IMAGE_SIZE}")
    if getattr(img, "n_frames", 1) != 1:
        raise SRIDecodeError("expected a single-frame SRI image")
    observed = parse_timestamp(img.info.get("comment"))

    keys = _rgb_keys(img)
    legend = extract_legend(keys)
    georef = fit_georeference(keys)
    H, W = keys.shape
    lon_of_x = georef["lon_at_x0"] + georef["lon_per_px"] * np.arange(W)
    lat_of_y = georef["lat_at_y0"] + georef["lat_per_px"] * np.arange(H)
    km_lon = 111.32 * np.cos(np.radians(SITE_LAT))
    dkm = np.hypot(((lon_of_x - SITE_LON) * km_lon)[None, :], ((lat_of_y - SITE_LAT) * 110.57)[:, None])

    in_frame = np.zeros((H, W), bool)
    in_frame[FRAME[2]:FRAME[3], FRAME[0]:FRAME[1]] = True
    in_range = in_frame & (dkm <= RANGE_KM)
    basemap_zone = in_frame & (dkm > RANGE_KM + 12)          # beyond the ring + its line: no echo possible
    basemap_colours = np.unique(keys[basemap_zone])

    leg_rgb = np.array([b.rgb for b in legend])
    ambiguous = {b.rgb for b in legend if (b.y0, b.y1) in BASEMAP_AMBIGUOUS_LEGEND_Y}
    ambiguous |= set(np.intersect1d(leg_rgb, basemap_colours).tolist())
    usable = [b for b in legend if b.rgb not in ambiguous]
    lut_keys = np.array([b.rgb for b in usable]); lut_vals = np.array([b.value for b in usable], np.float32)

    order = np.argsort(lut_keys)
    pos = np.clip(np.searchsorted(lut_keys[order], keys), 0, len(order) - 1)
    is_echo = in_range & (lut_keys[order][pos] == keys)
    is_basemap = in_range & ~is_echo & np.isin(keys, basemap_colours) & ~np.isin(keys, list(ambiguous)) & ~_dark(keys)

    conf = np.full((H, W), UNKNOWN, np.uint8)
    conf[is_basemap] = NO_ECHO
    conf[is_echo] = ECHO
    rain = np.full((H, W), np.nan, np.float32)
    rain[is_basemap] = 0.0
    rain[is_echo] = lut_vals[order][pos][is_echo]

    n = int(in_range.sum())
    stats = {"pixels_in_range": n,
             "echo_pct": 100.0 * int(is_echo.sum()) / n,
             "no_echo_pct": 100.0 * int(is_basemap.sum()) / n,
             "unknown_pct": 100.0 * (n - int(is_echo.sum()) - int(is_basemap.sum())) / n,
             "legend_colours_in_basemap_zone": int(np.isin(keys[basemap_zone], leg_rgb).sum()),
             "excluded_legend_colours": sorted(f"{c:06x}" for c in ambiguous if c in set(leg_rgb.tolist())),
             "max_rain_mm_h": float(np.nanmax(rain)) if is_echo.any() else 0.0}
    return DecodedSRI(observed, rain, conf, lon_of_x, lat_of_y, legend, georef, stats)


def pilot_window(dec: DecodedSRI, bbox_lonlat, margin_px: int = 6) -> tuple[slice, slice]:
    """Row/column slices of the source pixels covering `bbox_lonlat` (+ a margin for interpolation)."""
    w, s, e, n = bbox_lonlat
    xs = np.flatnonzero((dec.lon_of_x >= w) & (dec.lon_of_x <= e))
    ys = np.flatnonzero((dec.lat_of_y >= s) & (dec.lat_of_y <= n))
    if xs.size == 0 or ys.size == 0:
        raise SRIDecodeError("the pilot box lies outside the decoded image")
    return (slice(max(ys[0] - margin_px, 0), ys[-1] + margin_px + 1),
            slice(max(xs[0] - margin_px, 0), xs[-1] + margin_px + 1))


def window_to_model_grid(val: np.ndarray, lons: np.ndarray, lats: np.ndarray, grid) -> tuple[np.ndarray, np.ndarray]:
    """Any decoded/advected rain window (NaN = unknown) -> ([ny, nx] mm/h, validity weight). Valid-weighted
    bilinear resample: unknown pixels are dropped, never read as zero rain."""
    from .gridded import resample_to_model_grid
    val = np.asarray(val, dtype=np.float64)
    ok = np.isfinite(val)
    num = resample_to_model_grid(np.where(ok, val, 0.0), lons, lats, grid)
    den = resample_to_model_grid(ok.astype(np.float64), lons, lats, grid)
    return np.where(den > 1e-6, num / np.maximum(den, 1e-6), 0.0), den


def to_model_grid(dec: DecodedSRI, grid, bbox_lonlat) -> tuple[np.ndarray, np.ndarray, dict]:
    """Decoded pixels -> [ny, nx] on the model grid via the existing bilinear resampler, normalised by the
    valid-pixel weight so UNKNOWN pixels are dropped rather than read as zero. Cells with no valid source
    pixel nearby get 0 mm/h and zero validity weight (reported, never filled from elsewhere)."""
    rs, cs = pilot_window(dec, bbox_lonlat)
    val = dec.rain_mm_h[rs, cs].astype(np.float64)
    ok = np.isfinite(val)
    field_, den = window_to_model_grid(val, dec.lon_of_x[cs], dec.lat_of_y[rs], grid)
    conf_win = dec.confidence[rs, cs]
    win_stats = {"window_px": [int(rs.stop - rs.start), int(cs.stop - cs.start)],
                 "window_valid_pct": 100.0 * float(ok.mean()),
                 "window_echo_pct": 100.0 * float((conf_win == ECHO).mean()),
                 "model_cells_without_valid_source_pct": 100.0 * float((den <= 1e-6).mean())}
    return field_, den, win_stats


def _ramp(v: np.ndarray) -> np.ndarray:
    """0 mm/h -> white, then light blue -> dark blue -> red at 50 mm/h (display only); NaN -> grey."""
    t = np.clip(np.nan_to_num(v, nan=0.0) / 50.0, 0, 1)
    out = np.stack([255 * t, 255 * (1 - t) * 0.75, 255 * (1 - 0.6 * t)], -1)
    out[np.nan_to_num(v, nan=0.0) <= 0] = 255
    out[~np.isfinite(v)] = 150
    return out.astype(np.uint8)


def validation_panels(dec: DecodedSRI, source_bytes: bytes, grid_field: Optional[np.ndarray], out_path: Path,
                      bbox_lonlat=None, crop=(900, 1650, 850, 1600)) -> Path:
    """SOURCE IMAGE | DECODED MASK | DECODED RAINFALL | FINAL MODEL GRID, side by side (no accuracy claims).
    The pilot box is outlined in the first three panels; the fourth panel IS the pilot grid (same 0-50 scale)."""
    from PIL import Image, ImageDraw
    x0, x1, y0, y1 = crop
    src = Image.open(io.BytesIO(source_bytes)).convert("RGB").crop((x0, y0, x1, y1))
    c = dec.confidence[y0:y1, x0:x1]
    mask = np.zeros(c.shape + (3,), np.uint8)
    mask[c == UNKNOWN] = (150, 150, 150); mask[c == NO_ECHO] = (255, 255, 255); mask[c == ECHO] = (0, 90, 220)
    tiles = [src, Image.fromarray(mask), Image.fromarray(_ramp(dec.rain_mm_h[y0:y1, x0:x1]))]
    if grid_field is not None:
        tiles.append(Image.fromarray(_ramp(grid_field[::-1])).resize(src.size, Image.NEAREST))  # row 0 = south
    size = src.size
    if bbox_lonlat is not None:
        rs, cs = pilot_window(dec, bbox_lonlat, margin_px=0)
        box = (cs.start - x0, rs.start - y0, cs.stop - x0, rs.stop - y0)
        for t in tiles[:3]:
            ImageDraw.Draw(t).rectangle(box, outline=(0, 0, 0), width=2)
    labels = ["SOURCE IMAGE (IMD SRI, pilot boxed)", "DECODED MASK blue=echo white=no echo grey=unknown",
              "DECODED RAINFALL (legend bins, 0-50 mm/h)", "FINAL MODEL GRID (pilot, 0-50 mm/h)"]
    sheet = Image.new("RGB", (size[0] * len(tiles), size[1] + 24), "white")
    d = ImageDraw.Draw(sheet)
    for k, t in enumerate(tiles):
        sheet.paste(t, (k * size[0], 24))
        d.text((k * size[0] + 6, 5), labels[k], fill="black")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path)
    return out_path
