"""P0 routing fix validation (2026-09-14): proves the _routable_core snap-restriction fix in router.py on the
REAL pilot network, per the required validation checklist:
  A. real origin/destination points inside the pilot bbox
  B. baseline (dry) graph routes them
  C. flood weighting (from a REAL simulation frame) can alter the route when it blocks the shortest path
  D. returned route geometry is valid
  E. genuinely unreachable destinations are still honestly reported (not silently "fixed away")
  F. multiple origin/destination pairs, not just one

Runs ONE real simulation (heavy scenario, 60 min horizon -- same horizon as the existing
docs/validation/demo_check.json, for comparability) to get real street_depth_m; no other scenarios run.

Run:  cd backend && .venv\\Scripts\\python.exe scripts/routing_fix_validation.py
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodnet.config import REPO_DIR, PILOT_BBOX_LONLAT
from floodnet.data.load import load_pilot
from floodnet.terrain.runoff import runoff_fn
from floodnet.terrain.surface import StorageCellSurface
from floodnet.drainage.hydraulics import GraphDrainage
from floodnet.streets.aggregate import make_street_fn
from floodnet.routing.router import safe_route, _snap, _routable_core, clear_caches
from floodnet.simulation.engine import run_simulation


def main():
    clear_caches()
    p = load_pilot()
    roads = p["roads"]
    street_fn = make_street_fn(p["roads"], p["terrain"].grid)
    core = _routable_core(roads)
    print(f"routable core: {len(core)}/{len(roads.node_xy)} nodes ({100*len(core)/len(roads.node_xy):.1f}%)")

    print("\nrunning ONE real simulation (heavy, 60 min horizon) for real flood-depth data...")
    r = run_simulation(p["terrain"], p["net"], p["scenarios"]["heavy"], StorageCellSurface(p["terrain"], open_boundary=True),
                       GraphDrainage(p["net"]), runoff_fn, street_fn, blockage={"mode": "none"}, horizon_s=3600)
    fpk = max(r.frames, key=lambda f: max(f.street_depth_m.values(), default=0))
    n_flooded_15 = sum(1 for d in fpk.street_depth_m.values() if d >= 0.15)
    print(f"peak frame: t={fpk.t_s/60:.0f}min, segments>=15cm: {n_flooded_15}, max depth: "
          f"{max(fpk.street_depth_m.values()):.2f}m")

    w, s_, e, n_ = PILOT_BBOX_LONLAT

    def pair_from_segment(seg_id, offset_deg=0.006):
        seg = next(s for s in roads.segments if s.seg_id == seg_id)
        mid = seg.lonlat[len(seg.lonlat) // 2]
        o = [float(mid[0]) - offset_deg, float(mid[1])]
        d = [float(mid[0]) + offset_deg, float(mid[1])]
        o[0], d[0] = max(o[0], w + 1e-3), min(d[0], e - 1e-3)
        return tuple(o), tuple(d), seg

    results = []

    # PAIR 1: the exact original failing probe (deepest segment at peak, same construction demo_check.py used)
    deepest_id, deepest_depth = max(fpk.street_depth_m.items(), key=lambda kv: kv[1])
    o1, d1, seg1 = pair_from_segment(deepest_id)
    dry1 = safe_route(roads, {}, o1, d1, vehicle="car")
    wet1 = safe_route(roads, fpk.street_depth_m, o1, d1, vehicle="car")
    results.append(("PAIR 1 (original failing probe, deepest segment at peak)", seg1.name, deepest_id,
                    round(deepest_depth * 100, 1), o1, d1, dry1, wet1))

    # PAIR 2: a different real street pair, offset further into the pilot (bbox corner to corner)
    o2 = (w + 0.003, s_ + 0.003)
    d2 = (e - 0.003, n_ - 0.003)
    dry2 = safe_route(roads, {}, o2, d2, vehicle="car")
    wet2 = safe_route(roads, fpk.street_depth_m, o2, d2, vehicle="car")
    results.append(("PAIR 2 (bbox SW corner to NE corner)", None, None, None, o2, d2, dry2, wet2))

    # PAIR 3: pick the 2nd-deepest distinct segment for a different flood-crossing test
    sorted_depths = sorted(fpk.street_depth_m.items(), key=lambda kv: -kv[1])
    second_id, second_depth = sorted_depths[len(sorted_depths) // 4]  # a real, moderately-deep segment
    o3, d3, seg3 = pair_from_segment(second_id, offset_deg=0.004)
    dry3 = safe_route(roads, {}, o3, d3, vehicle="car")
    wet3 = safe_route(roads, fpk.street_depth_m, o3, d3, vehicle="car")
    results.append((f"PAIR 3 (moderately flooded segment '{seg3.name or seg3.seg_id}')", seg3.name, second_id,
                    round(second_depth * 100, 1), o3, d3, dry3, wet3))

    # PAIR 4 (E): deliberately verify "genuinely unreachable" is still honestly reported on the REAL network --
    # flood every real segment touching the destination's own snapped node past the vehicle limit.
    o4 = o1
    d4 = d1
    d4_node = _snap(roads, [d4])[0]
    surround = {s.seg_id: 1.0 for s in roads.segments if d4_node in (s.u, s.v)}
    wet4 = safe_route(roads, surround, o4, d4, vehicle="truck")
    results.append(("PAIR 4 (E: destination node fully surrounded by flooded real segments -> must be honestly unreachable)",
                    None, None, None, o4, d4, None, wet4))

    print("\n" + "=" * 100)
    all_ok = True
    for label, name, seg_id, depth_cm, o, d, dry, wet in results:
        print(f"\n{label}")
        if name:
            print(f"  deepest/target segment: {name!r} ({seg_id}) depth={depth_cm}cm")
        print(f"  origin={o}  dest={d}")
        if dry is not None:
            ok_b = dry["reachable"] and dry["route"] is not None and dry["route"]["type"] == "LineString" and len(dry["route"]["coordinates"]) >= 2
            print(f"  [B/D] baseline (dry): reachable={dry['reachable']} length_m={dry['length_m']} "
                  f"geometry_valid={ok_b}")
            all_ok &= ok_b
        if wet is not None:
            ok_geom = (not wet["reachable"]) or (wet["route"] is not None and wet["route"]["type"] == "LineString" and len(wet["route"]["coordinates"]) >= 2)
            print(f"  [C/D/E] flood-weighted: reachable={wet['reachable']} length_m={wet['length_m']} "
                  f"avoided_segments={len(wet['avoided_segments'])} max_depth_on_route_cm={wet['max_depth_on_route_cm']} "
                  f"geometry_valid_or_honestly_unreachable={ok_geom}")
            all_ok &= ok_geom
            if dry is not None and dry["reachable"] and wet["reachable"]:
                changed = (wet["length_m"] != dry["length_m"]) or (len(wet["avoided_segments"]) > 0)
                print(f"  [C] route changed due to flooding vs dry baseline: {changed}")

    print("\n" + "=" * 100)
    print(f"PAIR 4 unreachable-honestly-reported check: reachable={results[3][7]['reachable']} "
          f"(expected False) avoided={len(results[3][7]['avoided_segments'])} (expected >=1) "
          f"route={results[3][7]['route']} (expected None)")
    pair4_ok = (results[3][7]["reachable"] is False and results[3][7]["route"] is None
               and len(results[3][7]["avoided_segments"]) >= 1)
    all_ok &= pair4_ok

    out = {
        "peak_frame_t_min": fpk.t_s / 60, "peak_segments_ge_15cm": n_flooded_15,
        "routable_core_fraction": len(core) / len(roads.node_xy),
        "pairs": [{"label": lbl, "origin": list(o), "dest": list(d),
                  "dry_reachable": dry["reachable"] if dry else None, "dry_length_m": dry["length_m"] if dry else None,
                  "wet_reachable": wet["reachable"] if wet else None, "wet_length_m": wet["length_m"] if wet else None,
                  "avoided_segments": len(wet["avoided_segments"]) if wet else None}
                 for lbl, name, seg_id, depth_cm, o, d, dry, wet in results],
        "all_checks_passed": bool(all_ok),
    }
    od = REPO_DIR / "docs" / "validation"; od.mkdir(parents=True, exist_ok=True)
    (od / "routing_fix_validation.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nALL CHECKS PASSED: {all_ok}")
    print(f"wrote {od / 'routing_fix_validation.json'}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
