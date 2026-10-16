"""Data contracts shared by every module. THIS FILE IS THE INTERFACE. Change it only via the integrator.

Conventions (see config.py): EPSG:32643 metres for all x/y; depth in metres; volumes m3; flows m3/s; time seconds.
Every container carries a `provenance` (see provenance.py). Arrays are numpy; JSON helpers are provided for the API.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Optional
import numpy as np

from .provenance import Provenance


# ----------------------------------------------------------------------------- grid / terrain
@dataclass(frozen=True)
class Grid:
    """Regular grid in EPSG:32643. z[j, i]; x = x0 + (i+0.5)*res ; y = y0 + (j+0.5)*res ; row 0 = south."""
    x0: float
    y0: float
    res: float
    nx: int
    ny: int

    def cell_of(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(j, i) indices for point arrays; callers must clip/validate with `inside`."""
        i = np.floor((np.asarray(x) - self.x0) / self.res).astype(int)
        j = np.floor((np.asarray(y) - self.y0) / self.res).astype(int)
        return j, i

    def inside(self, j: np.ndarray, i: np.ndarray) -> np.ndarray:
        return (j >= 0) & (j < self.ny) & (i >= 0) & (i < self.nx)

    @property
    def cell_area(self) -> float:
        return self.res * self.res

    def to_dict(self) -> dict:
        return {"x0": self.x0, "y0": self.y0, "res": self.res, "nx": self.nx, "ny": self.ny, "crs": "EPSG:32643"}


@dataclass
class Terrain:
    grid: Grid
    z: np.ndarray                    # [ny, nx] float32, ground elevation (m, mTHD for the pilot)
    impervious: np.ndarray           # [ny, nx] float32 in [0,1], fraction of cell that is impervious
    building: np.ndarray             # [ny, nx] bool, True = building footprint (no-flow obstacle)
    provenance: Provenance
    impervious_provenance: Provenance
    building_provenance: Provenance


# ----------------------------------------------------------------------------- rainfall
@dataclass
class RainfallScenario:
    """Rainfall forcing for one run.

    TWO MODES, and the distinction is load-bearing for SR-01:

    - **Uniform (default).** `intensity_mm_h` is `[T]`; the same mm/h is applied to every cell of the model
      grid. Every provider that predates spatial support is this mode, and it stays bit-for-bit unchanged.
    - **Spatial.** `intensity_field_mm_h` is `[T, ny, nx]` ON `field_grid`, which MUST equal the Terrain grid
      the engine is run against (resampling/reprojection is the *provider's* job, not the engine's -- see
      `floodnet.rainfall.gridded`). `intensity_mm_h` is STILL populated in this mode, as the per-timestep
      **area mean** of the field, so that every existing scalar consumer (`to_dict`, the `/api/scenarios`
      payload, the SWMM adapter, frontend charts) keeps working unchanged and honestly. Anything reading
      `intensity_mm_h` on a spatial scenario is reading an area mean, and `to_dict()` says so via `spatial`.

    `is_spatial` is the single predicate callers should branch on. A scenario is never *implicitly* spatial:
    a provider must supply both `intensity_field_mm_h` and `field_grid`.
    """
    id: str
    name: str
    t_s: np.ndarray                  # [T] seconds from start, monotonically increasing, step RAIN_DT_S
    intensity_mm_h: np.ndarray       # [T] mm/h, piecewise-constant from t_s[k] to t_s[k+1]
                                     #     (spatial mode: the area MEAN of intensity_field_mm_h per timestep)
    provenance: Provenance
    description: str = ""
    # --- spatial mode (optional; both must be set together, or neither) -------------------------------
    intensity_field_mm_h: Optional[np.ndarray] = None   # [T, ny, nx] mm/h on `field_grid`
    field_grid: Optional[Grid] = None                   # grid the field is on; must match Terrain.grid

    def __post_init__(self):
        if (self.intensity_field_mm_h is None) != (self.field_grid is None):
            raise ValueError("RainfallScenario: intensity_field_mm_h and field_grid must be set together")
        if self.intensity_field_mm_h is not None:
            f = np.asarray(self.intensity_field_mm_h)
            if f.ndim != 3:
                raise ValueError(f"intensity_field_mm_h must be [T, ny, nx]; got shape {f.shape}")
            if f.shape[0] != len(self.t_s):
                raise ValueError(f"intensity_field_mm_h has {f.shape[0]} timesteps but t_s has {len(self.t_s)}")
            if (f.shape[1], f.shape[2]) != (self.field_grid.ny, self.field_grid.nx):
                raise ValueError(f"intensity_field_mm_h grid {f.shape[1:]} != field_grid "
                                 f"({self.field_grid.ny}, {self.field_grid.nx})")

    @property
    def is_spatial(self) -> bool:
        return self.intensity_field_mm_h is not None

    def _index_at(self, t: float) -> int:
        """Piecewise-constant lookup index for time t, or -1 if outside the series."""
        k = int(np.searchsorted(self.t_s, t, side="right") - 1)
        if k < 0 or k >= len(self.intensity_mm_h):
            return -1
        return k

    def intensity_at(self, t: float) -> float:
        """Scalar mm/h at time t. In spatial mode this is the AREA MEAN over the field -- use
        intensity_field_at() to get the actual per-cell field."""
        k = self._index_at(t)
        if k < 0: return 0.0
        return float(self.intensity_mm_h[k])

    def intensity_field_at(self, t: float) -> Optional[np.ndarray]:
        """[ny, nx] mm/h at time t, or None for a uniform scenario. Returns a read-only view: the engine
        must not mutate the stored field."""
        if self.intensity_field_mm_h is None:
            return None
        k = self._index_at(t)
        if k < 0:
            return np.zeros((self.field_grid.ny, self.field_grid.nx), dtype=np.float64)
        f = self.intensity_field_mm_h[k]
        view = f.view()
        view.flags.writeable = False
        return view

    def to_dict(self) -> dict:
        d = {"id": self.id, "name": self.name, "description": self.description,
             "t_min": (self.t_s / 60).tolist(), "intensity_mm_h": self.intensity_mm_h.tolist(),
             "total_mm": float(np.sum(self.intensity_mm_h * np.diff(np.append(self.t_s, self.t_s[-1] + 300)) / 3600.0)),
             "provenance": self.provenance.to_dict(),
             "spatial": self.is_spatial}
        if self.is_spatial:
            # The field itself is far too large for a JSON payload (T x ny x nx floats); advertise its shape
            # and grid so a client can tell this is a genuine spatial field, and state plainly that the
            # scalar series above is an area mean.
            f = np.asarray(self.intensity_field_mm_h)
            d["field"] = {"shape": list(f.shape), "grid": self.field_grid.to_dict(), "units": "mm/h",
                          "note": "intensity_mm_h above is the per-timestep AREA MEAN of this field",
                          "peak_cell_mm_h": float(np.max(f)), "min_cell_mm_h": float(np.min(f))}
        return d


