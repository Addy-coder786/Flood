"""Rainfall -> runoff depth per cell (metres generated during one step).

Model (rational-method style, per cell):
    d      = i_mm_h / 1000 / 3600 * dt_s                      gross rain depth (m)
    runoff = d * (imp * C_IMP + (1 - imp) * C_PERV)           on open (non-building) cells
Building cells: roof runoff d * C_IMP is NOT left on the roof; it is moved to the nearest non-building
cell (Euclidean nearest, precomputed once per Terrain and cached). The returned array is zero on building cells.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage

from ..contracts import Terrain
from ..provenance import Provenance, Tag

C_IMP = 0.95    # runoff coefficient, impervious urban surfaces (roofs, asphalt)
C_PERV = 0.35   # runoff coefficient, pervious urban surfaces (lawns/open ground, wet antecedent, monsoon)

PROVENANCE = Provenance(
    Tag.ESTIMATED,
    "Rational-method runoff coefficients (typical urban values: ASCE/WEF 1992 'Design and Construction of Urban "
    "Stormwater Management Systems'; Chow, Maidment & Mays 1988 Table 15.1.1)",
    f"C_imp={C_IMP} (roofs/asphalt 0.70-0.95), C_perv={C_PERV} (lawns/parks 0.10-0.35, upper bound for saturated "
    "monsoon conditions). Roof runoff redirected to nearest non-building cell (no roof storage).",
)

_CACHE: dict[int, tuple] = {}


def _prepare(terrain: Terrain) -> tuple:
    """Cache per terrain: coefficient grid, building mask, roof->target flat indices."""
    cached = getattr(terrain, "_runoff_cache", None)
    if cached is not None:
        return cached
    key = id(terrain)
    if key in _CACHE:
        return _CACHE[key]
    building = np.asarray(terrain.building, dtype=bool)
    imp = np.clip(np.asarray(terrain.impervious, dtype=np.float64), 0.0, 1.0)
    coef = imp * C_IMP + (1.0 - imp) * C_PERV
    coef[building] = 0.0
    ny, nx = building.shape
    n_open = int((~building).sum())
    if building.any() and n_open > 0:
        # distance_transform_edt gives, for each nonzero (building) cell, the index of the nearest zero (open) cell
        idx = ndimage.distance_transform_edt(building, return_distances=False, return_indices=True)
        tj, ti = idx[0][building], idx[1][building]
        target_flat = (tj * nx + ti).astype(np.int64)
    else:
        target_flat = np.zeros(0, dtype=np.int64)
    cached = (coef, building, target_flat, ny * nx)
    try:
        setattr(terrain, "_runoff_cache", cached)
    except Exception:  # frozen or slotted object; fall back to module cache
        _CACHE[key] = cached
    return cached


def runoff_fn(intensity_mm_h, dt_s: float, terrain: Terrain) -> np.ndarray:
    """Runoff depth (m) generated on each cell during dt_s. Zeros on building cells (roof runoff moved to
    nearest open cell). Total returned volume = sum(runoff) * cell_area.

    `intensity_mm_h` is EITHER a scalar (uniform rainfall -- every provider that predates spatial support)
    OR a [ny, nx] array of per-cell mm/h (spatial rainfall, e.g. a radar/gridded field already resampled to
    this terrain's grid by the provider).

    The scalar branch below is deliberately left byte-identical to the pre-spatial implementation: this file
    is part of the frozen scientific core, and existing runs must stay bit-for-bit reproducible. The spatial
    branch reduces to exactly the same mathematics, but computes the roof redirection with per-cell weights,
    which is a different floating-point summation order -- hence the explicit branch rather than one unified
    code path."""
    coef, building, target_flat, n = _prepare(terrain)

    arr = np.asarray(intensity_mm_h, dtype=np.float64)
    if arr.ndim == 0:
        # ---- uniform rainfall: ORIGINAL CODE PATH, unchanged ----
        d = float(intensity_mm_h) / 1000.0 / 3600.0 * float(dt_s)
        if d <= 0.0:
            return np.zeros(building.shape, dtype=np.float64)
        runoff = d * coef                                   # already zero on buildings
        if target_flat.size:
            roof = np.bincount(target_flat, minlength=n).astype(np.float64) * (d * C_IMP)
            runoff = runoff + roof.reshape(building.shape)  # roofs: every building cell contributes d*C_IMP
        return runoff

    # ---- spatial rainfall: per-cell intensity ----
    if arr.shape != building.shape:
        raise ValueError(f"spatial rainfall field {arr.shape} does not match terrain grid {building.shape}")
    if not np.any(arr > 0.0):
        return np.zeros(building.shape, dtype=np.float64)
    d = arr / 1000.0 / 3600.0 * float(dt_s)                 # [ny,nx] gross rain depth (m) this step
    runoff = d * coef                                       # already zero on buildings
    if target_flat.size:
        # each building cell contributes its OWN d * C_IMP to its nearest open cell
        roof = np.bincount(target_flat, weights=d[building] * C_IMP, minlength=n)
        runoff = runoff + roof.reshape(building.shape)
    return runoff
