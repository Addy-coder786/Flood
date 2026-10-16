"""DEM depression analysis and reliability flagging for the Mumbai pilot.

WHAT THIS DOES **NOT** DO: it does not modify the DEM, does not fill depressions, and does not cap flood
depth. The contour-derived elevations are official MCGM data and we do not overwrite them.

What it does instead
--------------------
1. `depressions()` labels closed depressions (cells below their spill elevation) and reports, per depression,
   whether it touches the grid edge, its volume, and its depth below spill.
2. `dem_reliability_mask()` flags cells the model cannot defensibly report a *street* water depth for:
   cells lying in an interior (non-edge-touching) closed depression whose bottom sits more than
   `tolerance_m` below the lowest surveyed manhole ground level within `radius_m`.

   Rationale (see docs/validation/EXTREME_DEPTH.md): across the pilot the DTM agrees with the 1,205
   independently surveyed MCGM manhole ground levels to mean +0.012 m / SD 0.283 m, and every one of the
   1,233 manholes reads >= 27.22 mTHD. A handful of contour-derived cells sit 8-11 m lower. Those low
   elevations ARE present in the source contour layer (they are not interpolation overshoot), but they
   describe something other than the street surface the manholes sit on -- plausibly a sub-surface feature
   (subway ramp, nallah bed, excavation) or a datum/attribute inconsistency in the contour layer. We could
   not resolve which from the available data, so we FLAG rather than alter.

   A flagged cell keeps its simulated water depth; it is simply excluded from headline street-depth
   statistics and marked in the API/UI as DEM-uncertain.

3. `outward_open_boundary()` builds the open-boundary mask used by the surface model. This is a *model*
   correction, not a data correction: the pilot is a clipped 2.4 x 2.6 km window out of Mumbai, so terrain
   that slopes out of the window must be allowed to carry water out of it. With a closed boundary the model
   accumulates water against the clip line, which is an artefact of the window, not of the city.
"""
from __future__ import annotations

import numpy as np
from scipy import ndimage
from scipy.spatial import cKDTree

from ..contracts import Grid, Terrain, DrainageNetwork
from ..provenance import Provenance, Tag


def _spill_fill(z: np.ndarray) -> np.ndarray:
    """Priority-flood fill: returns the water-surface elevation of each cell if every depression were
    filled to its spill point. filled - z is the depth below spill (0 where the cell is not in a pit)."""
    import heapq
    ny, nx = z.shape
    filled = np.full(z.shape, np.inf)
    seen = np.zeros(z.shape, dtype=bool)
    heap = []
    for j in range(ny):
        for i in (0, nx - 1):
            heapq.heappush(heap, (float(z[j, i]), j, i)); seen[j, i] = True; filled[j, i] = z[j, i]
    for i in range(nx):
        for j in (0, ny - 1):
            if not seen[j, i]:
                heapq.heappush(heap, (float(z[j, i]), j, i)); seen[j, i] = True; filled[j, i] = z[j, i]
    while heap:
        lvl, j, i = heapq.heappop(heap)
        for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            a, b = j + dj, i + di
            if 0 <= a < ny and 0 <= b < nx and not seen[a, b]:
                seen[a, b] = True
                filled[a, b] = max(float(z[a, b]), lvl)
                heapq.heappush(heap, (filled[a, b], a, b))
    return filled


def depressions(terrain: Terrain, min_depth_m: float = 0.25) -> dict:
    """Label closed depressions deeper than `min_depth_m` below their spill elevation."""
    z = np.asarray(terrain.z, dtype=np.float64)
    g = terrain.grid
    filled = _spill_fill(z)
    below = filled - z
    pit = below > min_depth_m
    lab, n = ndimage.label(pit)
    out = []
    for k in range(1, n + 1):
        m = lab == k
        J, I = np.nonzero(m)
        touches = bool(J.min() == 0 or J.max() == g.ny - 1 or I.min() == 0 or I.max() == g.nx - 1)
        out.append({
            "label": int(k), "n_cells": int(m.sum()), "area_m2": float(m.sum() * g.cell_area),
            "volume_m3": float(below[m].sum() * g.cell_area),
            "max_depth_below_spill_m": float(below[m].max()),
            "bottom_z_m": float(z[m].min()), "spill_elev_m": float((z + below)[m].max()),
            "touches_grid_edge": touches,
            "bbox_j": [int(J.min()), int(J.max())], "bbox_i": [int(I.min()), int(I.max())],
        })
    out.sort(key=lambda d: -d["volume_m3"])
    return {"labels": lab, "below_spill_m": below, "depressions": out, "n": n}