# ----------------------------------------------------------------------------- drainage network
@dataclass
class DrainageNetwork:
    """Directed graph. Arrays are aligned by index. All elevations mTHD (same datum as Terrain.z for the pilot)."""
    # nodes
    node_id: np.ndarray              # [N] str
    node_x: np.ndarray               # [N] float64 (EPSG:32643)
    node_y: np.ndarray               # [N]
    node_ground: np.ndarray          # [N] float32, GROUND_LEV (REAL)
    node_invert: np.ndarray          # [N] float32, derived = min of connected conduit inverts (ESTIMATED rule)
    node_is_outfall: np.ndarray      # [N] bool, inferred from graph sinks (ESTIMATED)
    node_storage_area_m2: np.ndarray # [N] float32, manhole plan area used for storage (ESTIMATED)
    node_inlet_cap_m3s: np.ndarray   # [N] float32, max capture rate from surface (ESTIMATED)
    node_cell_j: np.ndarray          # [N] int, grid cell of node (-1 if outside grid)
    node_cell_i: np.ndarray          # [N] int
    # edges (conduits)
    edge_id: np.ndarray              # [E] str
    edge_us: np.ndarray              # [E] int, index into nodes
    edge_ds: np.ndarray              # [E] int
    edge_length_m: np.ndarray        # [E] float32 (REAL)
    edge_shape: np.ndarray           # [E] str: CIRC | RECT | OREC | ARCH (REAL)
    edge_width_m: np.ndarray         # [E] float32 (REAL; diameter for CIRC)
    edge_height_m: np.ndarray        # [E] float32 (REAL)
    edge_us_invert: np.ndarray       # [E] float32 (REAL)
    edge_ds_invert: np.ndarray       # [E] float32 (REAL)
    edge_slope: np.ndarray           # [E] float32, (us-ds)/length, floored to min positive (ESTIMATED fix for 0.4%)
    edge_n: np.ndarray               # [E] float32, Manning n (ESTIMATED)
    edge_capacity_m3s: np.ndarray    # [E] float32, full-bore Manning capacity (computed from REAL geometry + ESTIMATED n)
    edge_blockage: np.ndarray        # [E] float32 in [0,1], 0 = clear (SCENARIO input, default 0)
    edge_status: np.ndarray          # [E] str: Existing | Proposal (REAL)
    provenance: dict = field(default_factory=dict)   # {"geometry": Provenance, "roughness": Provenance, ...} as dicts

    @property
    def n_nodes(self) -> int: return int(len(self.node_id))
    @property
    def n_edges(self) -> int: return int(len(self.edge_id))


