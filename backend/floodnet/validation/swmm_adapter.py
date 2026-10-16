"""Write a `DrainageNetwork` + `RainfallScenario` as a SWMM 5 .inp file (plain text; OFFLINE cross-check only).

This module is NOT used by the live solver. It exists so that `floodnet.drainage.hydraulics.GraphDrainage` can be
compared against EPA SWMM's dynamic-wave solver on the same network (see swmm_compare.py, docs/validation/SWMM.md).
The file is written by hand as text (no swmm_api object model) so that the mapping below is the whole story.

Mapping (DrainageNetwork -> SWMM)
---------------------------------
[JUNCTIONS]     one per non-outfall node. Name = sanitised node_id (see `sanitise`, mapping returned by
                `name_map`). Elevation = node_invert. MaxDepth = max(node_ground - node_invert, 0.1) (same floor as
                GraphDrainage.MIN_DEPTH_M). InitDepth 0, SurDepth 0, Aponded = node_storage_area_m2 (ALLOW_PONDING YES
                so water that surcharges is stored on that plan area and re-enters, like our surface coupling).
[OUTFALLS]      FREE outfall at node_invert for node_is_outfall nodes -- EXCEPT an outfall with more than
                one incoming conduit (SWMM's OUTFALL object accepts exactly one link; several real MCGM
                outfalls are a confluence of multiple drains reaching one point, e.g. a creek/sea outlet).
                For each such node: it becomes a SWMM JUNCTION (unchanged elevation/depth/storage), and a
                short (1 m) large-bore COLLECTOR conduit carries its combined flow to a new terminal
                OUTFALL node placed 1 m away at the same invert. This is standard SWMM practice for merging
                multiple discharges into one outfall and changes no hydraulic capacity in the real network
                -- the collector is sized so it is never the bottleneck. See `_split_multi_inlet_outfalls`.
[CONDUITS]      from/to = edge_us/edge_ds, Length = edge_length_m (>= 1 m), Roughness = edge_n,
                InOffset = edge_us_invert - node_invert[us] (>= 0), OutOffset = edge_ds_invert - node_invert[ds] (>= 0).
                MaxFlow = edge_capacity_m3s * (1 - edge_blockage) when edge_blockage > 0 (SWMM caps conduit flow at
                MaxFlow; this is the closest available analogue of our cap_eff), else 0 (= no cap).
[XSECTIONS]     CIRC -> CIRCULAR Geom1 = width (diameter). RECT / OREC / ARCH -> RECT_CLOSED Geom1 = height,
                Geom2 = width (ARCH is approximated as a closed rectangle: same width and rise, slightly more area).
[SUBCATCHMENTS] one per junction (ESTIMATED): Area = grid area / n_junctions (ha), Width = sqrt(area m2), %Slope 0.5,
                %Imperv 85, outlet = the junction. SUBAREAS N-Imperv 0.013, N-Perv 0.1, S-Imperv 0.05, S-Perv 0.05,
                PctZero 25. INFILTRATION HORTON 76.2 3.3 4.14 7 0. These runoff parameters are placeholders, NOT
                calibrated; they are labelled ESTIMATED in [TITLE].
[RAINGAGES]     one gage, INTENSITY, interval 0:05, TIMESERIES "rain" = scenario.intensity_mm_h at scenario.t_s.
[INFLOWS]       only when `direct_inflow_c` is given: every junction gets FLOW timeseries "qin" = C * i(t) * A_sub
                (m3/s), i.e. exactly the hydrograph the comparison feeds GraphDrainage, and the subcatchment runoff
                is switched off (IGNORE_RAINFALL YES). This is the "same inflow" cross-check mode.
[OPTIONS]       FLOW_UNITS CMS, FLOW_ROUTING = `routing` (DYNWAVE default), ALLOW_PONDING = `allow_ponding`
                (default NO: with the ESTIMATED Aponded = 1.5 m2 a ponded node reaches tens of metres of head and
                drives conduits far beyond capacity; with NO, SWMM "flooding loss" = our surcharge returned to the
                surface, which is the quantity compared), REPORT_STEP 00:05:00,
                WET_STEP / DRY_STEP 00:05:00, ROUTING_STEP = `routing_step_s` (5; use 1 if the run is unstable),
                MIN_SLOPE 0.001, START 2026-01-01 00:00 to 00:00 + horizon_min.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

import numpy as np

from ..contracts import DrainageNetwork, RainfallScenario

MIN_DEPTH_M = 0.1
MIN_LENGTH_M = 1.0
SUBCATCH_ESTIMATED = {"slope_pct": 0.5, "imperv_pct": 85, "n_imperv": 0.013, "n_perv": 0.1, "s_imperv": 0.05,
                      "s_perv": 0.05, "pct_zero": 25, "horton": (76.2, 3.3, 4.14, 7, 0)}


def sanitise(name: str) -> str:
    """SWMM names: no whitespace/semicolons/brackets. Keep [A-Za-z0-9_.-]; replace the rest with '_'."""
    s = re.sub(r"[^A-Za-z0-9_.\-]", "_", str(name))
    return s or "_"


def name_map(ids) -> dict[str, str]:
    """node_id/edge_id -> unique SWMM name (suffix _2, _3 ... on collisions after sanitising)."""
    out, seen = {}, set()
    for raw in ids:
        base = sanitise(raw)
        cand, k = base, 1
        while cand in seen:
            k += 1
            cand = f"{base}_{k}"
        seen.add(cand)
        out[str(raw)] = cand
    return out


def direct_inflow_hydrograph(scenario: RainfallScenario, area_m2: float, c: float) -> np.ndarray:
    """Rational-method inflow per junction, m3/s at scenario.t_s: Q = C * i[mm/h] / 3.6e6 * A[m2]."""
    return c * np.asarray(scenario.intensity_mm_h, dtype=float) / 3.6e6 * area_m2


def _split_multi_inlet_outfalls(net: DrainageNetwork) -> tuple[np.ndarray, list[dict]]:
    """SWMM allows exactly one link per OUTFALL. Real MCGM outfalls that are a confluence of several
    conduits (multiple upstream drains reaching one discharge point) must be re-expressed as a JUNCTION
    feeding one short, oversized COLLECTOR conduit into a new terminal OUTFALL. Returns
    (is_out_for_swmm[N] bool, collectors: list of {node_idx, name, invert}) -- `net` itself is untouched.
    """
    N = net.n_nodes
    is_out = np.asarray(net.node_is_outfall, dtype=bool).copy()
    indeg = np.zeros(N, dtype=int)
    for d in np.asarray(net.edge_ds, dtype=int):
        indeg[d] += 1
    collectors = []
    for i in range(N):
        if is_out[i] and indeg[i] > 1:
            is_out[i] = False  # becomes a SWMM junction; real outfall status is unchanged in `net`
            collectors.append({"node_idx": i, "invert": float(net.node_invert[i])})
    return is_out, collectors


def _hhmm(minutes: float) -> str:
    m = int(round(minutes))
    return f"{m // 60:02d}:{m % 60:02d}"


def _clock(minutes: float) -> tuple[str, str]:
    """(date, HH:MM:SS) for START + minutes (days roll over)."""
    m = int(round(minutes))
    d, rem = divmod(m, 24 * 60)
    return f"01/{1 + d:02d}/2026", f"{rem // 60:02d}:{rem % 60:02d}:00"


def write_inp(net: DrainageNetwork, scenario: RainfallScenario, out_path: Path | str, horizon_min: int = 180,
              routing: str = "DYNWAVE", catchment_area_ha: float | None = None, routing_step_s: float = 5.0,
              title_extra: str = "", allow_ponding: bool = False, direct_inflow_c: float | None = None) -> Path:
    """Write the .inp (see module docstring). `catchment_area_ha` = total drained area split evenly over junctions;
    default = 100 ha * (n_junctions / 200) is a crude stand-in only when no grid is supplied - pass the pilot grid area."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    N, E = net.n_nodes, net.n_edges
    nm = name_map(net.node_id)
    em = name_map(net.edge_id)
    inv = np.asarray(net.node_invert, dtype=float)
    ground = np.asarray(net.node_ground, dtype=float)
    depth = np.maximum(np.nan_to_num(ground - inv, nan=MIN_DEPTH_M), MIN_DEPTH_M)
    is_out, collectors = _split_multi_inlet_outfalls(net)
    storage = np.maximum(np.nan_to_num(np.asarray(net.node_storage_area_m2, dtype=float), nan=1.0), 0.5)
    junctions = [i for i in range(N) if not is_out[i]]
    if catchment_area_ha is None:
        catchment_area_ha = 100.0 * max(len(junctions), 1) / 200.0
    sub_area_ha = catchment_area_ha / max(len(junctions), 1)
    sub_width = math.sqrt(sub_area_ha * 1e4)
    P = SUBCATCH_ESTIMATED
    d0, t0 = _clock(0)
    d1, t1 = _clock(horizon_min)

    L = []
    w = L.append
    w("[TITLE]")
    w(f"floodnet SWMM cross-check export: scenario={scenario.id} routing={routing} horizon={horizon_min} min")
    w("Network geometry (inverts, shapes, sizes, lengths) = REAL (MCGM). Manning n, node storage area = ESTIMATED.")
    w("SUBCATCHMENTS / SUBAREAS / INFILTRATION parameters are ESTIMATED placeholders (uniform per junction), NOT calibrated.")
    if title_extra:
        w(title_extra)
    w("")
    w("[OPTIONS]")
    for k, v in [("FLOW_UNITS", "CMS"), ("INFILTRATION", "HORTON"), ("FLOW_ROUTING", routing), ("LINK_OFFSETS", "DEPTH"),
                 ("MIN_SLOPE", "0.001"), ("ALLOW_PONDING", "YES" if allow_ponding else "NO"), ("SKIP_STEADY_STATE", "NO"),
                 ("IGNORE_RAINFALL", "YES" if direct_inflow_c is not None else "NO"),
                 ("START_DATE", d0), ("START_TIME", t0), ("REPORT_START_DATE", d0), ("REPORT_START_TIME", t0),
                 ("END_DATE", d1), ("END_TIME", t1), ("SWEEP_START", "01/01"), ("SWEEP_END", "12/31"), ("DRY_DAYS", "0"),
                 ("REPORT_STEP", "00:05:00"), ("WET_STEP", "00:05:00"), ("DRY_STEP", "00:05:00"),
                 ("ROUTING_STEP", f"{routing_step_s:g}"), ("RULE_STEP", "00:00:00"),
                 ("INERTIAL_DAMPING", "PARTIAL"), ("NORMAL_FLOW_LIMITED", "BOTH"), ("FORCE_MAIN_EQUATION", "H-W"),
                 ("VARIABLE_STEP", "0.75"), ("LENGTHENING_STEP", "0"), ("MIN_SURFAREA", "1.167"),
                 ("MAX_TRIALS", "8"), ("HEAD_TOLERANCE", "0.0015"), ("SYS_FLOW_TOL", "5"), ("LAT_FLOW_TOL", "5"),
                 ("MINIMUM_STEP", "0.5"), ("THREADS", "1")]:
        w(f"{k:<20} {v}")
    w("")
    w("[EVAPORATION]")
    w("CONSTANT 0.0")
    w("DRY_ONLY NO")
    w("")
    w("[RAINGAGES]")
    w(";;Name  Format    Interval SCF  Source")
    w("RG1     INTENSITY 0:05     1.0  TIMESERIES rain")
    w("")
    w("[SUBCATCHMENTS]")
    w(";;Name  Raingage Outlet Area(ha) %Imperv Width %Slope CurbLen")
    for i in junctions:
        w(f"S_{nm[str(net.node_id[i])]} RG1 {nm[str(net.node_id[i])]} {sub_area_ha:.5f} {P['imperv_pct']} {sub_width:.2f} {P['slope_pct']} 0")
    w("")
    w("[SUBAREAS]")
    w(";;Subcatchment N-Imperv N-Perv S-Imperv S-Perv PctZero RouteTo")
    for i in junctions:
        w(f"S_{nm[str(net.node_id[i])]} {P['n_imperv']} {P['n_perv']} {P['s_imperv']} {P['s_perv']} {P['pct_zero']} OUTLET")
    w("")
    w("[INFILTRATION]")
    w(";;Subcatchment MaxRate MinRate Decay DryTime MaxInfil")
    h = " ".join(str(x) for x in P["horton"])
    for i in junctions:
        w(f"S_{nm[str(net.node_id[i])]} {h}")
    w("")
    w("[JUNCTIONS]")
    w(";;Name Elevation MaxDepth InitDepth SurDepth Aponded")
    for i in junctions:
        w(f"{nm[str(net.node_id[i])]} {inv[i]:.3f} {depth[i]:.3f} 0 0 {storage[i]:.2f}")
    w("")
    w("[OUTFALLS]")
    w(";;Name Elevation Type")
    for i in range(N):
        if is_out[i]:
            w(f"{nm[str(net.node_id[i])]} {inv[i]:.3f} FREE NO")
    collector_out_name = {}
    for c in collectors:
        out_name = f"{nm[str(net.node_id[c['node_idx']])]}_OUT"
        collector_out_name[c["node_idx"]] = out_name
        w(f"{out_name} {c['invert']:.3f} FREE NO")
    w("")
    us = np.asarray(net.edge_us, dtype=int)
    ds = np.asarray(net.edge_ds, dtype=int)
    length = np.maximum(np.nan_to_num(np.asarray(net.edge_length_m, dtype=float), nan=MIN_LENGTH_M), MIN_LENGTH_M)
    nval = np.nan_to_num(np.asarray(net.edge_n, dtype=float), nan=0.013)
    in_off = np.maximum(np.nan_to_num(np.asarray(net.edge_us_invert, dtype=float) - inv[us], nan=0.0), 0.0)
    out_off = np.maximum(np.nan_to_num(np.asarray(net.edge_ds_invert, dtype=float) - inv[ds], nan=0.0), 0.0)
    block = np.clip(np.nan_to_num(np.asarray(net.edge_blockage, dtype=float), nan=0.0), 0.0, 1.0)
    cap_eff = np.maximum(np.nan_to_num(np.asarray(net.edge_capacity_m3s, dtype=float), nan=0.0), 0.0) * (1.0 - block)
    width = np.maximum(np.nan_to_num(np.asarray(net.edge_width_m, dtype=float), nan=0.3), 0.05)
    height = np.maximum(np.nan_to_num(np.asarray(net.edge_height_m, dtype=float), nan=0.3), 0.05)
    w("[CONDUITS]")
    w(";;Name From To Length Roughness InOffset OutOffset InitFlow MaxFlow")
    for e in range(E):
        maxflow = f"{cap_eff[e]:.4f}" if block[e] > 0 else "0"
        w(f"{em[str(net.edge_id[e])]} {nm[str(net.node_id[us[e]])]} {nm[str(net.node_id[ds[e]])]} {length[e]:.2f} "
          f"{nval[e]:.4f} {in_off[e]:.3f} {out_off[e]:.3f} 0 {maxflow}")
    for c in collectors:
        jn = nm[str(net.node_id[c["node_idx"]])]
        w(f"{jn}_COLLECT {jn} {collector_out_name[c['node_idx']]} 1.00 0.0100 0 0 0 0")
    w("")
    w("[XSECTIONS]")
    w(";;Link Shape Geom1 Geom2 Geom3 Geom4 Barrels")
    for e in range(E):
        shp = str(net.edge_shape[e]).upper()
        if shp == "CIRC":
            w(f"{em[str(net.edge_id[e])]} CIRCULAR {width[e]:.3f} 0 0 0 1")
        else:  # RECT / OREC / ARCH -> closed rectangle
            w(f"{em[str(net.edge_id[e])]} RECT_CLOSED {height[e]:.3f} {width[e]:.3f} 0 0 1")
    for c in collectors:
        jn = nm[str(net.node_id[c["node_idx"]])]
        w(f"{jn}_COLLECT CIRCULAR 5.000 0 0 0 1")  # oversized: never the bottleneck, purely a confluence link
    w("")
    w("[TIMESERIES]")
    w(";;Name Date Time Value(mm/h)")
    t_min = np.asarray(scenario.t_s, dtype=float) / 60.0
    for tm, val in zip(t_min, np.asarray(scenario.intensity_mm_h, dtype=float)):
        if tm > horizon_min:
            break
        w(f"rain {_hhmm(tm)} {val:.3f}")
    if len(t_min) == 0 or t_min[-1] < horizon_min:
        w(f"rain {_hhmm(horizon_min)} 0.000")
    if direct_inflow_c is not None:
        q = direct_inflow_hydrograph(scenario, sub_area_ha * 1e4, direct_inflow_c)
        for tm, val in zip(t_min, q):
            if tm > horizon_min:
                break
            w(f"qin {_hhmm(tm)} {val:.6f}")
        if len(t_min) == 0 or t_min[-1] < horizon_min:
            w(f"qin {_hhmm(horizon_min)} 0.000000")
    w("")
    if direct_inflow_c is not None:
        w("[INFLOWS]")
        w(";;Node Constituent TimeSeries Type Mfactor Sfactor")
        for i in junctions:
            w(f"{nm[str(net.node_id[i])]} FLOW qin FLOW 1.0 1.0")
        w("")
    w("[REPORT]")
    w("INPUT NO")
    w("CONTROLS NO")
    w("SUBCATCHMENTS NONE")
    w("NODES NONE")
    w("LINKS NONE")
    w("")
    w("[COORDINATES]")
    for i in range(N):
        w(f"{nm[str(net.node_id[i])]} {float(net.node_x[i]):.2f} {float(net.node_y[i]):.2f}")
    for c in collectors:
        i = c["node_idx"]
        w(f"{collector_out_name[i]} {float(net.node_x[i]) + 1.0:.2f} {float(net.node_y[i]) + 1.0:.2f}")
    w("")
    out_path.write_text("\n".join(L) + "\n", encoding="ascii")
    return out_path


