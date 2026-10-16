"""Coupled simulation loop: rainfall -> runoff -> surface routing <-> drainage hydraulics -> frames.

This is the ONE place where components exchange water. Agents implement the protocols in contracts.py;
this loop must not be duplicated elsewhere. Units: seconds, metres, m3.
"""
from __future__ import annotations

import time
import uuid
import numpy as np

from ..config import HORIZON_S, FRAME_DT_S, MAX_SURFACE_DT_S
from ..contracts import (Terrain, DrainageNetwork, RainfallScenario, Frame, MassBalance,
                         SimulationResult, SurfaceModel, DrainageModel)


def run_simulation(terrain: Terrain, net: DrainageNetwork, scenario: RainfallScenario,
                   surface: SurfaceModel, drainage: DrainageModel,
                   runoff_fn, street_fn=None, blockage: dict | None = None,
                   horizon_s: int = HORIZON_S, frame_dt_s: int = FRAME_DT_S,
                   dt_s: float = MAX_SURFACE_DT_S, progress=None) -> SimulationResult:
    """
    runoff_fn(intensity_mm_h, dt_s, terrain) -> [ny,nx] runoff depth (m) generated in this dt  (floodnet.terrain.runoff)
    street_fn(depth_grid, grid) -> {seg_id: depth_m}                                          (floodnet.streets.aggregate)
    """
    t0 = time.time()
    blockage = blockage or {"mode": "none"}
    g = terrain.grid
    # Spatial rainfall (SR-01): if the scenario carries a [T, ny, nx] field it MUST already be on this
    # terrain's grid -- resampling/reprojection is the provider's job (floodnet.rainfall.gridded), not the
    # engine's, so that the engine never silently regrids and hides a CRS/extent mismatch.
    if scenario.is_spatial:
        fg = scenario.field_grid
        if (fg.ny, fg.nx) != (g.ny, g.nx) or abs(fg.res - g.res) > 1e-9 \
                or abs(fg.x0 - g.x0) > 1e-6 or abs(fg.y0 - g.y0) > 1e-6:
            raise ValueError(
                f"spatial rainfall field grid {fg.to_dict()} does not match the terrain grid {g.to_dict()}; "
                "the rainfall provider must resample onto the model grid before the engine sees it")
    nj, ni = net.node_cell_j, net.node_cell_i
    on_grid = g.inside(nj, ni) & ~net.node_is_outfall
    nj_g, ni_g = nj[on_grid], ni[on_grid]

    frames: list[Frame] = []
    rain_in = 0.0
    runoff_in = 0.0
    surcharge_accum = np.zeros(net.n_nodes, dtype=np.float32)
    t = 0.0
    next_frame = 0.0

    def snapshot():
        sd = street_fn(surface.depth, g) if street_fn else {}
        frames.append(Frame(
            t_s=t, depth=surface.depth.astype(np.float32).copy(),
            node_hgl=drainage.hgl().astype(np.float32), node_surcharging=drainage.surcharging().copy(),
            node_surcharge_m3=surcharge_accum.copy(), node_cause=drainage.cause().copy(),
            edge_flow_m3s=drainage.edge_flow_m3s().astype(np.float32), edge_util=drainage.edge_util().astype(np.float32),
            street_depth_m=sd, rain_mm_h=scenario.intensity_at(t)))

    snapshot()
    next_frame += frame_dt_s
    while t < horizon_s - 1e-9:
        step = min(dt_s, next_frame - t)
        # 1. rainfall -> runoff (per cell, metres over this step)
        rain_field = scenario.intensity_field_at(t)                         # [ny,nx] mm/h, or None if uniform
        if rain_field is None:
            # ---- uniform rainfall: ORIGINAL CODE PATH, byte-identical (frozen core) ----
            i_mm_h = scenario.intensity_at(t)
            runoff_depth = runoff_fn(i_mm_h, step, terrain)                 # [ny,nx] m
            rain_in += i_mm_h / 1000.0 / 3600.0 * step * g.cell_area * g.nx * g.ny  # gross rain volume on grid
        else:
            # ---- spatial rainfall: gross volume is the per-cell sum, not intensity x cell count ----
            runoff_depth = runoff_fn(rain_field, step, terrain)             # [ny,nx] m
            rain_in += float(np.sum(rain_field)) / 1000.0 / 3600.0 * step * g.cell_area
        runoff_in += float(np.sum(runoff_depth)) * g.cell_area                     # net runoff (after coefficient losses)
        surface.add_runoff(runoff_depth)
        # 2. surface -> drainage (inlet capture limited by inlet + network capacity)
        cap = drainage.inlet_capacity_m3(step)                               # [N] m3
        taken = np.zeros(net.n_nodes, dtype=np.float64)
        taken[on_grid] = surface.take_volume(nj_g, ni_g, cap[on_grid])
        drainage.add_inflow(taken)
        # 3. drainage hydraulics; surcharge volume comes back to the surface at the node cell
        surch = drainage.step(step)                                          # [N] m3
        if np.any(surch > 0):
            m = on_grid & (surch > 0)
            surface.add_volume(nj[m], ni[m], surch[m])
            surcharge_accum += surch.astype(np.float32)
        # 4. surface routing
        surface.step(step)
        t += step
        if t + 1e-9 >= next_frame:
            snapshot()
            surcharge_accum[:] = 0
            next_frame += frame_dt_s
            if progress: progress(t / horizon_s)

    surf = surface.total_volume_m3(); netv = drainage.stored_m3(); out = drainage.outflow_m3(); inf = surface.infiltrated_m3()
    bout = float(surface.boundary_out_m3()) if hasattr(surface, "boundary_out_m3") else 0.0
    abstraction = rain_in - runoff_in
    err = runoff_in - (surf + netv + out + inf + bout)
    mb = MassBalance(rain_in_m3=rain_in, surface_stored_m3=surf, network_stored_m3=netv, outfall_out_m3=out,
                     infiltration_m3=inf, error_m3=err, error_pct=(100.0 * err / runoff_in) if runoff_in > 0 else 0.0,
                     abstraction_m3=abstraction, runoff_in_m3=runoff_in, boundary_out_m3=bout)
    prov = {"terrain": terrain.provenance.to_dict(), "impervious": terrain.impervious_provenance.to_dict(),
            "buildings": terrain.building_provenance.to_dict(), "rainfall": scenario.provenance.to_dict(),
            "network": net.provenance}
    return SimulationResult(run_id=uuid.uuid4().hex[:10], scenario=scenario, blockage=blockage, grid=g,
                            frames=frames, mass_balance=mb, runtime_s=time.time() - t0, provenance=prov)
