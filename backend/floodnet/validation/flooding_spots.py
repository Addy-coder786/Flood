"""Spatial-pattern check of modelled flooding against MCGM "Flooding Spots" (layer 344) and Chitale-2005 named roads.

MEASUREMENT ONLY. Nothing in here changes the model; it runs the same wiring as the API (floodnet.api.state) and
reports what the model produces in the footprint of each MCGM spot.

Classes (working thresholds, same bands as config.SEVERITY_BANDS_CM; NOT cited guidance):
    DETECTED  max modelled depth in footprint >= 15 cm
    MARGINAL  5 cm <= max depth < 15 cm
    MISSED    max depth < 5 cm

Caveats that the report must carry (see research/mumbai/FLOOD_GROUND_TRUTH.md):
  * The MCGM layer is a chronic/undated inventory (~2017), not a single-event flood map. Names containing
    "(Delete)" / "(Tackled)" are flagged inactive by the data pipeline; only *active* spots are scored.
  * DEPTH / STRETCH are MCGM free-text attributes with UNKNOWN units (DEPTH 1.5-3.0 is unlikely to be metres of
    standing water on a road; it may be feet, a severity rank, or something else). They are reported verbatim.
  * The Chitale (2006) report names roads/areas, not coordinates; that match is qualitative only.
  * If the model floods most of the pilot at the scoring depth, agreement with the spots is uninformative — the
    base rate is therefore reported alongside every score.
  * A raw DETECTED count is NOT evidence of spatial skill: the footprints range from 7 to 2,468 cells, and the
    "max depth anywhere in the footprint" rule makes a large footprint near-certain to score DETECTED at any
    non-trivial base rate. `permutation_test()` below is the control for that; read it before quoting counts.

BOUNDARY CONDITION (fixed 2026-09-10). `run_pilot_scenario` previously constructed `StorageCellSurface(terrain)`
— i.e. the CLOSED boundary — while the shipped API (`floodnet.api.state:203`) uses `open_boundary=True`, despite
the docstring above claiming identical wiring. The closed boundary ponds water against the clip line and biases
this check toward over-detection. The default is now `open_boundary=True`, matching the API. Pass
`--closed-boundary` (or `open_boundary=False`) to reproduce the pre-fix runs.

CLI:
    python -m floodnet.validation.flooding_spots --scenarios heavy july2005 --horizon-min 180
    python -m floodnet.validation.flooding_spots --scenarios moderate heavy july2005 --permutations 2000
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
from pyproj import Transformer

from .. import config
from ..contracts import Grid, SimulationResult, DrainageNetwork, RoadGraph

log = logging.getLogger("floodnet.validation")

DETECT_CM = 15.0
MARGINAL_CM = 5.0
ONSET_THRESHOLDS_CM = (5.0, 15.0, 30.0)
DEFAULT_RADIUS_M = 50.0

# Spatial permutation test (see permutation_test). Fixed so the published p-values are reproducible.
PERM_N = 2000
PERM_SEED = 20260910

# Named locations in the Chitale Committee report (2006) that fall in / next to the pilot bbox.
# Substrings are matched case-insensitively against OSM road-segment names. Precision: named location only.
CHITALE_TERMS = ("hindmata", "ambedkar", "lakhamsi", "napoo", "king", "tilak", "senapati bapat",
                 "parel", "matunga", "dadar")

_to_utm = Transformer.from_crs(config.CRS_GEO, config.CRS_COMPUTE, always_xy=True)


# ----------------------------------------------------------------------------- geometry helpers
def _lonlat_to_xy(lonlat) -> np.ndarray:
    a = np.asarray(lonlat, dtype=float).reshape(-1, 2)
    x, y = _to_utm.transform(a[:, 0], a[:, 1])
    return np.column_stack([x, y])


def _cell_centres(grid: Grid) -> tuple[np.ndarray, np.ndarray]:
    xs = grid.x0 + (np.arange(grid.nx) + 0.5) * grid.res
    ys = grid.y0 + (np.arange(grid.ny) + 0.5) * grid.res
    return np.meshgrid(xs, ys)          # [ny, nx] each


def _points_in_polygon(px: np.ndarray, py: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Even-odd ray casting; poly [K,2]. Vectorised over points."""
    inside = np.zeros(px.shape, dtype=bool)
    k = len(poly)
    for a in range(k):
        x1, y1 = poly[a]; x2, y2 = poly[(a + 1) % k]
        cond = (y1 > py) != (y2 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            xint = x1 + (py - y1) * (x2 - x1) / (y2 - y1)
        inside ^= cond & (px < xint)
    return inside


def footprint_mask(grid: Grid, spot: dict, radius_m: float = DEFAULT_RADIUS_M) -> tuple[np.ndarray, str]:
    """[ny,nx] bool of cells whose centres are in the spot footprint. Returns (mask, kind)."""
    cx, cy = _cell_centres(grid)
    poly = spot.get("polygon_lonlat") or spot.get("polygon")
    if poly is not None and len(poly) >= 3:
        p = _lonlat_to_xy(poly) if "polygon_lonlat" in spot else np.asarray(poly, dtype=float)
        m = _points_in_polygon(cx, cy, p)
        if m.any():
            return m, "polygon"
    c = spot.get("lonlat_centroid") or spot.get("centroid_lonlat")
    if c is None:
        raise ValueError(f"spot {spot.get('name')!r} has neither polygon nor centroid")
    x, y = _lonlat_to_xy(c)[0]
    m = np.hypot(cx - x, cy - y) <= radius_m
    if not m.any():                                    # centroid outside grid or radius < half cell
        j, i = grid.cell_of(np.array([x]), np.array([y]))
        if grid.inside(j, i)[0]:
            m[j[0], i[0]] = True
    return m, f"radius_{radius_m:g}m"


def _centroid_xy(spot: dict, grid: Grid, mask: np.ndarray) -> tuple[float, float]:
    c = spot.get("lonlat_centroid") or spot.get("centroid_lonlat")
    if c is not None:
        return tuple(map(float, _lonlat_to_xy(c)[0]))
    cx, cy = _cell_centres(grid)
    return float(cx[mask].mean()), float(cy[mask].mean())


def _seg_distance(x: float, y: float, xy: np.ndarray) -> float:
    """Point-to-polyline distance (m)."""
    if len(xy) == 1:
        return float(np.hypot(xy[0, 0] - x, xy[0, 1] - y))
    a = xy[:-1]; b = xy[1:]
    ab = b - a; ap = np.array([x, y]) - a
    den = np.einsum("ij,ij->i", ab, ab)
    t = np.clip(np.where(den > 0, np.einsum("ij,ij->i", ap, ab) / np.where(den > 0, den, 1), 0), 0, 1)
    proj = a + ab * t[:, None]
    return float(np.min(np.hypot(proj[:, 0] - x, proj[:, 1] - y)))


# ----------------------------------------------------------------------------- per-run summaries
def depth_envelope(res: SimulationResult) -> np.ndarray:
    """Per-cell max depth over all frames (m)."""
    env = np.zeros_like(res.frames[0].depth, dtype=np.float32)
    for f in res.frames:
        np.maximum(env, f.depth, out=env)
    return env


def base_rate(res: SimulationResult, terrain, threshold_cm: float = DETECT_CM) -> dict:
    """Fraction of (non-building) pilot cells whose max-over-time depth >= threshold; plus the peak-frame value."""
    env = depth_envelope(res)
    valid = ~terrain.building
    thr = threshold_cm / 100.0
    frac_env = float(np.mean(env[valid] >= thr))
    per_frame = [float(np.mean(f.depth[valid] >= thr)) for f in res.frames]
    k = int(np.argmax(per_frame))
    return {"threshold_cm": threshold_cm, "n_cells": int(valid.sum()),
            "frac_cells_ge_threshold_envelope": frac_env,
            "frac_cells_ge_threshold_peak_frame": per_frame[k], "peak_frame_t_min": res.frames[k].t_s / 60.0,
            "frac_cells_ge_5cm_envelope": float(np.mean(env[valid] >= 0.05)),
            "envelope_percentiles_cm": {str(p): float(np.percentile(env[valid], p) * 100) for p in (50, 75, 90, 95, 99)}}


def _onsets(res: SimulationResult, mask: np.ndarray) -> tuple[dict, float, float]:
    """(onset_min per threshold, max_cm, t_of_max_min) for max-in-footprint depth over frames."""
    series = np.array([float(f.depth[mask].max()) for f in res.frames])
    t_min = np.array([f.t_s / 60.0 for f in res.frames])
    on = {}
    for thr in ONSET_THRESHOLDS_CM:
        hit = np.nonzero(series >= thr / 100.0)[0]
        on[f"{thr:g}cm"] = float(t_min[hit[0]]) if len(hit) else None
    k = int(np.argmax(series))
    return on, float(series[k] * 100), float(t_min[k])


def _nearest_node(res: SimulationResult, net: DrainageNetwork, x: float, y: float) -> dict:
    d = np.hypot(net.node_x - x, net.node_y - y)
    k = int(np.argmin(d))
    first = None; cause = ""; vol = 0.0
    for f in res.frames:
        vol += float(f.node_surcharge_m3[k])
        if first is None and bool(f.node_surcharging[k]):
            first = f.t_s / 60.0; cause = str(f.node_cause[k])
    return {"id": str(net.node_id[k]), "dist_m": float(d[k]), "ground_m": float(net.node_ground[k]),
            "surcharges": first is not None, "first_surcharge_min": first, "cause": cause,
            "surcharge_volume_m3": vol, "is_outfall": bool(net.node_is_outfall[k])}


def _nearest_road(res: SimulationResult, roads: Optional[RoadGraph], x: float, y: float) -> Optional[dict]:
    if roads is None:
        return None
    best = None; bd = np.inf
    for s in roads.segments:
        xy = np.asarray(s.xy, dtype=float)
        # cheap reject on bounding box before exact distance
        if xy[:, 0].min() - 300 > x or xy[:, 0].max() + 300 < x or xy[:, 1].min() - 300 > y or xy[:, 1].max() + 300 < y:
            continue
        dd = _seg_distance(x, y, xy)
        if dd < bd:
            bd, best = dd, s
    if best is None:
        return None
    dmax = max((float(f.street_depth_m.get(best.seg_id, 0.0)) for f in res.frames), default=0.0)
    return {"seg_id": best.seg_id, "name": best.name or "(unnamed)", "highway": best.highway,
            "dist_m": float(bd), "max_depth_cm": dmax * 100}


def classify(max_cm: float) -> str:
    if max_cm >= DETECT_CM:
        return "DETECTED"
    if max_cm >= MARGINAL_CM:
        return "MARGINAL"
    return "MISSED"


def evaluate_spots(pilot: dict, res: SimulationResult, spots: Optional[list[dict]] = None,
                   radius_m: float = DEFAULT_RADIUS_M) -> dict:
    """Score every spot (active and inactive, flagged) against one simulation result."""
    terrain = pilot["terrain"]; grid = terrain.grid; net = pilot["net"]; roads = pilot.get("roads")
    spots = pilot.get("hotspots", []) if spots is None else spots
    env = depth_envelope(res)
    valid = ~terrain.building
    env_valid_cm = np.sort(env[valid]) * 100
    br = base_rate(res, terrain)
    rows = []
    for s in spots:
        mask, kind = footprint_mask(grid, s, radius_m)
        x, y = _centroid_xy(s, grid, mask)
        on, max_cm, t_max = _onsets(res, mask)
        pct = float(np.searchsorted(env_valid_cm, max_cm, side="left") / max(len(env_valid_cm), 1) * 100)
        zc = terrain.z[mask]
        # local relief: footprint min z relative to the median z within 150 m (negative = local low)
        cx, cy = _cell_centres(grid)
        ring = (np.hypot(cx - x, cy - y) <= 150.0) & valid
        relief = float(zc.min() - np.median(terrain.z[ring])) if ring.any() else None
        rows.append({
            "name": s.get("name"), "active": bool(s.get("active", True)), "ward": s.get("ward"),
            "location": s.get("location"), "affect_road": s.get("affect_road"),
            "mcgm_depth_attr": s.get("depth_attr"), "mcgm_stretch_m": s.get("stretch_m"),
            "footprint": kind, "footprint_cells": int(mask.sum()), "footprint_building_cells": int((mask & ~valid).sum()),
            "footprint_z_min_m": float(zc.min()), "footprint_z_max_m": float(zc.max()), "local_relief_m": relief,
            "max_depth_cm": max_cm, "t_of_max_min": t_max, "onset_min": on,
            "cell_depth_percentile": pct, "class": classify(max_cm),
            "nearest_node": _nearest_node(res, net, x, y),
            "nearest_road": _nearest_road(res, roads, x, y),
        })
    act = [r for r in rows if r["active"]]
    counts = {c: sum(1 for r in act if r["class"] == c) for c in ("DETECTED", "MARGINAL", "MISSED")}
    return {"scenario": res.scenario.id, "scenario_name": res.scenario.name, "horizon_min": res.frames[-1].t_s / 60.0,
            "blockage": res.blockage, "mass_balance_error_pct": res.mass_balance.error_pct,
            "thresholds_cm": {"detected": DETECT_CM, "marginal": MARGINAL_CM},
            "base_rate": br, "active_counts": counts, "n_active": len(act), "n_total": len(rows),
            "uninformative": br["frac_cells_ge_threshold_envelope"] >= 0.5,
            "spots": rows}


# ----------------------------------------------------------------------------- spatial permutation test
def _kernel_of(mask: np.ndarray) -> np.ndarray:
    """Crop a full-grid footprint mask to its bounding box. Shape and cell count are preserved exactly."""
    js, iss = np.nonzero(mask)
    return np.ascontiguousarray(mask[js.min():js.max() + 1, iss.min():iss.max() + 1])


def _placement_counts(field: np.ndarray, kernel: np.ndarray) -> np.ndarray:
    """[ny-h+1, nx-w+1] int: number of True `field` cells covered by `kernel` at every top-left placement.

    Cross-correlation via FFT (scipy). Deterministic; rounded back to exact integers.
    """
    from scipy.signal import fftconvolve
    h, w = kernel.shape
    if h > field.shape[0] or w > field.shape[1]:
        return np.zeros((0, 0), dtype=np.int64)
    c = fftconvolve(field.astype(np.float64), kernel[::-1, ::-1].astype(np.float64), mode="valid")
    return np.rint(c).astype(np.int64)


def permutation_test(pilot: dict, res: SimulationResult, spots: Optional[list[dict]] = None,
                     n_perm: int = PERM_N, seed: int = PERM_SEED, radius_m: float = DEFAULT_RADIUS_M,
                     detect_cm: float = DETECT_CM) -> dict:
    """Null: "the modelled flooded footprint overlaps the known chronic-flooding spots no more than
    randomly-placed footprints of identical shape and cell count would."

    Each *active* spot footprint is cropped to its bounding box and translated to a uniformly-random valid
    top-left position in the pilot grid. Shape, orientation and exact cell count are preserved (no rotation, no
    resampling) — essential, because the real footprints span 7 to 2,468 cells and the scoring rule ("max depth
    ANYWHERE in the footprint >= 15 cm") makes detection a near-deterministic function of footprint size.

    Validity of a relocation:
      * the whole translated footprint must lie inside the grid; and
      * it must cover at least as many NON-BUILDING (flowable) cells as the real footprint does.
    The second constraint matters. Buildings are no-flow cells that are dry by construction (~21 % of the grid),
    so an unconstrained relocation can land on a building block, be unable to register any depth, and make the
    null artificially easy to beat — i.e. it would flatter the model. Requiring at least the observed wettable
    area makes every null placement at least as capable of scoring a detection as the real footprint is.
    Depths are read on non-building cells only, for the observed and the null placements alike.

    Relocations are independent across spots (they may overlap each other); no spot is required to avoid the
    real spot locations. Reported statistics, one-sided against the null "random does at least as well":

      detected     number of active spots scoring DETECTED (the headline count).
      sum_depth_cm sum over active spots of the max depth in the footprint (cm) — a continuous statistic with
                   far more resolution than a count over 5 spots, which is nearly powerless on its own.

    p = (1 + #{null >= observed}) / (n_perm + 1)  — the standard unbiased Monte-Carlo p-value; it can never be
    exactly 0. A LARGE p means the model did NOT beat random placement.
    """
    terrain = pilot["terrain"]; grid = terrain.grid
    all_spots = pilot.get("hotspots", []) if spots is None else spots
    active = [s for s in all_spots if bool(s.get("active", True))]
    env = depth_envelope(res)
    flowable = ~np.asarray(terrain.building, dtype=bool)
    env = np.where(flowable, env, 0.0)          # buildings are no-flow; make that explicit for both arms
    thr = detect_cm / 100.0
    rng = np.random.default_rng(seed)

    null_det = np.zeros((n_perm, len(active)), dtype=bool)
    null_dep = np.zeros((n_perm, len(active)), dtype=np.float64)
    obs_det = np.zeros(len(active), dtype=bool)
    obs_dep = np.zeros(len(active), dtype=np.float64)
    rows = []
    for si, s in enumerate(active):
        mask, kind = footprint_mask(grid, s, radius_m)
        kernel = _kernel_of(mask)
        h, w = kernel.shape
        n_cells = int(kernel.sum())
        n_flow_obs = int((mask & flowable).sum())
        obs_dep[si] = float(env[mask].max()) * 100.0
        obs_det[si] = obs_dep[si] >= detect_cm

        flow_cnt = _placement_counts(flowable, kernel)
        constraint = "flowable_cells >= observed"
        ok = flow_cnt >= max(n_flow_obs, 1)
        if not ok.any():                        # nothing can match the observed wettable area -> relax, and say so
            ok = flow_cnt >= 1
            constraint = "RELAXED: flowable_cells >= 1 (no placement matched the observed wettable area)"
        idx = np.flatnonzero(ok.ravel())
        if idx.size == 0:
            raise ValueError(f"spot {s.get('name')!r}: no valid relocation in a {grid.ny}x{grid.nx} grid "
                             f"for a {h}x{w} footprint")
        pick = rng.choice(idx, size=n_perm, replace=True)
        j0 = pick // ok.shape[1]
        i0 = pick % ok.shape[1]
        for p in range(n_perm):
            sub = env[j0[p]:j0[p] + h, i0[p]:i0[p] + w][kernel]
            d = float(sub.max()) * 100.0
            null_dep[p, si] = d
            null_det[p, si] = d >= detect_cm

        # exact per-spot detection probability over ALL valid placements (free, and a check on the sampling)
        wet_cnt = _placement_counts((env >= thr) & flowable, kernel)
        exact_p = float(np.mean((wet_cnt.ravel()[idx] >= 1)))
        rows.append({"name": (s.get("name") or "").strip(), "footprint": kind, "footprint_cells": n_cells,
                     "footprint_bbox": [int(h), int(w)], "flowable_cells": n_flow_obs,
                     "n_valid_placements": int(idx.size), "constraint": constraint,
                     "observed_max_depth_cm": obs_dep[si], "observed_detected": bool(obs_det[si]),
                     "null_detect_prob_exact": exact_p,
                     "null_detect_prob_sampled": float(null_det[:, si].mean())})

    det_obs = int(obs_det.sum()); det_null = null_det.sum(axis=1)
    dep_obs = float(obs_dep.sum()); dep_null = null_dep.sum(axis=1)
    p_det = float((1 + int((det_null >= det_obs).sum())) / (n_perm + 1))
    p_dep = float((1 + int((dep_null >= dep_obs).sum())) / (n_perm + 1))
    return {
        "scenario": res.scenario.id, "n_perm": int(n_perm), "seed": int(seed), "detect_cm": float(detect_cm),
        "n_active_spots": len(active),
        "null_model": "translate each active footprint to a uniformly random valid position; shape and cell "
                      "count preserved exactly; placements independent across spots",
        "validity_rule": "translated footprint fully inside grid AND covering >= as many non-building cells "
                         "as the real footprint",
        "p_value_definition": "(1 + #{null >= observed}) / (n_perm + 1), one-sided; large p = no skill",
        "detected": {"observed": det_obs, "null_mean": float(det_null.mean()), "null_sd": float(det_null.std(ddof=1)),
                     "null_median": float(np.median(det_null)), "p_value": p_det,
                     "null_ge_observed": int((det_null >= det_obs).sum())},
        "sum_max_depth_cm": {"observed": dep_obs, "null_mean": float(dep_null.mean()),
                             "null_sd": float(dep_null.std(ddof=1)), "null_median": float(np.median(dep_null)),
                             "p_value": p_dep, "null_ge_observed": int((dep_null >= dep_obs).sum())},
        "spots": rows,
    }


def render_permutation_markdown(runs: list[dict]) -> str:
    L = ["## Spatial permutation test (control for footprint size)", "",
         f"Null: each active MCGM footprint is translated to a uniformly-random valid position in the pilot, "
         f"preserving shape and exact cell count; {runs[0]['n_perm']} permutations, seed {runs[0]['seed']}; "
         "p = (1 + #{null >= observed}) / (n+1), one-sided. **A large p means the model did not beat chance.**", "",
         "| Scenario | Observed DETECTED | Null mean | Null SD | p (count) | Observed Σ max depth (cm) | Null mean | p (depth) |",
         "|---|---|---|---|---|---|---|---|"]
    for r in runs:
        d = r["detected"]; s = r["sum_max_depth_cm"]
        L.append(f"| `{r['scenario']}` | {d['observed']} / {r['n_active_spots']} | {d['null_mean']:.2f} | "
                 f"{d['null_sd']:.2f} | **{d['p_value']:.4f}** | {s['observed']:.1f} | {s['null_mean']:.1f} | "
                 f"**{s['p_value']:.4f}** |")
    L += ["", "Per-spot exact detection probability under the null (over *all* valid placements, not sampled):", "",
          "| Scenario | Spot | Cells | Observed max cm | Observed DETECTED | Null P(detect) | Valid placements |",
          "|---|---|---|---|---|---|---|"]
    for r in runs:
        for sp in r["spots"]:
            L.append(f"| `{r['scenario']}` | {sp['name']} | {sp['footprint_cells']} | "
                     f"{sp['observed_max_depth_cm']:.1f} | {'yes' if sp['observed_detected'] else 'no'} | "
                     f"{sp['null_detect_prob_exact']:.3f} | {sp['n_valid_placements']} |")
    L += ["", "**Power warning.** Only 5 active spots fall inside the pilot, so the count statistic can take six "
              "values and cannot reach conventional significance however well the model performs. The Σ-depth "
              "statistic is reported because it is continuous and therefore has real resolution.", ""]
    return "\n".join(L)


# ----------------------------------------------------------------------------- Chitale named roads
def chitale_match(pilot: dict, res: SimulationResult, terms=CHITALE_TERMS) -> list[dict]:
    roads = pilot.get("roads")
    out = []
    if roads is None:
        return out
    seg_max = {}
    for f in res.frames:
        for k, v in f.street_depth_m.items():
            if v > seg_max.get(k, 0.0):
                seg_max[k] = float(v)
    for term in terms:
        segs = [s for s in roads.segments if s.name and term in s.name.lower()]
        names = sorted({s.name for s in segs})
        if not segs:
            out.append({"term": term, "n_segments": 0, "names": [], "max_depth_cm": None, "class": "NO_ROAD_MATCH"})
            continue
        depths = [seg_max.get(s.seg_id, 0.0) for s in segs]
        mx = max(depths) * 100
        n_ge15 = sum(1 for d in depths if d >= DETECT_CM / 100)
        out.append({"term": term, "n_segments": len(segs), "names": names[:12], "max_depth_cm": mx,
                    "median_depth_cm": float(np.median(depths)) * 100,
                    "frac_segments_ge_15cm": n_ge15 / len(segs), "class": classify(mx)})
    return out


# ----------------------------------------------------------------------------- run wiring (copied from api.state)
def run_pilot_scenario(pilot: dict, scenario_id: str, horizon_min: int = 180, blockage: Optional[dict] = None,
                       open_boundary: bool = True):
    """Same wiring as `floodnet.api.state` — including `open_boundary=True`, which this function did NOT pass
    before 2026-09-10 (see the module docstring). Pass `open_boundary=False` to reproduce the pre-fix runs."""
    from ..simulation.engine import run_simulation
    from ..terrain.runoff import runoff_fn
    from ..terrain.surface import StorageCellSurface
    from ..drainage.hydraulics import GraphDrainage
    from ..streets.aggregate import make_street_fn
    sc = pilot["scenarios"]
    scen = sc[scenario_id] if isinstance(sc, dict) else next(s for s in sc if s.id == scenario_id)
    terrain = pilot["terrain"]; net = pilot["net"]
    street_fn = make_street_fn(pilot["roads"], terrain.grid) if pilot.get("roads") is not None else None
    surface = StorageCellSurface(terrain, open_boundary=bool(open_boundary))
    return run_simulation(terrain, net, scen, surface, GraphDrainage(net), runoff_fn,
                          street_fn=street_fn, blockage=blockage or {"mode": "none"},
                          horizon_s=int(horizon_min) * 60, frame_dt_s=config.FRAME_DT_S)


# ----------------------------------------------------------------------------- report
def _fmt(v, nd=1):
    return "-" if v is None else f"{v:.{nd}f}"


def render_markdown(report: dict) -> str:
    L = ["# Flooding Spots validation (MCGM layer 344) — pilot " + config.PILOT_NAME, "",
         f"Generated {report['generated']} on commit {report.get('commit', '?')}; horizon {report['horizon_min']} min; "
         f"blockage none; surface boundary "
         f"{'OPEN (open_boundary=True, matches the shipped API)' if report.get('open_boundary', True) else 'CLOSED (does NOT match the shipped API)'}"
         ". Measurement only — the model was not tuned for this check.", "",
         "## What this is and is not", "",
         "* MCGM \"Flooding Spots\" is a chronic, **undated** inventory (~2017); it is not a flood map for any storm. "
         "Only spots not flagged `(Delete)`/`(Tackled)` are scored (**active**); inactive ones are listed for reference.",
         "* Footprint = MCGM polygon (cells whose centre falls inside) or, if absent, a 50 m radius around the centroid. "
         "The polygons in this snapshot are ~100-500 m circles, so 'max depth in footprint' is a generous test.",
         f"* Class: DETECTED >= {DETECT_CM:g} cm, MARGINAL {MARGINAL_CM:g}-{DETECT_CM:g} cm, MISSED < {MARGINAL_CM:g} cm "
         "(max modelled surface depth in footprint over the run). Working thresholds, not cited guidance.",
         "* MCGM `DEPTH` / `STRETCH` attributes are free text of **unknown units**; they are reproduced verbatim, not compared.",
         "* Base rate: if a large share of all pilot cells exceeds the detection depth, agreement is **uninformative** "
         "(anything would be 'detected'). The percentile column places each spot in the distribution of per-cell max depth.",
         ""]
    for run in report["runs"]:
        br = run["base_rate"]; c = run["active_counts"]
        L += [f"## Scenario `{run['scenario']}` — {run['scenario_name']}", "",
              f"Active spots: **DETECTED {c['DETECTED']} / MARGINAL {c['MARGINAL']} / MISSED {c['MISSED']}** of {run['n_active']}. "
              f"Mass-balance error {run['mass_balance_error_pct']:.2f} %.", "",
              f"Base rate: {br['frac_cells_ge_threshold_envelope']*100:.1f} % of {br['n_cells']} non-building cells reach "
              f">= {br['threshold_cm']:g} cm at some point (peak single frame {br['frac_cells_ge_threshold_peak_frame']*100:.1f} % "
              f"at t = {br['peak_frame_t_min']:.0f} min); {br['frac_cells_ge_5cm_envelope']*100:.1f} % reach >= 5 cm. "
              f"Per-cell max-depth percentiles (cm): " + ", ".join(f"p{k}={v:.1f}" for k, v in br["envelope_percentiles_cm"].items()) + ".",
              ("**Agreement is uninformative for this scenario: the model floods most of the pilot at the scoring depth.**"
               if run["uninformative"] else
               "The scoring depth is exceeded in a minority of cells, so DETECTED is informative here."), "",
              "| Spot | Active | Footprint (cells) | Max depth cm (t) | Onset 5/15/30 cm (min) | Cell pctl | Class | "
              "Nearest node (dist m; surcharge) | Nearest road (dist m; max cm) | MCGM DEPTH/STRETCH (units?) | z min / relief m |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for s in run["spots"]:
            n = s["nearest_node"]; r = s["nearest_road"] or {}
            on = s["onset_min"]
            surch = (f"yes @{n['first_surcharge_min']:.0f} min, {n['cause'] or '?'}" if n["surcharges"] else "no")
            L.append(f"| {s['name'].strip()} | {'yes' if s['active'] else 'no'} | {s['footprint']} ({s['footprint_cells']}) | "
                     f"{s['max_depth_cm']:.1f} ({s['t_of_max_min']:.0f}) | {_fmt(on['5cm'],0)}/{_fmt(on['15cm'],0)}/{_fmt(on['30cm'],0)} | "
                     f"{s['cell_depth_percentile']:.0f} | **{s['class']}** | {n['id']} ({n['dist_m']:.0f}; {surch}) | "
                     f"{r.get('name','-')} ({_fmt(r.get('dist_m'),0)}; {_fmt(r.get('max_depth_cm'))}) | "
                     f"{s['mcgm_depth_attr']} / {s['mcgm_stretch_m']} | {s['footprint_z_min_m']:.2f} / {_fmt(s['local_relief_m'],2)} |")
        L.append("")
        misses = [s for s in run["spots"] if s["active"] and s["class"] != "DETECTED"]
        if misses:
            L += ["### Diagnosis of active spots not DETECTED", ""]
            for s in misses:
                L += [f"**{s['name'].strip()}** ({s['class']}, {s['max_depth_cm']:.1f} cm): " + diagnose(s), ""]
    ch = report.get("chitale") or {}
    if ch:
        L += [f"## Chitale Committee (2006) named locations — scenario `{ch['scenario']}`", "",
              "Qualitative, named-location precision only: the report names roads/areas that were submerged on 26-27 July 2005; "
              "it gives no coordinates, depths or times for them. Substrings are matched against OSM segment names inside the pilot.", "",
              "| Term | Segments | Example names | Max depth cm | Median cm | Share of segments >= 15 cm | Class |", "|---|---|---|---|---|---|---|"]
        for m in ch["matches"]:
            L.append(f"| {m['term']} | {m['n_segments']} | {'; '.join(m['names'][:4])} | {_fmt(m['max_depth_cm'])} | "
                     f"{_fmt(m.get('median_depth_cm'))} | {_fmt((m.get('frac_segments_ge_15cm') or 0)*100,0)} % | {m['class']} |")
        L += ["", f"Chitale base rate under `{ch['scenario']}`: {ch['frac_all_segments_ge_15cm']*100:.1f} % of all "
              f"{ch['n_segments_total']} named+unnamed road segments in the pilot reach >= 15 cm; a term scoring DETECTED is only "
              "meaningful relative to that share.", ""]
    L += ["## Files", "", "* `docs/validation/flooding_spots.json` — machine-readable version of everything above.",
          "* `backend/floodnet/validation/flooding_spots.py` — the code (CLI: `python -m floodnet.validation.flooding_spots`).", ""]
    return "\n".join(L)


def diagnose(s: dict) -> str:
    """One paragraph, evidence from the row only. No fixes."""
    n = s["nearest_node"]; r = s["nearest_road"] or {}
    parts = []
    if n["dist_m"] > 100:
        parts.append(f"nearest drainage node is {n['dist_m']:.0f} m away, so the spot is effectively outside the modelled network's "
                     "inlet reach (water there can only pond by terrain, not by surcharge)")
    else:
        parts.append(f"a drainage node ({n['id']}) sits {n['dist_m']:.0f} m away")
        if n["surcharges"]:
            parts.append(f"it does surcharge (from {n['first_surcharge_min']:.0f} min, cause {n['cause'] or 'unknown'}, "
                         f"{n['surcharge_volume_m3']:.0f} m3 returned to surface) but the returned water does not stay in the footprint")
        else:
            parts.append("it never surcharges in this run, i.e. the model thinks the local pipes cope")
    if s["local_relief_m"] is not None:
        if s["local_relief_m"] > 0.0:
            parts.append(f"the footprint's lowest cell is {s['local_relief_m']:+.2f} m relative to the 150 m-neighbourhood median, "
                         "i.e. the DTM has no local low here — runoff drains away rather than ponding")
        elif s["local_relief_m"] > -0.3:
            parts.append(f"local relief is only {s['local_relief_m']:+.2f} m, a very shallow depression at 10 m resolution")
        else:
            parts.append(f"the footprint does contain a local low ({s['local_relief_m']:+.2f} m vs neighbourhood) yet stays dry, "
                         "which points at runoff routing / inlet capture rather than terrain")
    if s["footprint_building_cells"] and s["footprint_building_cells"] > 0.5 * s["footprint_cells"]:
        parts.append(f"{s['footprint_building_cells']} of {s['footprint_cells']} footprint cells are building (no-flow) cells")
    name = (s["name"] or "").lower(); loc = (s["location"] or "").lower()
    if any(k in name or k in loc for k in ("station", "subway", "yard", "railway")):
        parts.append("the location is at/under a railway station or subway, a feature (underpass, track-side low) that a 10 m "
                     "DTM interpolated from 20 cm contours does not resolve")
    if r:
        parts.append(f"the nearest OSM segment ({r['name']}, {r['dist_m']:.0f} m) reaches at most {r['max_depth_cm']:.1f} cm")
    return "; ".join(parts) + "."


def build_report(pilot: dict, scenarios=("heavy", "july2005"), horizon_min: int = 180, chitale_scenario: str = "july2005",
                 progress=print, open_boundary: bool = True, n_perm: int = PERM_N, seed: int = PERM_SEED) -> dict:
    runs = []; chit = None
    for sid in scenarios:
        t0 = time.time()
        res = run_pilot_scenario(pilot, sid, horizon_min, open_boundary=open_boundary)
        progress(f"[spots] {sid}: engine {res.runtime_s:.1f}s wall {time.time()-t0:.1f}s, mb err {res.mass_balance.error_pct:.2f}%, "
                 f"boundary_out {res.mass_balance.boundary_out_m3:.1f} m3 (open_boundary={open_boundary})")
        run = evaluate_spots(pilot, res)
        if n_perm > 0:
            t1 = time.time()
            run["permutation"] = permutation_test(pilot, res, n_perm=n_perm, seed=seed)
            pd = run["permutation"]["detected"]
            progress(f"[perm]  {sid}: observed {pd['observed']}/{run['permutation']['n_active_spots']} DETECTED, "
                     f"null mean {pd['null_mean']:.2f} +/- {pd['null_sd']:.2f}, p = {pd['p_value']:.4f} "
                     f"({time.time()-t1:.1f}s)")
        runs.append(run)
        if sid == chitale_scenario:
            seg_max = {}
            for f in res.frames:
                for k, v in f.street_depth_m.items():
                    seg_max[k] = max(seg_max.get(k, 0.0), float(v))
            allv = list(seg_max.values())
            chit = {"scenario": sid, "matches": chitale_match(pilot, res),
                    "n_segments_total": len(allv),
                    "frac_all_segments_ge_15cm": float(np.mean([v >= DETECT_CM / 100 for v in allv])) if allv else 0.0}
    commit = None
    try:
        import subprocess
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=config.REPO_DIR, text=True).strip()
    except Exception:  # noqa: BLE001
        pass
    return {"generated": time.strftime("%Y-%m-%d %H:%M"), "commit": commit, "horizon_min": horizon_min,
            "pilot": config.PILOT_NAME, "open_boundary": bool(open_boundary), "runs": runs, "chitale": chit}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scenarios", nargs="+", default=["heavy", "july2005"])
    ap.add_argument("--horizon-min", type=int, default=180)
    ap.add_argument("--permutations", type=int, default=PERM_N,
                    help="spatial permutation test iterations (0 disables it)")
    ap.add_argument("--perm-seed", type=int, default=PERM_SEED)
    ap.add_argument("--closed-boundary", action="store_true",
                    help="reproduce the pre-2026-09-10 runs (closed boundary; does NOT match the shipped API)")
    ap.add_argument("--out-json", default=str(config.REPO_DIR / "docs" / "validation" / "flooding_spots.json"))
    ap.add_argument("--out-md", default=str(config.REPO_DIR / "docs" / "validation" / "FLOODING_SPOTS.md"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    from ..data.load import load_pilot
    pilot = load_pilot()
    rep = build_report(pilot, a.scenarios, a.horizon_min, open_boundary=not a.closed_boundary,
                       n_perm=a.permutations, seed=a.perm_seed)
    md = render_markdown(rep)
    perms = [r["permutation"] for r in rep["runs"] if r.get("permutation")]
    if perms:
        md += "\n" + render_permutation_markdown(perms)
    Path(a.out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(a.out_json, "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=1)
    with open(a.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    for run in rep["runs"]:
        line = (f"{run['scenario']}: active {run['active_counts']} "
                f"base-rate>=15cm {run['base_rate']['frac_cells_ge_threshold_envelope']*100:.1f}%")
        p = run.get("permutation")
        if p:
            line += (f" | permutation: observed {p['detected']['observed']}/{p['n_active_spots']} vs null mean "
                     f"{p['detected']['null_mean']:.2f}, p={p['detected']['p_value']:.4f}"
                     f" | sum-depth {p['sum_max_depth_cm']['observed']:.1f} cm vs null "
                     f"{p['sum_max_depth_cm']['null_mean']:.1f}, p={p['sum_max_depth_cm']['p_value']:.4f}")
        print(line)
    print(f"wrote {a.out_md} and {a.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
