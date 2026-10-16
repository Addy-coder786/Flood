"""OFFLINE cross-check of GraphDrainage against EPA SWMM 5 (pyswmm) on the real pilot network.

    backend/.venv/Scripts/python -m floodnet.validation.swmm_compare [--scenario heavy] [--horizon-min 180]

Method (see docs/validation/SWMM.md):
* Both models get the SAME per-junction inflow hydrograph Q_i(t) = C * i(t) * A_sub, with C = 0.85 and A_sub = pilot
  grid area / n_junctions. This BYPASSES the 2D surface entirely (no inlet capacity, no ponding, no re-entry); the
  comparison is purely network hydraulics. SWMM receives the hydrograph through [INFLOWS] with IGNORE_RAINFALL YES.
* A second, standalone `heavy.inp` with SWMM's own subcatchment runoff (ESTIMATED parameters) is written for reference
  and also run, so the reader can see how much the runoff assumption alone moves the answer.
* Surcharge in GraphDrainage (volume returned to the surface) is compared with SWMM node flooding (ALLOW_PONDING NO).

Never fabricates numbers: if pyswmm is missing or the simulation fails, comparison.json and SWMM.md carry
"EXECUTION UNAVAILABLE: <error>" and only the .inp files are produced.
"""
from __future__ import annotations

import argparse
import json
import time
import traceback
from pathlib import Path

import numpy as np

from ..config import DATA_PROCESSED, HORIZON_S
from ..contracts import DrainageNetwork, RainfallScenario
from ..data.load import load_pilot
from ..drainage.hydraulics import GraphDrainage
from .swmm_adapter import write_inp, validate_inp, name_map, direct_inflow_hydrograph

RUNOFF_C = 0.85
OUR_DT_S = 5.0
SWMM_ADVANCE_S = 30.0
OUT_DIR = DATA_PROCESSED / "swmm"
DOC = Path(__file__).resolve().parents[3] / "docs" / "validation" / "SWMM.md"


# --------------------------------------------------------------------------- our model
def run_graph_drainage(net: DrainageNetwork, scenario: RainfallScenario, sub_area_m2: float, horizon_s: float,
                       dt: float = OUR_DT_S) -> dict:
    """Drive GraphDrainage with Q = C*i*A per junction; surcharge is discarded (never re-enters)."""
    model = GraphDrainage(net)
    junction = ~np.asarray(net.node_is_outfall, dtype=bool)
    q_series = direct_inflow_hydrograph(scenario, sub_area_m2, RUNOFF_C)
    max_depth = np.zeros(net.n_nodes)
    flood = np.zeros(net.n_nodes)
    max_q = np.zeros(net.n_edges)
    inflow = 0.0
    t = 0.0
    t0 = time.time()
    while t < horizon_s:
        k = int(np.searchsorted(scenario.t_s, t, side="right") - 1)
        q = float(q_series[k]) if 0 <= k < len(q_series) else 0.0
        vol = np.where(junction, q * dt, 0.0)
        inflow += vol.sum()
        model.add_inflow(vol)
        flood += model.step(dt)
        max_depth = np.maximum(max_depth, model.hgl() - model.invert)
        max_q = np.maximum(max_q, np.abs(model.edge_flow_m3s()))
        t += dt
    return {"max_depth": max_depth, "flood_m3": flood, "max_q": max_q, "outflow_m3": model.outflow_m3(),
            "inflow_m3": inflow, "stored_m3": model.stored_m3(), "runtime_s": time.time() - t0}


