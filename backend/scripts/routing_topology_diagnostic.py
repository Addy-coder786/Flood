"""P0 routing diagnostic (2026-09-14 audit): inspect the REAL pilot road graph's topology to find the exact
root cause of the demo_check.json routing_probe failure (baseline nx.shortest_path raised NetworkXNoPath for
a genuine in-bbox origin/destination pair -- BEFORE any flood weighting was applied).

Read-only: loads already-processed pilot data (data/processed/pilot/roads.json, 1.3MB), builds the same
directed graph router.py uses, and reports weakly/strongly connected components, isolated nodes, dangling
endpoints, node-index validity, coordinate duplicates, and a highway-tag breakdown. Does not alter any data
or geometry. Writes docs/validation/routing_topology.json.

Run:  cd backend && .venv\\Scripts\\python.exe scripts/routing_topology_diagnostic.py
"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import networkx as nx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from floodnet.config import REPO_DIR
from floodnet.data.load import load_pilot
from floodnet.routing.router import build_graph, _snap


def main():
    p = load_pilot()
    roads = p["roads"]
    print(f"roads.provenance: {roads.provenance.tag} -- {roads.provenance.source}")
    print(f"n_nodes(node_xy)={len(roads.node_xy)}  n_segments={len(roads.segments)}")

    # 1. node-index validity: does every segment's u/v refer to a real node_xy row?
    n_nodes = len(roads.node_xy)
    bad_idx = [s.seg_id for s in roads.segments if not (0 <= s.u < n_nodes and 0 <= s.v < n_nodes)]
    print(f"\n[1] segments with out-of-range u/v node index: {len(bad_idx)}")
    if bad_idx[:5]:
        print("    examples:", bad_idx[:5])

    # 2. coordinate duplicates: distinct node indices sitting at (near-)identical coordinates,
    #    which would indicate a splitting/merge failure in build_road_graph's OSM-node-id keying.
    coords = np.round(roads.node_xy, 1)  # 0.1 m in EPSG:32643 -- generous, catches true duplicates only
    seen: dict[tuple, list[int]] = {}
    for i, (x, y) in enumerate(coords):
        seen.setdefault((float(x), float(y)), []).append(i)
    dup_groups = [v for v in seen.values() if len(v) > 1]
    print(f"\n[2] node indices sharing the same (rounded 0.1m) coordinate: "
          f"{sum(len(v) for v in dup_groups)} nodes in {len(dup_groups)} groups")
    if dup_groups[:5]:
        print("    examples (node indices):", dup_groups[:5])

    # 3. highway tag breakdown -- specifically check for *_link types (ramps/flyover connectors)
    hw_counts = Counter(s.highway for s in roads.segments)
    print(f"\n[3] highway tag breakdown: {dict(hw_counts)}")
    link_types = {k: v for k, v in hw_counts.items() if k.endswith("_link")}
    print(f"    *_link segments present: {link_types if link_types else 'NONE -- excluded by data/osm.py HIGHWAYS filter'}")

    # 4. build the exact directed graph router.py uses, then connectivity stats
    G = build_graph(roads)
    print(f"\n[4] directed graph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")
    wcc = sorted(nx.weakly_connected_components(G), key=len, reverse=True)
    scc = sorted(nx.strongly_connected_components(G), key=len, reverse=True)
    print(f"    weakly connected components: {len(wcc)}; sizes (top 10): {[len(c) for c in wcc[:10]]}")
    print(f"    strongly connected components: {len(scc)}; sizes (top 10): {[len(c) for c in scc[:10]]}")
    isolated = [n for n in G.nodes if G.degree(n) == 0]
    print(f"    isolated nodes (degree 0): {len(isolated)}")
    und = G.to_undirected()
    dangling = [n for n in und.nodes if und.degree(n) == 1]
    print(f"    dangling endpoints (undirected degree 1): {len(dangling)}")

    # 5. which weakly-connected component is each highway type mostly in? (flyover-link hypothesis check)
    comp_of = {}
    for k, comp in enumerate(wcc):
        for n in comp:
            comp_of[n] = k
    seg_home_comp = Counter()
    flyover_segs = [s for s in roads.segments if "flyover" in (s.name or "").lower()]
    print(f"\n[5] segments with 'flyover' in name: {len(flyover_segs)}")
    for s in flyover_segs:
        cu, cv = comp_of.get(s.u), comp_of.get(s.v)
        print(f"    {s.seg_id} '{s.name}' highway={s.highway} u_comp={cu}(size {len(wcc[cu]) if cu is not None else '?'}) "
              f"v_comp={cv}(size {len(wcc[cv]) if cv is not None else '?'})")

    # 6. reproduce the exact failing probe from docs/validation/demo_check.json
    origin = [72.836, 19.0091018]
    dest = [72.8477608, 19.0091018]
    o_idx, d_idx = _snap(roads, [tuple(origin), tuple(dest)])
    o_comp, d_comp = comp_of.get(o_idx), comp_of.get(d_idx)
    print(f"\n[6] probe reproduction: origin snaps to node {o_idx} (component {o_comp}, size "
          f"{len(wcc[o_comp]) if o_comp is not None else '?'}); dest snaps to node {d_idx} (component {d_comp}, "
          f"size {len(wcc[d_comp]) if d_comp is not None else '?'})")
    try:
        path = nx.shortest_path(G, o_idx, d_idx, weight="length_m")
        print(f"    shortest_path SUCCEEDED, {len(path)} nodes")
    except nx.NetworkXNoPath:
        print(f"    shortest_path FAILED: NetworkXNoPath (same component: {o_comp == d_comp})")

    out = {
        "n_nodes": n_nodes, "n_segments": len(roads.segments),
        "bad_node_index_segments": len(bad_idx),
        "coordinate_duplicate_node_count": sum(len(v) for v in dup_groups),
        "coordinate_duplicate_groups": len(dup_groups),
        "highway_tag_counts": dict(hw_counts),
        "link_type_segments_present": link_types,
        "directed_nodes": G.number_of_nodes(), "directed_edges": G.number_of_edges(),
        "weakly_connected_components": len(wcc), "wcc_sizes_top10": [len(c) for c in wcc[:10]],
        "strongly_connected_components": len(scc), "scc_sizes_top10": [len(c) for c in scc[:10]],
        "isolated_nodes": len(isolated), "dangling_endpoints": len(dangling),
        "flyover_segment_count": len(flyover_segs),
        "probe_origin_component": o_comp, "probe_dest_component": d_comp,
        "probe_same_component": o_comp == d_comp if o_comp is not None and d_comp is not None else None,
    }
    od = REPO_DIR / "docs" / "validation"; od.mkdir(parents=True, exist_ok=True)
    (od / "routing_topology.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\nwrote {od / 'routing_topology.json'}")


if __name__ == "__main__":
    main()
