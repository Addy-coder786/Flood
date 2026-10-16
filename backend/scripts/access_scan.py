"""Scan random origin/destination pairs and dispatch times on one simulated run and count how often the
current-status-only route is invalid at arrival (crosses a road at/over the vehicle limit by the time the vehicle
gets there) while the arrival-aware route is not. SIMULATED evidence only: forecast and "truth" are the same run.

Run (from backend/):  .venv/Scripts/python scripts/access_scan.py [scenario] [vehicle] [pairs] [seed]
Writes docs/validation/access_scan_<scenario>_<vehicle>.json
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter
from datetime import datetime, timezone

import numpy as np

from floodnet import config
from floodnet.access.arrival_router import plan_access
from floodnet.access.events import ClosureBook
from floodnet.access.road_status import Forecast, build_context, load_dem_mask
from floodnet.api import state
from floodnet.routing.router import _cached_graph, _routable_core


def main(scenario: str = "cloudburst", vehicle: str = "car", pairs: int = 150, seed: int = 7) -> dict:
    p = state.get_pilot()
    roads = p["roads"]
    res = state.run_scenario(scenario, {"mode": "none"}, 180)
    t =tuple(f.t_s / 60.0 for f in res.frames)
    streets = {sid: [float(f.street_depth_m.get(sid, 0.0)) * 100.0 for f in res.frames]
               for sid in {s for f in res.frames for s in f.street_depth_m}}
    forecast = Forecast(t, streets, label=f"{scenario} run {res.run_id}")
    ctx = build_context(state.street_fn_for(roads, p["terrain"].grid).cells, load_dem_mask())
    G = _cached_graph(roads)
    core = _routable_core(roads)
    rng = random.Random(seed)
    dispatches = [0, 15, 30, 45, 60, 90, 120]
    tally: Counter = Counter()
    examples = []
    for _ in range(pairs):
        o, d = (int(x) for x in rng.sample(list(core), 2))
        for disp in dispatches:
            out = plan_access(roads, G, o, d, vehicle=vehicle, dispatch_t_min=disp, forecast=forecast,
                              seg_ctx=ctx, closures=ClosureBook([], disp))
            a, c, cmp_ = out["arrival_aware"], out["current_status"], out["comparison"]
            tally["cases"] += 1
            tally["same_route"] += cmp_["same_route"]
            tally["arrival_unreachable"] += not a["reachable"]
            tally["current_unreachable"] += not c["reachable"]
            if c["reachable"] and not c["valid_at_arrival"]:
                tally["current_invalid_at_arrival"] += 1
                if a["reachable"]:
                    tally["current_invalid_arrival_ok"] += 1
                    if len(examples) < 10:
                        examples.append({"origin_lonlat": roads.node_lonlat[o].round(6).tolist(),
                                         "dest_lonlat": roads.node_lonlat[d].round(6).tolist(),
                                         "dispatch_t_min": disp, "summary": cmp_["summary"],
                                         "blocked_at_arrival": c["blocked_at_arrival"][:3]})
    report = {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tag": "SIMULATED",
              "scenario": scenario, "run_id": res.run_id, "vehicle": vehicle, "pairs": pairs, "seed": seed,
              "dispatch_t_min": dispatches, "counts": dict(tally), "examples": examples,
              "caveat": "forecast and outcome are the same simulated run; this shows where the two methods "
                        "disagree, not real-world accuracy"}
    out_path = config.REPO_DIR / "docs" / "validation" / f"access_scan_{scenario}_{vehicle}.json"
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("scenario", "vehicle", "counts")}, indent=1))
    print("examples:", [(e["origin_lonlat"], e["dest_lonlat"], e["dispatch_t_min"]) for e in examples[:3]])
    return report


if __name__ == "__main__":
    a = sys.argv[1:]
    main(*(a[:2]), *([int(x) for x in a[2:4]]))