# --------------------------------------------------------------------------- swmm
def run_swmm(inp: Path, net: DrainageNetwork, advance_s: float = SWMM_ADVANCE_S) -> dict:
    """Run pyswmm; per-node max depth and flooding volume (integrated node.flooding * dt, cross-checked against
    node.statistics['flooding_volume']), per-link max |flow|, total outfall volume. Raises on failure."""
    from pyswmm import Simulation, Nodes, Links
    nm = name_map(net.node_id)
    em = name_map(net.edge_id)
    node_names = [nm[str(i)] for i in net.node_id]
    link_names = [em[str(i)] for i in net.edge_id]
    is_out = np.asarray(net.node_is_outfall, dtype=bool)
    N, E = net.n_nodes, net.n_edges
    max_depth = np.zeros(N)
    flood = np.zeros(N)
    max_q = np.zeros(E)
    outflow = np.zeros(N)
    t0 = time.time()
    with Simulation(str(inp)) as sim:
        nodes = Nodes(sim)
        links = Links(sim)
        nobj = [nodes[n] for n in node_names]
        lobj = [links[l] for l in link_names]
        sim.step_advance(int(advance_s))
        prev = sim.start_time
        for _ in sim:
            dt = (sim.current_time - prev).total_seconds()
            prev = sim.current_time
            for i, n in enumerate(nobj):
                d = n.depth
                if d > max_depth[i]:
                    max_depth[i] = d
                if is_out[i]:
                    outflow[i] += n.total_inflow * dt
                else:
                    flood[i] += n.flooding * dt
            for e, l in enumerate(lobj):
                q = abs(l.flow)
                if q > max_q[e]:
                    max_q[e] = q
        stats_flood = np.zeros(N)
        stats_out = np.zeros(N)
        for i, n in enumerate(nobj):
            try:
                if is_out[i]:
                    stats_out[i] = float(n.outfall_statistics.get("total_volume", 0.0))
                else:
                    stats_flood[i] = float(n.statistics.get("flooding_volume", 0.0))
            except Exception:
                pass
        for e, l in enumerate(lobj):
            try:
                max_q[e] = max(max_q[e], abs(float(l.conduit_statistics.get("peak_flow", 0.0))))
            except Exception:
                pass
        err = sim.flow_routing_error
    return {"max_depth": max_depth, "flood_m3": flood, "flood_m3_stats": stats_flood, "max_q": max_q,
            "outflow_m3": float(outflow.sum()), "outflow_m3_stats": float(stats_out.sum()),
            "routing_error_pct": float(err), "runtime_s": time.time() - t0}


# --------------------------------------------------------------------------- metrics
def compare(ours: dict, swmm: dict, net: DrainageNetwork, flood_tol_m3: float = 1.0) -> dict:
    from scipy.stats import spearmanr
    junction = ~np.asarray(net.node_is_outfall, dtype=bool)
    a = set(np.flatnonzero(junction & (ours["flood_m3"] > flood_tol_m3)).tolist())
    b = set(np.flatnonzero(junction & (swmm["flood_m3"] > flood_tol_m3)).tolist())
    inter, union = len(a & b), len(a | b)
    d_ours, d_swmm = ours["max_depth"][junction], swmm["max_depth"][junction]
    rho, p = spearmanr(d_ours, d_swmm)
    # depth-ratio on nodes wetted in both
    both = (d_ours > 0.01) & (d_swmm > 0.01)
    rho_q, _ = spearmanr(ours["max_q"], swmm["max_q"]) if net.n_edges > 2 else (np.nan, np.nan)
    ids = np.asarray(net.node_id)
    return {
        "n_junctions": int(junction.sum()),
        "surcharging_nodes_ours": len(a), "surcharging_nodes_swmm": len(b),
        "surcharging_intersection": inter, "surcharging_union": union,
        "surcharging_jaccard": (inter / union) if union else None,
        "surcharging_flood_tol_m3": flood_tol_m3,
        "spearman_node_peak_depth": None if np.isnan(rho) else float(rho),
        "spearman_node_peak_depth_p": None if np.isnan(p) else float(p),
        "spearman_edge_peak_flow": None if np.isnan(rho_q) else float(rho_q),
        "n_nodes_wet_in_both": int(both.sum()),
        "median_depth_ratio_ours_over_swmm_wet_both": float(np.median(d_ours[both] / d_swmm[both])) if both.any() else None,
        "flooded_volume_ours_m3": float(ours["flood_m3"].sum()),
        "flooded_volume_swmm_m3": float(swmm["flood_m3"].sum()),
        "flooded_volume_ratio_ours_over_swmm": (float(ours["flood_m3"].sum() / swmm["flood_m3"].sum())
                                                if swmm["flood_m3"].sum() > 0 else None),
        "outfall_volume_ours_m3": float(ours["outflow_m3"]),
        "outfall_volume_swmm_m3": float(swmm["outflow_m3"]),
        "outfall_volume_ratio_ours_over_swmm": (float(ours["outflow_m3"] / swmm["outflow_m3"])
                                                if swmm["outflow_m3"] > 0 else None),
        "top10_flood_nodes_ours": [str(ids[i]) for i in np.argsort(-ours["flood_m3"])[:10]],
        "top10_flood_nodes_swmm": [str(ids[i]) for i in np.argsort(-swmm["flood_m3"])[:10]],
    }


