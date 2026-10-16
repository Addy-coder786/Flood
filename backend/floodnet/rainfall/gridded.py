"""Gridded (spatially varying) rainfall -> RainfallScenario on the model grid.

WHY THIS MODULE EXISTS
----------------------
The problem statement asks for "high-resolution rainfall nowcasts (from Doppler Weather Radars)" routed "across a 2D
surface terrain model". Until this module, `RainfallScenario` could only carry a `[T]` scalar series, so
rainfall was spatially uniform **by construction, for every provider** -- obtaining radar data would not by
itself have satisfied the requirement, because the engine had nowhere to put a field. That structural gap was
recorded as decision D-15. This module closes the engine-side half of it: any source that can produce a
gridded rainfall field can now be resampled onto the model grid and handed to the engine as a genuine
`[T, ny, nx]` field.

WHAT THIS MODULE DOES *NOT* DO
------------------------------
It does not make rainfall real. It is transport and regridding only. The provenance of whatever field is
passed in is preserved verbatim and is the caller's responsibility:

  - a real radar/QPE product -> tag it REAL (and name the product, resolution, timestamp)
  - an NWP field             -> tag it NWP
  - an invented test field   -> tag it SYNTHETIC

`synthetic_moving_storm()` at the bottom is SYNTHETIC and is labelled as such in its provenance. It exists so
the spatial pathway can be exercised and tested end-to-end without waiting on a data licence, and it must
never be presented as observed rainfall of any kind, radar or otherwise.

RESAMPLING CONTRACT
-------------------
The engine deliberately refuses a field whose grid does not match the terrain grid, so regridding happens
here, once, explicitly. `resample_to_model_grid()` transforms each model cell CENTRE to the source CRS and
samples the source field there (bilinear, with edge clamping), which is the correct direction for
"which source pixel does this model cell fall in" and avoids the common error of resampling the model grid
onto the coarser source.

A source coarser than the model grid (e.g. a 0.1 deg satellite product ~ 11 km over a 2.4 km pilot) will
resample to a field that is nearly or exactly uniform. That is not a bug and it must not be hidden: it means
the source carries no sub-pilot spatial information. `describe_effective_resolution()` reports exactly that,
and `scenario_from_gridded()` records it in the scenario's provenance note, so a uniform-looking "spatial"
field can never be passed off as resolving the storm.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ..config import CRS_COMPUTE, CRS_GEO, RAIN_DT_S
from ..contracts import Grid, RainfallScenario
from ..provenance import Provenance, Tag


def model_cell_lonlat(grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    """(lon, lat) of every model-grid cell centre, each [ny, nx]. EPSG:32643 -> EPSG:4326."""
    from pyproj import Transformer
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    X, Y = np.meshgrid(xs, ys)                      # [ny, nx], row 0 = south (Grid convention)
    inv = Transformer.from_crs(CRS_COMPUTE, CRS_GEO, always_xy=True)
    lon, lat = inv.transform(X.ravel(), Y.ravel())
    return lon.reshape(grid.ny, grid.nx), lat.reshape(grid.ny, grid.nx)


def resample_to_model_grid(field: np.ndarray, src_lons: np.ndarray, src_lats: np.ndarray,
                           grid: Grid) -> np.ndarray:
    """Resample one [n_lat, n_lon] source field (indexed [lat, lon]) onto `grid` -> [ny, nx].

    `src_lons` / `src_lats` are the 1-D coordinate axes of the source, each monotonically increasing or
    decreasing. Bilinear interpolation; points outside the source extent are clamped to the nearest edge
    value rather than set to NaN, because a pilot box sitting at the edge of a radar/satellite domain should
    still get that domain's nearest real value instead of a hole.
    """
    from scipy.interpolate import RegularGridInterpolator

    field = np.asarray(field, dtype=np.float64)
    lats = np.asarray(src_lats, dtype=np.float64)
    lons = np.asarray(src_lons, dtype=np.float64)
    if field.shape != (lats.size, lons.size):
        raise ValueError(f"field shape {field.shape} does not match (lat, lon) axes ({lats.size}, {lons.size})")

    # RegularGridInterpolator needs strictly increasing axes; flip if the source is stored north-to-south.
    if lats[0] > lats[-1]:
        lats = lats[::-1]
        field = field[::-1, :]
    if lons[0] > lons[-1]:
        lons = lons[::-1]
        field = field[:, ::-1]

    interp = RegularGridInterpolator((lats, lons), field, method="linear",
                                     bounds_error=False, fill_value=None)  # fill_value=None -> extrapolate
    lon_c, lat_c = model_cell_lonlat(grid)
    # clamp to the source extent so "extrapolation" is really edge-clamping, never a runaway linear ramp
    lat_q = np.clip(lat_c, lats[0], lats[-1])
    lon_q = np.clip(lon_c, lons[0], lons[-1])
    out = interp(np.column_stack([lat_q.ravel(), lon_q.ravel()]))
    return np.maximum(out.reshape(grid.ny, grid.nx), 0.0)   # rainfall cannot be negative


def describe_effective_resolution(src_lons: np.ndarray, src_lats: np.ndarray, grid: Grid) -> dict:
    """How much spatial information does this source actually carry over the pilot?

    Returns the source pixel size in metres and how many source pixels the pilot spans. If that is ~1, the
    source cannot resolve anything inside the pilot and any 'spatial' field derived from it is effectively
    uniform -- the caller must say so rather than implying the field resolves the storm.
    """
    lats = np.asarray(src_lats, dtype=np.float64)
    lons = np.asarray(src_lons, dtype=np.float64)
    dlat = float(np.abs(np.diff(lats)).mean()) if lats.size > 1 else float("nan")
    dlon = float(np.abs(np.diff(lons)).mean()) if lons.size > 1 else float("nan")
    lat0 = float(np.mean(lats))
    m_per_deg_lat = 111_132.0
    m_per_deg_lon = 111_320.0 * np.cos(np.deg2rad(lat0))
    px_y_m = dlat * m_per_deg_lat
    px_x_m = dlon * m_per_deg_lon
    pilot_y_m = grid.ny * grid.res
    pilot_x_m = grid.nx * grid.res
    spans_y = pilot_y_m / px_y_m if px_y_m > 0 else float("nan")
    spans_x = pilot_x_m / px_x_m if px_x_m > 0 else float("nan")
    return {"source_pixel_m": [round(px_x_m, 1), round(px_y_m, 1)],
            "pilot_extent_m": [round(pilot_x_m, 1), round(pilot_y_m, 1)],
            "pilot_spans_source_pixels": [round(spans_x, 3), round(spans_y, 3)],
            "resolves_within_pilot": bool(spans_x >= 2.0 and spans_y >= 2.0)}


def scenario_from_gridded(scenario_id: str, name: str, t_s: np.ndarray, fields: Sequence[np.ndarray],
                          src_lons: np.ndarray, src_lats: np.ndarray, grid: Grid,
                          provenance: Provenance, description: str = "",
                          already_on_model_grid: bool = False) -> RainfallScenario:
    """Build a spatial RainfallScenario from a time sequence of gridded rainfall fields (mm/h).

    `fields[k]` is the field valid from t_s[k] to t_s[k+1], indexed [lat, lon] on (src_lats, src_lons) --
    unless `already_on_model_grid`, in which case each field must already be [ny, nx] on `grid`.

    The returned scenario carries BOTH the [T, ny, nx] field and the [T] area-mean series, so uniform-mode
    consumers keep working (see RainfallScenario's docstring).
    """
    t_s = np.asarray(t_s, dtype=float)
    if len(fields) != len(t_s):
        raise ValueError(f"{len(fields)} fields but {len(t_s)} timesteps")

    if already_on_model_grid:
        stack = np.stack([np.asarray(f, dtype=np.float64) for f in fields], axis=0)
        if stack.shape[1:] != (grid.ny, grid.nx):
            raise ValueError(f"fields are {stack.shape[1:]}, model grid is ({grid.ny}, {grid.nx})")
        res_note = "field supplied directly on the model grid; no resampling performed"
    else:
        stack = np.stack([resample_to_model_grid(f, src_lons, src_lats, grid) for f in fields], axis=0)
        eff = describe_effective_resolution(src_lons, src_lats, grid)
        res_note = (f"resampled (bilinear) onto the {grid.nx}x{grid.ny} @ {grid.res:g} m model grid. "
                    f"Source pixel ~{eff['source_pixel_m'][0]:.0f} x {eff['source_pixel_m'][1]:.0f} m; the pilot "
                    f"spans {eff['pilot_spans_source_pixels'][0]:.2f} x {eff['pilot_spans_source_pixels'][1]:.2f} "
                    f"source pixels, so this source "
                    + ("DOES resolve structure inside the pilot."
                       if eff["resolves_within_pilot"] else
                       "CANNOT resolve structure inside the pilot -- the resampled field is effectively "
                       "uniform and must not be presented as resolving the storm."))

    area_mean = stack.reshape(stack.shape[0], -1).mean(axis=1)
    prov = Provenance(provenance.tag, provenance.source,
                      (provenance.note + " | " if provenance.note else "") + res_note)
    return RainfallScenario(id=scenario_id, name=name, t_s=t_s, intensity_mm_h=area_mean,
                            provenance=prov, description=description,
                            intensity_field_mm_h=stack, field_grid=grid)


# --------------------------------------------------------------------------------------------------
# SYNTHETIC spatial test field -- NOT radar, NOT observed, NOT a nowcast.
# --------------------------------------------------------------------------------------------------
SYNTHETIC_SPATIAL_ID = "synthetic_spatial"


def synthetic_moving_storm(grid: Grid, horizon_s: int = 3 * 3600, dt_s: int = RAIN_DT_S,
                           peak_mm_h: float = 120.0, sigma_m: float = 700.0,
                           bearing_deg: float = 90.0, speed_m_s: float = 4.0) -> RainfallScenario:
    """An INVENTED Gaussian rain cell tracking across the pilot, as a [T, ny, nx] field.

    Purpose: exercise and test the spatial-rainfall pathway end to end (field -> per-cell runoff -> surface
    routing -> drainage -> street depth) without waiting on a data licence. It is tagged SYNTHETIC and its
    provenance says plainly that it is invented. **It is not radar, not observed, and not a nowcast**, and
    nothing in the UI, API or documentation may present it as any of those.

    Defaults: a 120 mm/h peak (matching the existing `cloudburst` design storm's peak so the two are
    comparable), sigma 700 m, travelling due east at 4 m/s.
    """
    t_s = np.arange(0, horizon_s + dt_s, dt_s, dtype=float)
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    X, Y = np.meshgrid(xs, ys)

    # start the cell one sigma outside the upwind edge so it tracks in, across, and out
    th = np.deg2rad(bearing_deg)
    ux, uy = np.sin(th), np.cos(th)          # bearing 90 deg = +x (east)
    cx0 = grid.x0 + grid.nx * grid.res / 2 - ux * (grid.nx * grid.res / 2 + sigma_m)
    cy0 = grid.y0 + grid.ny * grid.res / 2 - uy * (grid.ny * grid.res / 2 + sigma_m)

    fields = []
    for t in t_s:
        cx = cx0 + ux * speed_m_s * t
        cy = cy0 + uy * speed_m_s * t
        r2 = (X - cx) ** 2 + (Y - cy) ** 2
        fields.append(peak_mm_h * np.exp(-r2 / (2.0 * sigma_m ** 2)))

    prov = Provenance(
        Tag.SYNTHETIC,
        "floodnet.rainfall.gridded.synthetic_moving_storm",
        f"INVENTED spatial test field: Gaussian rain cell, peak {peak_mm_h:g} mm/h, sigma {sigma_m:g} m, "
        f"tracking at {speed_m_s:g} m/s on bearing {bearing_deg:g} deg. Exists to exercise the [T, ny, nx] "
        "spatial-rainfall pathway. NOT radar, NOT observed, NOT a nowcast -- never present it as any of those.")
    return scenario_from_gridded(
        SYNTHETIC_SPATIAL_ID, f"Synthetic moving storm (spatial field, peak {peak_mm_h:g} mm/h)",
        t_s, fields, src_lons=np.zeros(0), src_lats=np.zeros(0), grid=grid, provenance=prov,
        description=("SYNTHETIC spatially-varying storm used to exercise and validate the gridded-rainfall "
                     "pathway. Invented, not observed; not radar."),
        already_on_model_grid=True)