# --------------------------------------------------------------------------- structural validator
_SECTION = re.compile(r"^\[(\w+)\]\s*$")


def parse_sections(text: str) -> dict[str, list[list[str]]]:
    """Section name -> list of token rows (comments stripped)."""
    out: dict[str, list[list[str]]] = {}
    cur = None
    for line in text.splitlines():
        line = line.split(";", 1)[0].strip()
        if not line:
            continue
        m = _SECTION.match(line)
        if m:
            cur = m.group(1).upper()
            out.setdefault(cur, [])
            continue
        if cur is not None:
            out[cur].append(line.split())
    return out


def validate_inp(path: Path | str) -> list[str]:
    """Return a list of structural problems (empty = OK): unique names, every node/link/subcatchment ref exists,
    offsets >= 0, positive lengths, every conduit has an XSECTION, rain gage + timeseries referenced exist."""
    sec = parse_sections(Path(path).read_text(encoding="utf-8"))
    problems = []
    junc = [r[0] for r in sec.get("JUNCTIONS", [])]
    outf = [r[0] for r in sec.get("OUTFALLS", [])]
    nodes = junc + outf
    if len(set(nodes)) != len(nodes):
        problems.append("duplicate node names")
    if not outf:
        problems.append("no outfalls")
    nodeset = set(nodes)
    conds = sec.get("CONDUITS", [])
    links = [r[0] for r in conds]
    if len(set(links)) != len(links):
        problems.append("duplicate conduit names")
    for r in conds:
        name, a, b = r[0], r[1], r[2]
        if a not in nodeset:
            problems.append(f"conduit {name}: from-node {a} missing")
        if b not in nodeset:
            problems.append(f"conduit {name}: to-node {b} missing")
        if float(r[3]) <= 0:
            problems.append(f"conduit {name}: non-positive length")
        if float(r[5]) < 0 or float(r[6]) < 0:
            problems.append(f"conduit {name}: negative offset")
    xs = {r[0] for r in sec.get("XSECTIONS", [])}
    for name in links:
        if name not in xs:
            problems.append(f"conduit {name}: no XSECTION")
    for r in sec.get("XSECTIONS", []):
        if float(r[2]) <= 0:
            problems.append(f"xsection {r[0]}: Geom1 <= 0")
    gages = {r[0] for r in sec.get("RAINGAGES", [])}
    ts = {r[0] for r in sec.get("TIMESERIES", [])}
    for r in sec.get("RAINGAGES", []):
        if r[4].upper() == "TIMESERIES" and r[5] not in ts:
            problems.append(f"raingage {r[0]}: timeseries {r[5]} missing")
    subs = sec.get("SUBCATCHMENTS", [])
    subnames = [r[0] for r in subs]
    if len(set(subnames)) != len(subnames):
        problems.append("duplicate subcatchment names")
    for r in subs:
        if r[1] not in gages:
            problems.append(f"subcatchment {r[0]}: raingage {r[1]} missing")
        if r[2] not in nodeset:
            problems.append(f"subcatchment {r[0]}: outlet {r[2]} missing")
        if float(r[3]) <= 0:
            problems.append(f"subcatchment {r[0]}: area <= 0")
    subset = set(subnames)
    for key in ("SUBAREAS", "INFILTRATION"):
        for r in sec.get(key, []):
            if r[0] not in subset:
                problems.append(f"{key} {r[0]}: unknown subcatchment")
    for r in sec.get("JUNCTIONS", []):
        if float(r[2]) <= 0:
            problems.append(f"junction {r[0]}: MaxDepth <= 0")
    return problems
