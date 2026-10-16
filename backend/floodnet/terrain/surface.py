"""2D surface water: simplified storage-cell (diffusive-wave) scheme on the Terrain grid.

Physics (explainable, mass-conserving, fully vectorised):
  * Each cell stores depth h (m); water surface H = z + h.
  * For each of the 4 neighbour faces the unit-width flux is a Manning-type resistance law
        q = (1/n) * h_eff^(5/3) * sqrt(|dH| / dx)          [m2/s]
    with h_eff = depth in the upstream (higher-H) cell above the higher of the two bed elevations
    (LISFLOOD-FP style "flow depth", Bates & De Roo 2000).
  * Face volume per sub-step V = q * res * dt is limited so that (a) the total outflow of a cell never exceeds
    its stored volume and (b) a face never moves more than 1/8 of the head-difference volume |dH|*A
    (a cell has 4 faces and each face touches 2 cells -> no level inversion / checkerboard; flow limiter in
    the spirit of Hunter et al. 2005).
  * Adaptive sub-stepping inside step(): dt_sub <= 0.5 * res / max(velocity), velocity = q / h_eff,
    with a floor DT_MIN_S to bound the work.
  * Buildings are no-flow obstacles (all faces touching a building cell are masked).
  * Boundary: CLOSED for the MVP (no water leaves the grid edge). Water reaching the edge accumulates there;
    a documented simplification — the pilot area drains through the MCGM network / outfalls, not overland exits.
  * Depths below H_MIN are treated as dry for flux purposes (state is never truncated, so mass is conserved).
  * Infiltration: optional constant rate on the pervious fraction (default 0 for the MVP); accumulated in
    infiltrated_m3().
"""
from __future__ import annotations

import numpy as np

from ..contracts import Terrain
from ..provenance import Provenance, Tag

MANNING_N = 0.03      # urban street / mixed surface roughness
H_MIN = 1e-6          # m, dry threshold for flux computation
DT_MIN_S = 0.05       # s, floor for the adaptive sub-step
CFL = 0.5
HEAD_CAP = 1.0 / 8.0  # max fraction of the head-difference volume a face may move per sub-step (4 faces x 2 cells)

PROVENANCE = Provenance(
    Tag.ESTIMATED,
    "Manning n for overland flow on urban streets (Chow 1959 Table 5-6: asphalt/concrete 0.011-0.016; "
    "Engman 1986 / HEC-RAS 2D urban-mixed 0.02-0.05)",
    f"n={MANNING_N} single value for the whole grid: streets plus kerbs, parked vehicles, debris. "
    "Storage-cell diffusive-wave scheme after Bates & De Roo (2000), flow limiter after Hunter et al. (2005).",
)


BND_MIN_SLOPE = 1e-3   # floor on the outward friction slope at an open boundary (ESTIMATED)