def dem_reliability_mask(terrain: Terrain, net: DrainageNetwork, tolerance_m: float = 3.0,
                         radius_m: float = 250.0, min_depth_m: float = 0.25) -> tuple[np.ndarray, dict]:
    """Flag cells in interior closed depressions that sit far below the nearest surveyed manhole ground
    levels. Returns (mask[ny,nx] bool, report dict). The DEM itself is untouched."""
    z = np.asarray(terrain.z, dtype=np.float64)
    g = terrain.grid
    d = depressions(terrain, min_depth_m=min_depth_m)
    lab = d["labels"]

    j, i = net.node_cell_j, net.node_cell_i
    ok = g.inside(j, i)
    mx = g.x0 + (i[ok] + 0.5) * g.res
    my = g.y0 + (j[ok] + 0.5) * g.res
    gl = np.asarray(net.node_ground, dtype=np.float64)[ok]
    tree = cKDTree(np.c_[mx, my])

    mask = np.zeros(z.shape, dtype=bool)
    flagged = []
    for dep in d["depressions"]:
        if dep["touches_grid_edge"]:
            continue  # handled by the open boundary, not a data-reliability question
        k = dep["label"]
        m = lab == k
        J, I = np.nonzero(m)
        X = g.x0 + (I + 0.5) * g.res
        Y = g.y0 + (J + 0.5) * g.res
        # lowest surveyed manhole ground level near this depression
        idx = tree.query_ball_point(np.c_[X.mean(), Y.mean()], r=radius_m)[0]
        if not idx:
            continue
        local_min_gl = float(gl[idx].min())
        deficit = local_min_gl - dep["bottom_z_m"]
        if deficit > tolerance_m:
            mask |= m
            flagged.append({**{kk: vv for kk, vv in dep.items() if kk != "label"},
                            "label": k, "local_min_manhole_ground_m": local_min_gl,
                            "bottom_below_local_manholes_m": round(deficit, 2),
                            "n_manholes_within_radius": len(idx)})
    report = {
        "tolerance_m": tolerance_m, "radius_m": radius_m, "min_depth_m": min_depth_m,
        "n_depressions_total": d["n"],
        "n_depressions_flagged": len(flagged),
        "n_cells_flagged": int(mask.sum()),
        "pct_of_grid_flagged": round(100.0 * mask.sum() / mask.size, 3),
        "flagged": flagged,
        "provenance": Provenance(
            Tag.ESTIMATED, "floodnet.terrain.pits.dem_reliability_mask",
            f"cells in interior closed depressions whose bottom lies >{tolerance_m} m below the lowest "
            f"surveyed MCGM manhole ground level within {radius_m} m; DEM NOT modified").to_dict(),
    }
    return mask, report


def outward_open_boundary(terrain: Terrain) -> np.ndarray:
    """Edge cells whose terrain slopes out of the domain -> water may leave there.

    The pilot is a clip out of Mumbai; a closed boundary would pond water against the clip line.
    An edge cell is opened when it is not a building and its elevation is <= the neighbouring cell one
    step inside the domain (i.e. the ground continues downhill out of the window)."""
    z = np.asarray(terrain.z, dtype=np.float64)
    b = np.asarray(terrain.building, dtype=bool)
    m = np.zeros(z.shape, dtype=bool)
    m[0, :] |= z[0, :] <= z[1, :]
    m[-1, :] |= z[-1, :] <= z[-2, :]
    m[:, 0] |= z[:, 0] <= z[:, 1]
    m[:, -1] |= z[:, -1] <= z[:, -2]
    return m & ~b
