"""Self-contained simulation result ("run bundle") for serverless hosting.

POST /api/simulate (and /api/compare, /api/storm/replay) return EVERYTHING the dashboard needs to render a run: the
timeline, every frame's flood-depth image data, street depths, drainage node/edge state, the aggregate series, the
hotspot summary, the CAP draft and the per-segment context the street inspector uses. The browser rebuilds each
frame locally (frontend-react/src/lib/runBundle.js) in exactly the shape GET /api/simulation/{run_id}/frame/{t}
returns, so no follow-up request depends on which server instance computed the run (on Vercel, any request can
land on any instance, and instance memory is not shared).

Encoding: large per-frame arrays are sent as base64(zlib(little-endian typed array)), row-major [frame, item].
Node and edge arrays are the engine's own float32 values, unchanged; street depths are sent as float32. The depth
IMAGE is sent as its 8-bit colour level (api/png.py::depth_png_base64 draws from the same fraction), which reproduces
the PNG to within 1/255 per colour channel. Every other frame, series and inspector value matches the per-run
endpoints exactly (checked against them for a real run).
"""
from __future__ import annotations

import base64
import threading
import zlib
from typing import Optional

import numpy as np

from .. import config
from ..analysis.hotspots import FLOOD_THRESHOLD_CM
from ..contracts import SimulationResult

BUNDLE_VERSION = 1
MIN_VISIBLE_M = 0.01                      # same as png.depth_png_base64
CAUSE_CODES = ["", "overcapacity", "downstream", "blockage"]
# zlib level for every packed array. Lossless at any level (the decoded bytes are identical). Measured 2026-10-11 on the
# real pilot, heavy scenario, 180 min: level 9 spent ~374 ms compressing the depth-level array alone (~0.47 s of a
# ~0.67 s warm POST /api/simulate) for a 227 KB result; level 6 took ~36 ms for 258 KB (+~40 KB of base64 on a
# ~1.5 MB response). docs/PERFORMANCE_OPTIMIZATION.md, "2026-10-11 Run Forecast profiling".
ZLIB_LEVEL = 6


def pack(arr: np.ndarray, dtype: str) -> dict:
    a = np.ascontiguousarray(arr, dtype=np.dtype(dtype).newbyteorder("<"))
    return {"dtype": dtype, "shape": list(a.shape),
            "data": base64.b64encode(zlib.compress(a.tobytes(), ZLIB_LEVEL)).decode("ascii")}


def unpack(p: dict) -> np.ndarray:
    raw = zlib.decompress(base64.b64decode(p["data"]))
    return np.frombuffer(raw, dtype=np.dtype(p["dtype"]).newbyteorder("<")).reshape(p["shape"])


def depth_levels(depth_m: np.ndarray, run_max_m: float) -> np.ndarray:
    """0 = not drawn (< MIN_VISIBLE_M); 1..255 = depth as a fraction of the run-wide maximum, 1/254 steps."""
    d = np.nan_to_num(np.asarray(depth_m, dtype=np.float64), nan=0.0)
    if run_max_m <= 0:
        return np.zeros(d.shape, dtype=np.uint8)
    frac = np.clip(d / run_max_m, 0.0, 1.0)
    return np.where(d >= MIN_VISIBLE_M, np.round(frac * 254.0) + 1.0, 0.0).astype(np.uint8)


def _frame_delta(levels: np.ndarray) -> np.ndarray:
    """uint8 modular differences between consecutive frames (exact; decoded by a running sum mod 256)."""
    out = levels.copy()
    out[1:] = (levels[1:].astype(np.int16) - levels[:-1].astype(np.int16)).astype(np.uint8)
    return out


_segment_context_cache: list = []        # [(net, terrain, roads, seg_ids tuple, context)] -- at most one entry
_segment_context_lock = threading.Lock()


def clear_caches() -> None:
    """Drop the per-pilot segment context (called by api/state.py whenever the pilot is replaced)."""
    with _segment_context_lock:
        _segment_context_cache.clear()


def _segment_context(pilot: dict, seg_ids: list[str]) -> dict:
    """Per-pilot cached _compute_segment_context. Its inputs (pilot network, terrain, roads, segment list) are static
    for the life of the loaded pilot, yet it was recomputed on every POST /api/simulate. The cache entry keeps
    strong references to the exact objects it was computed from and is used only when the same objects (`is`) and
    the same segment list are passed again, so a swapped pilot can never be served another pilot's context."""
    net, terrain, roads, key = pilot["net"], pilot["terrain"], pilot["roads"], tuple(seg_ids)
    with _segment_context_lock:
        for c_net, c_terrain, c_roads, c_key, ctx in _segment_context_cache:
            if c_net is net and c_terrain is terrain and c_roads is roads and c_key == key:
                return ctx
    ctx = _compute_segment_context(pilot, seg_ids)
    with _segment_context_lock:
        _segment_context_cache[:] = [(net, terrain, roads, key, ctx)]
    return ctx