# ----------------------------------------------------------------------------- roads
@dataclass
class RoadSegment:
    seg_id: str
    name: str
    highway: str
    osm_way_id: int
    u: int                           # road-graph node index
    v: int
    length_m: float
    oneway: bool
    xy: np.ndarray                   # [K, 2] EPSG:32643 polyline
    lonlat: np.ndarray               # [K, 2] for the API


@dataclass
class RoadGraph:
    node_xy: np.ndarray              # [M, 2]
    node_lonlat: np.ndarray          # [M, 2]
    segments: list[RoadSegment]
    provenance: Provenance           # OSM ODbL attribution required


# ----------------------------------------------------------------------------- simulation output
@dataclass
class Frame:
    t_s: float
    depth: np.ndarray                # [ny, nx] float32 metres, surface water depth
    node_hgl: np.ndarray             # [N] float32 hydraulic grade (m, mTHD)
    node_surcharging: np.ndarray     # [N] bool
    node_surcharge_m3: np.ndarray    # [N] float32, volume returned to surface during the last frame interval
    node_cause: np.ndarray           # [N] str: "" | "overcapacity" | "blockage" | "downstream"
    edge_flow_m3s: np.ndarray        # [E] float32
    edge_util: np.ndarray            # [E] float32, flow / effective capacity
    street_depth_m: dict[str, float] # seg_id -> representative depth (m) on the segment (max over cells)
    rain_mm_h: float


@dataclass
class MassBalance:
    rain_in_m3: float
    surface_stored_m3: float
    network_stored_m3: float
    outfall_out_m3: float
    infiltration_m3: float
    error_m3: float
    error_pct: float
    abstraction_m3: float = 0.0      # rain that never became runoff (runoff-coefficient losses); counted so the balance closes
    runoff_in_m3: float = 0.0        # net runoff delivered to the surface
    boundary_out_m3: float = 0.0     # volume that left the domain through the open (clip-line) boundary


@dataclass
class SimulationResult:
    run_id: str
    scenario: RainfallScenario
    blockage: dict                   # {"mode": "none|fraction|edges", "fraction": 0.0, "edge_ids": [...]}
    grid: Grid
    frames: list[Frame]
    mass_balance: MassBalance
    runtime_s: float
    provenance: dict                 # merged provenance of all inputs, as dicts
    notes: list[str] = field(default_factory=list)


# ----------------------------------------------------------------------------- model protocols (engine wires these)
class SurfaceModel(Protocol):
    """2D surface water on Terrain.grid. Implemented in floodnet.terrain.surface."""
    depth: np.ndarray                                  # [ny, nx] metres (live state)
    def add_volume(self, j: np.ndarray, i: np.ndarray, volume_m3: np.ndarray) -> None: ...
    def add_runoff(self, runoff_depth_m: np.ndarray) -> None: ...      # [ny,nx] metres added uniformly per cell
    def take_volume(self, j: np.ndarray, i: np.ndarray, max_m3: np.ndarray) -> np.ndarray: ...  # returns m3 actually removed
    def step(self, dt_s: float) -> float: ...          # advances state; returns the internal dt actually used (may be < dt_s and loop)
    def total_volume_m3(self) -> float: ...
    def infiltrated_m3(self) -> float: ...


class DrainageModel(Protocol):
    """Graph hydraulics on DrainageNetwork. Implemented in floodnet.drainage.hydraulics."""
    net: DrainageNetwork
    def inlet_capacity_m3(self, dt_s: float) -> np.ndarray: ...       # [N] max volume each node can accept from surface this dt
    def add_inflow(self, volume_m3: np.ndarray) -> None: ...           # [N]
    def step(self, dt_s: float) -> np.ndarray: ...                     # [N] surcharge volume (m3) to return to surface
    def hgl(self) -> np.ndarray: ...                                   # [N]
    def edge_flow_m3s(self) -> np.ndarray: ...                         # [E]
    def edge_util(self) -> np.ndarray: ...                             # [E]
    def surcharging(self) -> np.ndarray: ...                           # [N] bool
    def cause(self) -> np.ndarray: ...                                 # [N] str
    def stored_m3(self) -> float: ...
    def outflow_m3(self) -> float: ...                                 # cumulative at outfalls