class StorageCellSurface:
    """Implements floodnet.contracts.SurfaceModel."""

    def __init__(self, terrain: Terrain, n: float = MANNING_N, infiltration_rate_mm_h: float = 0.0,
                 dt_min_s: float = DT_MIN_S, cfl: float = CFL, open_boundary=None):
        self.terrain = terrain
        self.grid = terrain.grid
        self.res = float(self.grid.res)
        self.area = float(self.grid.cell_area)
        self.n = float(n)
        self.dt_min = float(dt_min_s)
        self.cfl = float(cfl)
        self.z = np.asarray(terrain.z, dtype=np.float64)
        self.building = np.asarray(terrain.building, dtype=bool)
        self.open = ~self.building
        self.imp = np.clip(np.asarray(terrain.impervious, dtype=np.float64), 0.0, 1.0)
        self.depth = np.zeros(self.z.shape, dtype=np.float64)      # live state, metres
        # infiltration: m/s on the pervious fraction of open cells
        self.infil_rate = float(infiltration_rate_mm_h) / 1000.0 / 3600.0
        self._infil_depth_rate = self.infil_rate * (1.0 - self.imp) * self.open
        self._infiltrated = 0.0
        # Open (free-outfall) boundary. The pilot is a clip out of Mumbai; terrain that runs downhill out
        # of the window must be able to carry water out of it, otherwise water ponds against the clip line.
        # `open_boundary` may be None/False (closed), True (auto: edge cells sloping outward), or a mask.
        if open_boundary is None or open_boundary is False:
            self.open_boundary = np.zeros(self.z.shape, dtype=bool)
        elif open_boundary is True:
            from .pits import outward_open_boundary
            self.open_boundary = outward_open_boundary(terrain)
        else:
            self.open_boundary = np.asarray(open_boundary, dtype=bool)
        # cached once: self.open_boundary is fixed for the life of the instance (set only here, never
        # reassigned), so `.any()` need not be recomputed on every _substep() call (perf only, no physics
        # change).
        self._has_open_boundary = bool(self.open_boundary.any())
        self._boundary_out = 0.0
        # outward friction slope at the boundary = local terrain slope, floored so the outfall stays finite
        self._bnd_sqrt_s = np.zeros(self.z.shape)
        if self.open_boundary.any():
            zz = self.z
            sl = np.zeros_like(zz)
            sl[0, :] = np.maximum(zz[1, :] - zz[0, :], 0.0) / self.res
            sl[-1, :] = np.maximum(zz[-2, :] - zz[-1, :], 0.0) / self.res
            sl[:, 0] = np.maximum(sl[:, 0], np.maximum(zz[:, 1] - zz[:, 0], 0.0) / self.res)
            sl[:, -1] = np.maximum(sl[:, -1], np.maximum(zz[:, -2] - zz[:, -1], 0.0) / self.res)
            self._bnd_sqrt_s = np.sqrt(np.maximum(sl, BND_MIN_SLOPE)) * self.open_boundary
        # precomputed face geometry (x-faces: between (j,i) and (j,i+1); y-faces: between (j,i) and (j+1,i))
        self.zmax_x = np.maximum(self.z[:, :-1], self.z[:, 1:])
        self.zmax_y = np.maximum(self.z[:-1, :], self.z[1:, :])
        self.open_x = self.open[:, :-1] & self.open[:, 1:]
        self.open_y = self.open[:-1, :] & self.open[1:, :]
        self.inv_n = 1.0 / self.n
        self.sqrt_inv_dx = 1.0 / np.sqrt(self.res)
        # Pre-allocated scratch buffers for _substep() (perf only): _substep was allocating ~15-20 fresh
        # full-grid/face-shaped arrays per call across 3,905 calls (67.6% of total runtime self-time,
        # profiled on the heavy/70%-blockage/180-min pilot scenario). _substep now writes into these via
        # numpy's `out=` kwarg instead of allocating new arrays; the sequence of operations, the operands
        # and the floating-point operation order are unchanged from before -- only where results are
        # stored. See docs/PERFORMANCE_OPTIMIZATION.md section 6-7.
        grid_shape = self.z.shape
        x_face_shape = self.zmax_x.shape   # (ny, nx-1)
        y_face_shape = self.zmax_y.shape   # (ny-1, nx)
        self._H = np.empty(grid_shape, dtype=np.float64)
        self._out = np.empty(grid_shape, dtype=np.float64)
        self._stored = np.empty(grid_shape, dtype=np.float64)
        self._scale = np.empty(grid_shape, dtype=np.float64)
        self._hb = np.empty(grid_shape, dtype=np.float64)
        self._q = np.empty(grid_shape, dtype=np.float64)
        self._dh = np.empty(grid_shape, dtype=np.float64)
        self._inf = np.empty(grid_shape, dtype=np.float64)
        self._dHx = np.empty(x_face_shape, dtype=np.float64)
        self._hx = np.empty(x_face_shape, dtype=np.float64)
        self._vx = np.empty(x_face_shape, dtype=np.float64)
        self._Vx = np.empty(x_face_shape, dtype=np.float64)
        self._dHy = np.empty(y_face_shape, dtype=np.float64)
        self._hy = np.empty(y_face_shape, dtype=np.float64)
        self._vy = np.empty(y_face_shape, dtype=np.float64)
        self._Vy = np.empty(y_face_shape, dtype=np.float64)
        self.last_dt_used = 0.0
        self.substeps = 0

    # ------------------------------------------------------------------ sources / sinks
    def add_volume(self, j: np.ndarray, i: np.ndarray, volume_m3: np.ndarray) -> None:
        j = np.asarray(j, dtype=np.int64); i = np.asarray(i, dtype=np.int64)
        v = np.asarray(volume_m3, dtype=np.float64)
        if j.size == 0:
            return
        np.add.at(self.depth, (j, i), v / self.area)

    def add_runoff(self, runoff_depth_m: np.ndarray) -> None:
        self.depth += np.asarray(runoff_depth_m, dtype=np.float64)

    def take_volume(self, j: np.ndarray, i: np.ndarray, max_m3: np.ndarray) -> np.ndarray:
        j = np.asarray(j, dtype=np.int64); i = np.asarray(i, dtype=np.int64)
        req = np.maximum(np.asarray(max_m3, dtype=np.float64), 0.0)
        if j.size == 0:
            return np.zeros(0, dtype=np.float64)
        nx = self.depth.shape[1]
        flat = j * nx + i
        avail = self.depth.reshape(-1)[flat] * self.area
        # duplicates in the same cell share the available volume proportionally
        req_cell = np.bincount(flat, weights=req, minlength=self.depth.size)[flat]
        frac = np.where(req_cell > 0, np.minimum(1.0, avail / np.maximum(req_cell, 1e-300)), 0.0)
        removed = req * frac
        np.add.at(self.depth, (j, i), -removed / self.area)
        np.maximum(self.depth, 0.0, out=self.depth)
        return removed

    # ------------------------------------------------------------------ routing
    def _substep(self, remaining: float) -> float:
        """One explicit sub-step of at most `remaining` seconds; returns the dt actually used.

        Perf note: all full-grid/face-shaped intermediates are written into pre-allocated scratch
        buffers (self._H, self._dHx, ...; see __init__) via numpy's `out=` kwarg rather than being
        freshly allocated every call. This is allocation-strategy only -- the sequence of operations,
        the operands and the floating-point operation order are byte-for-byte the same as before.
        """
        h = self.depth
        A = self.area
        H = self._H; np.add(self.z, h, out=H)
        # x faces (between (j,i) and (j,i+1)); y faces (between (j,i) and (j+1,i))
        dHx = self._dHx; np.subtract(H[:, :-1], H[:, 1:], out=dHx)
        dHy = self._dHy; np.subtract(H[:-1, :], H[1:, :], out=dHy)
        hx = self._hx; np.maximum(H[:, :-1], H[:, 1:], out=hx); hx -= self.zmax_x
        hy = self._hy; np.maximum(H[:-1, :], H[1:, :], out=hy); hy -= self.zmax_y
        hx[(hx < H_MIN) | ~self.open_x] = 0.0
        hy[(hy < H_MIN) | ~self.open_y] = 0.0
        # velocity = (1/n) h_eff^(2/3) sqrt(|dH|/dx); unit flux q = v * h_eff (m2/s)
        vx = self._vx; np.cbrt(hx * hx, out=vx); vx *= np.sqrt(np.abs(dHx)); vx *= self.inv_n * self.sqrt_inv_dx
        vy = self._vy; np.cbrt(hy * hy, out=vy); vy *= np.sqrt(np.abs(dHy)); vy *= self.inv_n * self.sqrt_inv_dx
        vmax = max(float(vx.max()), float(vy.max()))
        dt = remaining if vmax <= 0.0 else min(remaining, self.cfl * self.res / vmax)
        dt = max(dt, min(self.dt_min, remaining))
        # signed face volumes (m3) over dt, capped at HEAD_CAP * head-difference volume (no level inversion)
        Vx = self._Vx; np.multiply(vx, hx, out=Vx); Vx *= self.res * dt; np.minimum(Vx, HEAD_CAP * np.abs(dHx) * A, out=Vx); np.copysign(Vx, dHx, out=Vx)
        Vy = self._Vy; np.multiply(vy, hy, out=Vy); Vy *= self.res * dt; np.minimum(Vy, HEAD_CAP * np.abs(dHy) * A, out=Vy); np.copysign(Vy, dHy, out=Vy)
        # total outflow per cell must not exceed stored volume -> scale faces by upstream cell factor
        out = self._out; out.fill(0.0)
        out[:, :-1] += np.maximum(Vx, 0.0); out[:, 1:] -= np.minimum(Vx, 0.0)
        out[:-1, :] += np.maximum(Vy, 0.0); out[1:, :] -= np.minimum(Vy, 0.0)
        stored = self._stored; np.multiply(h, A, out=stored)
        over = out > stored
        if over.any():
            scale = self._scale; scale.fill(1.0)
            scale[over] = stored[over] / out[over]
            Vx *= np.where(Vx > 0, scale[:, :-1], scale[:, 1:])
            Vy *= np.where(Vy > 0, scale[:-1, :], scale[1:, :])
        Vx *= 1.0 / A; Vy *= 1.0 / A
        h[:, :-1] -= Vx; h[:, 1:] += Vx
        h[:-1, :] -= Vy; h[1:, :] += Vy
        np.maximum(h, 0.0, out=h)
        if self._has_open_boundary:
            # free outfall at the domain edge: q = (1/n) h^(5/3) sqrt(S) per unit width (m2/s)
            hb = self._hb; hb.fill(0.0); hb[self.open_boundary] = h[self.open_boundary]
            q = self._q; np.multiply(self.inv_n * hb * np.cbrt(hb * hb), self._bnd_sqrt_s, out=q)   # h^(5/3)
            dh = self._dh; np.minimum(hb, q * dt / self.res, out=dh)
            h -= dh
            self._boundary_out += float(dh.sum()) * A
        if self.infil_rate > 0.0:
            inf = self._inf; np.minimum(h, self._infil_depth_rate * dt, out=inf)
            h -= inf
            self._infiltrated += float(inf.sum()) * A
        return dt

    def step(self, dt_s: float) -> float:
        """Advance the surface by dt_s using adaptive CFL-limited sub-steps. Returns the first sub-step size used."""
        remaining = float(dt_s)
        first = None
        while remaining > 1e-9:
            dt = self._substep(remaining)
            remaining -= dt
            self.substeps += 1
            if first is None:
                first = dt
        self.last_dt_used = first if first is not None else float(dt_s)
        return self.last_dt_used

    # ------------------------------------------------------------------ diagnostics
    def total_volume_m3(self) -> float:
        return float(self.depth.sum()) * self.area

    def infiltrated_m3(self) -> float:
        return float(self._infiltrated)

    def boundary_out_m3(self) -> float:
        """Cumulative volume that left the domain through the open boundary (m3)."""
        return float(self._boundary_out)