def _compute_segment_context(pilot: dict, seg_ids: list[str]) -> dict:
    """Static per-segment context for the street inspector: nearest drainage node and ground elevation under the
    segment midpoint -- the same values GET /api/simulation/{run_id}/explain/{seg_id} computes."""
    from scipy.spatial import cKDTree
    from .main import _segment_midpoint_xy          # shared geometry helper
    net, terrain, roads = pilot["net"], pilot["terrain"], pilot["roads"]
    by_id = {s.seg_id: s for s in roads.segments}
    mids = np.array([_segment_midpoint_xy(by_id[s]) for s in seg_ids], dtype=float).reshape(-1, 2)
    tree = cKDTree(np.column_stack([net.node_x, net.node_y]))
    dist, idx = tree.query(mids)
    grid = terrain.grid
    j, i = grid.cell_of(mids[:, 0], mids[:, 1])
    inside = grid.inside(j, i)
    ground = [float(terrain.z[jj, ii]) if ok else None for jj, ii, ok in zip(j, i, inside)]
    return {"node_index": [int(k) for k in idx], "node_distance_m": [round(float(d), 3) for d in dist],
            "ground_elevation_m": ground,
            "terrain_note_inside": "ground elevation of the terrain grid cell under the segment's midpoint",
            "terrain_note_outside": "segment midpoint falls outside the terrain grid; no elevation available"}


def run_bundle(res: SimulationResult, pilot: dict) -> dict:
    from . import state
    from .main import _node_ground_floor, _frame_max_cm
    net, roads = pilot["net"], pilot.get("roads")
    frames = res.frames
    seg_ids = [s.seg_id for s in roads.segments] if roads is not None else sorted(
        {sid for f in frames for sid in f.street_depth_m})
    run_max_m = max((float(np.nanmax(f.depth)) for f in frames), default=0.0)
    lon, lat = state.xy_to_lonlat(net.node_x, net.node_y)
    cause_index = {c: k for k, c in enumerate(CAUSE_CODES)}
    causes = np.array([[cause_index.get(str(c), 0) for c in f.node_cause] for f in frames], dtype=np.uint8)
    unknown = sorted({str(c) for f in frames for c in f.node_cause} - set(CAUSE_CODES))
    if unknown:                                      # never silently map a new engine cause to ""
        raise ValueError(f"run bundle: unknown node causes {unknown}")
    return {
        "version": BUNDLE_VERSION,
        "encoding": "base64(zlib(little-endian typed array)), row-major [frame, item]",
        "t_min": [f.t_s / 60.0 for f in frames],
        "rain_mm_h": [float(f.rain_mm_h) for f in frames],
        "max_depth_cm": [_frame_max_cm(f) for f in frames],
        "depth": {"grid": res.grid.to_dict(), "bbox_lonlat": state.grid_bbox_lonlat(res.grid),
                  "scale_max_cm": run_max_m * 100.0, "min_visible_m": MIN_VISIBLE_M,
                  "row0": "south (flip vertically for an image)",
                  "levels_delta": pack(_frame_delta(np.stack([depth_levels(f.depth, run_max_m) for f in frames])), "u1")},
        "nodes": {"ids": [str(x) for x in net.node_id],
                  "lon": [round(float(x), 7) for x in lon], "lat": [round(float(x), 7) for x in lat],
                  "invert_m": [float(x) for x in np.asarray(net.node_invert, dtype=np.float64)],
                  "ground_floor_m": [float(x) for x in _node_ground_floor(net)],
                  "hgl_m": pack(np.stack([f.node_hgl for f in frames]), "f4"),
                  "surcharge_m3": pack(np.stack([f.node_surcharge_m3 for f in frames]), "f4"),
                  "surcharging_raw": pack(np.stack([f.node_surcharging for f in frames]).astype(np.uint8), "u1"),
                  "cause": pack(causes, "u1"), "cause_codes": CAUSE_CODES},
        "edges": {"ids": [str(x) for x in net.edge_id],
                  "us": [int(x) for x in net.edge_us], "ds": [int(x) for x in net.edge_ds],
                  "capacity_m3s": [float(x) for x in net.edge_capacity_m3s],
                  "blockage": [float(x) for x in net.edge_blockage],
                  "util": pack(np.stack([f.edge_util for f in frames]), "f4"),
                  "flow_m3s": pack(np.stack([f.edge_flow_m3s for f in frames]), "f4")},
        "streets": {"seg_ids": seg_ids,
                    "depth_m": pack(np.array([[f.street_depth_m.get(s, 0.0) for s in seg_ids] for f in frames]), "f4")},
        "segments": _segment_context(pilot, seg_ids) if roads is not None else None,
        "thresholds": {"severity_bands_cm": [[float(lim), label] for lim, label in config.SEVERITY_BANDS_CM],
                       "vehicle_limit_cm": dict(config.VEHICLE_LIMIT_CM),
                       "flood_threshold_cm": float(FLOOD_THRESHOLD_CM)},
    }


def explain_provenance(pilot: dict) -> dict:
    from . import state
    return {"roads": pilot["roads"].provenance.to_dict(), "network": state.pilot_provenance(pilot)["network"],
            "terrain": pilot["terrain"].provenance.to_dict()}


def self_contained(res: SimulationResult, summary: dict, series: dict, hotspots: dict,
                   alert: Optional[dict], alert_error: Optional[str], pilot: dict) -> dict:
    """The complete POST /api/simulate response: the existing summary fields (unchanged, so every existing
    consumer keeps working) plus the bundle, the aggregate series, hotspots and the CAP draft."""
    out = dict(summary)
    series = {k: v for k, v in series.items() if k != "streets"}   # street series are rebuilt from the bundle
    out.update({"self_contained": True, "bundle": run_bundle(res, pilot), "series": series,
                "hotspots": hotspots, "alert": alert, "alert_error": alert_error,
                "explain_provenance": explain_provenance(pilot)})
    return out