def _fmt(x, nd=3):
    if x is None:
        return "n/a"
    return f"{x:.{nd}f}" if isinstance(x, float) else str(x)


def write_doc(report: dict, path: Path = DOC) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    L = ["# SWMM cross-check (offline)", "",
         f"Generated by `floodnet.validation.swmm_compare` on {report['generated']}. "
         "SWMM is an OFFLINE cross-check only; the live solver (`floodnet.drainage.hydraulics.GraphDrainage`) is untouched.", "",
         "## Setup", "",
         f"* Network: real pilot (`data/processed/pilot/network.json`), {report['n_nodes']} nodes "
         f"({report['n_outfalls']} inferred outfalls), {report['n_edges']} conduits. Scenario `{report['scenario']}` "
         f"(peak {report['scenario_peak_mm_h']:.0f} mm/h, {report['scenario_total_mm']:.1f} mm), horizon {report['horizon_min']} min.",
         f"* Files: `data/processed/pilot/swmm/{report['scenario']}.inp` (standalone: SWMM subcatchment runoff, ESTIMATED "
         f"parameters) and `{report['scenario']}_direct.inp` (same-inflow mode used for the comparison). Adapter mapping is "
         "documented in `backend/floodnet/validation/swmm_adapter.py`.",
         f"* Inflow for the comparison: identical per-junction hydrograph Q = C i A with C = {RUNOFF_C}, "
         f"A = {report['sub_area_ha']:.3f} ha (pilot grid area {report['grid_area_ha']:.1f} ha / {report['n_junctions']} junctions). "
         "This BYPASSES the 2D surface: no inlet-capacity limit, no ponding, no re-entry of surcharged water. "
         "It is a pure network-hydraulics comparison, not an end-to-end one.",
         "* SWMM: DYNWAVE, ALLOW_PONDING NO (so SWMM 'flooding' = water leaving the network = our 'surcharge returned "
         f"to surface'), ROUTING_STEP {report['routing_step_s']} s. Ours: dt = {OUR_DT_S} s.", ""]
    if report.get("execution_unavailable"):
        L += ["## Result", "", f"**EXECUTION UNAVAILABLE: {report['execution_unavailable']}**", "",
              "The .inp files were written and pass the structural validator; no comparison numbers exist."]
        path.write_text("\n".join(L) + "\n", encoding="utf-8")
        return
    c = report["comparison"]
    s = report["swmm_direct"]
    o = report["ours"]
    L += ["## Headline (same-inflow mode)", "",
          "| Metric | GraphDrainage | SWMM DYNWAVE | Ratio / score |", "|---|---|---|---|",
          f"| Surcharging junctions (> {c['surcharging_flood_tol_m3']} m3 flooded) | {c['surcharging_nodes_ours']} | {c['surcharging_nodes_swmm']} | "
          f"Jaccard {_fmt(c['surcharging_jaccard'])} ({c['surcharging_intersection']} shared / {c['surcharging_union']} union) |",
          f"| Node peak depth (rank agreement) | - | - | Spearman rho {_fmt(c['spearman_node_peak_depth'])} (p {_fmt(c['spearman_node_peak_depth_p'], 3)}) |",
          f"| Edge peak flow (rank agreement) | - | - | Spearman rho {_fmt(c['spearman_edge_peak_flow'])} |",
          f"| Flooded volume (m3) | {c['flooded_volume_ours_m3']:.0f} | {c['flooded_volume_swmm_m3']:.0f} | {_fmt(c['flooded_volume_ratio_ours_over_swmm'], 2)} |",
          f"| Outfall volume (m3) | {c['outfall_volume_ours_m3']:.0f} | {c['outfall_volume_swmm_m3']:.0f} | {_fmt(c['outfall_volume_ratio_ours_over_swmm'], 2)} |",
          f"| Inflow delivered (m3) | {o['inflow_m3']:.0f} | {s['inflow_m3']:.0f} | - |",
          f"| Median peak-depth ratio on {c['n_nodes_wet_in_both']} nodes wet in both | - | - | {_fmt(c['median_depth_ratio_ours_over_swmm_wet_both'], 2)} |",
          f"| Runtime (s) | {o['runtime_s']:.1f} | {s['runtime_s']:.1f} (pyswmm, 30 s polling) | - |",
          f"| SWMM flow-routing continuity error | - | {s['routing_error_pct']:.2f} % | - |", "",
          f"Top-10 flooded nodes, ours: {', '.join(c['top10_flood_nodes_ours'])}", "",
          f"Top-10 flooded nodes, SWMM: {', '.join(c['top10_flood_nodes_swmm'])}", ""]
    if report.get("swmm_standalone"):
        st = report["swmm_standalone"]
        L += ["## Standalone SWMM (its own subcatchment runoff, ESTIMATED parameters)", "",
              "| Metric | value |", "|---|---|",
              f"| Flooded volume (m3) | {st['flood_m3_total']:.0f} |", f"| Outfall volume (m3) | {st['outflow_m3']:.0f} |",
              f"| Surcharging junctions | {st['n_flooding_nodes']} |", f"| Routing continuity error | {st['routing_error_pct']:.2f} % |", ""]
    elif report.get("swmm_standalone_error"):
        L += ["## Standalone SWMM", "", f"EXECUTION UNAVAILABLE: {report['swmm_standalone_error']}", ""]
    L += ["## Interpretation (honest)", "",
          "* GraphDrainage is a storage-node / capacity-edge scheme (no momentum, no backwater, no reverse flow, capped at "
          "full-bore). SWMM DYNWAVE solves the Saint-Venant equations and lets conduits carry more than full-bore under "
          "surcharge head, and lets water flow backwards. Differences are therefore expected in the *amount* of flooding; "
          "what we are checking is whether the *where* (which nodes, in which order) agrees.",
          "* Jaccard and Spearman above are the location / ranking agreement. A Jaccard near 1 and rho near 1 would mean the "
          "two models point to the same hotspots; values well below that mean the simplified scheme should not be read as a "
          "quantitative substitute for a hydrodynamic model on this network.",
          "* Volume ratios > 1 mean GraphDrainage floods more than SWMM (it cannot exceed full-bore capacity, so it spills "
          "earlier); < 1 means less.",
          "* Both runs share the same ESTIMATED inputs: Manning n = 0.013, node storage area 1.5 m2, inferred outfalls "
          f"({report['n_outfalls']} graph sinks treated as free outfalls, which is optimistic for tide-locked outfalls), "
          "uniform subcatchment areas. None of this is calibrated against observed flooding; this document compares two "
          "models, not a model against reality.",
          "* The pilot inflow (uniform area per junction) is a crude proxy for the true catchment split; flooded-volume "
          "totals should be read as order-of-magnitude only.", ""]
    path.write_text("\n".join(L) + "\n", encoding="utf-8")


