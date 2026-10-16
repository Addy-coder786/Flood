"""CLI: build the Hindmata/Dadar pilot dataset into data/processed/pilot/.

    backend/.venv/Scripts/python -m floodnet.data.build_pilot [--res 10] [--include-proposal] [--offline]

Writes network.json, terrain.npz + terrain.json, roads.json, hotspots.json, scenarios.json, PROVENANCE.md.
Every fetch has a labelled fallback; nothing SYNTHETIC is ever described as Mumbai data.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone

import numpy as np

from ..config import (DATA_PROCESSED, PILOT_BBOX_LONLAT, PILOT_MARGIN_M, GRID_RES_M, PILOT_NAME)
from ..contracts import Grid
from ..provenance import Provenance, Tag
from ..contracts import Terrain as _Terrain
from . import mcgm, contours as contours_mod, osm as osm_mod, hotspots as hs_mod, scenarios as sc_mod

log = logging.getLogger("floodnet.data.build_pilot")


def build(res: float = GRID_RES_M, include_proposal: bool = False, offline: bool = False,
          bbox=PILOT_BBOX_LONLAT, margin_m: float = PILOT_MARGIN_M, out_dir=DATA_PROCESSED) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    grid = mcgm.pilot_grid(bbox, margin_m, res)
    log.info("grid %dx%d @ %.1f m", grid.nx, grid.ny, grid.res)
    counts = {"grid_nx": grid.nx, "grid_ny": grid.ny, "res_m": grid.res}

    # 1. network (REAL snapshot, always available)
    net = mcgm.build_network(bbox, margin_m, include_proposal=include_proposal, grid=grid)
    with open(out_dir / "network.json", "w", encoding="utf-8") as fh:
        json.dump({"grid": grid.to_dict(), **mcgm.network_to_dict(net)}, fh)
    counts.update(nodes=net.n_nodes, edges=net.n_edges, outfalls=int(net.node_is_outfall.sum()))
    log.info("network: %d nodes, %d edges, %d outfalls", net.n_nodes, net.n_edges, int(net.node_is_outfall.sum()))

    # 2. terrain z
    node_pts = np.column_stack([net.node_x, net.node_y, net.node_ground.astype(float)])
    feats = None
    try:
        if offline and not contours_mod.DEFAULT_CACHE.exists():
            raise RuntimeError("offline and no contour cache")
        feats = contours_mod.fetch_contours(bbox, margin_m)
    except Exception as ex:  # noqa: BLE001
        log.error("contours unavailable (%s): falling back to manhole GROUND_LEV points only", ex)
    try:
        z, z_prov = contours_mod.contours_to_dtm(feats, node_pts, grid)
    except Exception as ex:  # noqa: BLE001
        log.error("DTM interpolation failed (%s): using SyntheticSlope (SYNTHETIC)", ex)
        z, z_prov = contours_mod.SyntheticSlope().build(grid)
    counts["contours"] = len(feats) if feats else 0

    # 3. OSM roads / buildings / impervious
    js = None
    has_cache = any((osm_mod.OSM_RAW / f).exists() for f in ("pilot_osm.json", "roads.json"))
    try:
        if offline and not has_cache:
            raise RuntimeError("offline and no OSM cache")
        js = osm_mod.fetch_osm(bbox, margin_m)
    except Exception as ex:  # noqa: BLE001
        log.error("OSM unavailable (%s): trying MCGM Road Centerline layer 156", ex)
    if js is not None:
        roads = osm_mod.build_road_graph(js)
        bmask = osm_mod.building_mask(js, grid)
        if bmask.any():
            imp, imp_prov = osm_mod.impervious_fraction(js, grid, bmask, roads)
            b_prov = Provenance(Tag.REAL, osm_mod.OSM_PROVENANCE.source,
                                "cell centroid inside an OSM building footprint (contains_xy)")
        else:
            imp = np.full((grid.ny, grid.nx), 0.85, dtype=np.float32)
            imp_prov = Provenance(Tag.ESTIMATED, "assumption", "uniform 0.85; no building footprints available at build time")
            b_prov = Provenance(Tag.ESTIMATED, "none", "no building footprints available at build time; mask all False")
    else:
        try:
            roads = osm_mod.fetch_mcgm_road_centerlines(bbox, margin_m)
            log.warning("roads: using MCGM Road Centerline layer 156 (%d segments)", len(roads.segments))
        except Exception as ex2:  # noqa: BLE001
            log.error("MCGM layer 156 unavailable (%s): SYNTHETIC lattice road graph", ex2)
            roads = osm_mod.synthetic_lattice_roads(grid)
        bmask = np.zeros((grid.ny, grid.nx), dtype=bool)
        imp = np.full((grid.ny, grid.nx), 0.85, dtype=np.float32)
        imp_prov = Provenance(Tag.ESTIMATED, "assumption", "uniform 0.85; no building footprints available at build time")
        b_prov = Provenance(Tag.ESTIMATED, "none", "no building footprints available at build time; mask all False")
    counts.update(road_segments=len(roads.segments), road_nodes=int(len(roads.node_xy)),
                  building_cells=int(bmask.sum()))

    np.savez_compressed(out_dir / "terrain.npz", z=z.astype(np.float32), impervious=imp.astype(np.float32),
                        building=bmask.astype(bool))
    # DEM reliability flagging (Task 1/2 validation pass): the DTM is NOT modified. A handful of
    # contour-derived cells sit far below every surveyed manhole ground level nearby; those cells are
    # flagged (not altered) so the API/UI can exclude them from headline street-depth claims.
    from ..terrain.pits import dem_reliability_mask
    _terr_for_mask = _Terrain(grid=grid, z=z, impervious=imp, building=bmask,
                              provenance=z_prov, impervious_provenance=imp_prov, building_provenance=b_prov)
    reliability_mask, reliability_report = dem_reliability_mask(_terr_for_mask, net)
    counts["dem_flagged_cells"] = reliability_report["n_cells_flagged"]
    with open(out_dir / "terrain.json", "w", encoding="utf-8") as fh:
        json.dump({"grid": grid.to_dict(), "z_stats": {"min": float(np.nanmin(z)), "max": float(np.nanmax(z)),
                                                       "datum": "mTHD (Town Hall Datum), MSL offset UNVERIFIED"},
                   "provenance": z_prov.to_dict(), "impervious_provenance": imp_prov.to_dict(),
                   "building_provenance": b_prov.to_dict(),
                   "pit_handling": {
                       "dem_modified": False,
                       "open_boundary": "auto (outward_open_boundary): edge cells sloping out of the clip "
                                        "window act as a free outfall in the surface model, so water can "
                                        "leave the pilot instead of ponding against the clip line",
                       "dem_reliability_mask": reliability_report,
                   }}, fh)
    np.savez_compressed(out_dir / "dem_reliability_mask.npz", mask=reliability_mask.astype(bool))
    with open(out_dir / "roads.json", "w", encoding="utf-8") as fh:
        json.dump(osm_mod.roads_to_dict(roads), fh)

    # 4. hotspots
    recs = [] if (offline and not hs_mod.DEFAULT_CACHE.exists()) else hs_mod.build_hotspots(bbox)
    with open(out_dir / "hotspots.json", "w", encoding="utf-8") as fh:
        json.dump({"provenance": hs_mod.PROVENANCE.to_dict(), "hotspots": recs}, fh)
    counts.update(hotspots=len(recs), hotspots_active=sum(1 for r in recs if r["active"]))

    # 5. scenarios
    scs = sc_mod.scenarios()
    with open(out_dir / "scenarios.json", "w", encoding="utf-8") as fh:
        json.dump({k: v.to_dict() for k, v in scs.items()}, fh)
    counts["scenarios"] = len(scs)

    # 6. PROVENANCE.md
    rows = [("Drainage geometry, lengths, shapes, W/H, inverts, status", net.provenance["geometry"]),
            ("Manhole ground levels", net.provenance["ground_level"]),
            ("Node inverts", net.provenance["node_invert"]), ("Outfalls", net.provenance["outfalls"]),
            ("Conduit slope", net.provenance["slope"]), ("Manning n", net.provenance["roughness"]),
            ("Conduit capacity", net.provenance["capacity"]), ("Manhole storage area", net.provenance["storage_area"]),
            ("Inlet capacity", net.provenance["inlet_capacity"]),
            ("Terrain z", z_prov.to_dict()), ("Impervious fraction", imp_prov.to_dict()),
            ("Building mask", b_prov.to_dict()), ("Roads", roads.provenance.to_dict()),
            ("Flooding hotspots", hs_mod.PROVENANCE.to_dict())] + \
           [(f"Scenario `{k}`", v.provenance.to_dict()) for k, v in scs.items()]
    md = [f"# Processed pilot dataset — {PILOT_NAME}", "",
          f"Built {datetime.now(timezone.utc).isoformat(timespec='seconds')} by `floodnet.data.build_pilot`. "
          f"bbox (lon/lat) {bbox}, margin {margin_m:.0f} m, grid {grid.nx}x{grid.ny} @ {grid.res} m (EPSG:32643).", "",
          "Counts: " + ", ".join(f"{k}={v}" for k, v in counts.items()), "",
          "| Item | Tag | Source | Note |", "|---|---|---|---|"]
    for name, p in rows:
        md.append(f"| {name} | **{p['tag']}** | {p['source']} | {p['note']} |")
    md += ["", "Datum caveat: all elevations are mTHD (Town Hall Datum); the offset to MSL is UNVERIFIED. "
               "MCGM data: credit MCGM / Esri India, no explicit open licence. OSM: (c) OpenStreetMap contributors, ODbL."]
    with open(out_dir / "PROVENANCE.md", "w", encoding="utf-8") as fh:
        fh.write("\n".join(md) + "\n")
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--res", type=float, default=GRID_RES_M)
    ap.add_argument("--include-proposal", action="store_true")
    ap.add_argument("--offline", action="store_true", help="never hit the network; use caches or fallbacks")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    counts = build(res=a.res, include_proposal=a.include_proposal, offline=a.offline)
    print(json.dumps(counts, indent=2))
    print(f"written to {DATA_PROCESSED}")


if __name__ == "__main__":
    sys.exit(main())
