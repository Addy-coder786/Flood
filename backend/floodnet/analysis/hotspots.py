"""Flood hotspot intelligence: operational summaries DERIVED from a finished simulation -- no new physics.

Everything here is a deterministic reduction of `SimulationResult.frames` (street depth per segment, the depth
grid) and the road graph. "Flooded" means >= FLOOD_THRESHOLD_CM, the lower edge of FloodNet's first non-clear
severity band, so these numbers agree with the map legend. Nothing here is a validation or accuracy statement.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import numpy as np

from .. import config

FLOOD_THRESHOLD_CM = float(config.SEVERITY_BANDS_CM[0][0])          # 5 cm: below this the legend says "clear"
INTERSECTION_MIN_DEGREE = 3


def _severity(depth_cm: float) -> str:
    for limit, label in config.SEVERITY_BANDS_CM:
        if depth_cm < limit:
            return label
    return "critical"


def flood_intelligence(res, roads, terrain=None, top_n: int = 5) -> dict:
    frames = res.frames
    t_min = [float(f.t_s) / 60.0 for f in frames]
    if not frames:
        return {"threshold_cm": FLOOD_THRESHOLD_CM, "hotspots": [], "frames": 0}

    # ---- per segment: peak, peak time, onset, flooded duration ------------------------------------------------
    seg_ids = sorted({sid for f in frames for sid in f.street_depth_m})
    dt_min = (t_min[1] - t_min[0]) if len(t_min) > 1 else 0.0
    per_seg = {}
    for sid in seg_ids:
        series = np.array([float(f.street_depth_m.get(sid, 0.0)) * 100.0 for f in frames])
        k = int(np.argmax(series))
        wet = np.flatnonzero(series >= FLOOD_THRESHOLD_CM)
        per_seg[sid] = {"peak_cm": float(series[k]), "peak_t_min": t_min[k],
                        "onset_t_min": t_min[int(wet[0])] if wet.size else None,
                        "flooded_min": float(wet.size * dt_min)}
    affected = {sid: s for sid, s in per_seg.items() if s["peak_cm"] >= FLOOD_THRESHOLD_CM}

    # ---- roads: length + intersections ------------------------------------------------------------------------
    seg_by_id = {s.seg_id: s for s in (roads.segments if roads is not None else [])}
    degree: dict[int, int] = defaultdict(int)
    for s in seg_by_id.values():
        degree[s.u] += 1
        degree[s.v] += 1
    length_m = float(sum(seg_by_id[sid].length_m for sid in affected if sid in seg_by_id))
    nodes = {n for sid in affected if sid in seg_by_id for n in (seg_by_id[sid].u, seg_by_id[sid].v)}
    intersections = sorted(n for n in nodes if degree[n] >= INTERSECTION_MIN_DEGREE)

    # ---- surface: flooded area over time (open ground only -- building cells hold no street water) -------------
    open_cells = ~terrain.building if terrain is not None and getattr(terrain, "building", None) is not None else None
    cell_area = float(res.grid.res) ** 2
    area_series = []
    for f in frames:
        wet = np.nan_to_num(np.asarray(f.depth), nan=0.0) * 100.0 >= FLOOD_THRESHOLD_CM
        if open_cells is not None and open_cells.shape == wet.shape:
            wet &= open_cells
        area_series.append(float(wet.sum()) * cell_area)
    k_area = int(np.argmax(area_series))

    # ---- headline numbers -------------------------------------------------------------------------------------
    max_sid = max(affected, key=lambda s: (affected[s]["peak_cm"], s), default=None)
    onsets = [s["onset_t_min"] for s in affected.values() if s["onset_t_min"] is not None]

    # ---- ranking: deepest first, then earliest onset, then longest flooded, then id -- fully deterministic ------
    def rank_key(sid):
        s = affected[sid]
        return (-round(s["peak_cm"], 3), s["onset_t_min"] if s["onset_t_min"] is not None else 1e9, -s["flooded_min"], sid)

    best_per_street: dict[str, str] = {}
    for sid in sorted(affected, key=rank_key):
        seg = seg_by_id.get(sid)
        key = (seg.name or "").strip() if seg is not None and (seg.name or "").strip() else f"#{sid}"
        best_per_street.setdefault(key, sid)                 # one entry per named street: its worst segment
    hotspots = []
    for rank, (name, sid) in enumerate(list(best_per_street.items())[:top_n], start=1):
        s, seg = affected[sid], seg_by_id.get(sid)
        mid = seg.lonlat[len(seg.lonlat) // 2] if seg is not None and len(seg.lonlat) else None
        hotspots.append({"rank": rank, "seg_id": sid, "name": None if name.startswith("#") else name,
                         "peak_depth_cm": round(s["peak_cm"], 1), "peak_t_min": s["peak_t_min"],
                         "onset_t_min": s["onset_t_min"], "flooded_min": s["flooded_min"],
                         "severity": _severity(s["peak_cm"]),
                         "lonlat": [float(mid[0]), float(mid[1])] if mid is not None else None})

    return {
        "threshold_cm": FLOOD_THRESHOLD_CM, "frames": len(frames), "horizon_min": t_min[-1],
        "max_depth_cm": round(affected[max_sid]["peak_cm"], 1) if max_sid else 0.0,
        "max_depth_seg_id": max_sid,
        "peak_t_min": affected[max_sid]["peak_t_min"] if max_sid else None,
        "earliest_onset_min": min(onsets) if onsets else None,
        "flooded_area_m2": round(area_series[k_area], 1), "flooded_area_t_min": t_min[k_area] if area_series[k_area] > 0 else None,
        "affected_road_length_m": round(length_m, 1), "affected_segments": len(affected),
        "affected_intersections": len(intersections),
        "hotspots": hotspots,
        "basis": "model output for this run; not validated against observed flood depths",
    }


def segment_timing(res, seg_id: str) -> Optional[dict]:
    """Peak, peak time and onset for ONE street segment (the street inspector), from the same run."""
    frames = res.frames
    if not frames or not any(seg_id in f.street_depth_m for f in frames):
        return None
    series = np.array([float(f.street_depth_m.get(seg_id, 0.0)) * 100.0 for f in frames])
    t_min = [float(f.t_s) / 60.0 for f in frames]
    k = int(np.argmax(series))
    wet = np.flatnonzero(series >= FLOOD_THRESHOLD_CM)
    return {"peak_depth_cm": round(float(series[k]), 1), "peak_t_min": t_min[k],
            "onset_t_min": t_min[int(wet[0])] if wet.size else None, "threshold_cm": FLOOD_THRESHOLD_CM}