def _json_safe(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, np.ndarray):
            continue
        if isinstance(v, (np.floating, np.integer)):
            v = v.item()
        out[k] = v
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", default="heavy")
    ap.add_argument("--horizon-min", type=int, default=HORIZON_S // 60)
    ap.add_argument("--routing-step", type=float, default=5.0)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)

    pilot = load_pilot()
    net: DrainageNetwork = pilot["net"]
    scenario: RainfallScenario = pilot["scenarios"][args.scenario]
    grid = pilot["terrain"].grid
    grid_area_ha = grid.nx * grid.ny * grid.cell_area / 1e4
    n_junc = int((~np.asarray(net.node_is_outfall, dtype=bool)).sum())
    sub_area_ha = grid_area_ha / n_junc
    args.out_dir.mkdir(parents=True, exist_ok=True)

    inp_standalone = write_inp(net, scenario, args.out_dir / f"{args.scenario}.inp", horizon_min=args.horizon_min,
                               catchment_area_ha=grid_area_ha, routing_step_s=args.routing_step)
    inp_direct = write_inp(net, scenario, args.out_dir / f"{args.scenario}_direct.inp", horizon_min=args.horizon_min,
                           catchment_area_ha=grid_area_ha, routing_step_s=args.routing_step, direct_inflow_c=RUNOFF_C,
                           title_extra=f"SAME-INFLOW MODE: [INFLOWS] Q = {RUNOFF_C} * i * A per junction, IGNORE_RAINFALL YES")
    for p in (inp_standalone, inp_direct):
        probs = validate_inp(p)
        if probs:
            raise SystemExit(f"{p}: structural problems: {probs[:10]}")
    print(f"wrote {inp_standalone} and {inp_direct} (valid)")

    report = {"generated": time.strftime("%Y-%m-%d %H:%M"), "scenario": args.scenario,
              "scenario_peak_mm_h": float(np.max(scenario.intensity_mm_h)), "scenario_total_mm": scenario.to_dict()["total_mm"],
              "horizon_min": args.horizon_min, "routing_step_s": args.routing_step, "runoff_c": RUNOFF_C,
              "n_nodes": net.n_nodes, "n_edges": net.n_edges, "n_outfalls": int(net.node_is_outfall.sum()),
              "n_junctions": n_junc, "grid_area_ha": grid_area_ha, "sub_area_ha": sub_area_ha,
              "inp_standalone": str(inp_standalone), "inp_direct": str(inp_direct)}

    ours = run_graph_drainage(net, scenario, sub_area_ha * 1e4, args.horizon_min * 60.0)
    report["ours"] = _json_safe(ours)
    print(f"ours: flooded {ours['flood_m3'].sum():.0f} m3, outfall {ours['outflow_m3']:.0f} m3, {ours['runtime_s']:.1f} s")

    try:
        swmm = run_swmm(inp_direct, net)
        swmm["inflow_m3"] = float(ours["inflow_m3"])  # identical hydrograph by construction
        report["swmm_direct"] = _json_safe(swmm)
        report["comparison"] = compare(ours, swmm, net)
        print(f"swmm(direct): flooded {swmm['flood_m3'].sum():.0f} m3 (stats {swmm['flood_m3_stats'].sum():.0f}), "
              f"outfall {swmm['outflow_m3']:.0f} m3 (stats {swmm['outflow_m3_stats']:.0f}), err {swmm['routing_error_pct']:.2f} %, {swmm['runtime_s']:.1f} s")
        print(json.dumps(report["comparison"], indent=1))
    except Exception as exc:  # noqa: BLE001
        report["execution_unavailable"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        print("EXECUTION UNAVAILABLE:", report["execution_unavailable"])
    if "execution_unavailable" not in report:
        try:
            st = run_swmm(inp_standalone, net)
            junction = ~np.asarray(net.node_is_outfall, dtype=bool)
            report["swmm_standalone"] = {"flood_m3_total": float(st["flood_m3"].sum()), "outflow_m3": st["outflow_m3"],
                                         "n_flooding_nodes": int((junction & (st["flood_m3"] > 1.0)).sum()),
                                         "routing_error_pct": st["routing_error_pct"], "runtime_s": st["runtime_s"]}
        except Exception as exc:  # noqa: BLE001
            report["swmm_standalone_error"] = f"{type(exc).__name__}: {exc}"

    # per-node table for later inspection
    ids = [str(i) for i in net.node_id]
    report["per_node"] = {"node_id": ids, "ours_max_depth_m": ours["max_depth"].round(4).tolist(),
                          "ours_flood_m3": ours["flood_m3"].round(2).tolist()}
    if "swmm_direct" in report:
        report["per_node"]["swmm_max_depth_m"] = swmm["max_depth"].round(4).tolist()
        report["per_node"]["swmm_flood_m3"] = swmm["flood_m3"].round(2).tolist()
    (args.out_dir / "comparison.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    write_doc(report)
    print(f"wrote {args.out_dir / 'comparison.json'} and {DOC}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
