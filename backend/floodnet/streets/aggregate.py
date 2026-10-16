"""Street aggregation: grid depth -> per-road-segment representative depth (max over cells within a buffer).

`make_street_fn` precomputes, once, which grid cells fall inside a `buffer_m` corridor around each segment
polyline; the returned `street_fn(depth_grid, grid)` is then a cheap gather + max per segment and is called
by the simulation engine at every output frame.
"""
from __future__ import annotations

import numpy as np

from ..contracts import Grid, RoadGraph
from ..config import SEVERITY_BANDS_CM, VEHICLE_LIMIT_CM


def _cells_per_segment(roads: RoadGraph, grid: Grid, buffer_m: float) -> dict[str, np.ndarray]:
    import shapely
    from shapely.geometry import LineString
    res = grid.res
    out: dict[str, np.ndarray] = {}
    for seg in roads.segments:
        xy = np.asarray(seg.xy, dtype=float)
        if len(xy) < 2:
            out[seg.seg_id] = np.zeros(0, dtype=np.int64); continue
        poly = LineString(xy).buffer(buffer_m)
        minx, miny, maxx, maxy = poly.bounds
        i0 = max(int(np.floor((minx - grid.x0) / res)), 0); i1 = min(int(np.floor((maxx - grid.x0) / res)), grid.nx - 1)
        j0 = max(int(np.floor((miny - grid.y0) / res)), 0); j1 = min(int(np.floor((maxy - grid.y0) / res)), grid.ny - 1)
        if i1 < i0 or j1 < j0:
            out[seg.seg_id] = np.zeros(0, dtype=np.int64); continue
        ii, jj = np.meshgrid(np.arange(i0, i1 + 1), np.arange(j0, j1 + 1))
        cx = grid.x0 + (ii + 0.5) * res; cy = grid.y0 + (jj + 0.5) * res
        hit = shapely.contains_xy(poly, cx.ravel(), cy.ravel())
        out[seg.seg_id] = (jj.ravel()[hit] * grid.nx + ii.ravel()[hit]).astype(np.int64)
    return out


def make_street_fn(roads: RoadGraph, grid: Grid, buffer_m: float = 6.0):
    """Returns street_fn(depth_grid, grid) -> {seg_id: max depth (m) over cells within buffer_m}; no cells -> 0."""
    cells = _cells_per_segment(roads, grid, buffer_m)
    order = [s.seg_id for s in roads.segments]

    def street_fn(depth_grid: np.ndarray, g: Grid | None = None) -> dict[str, float]:
        flat = np.asarray(depth_grid).ravel()
        result: dict[str, float] = {}
        for sid in order:
            idx = cells[sid]
            result[sid] = float(flat[idx].max()) if idx.size else 0.0
        return result

    street_fn.cells = cells   # exposed for tests / diagnostics
    return street_fn


def severity(depth_cm: float) -> str:
    for limit, label in SEVERITY_BANDS_CM:
        if depth_cm < limit:
            return label
    return "critical"


def passable(depth_cm: float, vehicle: str = "car") -> bool:
    if vehicle not in VEHICLE_LIMIT_CM:
        raise KeyError(f"unknown vehicle '{vehicle}'; known: {sorted(VEHICLE_LIMIT_CM)}")
    return float(depth_cm) < VEHICLE_LIMIT_CM[vehicle]


def streets_geojson(roads: RoadGraph, street_depth_m: dict[str, float]) -> dict:
    feats = []
    for seg in roads.segments:
        d_cm = float(street_depth_m.get(seg.seg_id, 0.0)) * 100.0
        feats.append({"type": "Feature",
                      "geometry": {"type": "LineString", "coordinates": np.asarray(seg.lonlat, dtype=float).round(7).tolist()},
                      "properties": {"seg_id": seg.seg_id, "name": seg.name, "highway": seg.highway,
                                     "depth_cm": round(d_cm, 1), "severity": severity(d_cm),
                                     "passable_car": passable(d_cm, "car"),
                                     "passable_ambulance": passable(d_cm, "ambulance")}})
    return {"type": "FeatureCollection", "features": feats,
            "provenance": {"roads": roads.provenance.to_dict(), "depths": "derived from simulation frame"}}
