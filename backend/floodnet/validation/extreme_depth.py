"""Forensic check of the deepest simulated cells on the REAL pilot (scenario `heavy`, no blockage, 180 min).

    backend/.venv/Scripts/python -m floodnet.validation.extreme_depth [--scenario heavy] [--horizon 180] [--top 5]

For the argmax cell of max-over-time depth and the top-N cells it reports geometry, terrain neighbourhood,
nearest drainage node / road / MCGM Flooding Spot, the closed depression the cell sits in (flood-fill below the
spill level) and the MCGM contour points within 30 m, then assigns a verdict:
genuine depression / interpolation artefact / boundary trapping / ambiguous.
Writes docs/validation/EXTREME_DEPTH.md and docs/validation/extreme_depth.json.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

from .. import config
from ..api.state import xy_to_lonlat, lonlat_to_xy, build_models, street_fn_for
from ..contracts import Grid, Terrain
from ..data.contours import DEFAULT_CACHE as CONTOUR_CACHE, densify_contours
from ..data.load import load_pilot
from ..simulation.engine import run_simulation

OUT_DIR = config.REPO_DIR / "docs" / "validation"


# ----------------------------------------------------------------------------- terrain helpers
def depression(z: np.ndarray, open_mask: np.ndarray, j0: int, i0: int, max_cells: int = 20000) -> dict:
    """Closed depression containing (j0,i0): grow a flood-fill in ascending z (priority) over open cells until the
    next cell to add is on the grid boundary (spill off-grid) or is lower than the current pool level (spill into a
    lower region). Returns spill elevation, member cells, volume below spill, whether the spill is the boundary."""
    import heapq
    ny, nx = z.shape
    # descend (steepest, open cells only) to the local minimum so the pool is seeded at its bottom
    while True:
        cand = [(float(z[jj, ii]), jj, ii) for jj, ii in ((j0 + 1, i0), (j0 - 1, i0), (j0, i0 + 1), (j0, i0 - 1))
                if 0 <= jj < ny and 0 <= ii < nx and open_mask[jj, ii] and z[jj, ii] < z[j0, i0]]
        if not cand:
            break
        _, j0, i0 = min(cand)
    seen = np.zeros_like(z, dtype=bool)
    heap = [(float(z[j0, i0]), j0, i0)]
    seen[j0, i0] = True
    members: list[tuple[int, int]] = []
    level = float(z[j0, i0])
    spill = None
    spill_cell = None
    while heap:
        zz, j, i = heapq.heappop(heap)
        if zz < level - 1e-9:                     # a lower cell reached -> the pool spills here
            spill, spill_cell = level, (j, i)
            break
        level = max(level, zz)
        members.append((j, i))
        if j == 0 or i == 0 or j == ny - 1 or i == nx - 1:
            spill, spill_cell = level, (j, i)      # boundary: the closed model traps water here
            break
        if len(members) >= max_cells:
            spill, spill_cell = level, (j, i)
            break
        for dj, di in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            jj, ii = j + dj, i + di
            if 0 <= jj < ny and 0 <= ii < nx and not seen[jj, ii] and open_mask[jj, ii]:
                seen[jj, ii] = True
                heapq.heappush(heap, (float(z[jj, ii]), jj, ii))
    if spill is None:
        spill, spill_cell = level, (j0, i0)
    mem = np.array(members, dtype=int).reshape(-1, 2)
    zm = z[mem[:, 0], mem[:, 1]]
    below = zm < spill - 1e-9
    return {"spill_elev_m": round(spill, 3), "spill_cell": [int(spill_cell[0]), int(spill_cell[1])],
            "spill_on_boundary": bool(spill_cell[0] in (0, ny - 1) or spill_cell[1] in (0, nx - 1)),
            "n_cells_below_spill": int(below.sum()), "area_m2": float(below.sum()) * 100.0,
            "volume_m3": float(np.sum((spill - zm[below]))) * 100.0,
            "bottom_cell": [int(j0), int(i0)], "bottom_z_m": round(float(z[j0, i0]), 3),
            "max_fill_depth_m": round(float(spill - z[j0, i0]), 3)}


def nearest_segment(roads, x: float, y: float) -> tuple[str, str, float]:
    best = (None, None, np.inf)
    for s in roads.segments:
        p = s.xy
        if len(p) < 2:
            d = float(np.hypot(p[0, 0] - x, p[0, 1] - y)) if len(p) else np.inf
        else:
            a, b = p[:-1], p[1:]
            ab = b - a
            t = np.clip(np.sum((np.array([x, y]) - a) * ab, axis=1) / np.maximum(np.sum(ab * ab, axis=1), 1e-9), 0, 1)
            proj = a + t[:, None] * ab
            d = float(np.min(np.hypot(proj[:, 0] - x, proj[:, 1] - y)))
        if d < best[2]:
            best = (s.seg_id, s.name or f"({s.highway})", d)
    return best


def nearest_hotspot(hotspots: list[dict], x: float, y: float) -> dict | None:
    best = None
    for h in hotspots:
        poly = np.asarray(h.get("polygon_lonlat") or [h["lonlat_centroid"]], dtype=float)
        px, py = lonlat_to_xy(poly[:, 0], poly[:, 1])
        d = float(np.min(np.hypot(np.asarray(px) - x, np.asarray(py) - y)))
        if best is None or d < best["dist_m"]:
            best = {"name": h["name"].strip(), "active": bool(h["active"]), "dist_m": round(d, 1)}
    return best


# ----------------------------------------------------------------------------- main analysis
def analyse(scenario_id: str = "heavy", horizon_min: int = 180, top_n: int = 5) -> dict:
    p = load_pilot()
    terrain: Terrain = p["terrain"]; net = p["net"]; roads = p["roads"]; hotspots = p["hotspots"]
    g: Grid = terrain.grid
    scen = p["scenarios"][scenario_id]
    surface, drainage, runoff_fn = build_models(terrain, net)
    res = run_simulation(terrain, net, scen, surface, drainage, runoff_fn, street_fn=street_fn_for(roads, g),
                         blockage={"mode": "none"}, horizon_s=horizon_min * 60, frame_dt_s=config.FRAME_DT_S)
    depths = np.stack([f.depth for f in res.frames])            # [T, ny, nx]
    dmax = depths.max(axis=0); tmax = depths.argmax(axis=0)
    z = terrain.z.astype(float); bld = terrain.building; open_mask = ~bld
    contour_pts = densify_contours(json.load(open(CONTOUR_CACHE, encoding="utf-8"))["features"]) \
        if CONTOUR_CACHE.exists() else np.zeros((0, 3))

    order = np.argsort(dmax.ravel())[::-1][:top_n]
    cells = []
    for rank, flat in enumerate(order):
        j, i = int(flat // g.nx), int(flat % g.nx)
        x = g.x0 + (i + 0.5) * g.res; y = g.y0 + (j + 0.5) * g.res
        lon, lat = xy_to_lonlat(x, y)
        js, je = max(0, j - 2), min(g.ny, j + 3); is_, ie = max(0, i - 2), min(g.nx, i + 3)
        nb = z[js:je, is_:ie]
        nb_b = bld[js:je, is_:ie]
        on_boundary = j in (0, g.ny - 1) or i in (0, g.nx - 1)
        adj_bld = bool(bld[max(0, j - 1):j + 2, max(0, i - 1):i + 2].any()) and not bld[j, i]
        # nearest node
        dn = np.hypot(net.node_x - x, net.node_y - y); k = int(np.argmin(dn))
        nj, ni = int(net.node_cell_j[k]), int(net.node_cell_i[k])
        z_at_node = float(z[nj, ni]) if g.inside(np.array([nj]), np.array([ni]))[0] else None
        node = {"id": str(net.node_id[k]), "dist_m": round(float(dn[k]), 1), "ground_lev_m": round(float(net.node_ground[k]), 3),
                "dtm_z_at_node_cell_m": None if z_at_node is None else round(z_at_node, 3),
                "dtm_minus_ground_lev_m": None if z_at_node is None else round(z_at_node - float(net.node_ground[k]), 3),
                "is_outfall": bool(net.node_is_outfall[k])}
        seg_id, seg_name, seg_d = nearest_segment(roads, x, y)
        near_c = contour_pts[np.hypot(contour_pts[:, 0] - x, contour_pts[:, 1] - y) <= 30.0] if len(contour_pts) else contour_pts
        dep = depression(z, open_mask, j, i)
        # verdict
        reasons = []
        neigh_min = float(np.min(np.where(nb_b, np.inf, nb)))
        z0 = float(z[j, i])
        lower_than_all = z0 <= neigh_min + 1e-6
        if on_boundary or dep["spill_on_boundary"]:
            verdict = "boundary trapping"
            reasons.append("cell or its depression spills onto the grid boundary; the closed-boundary surface model cannot let water leave")
        elif len(near_c) == 0:
            verdict = "interpolation artefact"
            reasons.append("no MCGM contour point within 30 m: the DTM here is a griddata linear/nearest fill between distant supports")
        else:
            hts = np.unique(np.round(near_c[:, 2], 2))
            if z0 < hts.min() - 0.15:
                verdict = "interpolation artefact"
                reasons.append(f"cell z {z0:.2f} is below every contour height within 30 m (min {hts.min():.2f})")
            elif dep["volume_m3"] > 0 and dep["n_cells_below_spill"] >= 3 and (near_c[:, 2] <= z0 + 0.2).sum() > 0:
                verdict = "genuine depression"
                reasons.append(f"contours within 30 m bracket the cell ({hts.min():.2f}-{hts.max():.2f}); closed depression of "
                               f"{dep['n_cells_below_spill']} cells / {dep['volume_m3']:.0f} m3 below spill {dep['spill_elev_m']:.2f}")
            else:
                verdict = "ambiguous"
                reasons.append("contour support present but the depression is tiny (1-2 cells) or no contour point at/below the cell level")
        if node["dtm_minus_ground_lev_m"] is not None and abs(node["dtm_minus_ground_lev_m"]) > 0.5:
            reasons.append(f"DTM at nearest manhole differs from GROUND_LEV by {node['dtm_minus_ground_lev_m']:+.2f} m")
        if adj_bld:
            reasons.append("adjacent to OSM building cells (no-flow faces reduce the outlets of this cell)")
        cells.append({
            "rank": rank + 1, "j": j, "i": i, "x": round(x, 2), "y": round(y, 2), "lon": round(float(lon), 6), "lat": round(float(lat), 6),
            "ground_z_m": round(z0, 3), "max_depth_m": round(float(dmax[j, i]), 3), "t_max_min": int(res.frames[int(tmax[j, i])].t_s // 60),
            "neighbourhood_5x5_z": [[None if nb_b[a, b] else round(float(nb[a, b]), 2) for b in range(nb.shape[1])] for a in range(nb.shape[0])],
            "neighbourhood_rows_j": list(range(js, je)), "neighbourhood_cols_i": list(range(is_, ie)),
            "is_building_cell": bool(bld[j, i]), "adjacent_to_building": adj_bld, "on_grid_boundary": on_boundary,
            "lower_than_all_open_neighbours": lower_than_all,
            "nearest_node": node, "nearest_road": {"seg_id": seg_id, "name": seg_name, "dist_m": round(seg_d, 1)},
            "nearest_flooding_spot": nearest_hotspot(hotspots, x, y), "depression": dep,
            "contours_within_30m": {"count": int(len(near_c)),
                                    "n_at_or_below_cell_z_plus_0_2": int((near_c[:, 2] <= z0 + 0.2).sum()) if len(near_c) else 0,
                                    "height_range_m": round(float(near_c[:, 2].max() - near_c[:, 2].min()), 2) if len(near_c) else None,
                                    "heights": sorted(set(round(float(v), 2) for v in near_c[:, 2])) if len(near_c) else []},
            "verdict": verdict, "reasons": reasons})
    mb = res.mass_balance
    edge_cells = np.zeros_like(dmax, dtype=bool); edge_cells[0, :] = edge_cells[-1, :] = edge_cells[:, 0] = edge_cells[:, -1] = True
    summary = {"scenario": scenario_id, "horizon_min": horizon_min, "blockage": "none", "grid": g.to_dict(),
               "max_depth_m": round(float(dmax.max()), 3), "cells_over_1m": int((dmax > 1.0).sum()),
               "cells_over_0_5m": int((dmax > 0.5).sum()), "boundary_cells_over_0_5m": int(((dmax > 0.5) & edge_cells).sum()),
               "boundary_share_of_final_surface_volume_pct": round(100.0 * float(depths[-1][edge_cells].sum() / max(depths[-1].sum(), 1e-9)), 2),
               "mass_balance": mb.__dict__, "runtime_s": round(res.runtime_s, 1),
               "surcharging_nodes_any_frame": int(np.any(np.stack([f.node_surcharging for f in res.frames]), axis=0).sum()),
               "flooded_segments_over_15cm": int(sum(1 for k, v in res.frames[-1].street_depth_m.items() if v > 0.15))
               if res.frames[-1].street_depth_m else None}
    return {"summary": summary, "cells": cells}


def to_markdown(r: dict) -> str:
    s = r["summary"]; mb = s["mass_balance"]
    L = [f"# Extreme-depth forensic check — scenario `{s['scenario']}`, no blockage, {s['horizon_min']} min", "",
         f"Grid {s['grid']['nx']}x{s['grid']['ny']} @ {s['grid']['res']} m. Max depth **{s['max_depth_m']} m**; "
         f"{s['cells_over_1m']} cells > 1 m, {s['cells_over_0_5m']} cells > 0.5 m ({s['boundary_cells_over_0_5m']} of them on the grid edge). "
         f"Grid-edge cells hold {s['boundary_share_of_final_surface_volume_pct']} % of the final surface volume.", "",
         f"Mass balance: runoff in {mb['runoff_in_m3']:.0f} m3, surface {mb['surface_stored_m3']:.0f}, network {mb['network_stored_m3']:.0f}, "
         f"outfall {mb['outfall_out_m3']:.0f}, error {mb['error_pct']:.3f} %. Surcharging nodes (any frame): {s['surcharging_nodes_any_frame']}; "
         f"segments > 15 cm at t_end: {s['flooded_segments_over_15cm']}.", "",
         "| # | (j,i) | lon,lat | z m | max depth m @ min | bldg adj | edge | nearest node (dist, GL, DTM-GL) | road (dist) | flooding spot (dist) | depression cells/m3/spill | contours ≤30 m | verdict |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for c in r["cells"]:
        n = c["nearest_node"]; d = c["depression"]; h = c["nearest_flooding_spot"] or {}
        L.append(f"| {c['rank']} | ({c['j']},{c['i']}) | {c['lon']:.5f},{c['lat']:.5f} | {c['ground_z_m']:.2f} | {c['max_depth_m']:.2f} @ {c['t_max_min']} "
                 f"| {'B' if c['is_building_cell'] else ('adj' if c['adjacent_to_building'] else 'no')} | {'yes' if c['on_grid_boundary'] else 'no'} "
                 f"| {n['id']} ({n['dist_m']} m, GL {n['ground_lev_m']:.2f}, {'off-grid' if n['dtm_minus_ground_lev_m'] is None else format(n['dtm_minus_ground_lev_m'], '+.2f')}) | {c['nearest_road']['name']} ({c['nearest_road']['dist_m']} m) "
                 f"| {h.get('name','-')} ({'active' if h.get('active') else 'inactive'}, {h.get('dist_m','-')} m) "
                 f"| {d['n_cells_below_spill']} / {d['volume_m3']:.0f} / {d['spill_elev_m']:.2f}{' (edge)' if d['spill_on_boundary'] else ''} "
                 f"| {c['contours_within_30m']['count']} {c['contours_within_30m']['heights']} | **{c['verdict']}** |")
    for c in r["cells"]:
        L += ["", f"## Cell {c['rank']}: (j={c['j']}, i={c['i']}) — {c['verdict']}", "",
              f"x,y = {c['x']}, {c['y']} (EPSG:32643); lon/lat {c['lon']}, {c['lat']}. Ground z {c['ground_z_m']} m, max depth {c['max_depth_m']} m at t={c['t_max_min']} min. "
              f"Lower than every open neighbour: {c['lower_than_all_open_neighbours']}.", "",
              "5x5 ground z (rows = j ascending south->north, cols = i ascending west->east, `B` = building):", "",
              "| j\\i | " + " | ".join(str(v) for v in c["neighbourhood_cols_i"]) + " |", "|---|" + "---|" * len(c["neighbourhood_cols_i"])]
        for jr, row in zip(c["neighbourhood_rows_j"], c["neighbourhood_5x5_z"]):
            L.append(f"| {jr} | " + " | ".join("B" if v is None else f"{v:.2f}" for v in row) + " |")
        L += ["", "Reasons: " + "; ".join(c["reasons"]) + "."]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default="heavy"); ap.add_argument("--horizon", type=int, default=180)
    ap.add_argument("--top", type=int, default=5); ap.add_argument("--out", default=str(OUT_DIR))
    a = ap.parse_args(argv)
    r = analyse(a.scenario, a.horizon, a.top)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "extreme_depth.json").write_text(json.dumps(r, indent=1, default=float), encoding="utf-8")
    (out / "EXTREME_DEPTH.md").write_text(to_markdown(r), encoding="utf-8")
    print(to_markdown(r))
    print(f"written {out / 'EXTREME_DEPTH.md'} and extreme_depth.json")


if __name__ == "__main__":
    sys.exit(main())
