"""Process-wide state for the API: loaded pilot (REAL or DEMONSTRATION fallback), run cache, CRS helpers.

All sibling packages (data, terrain, drainage, streets, routing) are imported LAZILY so the server always starts;
callers turn `ModuleMissing` into HTTP 503 with an informative message.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import uuid
from collections import OrderedDict
from functools import lru_cache
from typing import Any, Optional

import numpy as np
from pyproj import Transformer

from .. import config
from ..contracts import SimulationResult, Grid

log = logging.getLogger("floodnet.api")

MAX_RUNS = 6
ATTRIBUTION = ["© OpenStreetMap contributors (ODbL)",
               "MCGM GIS (Storm Water Drains, Manholes, Contours, Flooding Spots)"]


class ModuleMissing(RuntimeError):
    """A sibling module written by another agent is not available yet (-> HTTP 503)."""


# ----------------------------------------------------------------------------- CRS helpers
_to_geo = Transformer.from_crs(config.CRS_COMPUTE, config.CRS_GEO, always_xy=True)
_to_utm = Transformer.from_crs(config.CRS_GEO, config.CRS_COMPUTE, always_xy=True)


def xy_to_lonlat(x, y):
    return _to_geo.transform(x, y)


def lonlat_to_xy(lon, lat):
    return _to_utm.transform(lon, lat)


def grid_bbox_lonlat(grid: Grid) -> list[float]:
    xs = [grid.x0, grid.x0 + grid.nx * grid.res]
    ys = [grid.y0, grid.y0 + grid.ny * grid.res]
    cx = np.array([xs[0], xs[1], xs[1], xs[0]]); cy = np.array([ys[0], ys[0], ys[1], ys[1]])
    lon, lat = xy_to_lonlat(cx, cy)
    return [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]


# ----------------------------------------------------------------------------- pilot loading
_pilot: Optional[dict] = None
_pilot_error: Optional[str] = None
_data_mode: str = "UNLOADED"
_pilot_lock = threading.Lock()


def _try_load() -> tuple[Optional[dict], str, Optional[str]]:
    errors = []
    try:
        from ..data.load import load_pilot  # Agent C
        p = load_pilot()
        log.info("Pilot loaded: REAL (floodnet.data.load.load_pilot)")
        return p, "REAL", None
    except Exception as e:  # noqa: BLE001
        errors.append(f"load_pilot failed: {type(e).__name__}: {e}")
        log.warning("REAL pilot unavailable (%s); falling back to SYNTHETIC/DEMONSTRATION fixture", e)
    try:
        from ..data.fixtures import synthetic_pilot  # Agent F
        p = synthetic_pilot()
        log.error("*** DATA MODE = DEMONSTRATION: serving SYNTHETIC pilot, NOT real Mumbai data ***")
        return p, "DEMONSTRATION", None
    except Exception as e:  # noqa: BLE001
        errors.append(f"synthetic_pilot failed: {type(e).__name__}: {e}")
        log.error("No pilot available: %s", errors)
    return None, "UNAVAILABLE", "; ".join(errors)


def _normalize(p: dict) -> dict:
    """Accept scenarios as list or dict; guarantee the keys the API expects."""
    p = dict(p)
    sc = p.get("scenarios") or {}
    if not isinstance(sc, dict):
        sc = {s.id: s for s in sc}
    p["scenarios"] = sc
    p.setdefault("hotspots", [])
    p.setdefault("roads", None)
    return p


def get_pilot() -> dict:
    """Returns dict(terrain, net, roads, hotspots, scenarios). Raises ModuleMissing if nothing can be loaded."""
    global _pilot, _pilot_error, _data_mode
    with _pilot_lock:
        if _pilot is None and _pilot_error is None:
            _pilot, _data_mode, _pilot_error = _try_load()
            if _pilot is not None:
                _pilot = _normalize(_pilot)
        if _pilot is None:
            raise ModuleMissing(
                "Pilot data not available: neither floodnet.data.load.load_pilot (REAL, needs build_pilot output in "
                f"{config.DATA_PROCESSED}) nor floodnet.data.fixtures.synthetic_pilot (DEMONSTRATION) could be loaded. "
                f"Details: {_pilot_error}")
        return _pilot


def _clear_derived_caches() -> None:
    """The `id(roads)`/`id(...)` caches below (street_fn here; routing graph + snap KDTree in
    floodnet.routing.router) are only safe while the cached `roads` object stays alive somewhere with a
    strong reference (normally: as _pilot["roads"], for the life of the process) -- Python can reuse a freed
    object's id(), which would otherwise let a stale cache entry silently match a *different* new RoadGraph.
    set_pilot/reset_pilot are the only places `roads` is ever replaced (tests / integrator override), so
    clear every such cache there. Also drops memoized simulation results (_run_scenario_cached): a swapped
    pilot can reuse the same scenario ids (e.g. synthetic fixtures) over different terrain/network data."""
    _street_fn_cache.clear()
    clear_scenario_cache()
    clear_physics_memo()
    try:
        from . import bundle
        bundle.clear_caches()
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..routing import router
        router.clear_caches()
    except Exception:  # noqa: BLE001
        pass


def set_pilot(pilot: dict, data_mode: str) -> None:
    """Test hook / integrator override."""
    global _pilot, _pilot_error, _data_mode
    with _pilot_lock:
        _pilot, _pilot_error, _data_mode = _normalize(pilot), None, data_mode
        _clear_derived_caches()


def data_mode() -> str:
    """PILOT-level mode: whether the GIS/network inputs loaded from real sources (REAL) or the placeholder
    fallback (DEMONSTRATION). Says nothing about the rainfall driving a particular run -- see run_data_mode."""
    return _data_mode


MIXED = "MIXED"


def run_data_mode(res: Optional[SimulationResult]) -> str:
    """RUN-level mode. A REAL pilot driven by an ESTIMATED rainfall series (the IMD live path: REAL station
    observation + FloodNet's 3-hour persistence continuation) is MIXED -- reporting it as REAL would imply the
    whole forecast input was observed. Every other run keeps the pilot mode, exactly as before."""
    if res is None:
        return _data_mode
    rs = (res.provenance or {}).get("rainfall_source") or {}
    if _data_mode == "REAL" and rs.get("data_mode") == "ESTIMATED":
        return MIXED
    return _data_mode


def reset_pilot() -> None:
    global _pilot, _pilot_error, _data_mode
    with _pilot_lock:
        _pilot, _pilot_error, _data_mode = None, None, "UNLOADED"
        _clear_derived_caches()


def pilot_provenance(p: dict) -> dict:
    t = p["terrain"]; net = p["net"]; roads = p.get("roads")
    prov = {"terrain": t.provenance.to_dict(), "impervious": t.impervious_provenance.to_dict(),
            "buildings": t.building_provenance.to_dict(),
            "network": {k: (v.to_dict() if hasattr(v, "to_dict") else v) for k, v in (net.provenance or {}).items()},
            "roads": roads.provenance.to_dict() if roads is not None and hasattr(roads, "provenance") else None,
            "data_mode": _data_mode}
    return prov


# ----------------------------------------------------------------------------- run cache
_runs: "OrderedDict[str, SimulationResult]" = OrderedDict()
_runs_lock = threading.Lock()
sim_lock = threading.Lock()          # only one simulation at a time


def put_run(res: SimulationResult) -> None:
    with _runs_lock:
        _runs[res.run_id] = res
        while len(_runs) > MAX_RUNS:
            _runs.popitem(last=False)


def get_run(run_id: str) -> Optional[SimulationResult]:
    with _runs_lock:
        r = _runs.get(run_id)
        if r is not None:
            _runs.move_to_end(run_id)
        return r


def latest_run() -> Optional[SimulationResult]:
    with _runs_lock:
        return next(reversed(_runs.values()), None)


def list_runs() -> list[str]:
    with _runs_lock:
        return list(_runs.keys())


# ----------------------------------------------------------------------------- simulation wiring (lazy imports)
def build_models(terrain, net) -> tuple[Any, Any, Any]:
    """Returns (surface, drainage, runoff_fn); raises ModuleMissing naming the missing piece."""
    try:
        from ..terrain.runoff import runoff_fn
    except Exception as e:  # noqa: BLE001
        raise ModuleMissing(f"floodnet.terrain.runoff.runoff_fn unavailable (Agent A): {e}") from e
    try:
        from ..terrain.surface import StorageCellSurface
    except Exception as e:  # noqa: BLE001
        raise ModuleMissing(f"floodnet.terrain.surface.StorageCellSurface unavailable (Agent A): {e}") from e
    try:
        from ..drainage.hydraulics import GraphDrainage
    except Exception as e:  # noqa: BLE001
        raise ModuleMissing(f"floodnet.drainage.hydraulics.GraphDrainage unavailable (Agent B): {e}") from e
    return StorageCellSurface(terrain, open_boundary=True), GraphDrainage(net), runoff_fn


_street_fn_cache: dict[int, Any] = {}


def street_fn_for(roads, grid):
    """street_fn from Agent F, or None (frames will then carry no per-street depth).

    `_cells_per_segment` (inside make_street_fn) does a shapely buffer + contains_xy per road segment -- real
    work, and `roads`/`grid` are the same immutable pilot objects on every simulate call, so it was being
    rebuilt from scratch on every /api/simulate request for no reason. Cached by `id(roads)`: the pilot is
    loaded once per process (see get_pilot) and never replaced except via set_pilot/reset_pilot in tests, at
    which point a new `roads` object (new id) naturally gets a fresh entry."""
    if roads is None:
        return None
    key = id(roads)
    fn = _street_fn_cache.get(key)
    if fn is not None:
        return fn
    try:
        from ..streets.aggregate import make_street_fn
        fn = make_street_fn(roads, grid)
    except Exception as e:  # noqa: BLE001
        log.warning("floodnet.streets.aggregate.make_street_fn unavailable: %s (street depths disabled)", e)
        return None
    _street_fn_cache[key] = fn
    return fn


def _blockage_fallback(net, spec: dict):
    """Local implementation used only when floodnet.drainage.scenarios.apply_blockage is unavailable."""
    import copy
    n2 = copy.copy(net)
    n2.edge_blockage = np.array(net.edge_blockage, dtype=np.float32, copy=True)
    mode = spec.get("mode", "none")
    frac = float(spec.get("fraction", 0.8) if spec.get("fraction") is not None else 0.8)
    if mode == "none":
        return n2
    if mode == "fraction":
        n2.edge_blockage[:] = np.maximum(n2.edge_blockage, frac)
    elif mode == "edges":
        ids = set(spec.get("edge_ids") or [])
        m = np.array([e in ids for e in net.edge_id])
        n2.edge_blockage[m] = np.maximum(n2.edge_blockage[m], frac)
    elif mode == "random":
        share = float(spec.get("share", 0.2) or 0.2)
        rng = np.random.default_rng(spec.get("seed", 42))
        m = rng.random(net.n_edges) < share
        n2.edge_blockage[m] = np.maximum(n2.edge_blockage[m], frac)
    elif mode == "near":
        lon, lat = spec.get("lonlat") or [None, None]
        if lon is None:
            raise ValueError("blockage mode 'near' requires lonlat")
        r = float(spec.get("radius_m", 150.0) or 150.0)
        x, y = lonlat_to_xy(lon, lat)
        mx = 0.5 * (net.node_x[net.edge_us] + net.node_x[net.edge_ds])
        my = 0.5 * (net.node_y[net.edge_us] + net.node_y[net.edge_ds])
        m = np.hypot(mx - x, my - y) <= r
        n2.edge_blockage[m] = np.maximum(n2.edge_blockage[m], frac)
    else:
        raise ValueError(f"unknown blockage mode {mode!r}")
    return n2


def apply_blockage(net, spec: dict):
    if not spec or spec.get("mode", "none") == "none":
        return net
    try:
        from ..drainage.scenarios import apply_blockage as _ab
        return _ab(net, spec)
    except ImportError as e:
        log.warning("floodnet.drainage.scenarios.apply_blockage unavailable (%s); using API fallback", e)
        return _blockage_fallback(net, spec)


def _canon_blockage(spec: Optional[dict]) -> str:
    return json.dumps(spec or {"mode": "none"}, sort_keys=True, default=str)


# ----------------------------------------------------------------------------- physics reuse (identical inputs)
# Provider-driven runs (live / ecmwf / auto / demo / gridded) used to re-run the full physics on every request even
# when the provider returned the very same rainfall from its own TTL cache (ECMWF and IMD: 10 min) -- ~8-19 s of
# identical recomputation per "Run Forecast" click (measured 2026-10-11, docs/PERFORMANCE_OPTIMIZATION.md). The
# engine is deterministic, so a run is reused ONLY when every physics input is identical: the same loaded pilot
# objects, the same canonical blockage spec, the same horizon and frame step, and a rainfall scenario with the same
# id, name, description, provenance (tag = data mode, source, note), time axis, intensities and (spatial) field and
# grid. Freshness stays with the providers: they are still called on every request, and any new data -- even one
# changed value or a different provenance note -- is a different key and is simulated. Source metadata
# (rainfall_source, timestamps, staleness) is never cached here: callers attach the CURRENT provider metadata to
# every result. Bounded to PHYSICS_MEMO_MAX entries (a 180-min result is ~10 MB; a 720-min one ~40 MB).
PHYSICS_MEMO_MAX = 4
_physics_memo: "OrderedDict[tuple, SimulationResult]" = OrderedDict()
_physics_memo_lock = threading.Lock()


def clear_physics_memo() -> None:
    with _physics_memo_lock:
        _physics_memo.clear()


def _array_digest(a) -> Optional[str]:
    if a is None:
        return None
    import hashlib
    arr = np.ascontiguousarray(a)
    h = hashlib.blake2b(digest_size=20)
    h.update(f"{arr.dtype.str}|{arr.shape}|".encode())
    h.update(arr.tobytes())
    return h.hexdigest()


def _physics_key(scen, blockage: dict, horizon_min: int, p: dict) -> tuple:
    pr = scen.provenance
    fg = scen.field_grid
    return (id(p["terrain"]), id(p["net"]), id(p.get("roads")), _data_mode,
            _canon_blockage(blockage), int(horizon_min), int(config.FRAME_DT_S),
            scen.id, scen.name, scen.description,
            (pr.tag.value, pr.source, pr.note) if pr is not None else None,
            _array_digest(scen.t_s), _array_digest(scen.intensity_mm_h), _array_digest(scen.intensity_field_mm_h),
            json.dumps(fg.to_dict(), sort_keys=True) if fg is not None else None)


def _run_physics(scen, blockage: dict, horizon_min: int, p: dict) -> SimulationResult:
    key = _physics_key(scen, blockage, horizon_min, p)
    with _physics_memo_lock:
        hit = _physics_memo.get(key)
        if hit is not None:
            _physics_memo.move_to_end(key)
    if hit is not None and hit[0] is p["terrain"] and hit[1] is p["net"]:
        res = copy.copy(hit[2])   # fresh object; frames / mass balance shared read-only (as _run_scenario_cached)
        res.scenario = scen       # the object this request's provider returned (identical content, see the key)
    else:
        res = _run_physics_uncached(scen, blockage, horizon_min, p)
        with _physics_memo_lock:
            # the entry pins the pilot objects whose id() is in the key, so an id can never be reused under it
            _physics_memo[key] = (p["terrain"], p["net"], res)
            while len(_physics_memo) > PHYSICS_MEMO_MAX:
                _physics_memo.popitem(last=False)
        res = copy.copy(res)
    res.provenance = dict(res.provenance)
    return res


def _run_physics_uncached(scen, blockage: dict, horizon_min: int, p: dict) -> SimulationResult:
    from ..simulation.engine import run_simulation
    net = apply_blockage(p["net"], blockage)
    terrain = p["terrain"]
    with sim_lock:
        surface, drainage, runoff_fn = build_models(terrain, net)
        street_fn = street_fn_for(p.get("roads"), terrain.grid)
        res = run_simulation(terrain, net, scen, surface, drainage, runoff_fn, street_fn=street_fn,
                             blockage=blockage or {"mode": "none"}, horizon_s=int(horizon_min) * 60,
                             frame_dt_s=config.FRAME_DT_S)
    res.provenance = dict(res.provenance)
    res.provenance["roads"] = (p["roads"].provenance.to_dict() if p.get("roads") is not None else None)
    res.provenance["data_mode"] = _data_mode
    return res


@lru_cache(maxsize=8)
def _run_scenario_cached(scenario_id: str, blockage_json: str, horizon_min: int) -> SimulationResult:
    """The actual physics run for a STATIC pilot scenario (moderate/heavy/cloudburst/july2005), memoized by
    the fully-canonicalised request. A simulation is deterministic given (scenario_id, blockage spec,
    horizon) -- the engine has no randomness -- so an identical repeat request (the frontend re-opening a
    scenario, /api/compare's 'normal' side reused across several 'blocked' variants, a client retry) is pure
    recomputation otherwise. `run_scenario` below still mints a fresh run_id and a fresh `_runs` entry on
    every call, so the REST contract (each POST /api/simulate returns its own run_id) is unchanged -- only
    the expensive physics loop is shared. Bounded to 8 entries (each holds ~37 frames of full-grid state)
    since this is meant to catch the handful of requests a demo session actually repeats, not to cache
    unboundedly. Deliberately NOT used for scenario_id='live' -- see _run_live_scenario: an lru_cache here
    would freeze a live run's rainfall forever at whatever it was on the first request, defeating the point
    of "live"; IMDObservationProvider has its own short-TTL cache for that instead."""
    p = get_pilot()
    scen = p["scenarios"].get(scenario_id)
    if scen is None:
        raise KeyError(scenario_id)
    return _run_physics(scen, json.loads(blockage_json), horizon_min, p)


def _run_live_scenario(blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='live': fetch the current IMD-observed rainfall (via the module-level, TTL-cached
    IMDObservationProvider) and run the same physics engine on it. Raises ProviderUnavailable (propagated to
    the caller as HTTP 503, see api/main.py) rather than silently substituting a scenario/replay while still
    labelled live -- see docs/LIVE_RAINFALL_AUDIT.md and the FAILURE FALLBACK design it documents."""
    from ..rainfall.provider import list_providers, LIVE_ID, ProviderUnavailable
    try:
        scen, meta = list_providers()[LIVE_ID].get(LIVE_ID)
    except ProviderUnavailable as ex:
        source_manager.note_attempt(LIVE_ID, "LIVE", False, str(ex))   # so status never shows a stale "IMD LIVE"
        raise
    source_manager.note_attempt(LIVE_ID, "LIVE", True, None)
    res = _run_physics(scen, blockage, horizon_min, get_pilot())
    res.provenance = dict(res.provenance)
    # Structured (not just prose) live-source detail for the UI's SOURCE/MODE/STATION/RETRIEVED/RAINFALL/
    # FORECAST EXTENSION breakdown -- see RainfallSourceMeta.detail. Additive key; res.provenance["rainfall"]
    # (the scenario's own Provenance) is untouched, so nothing that already reads it needs to change.
    res.provenance["rainfall_source"] = meta.to_dict()
    res.provenance["pilot_data_mode"] = _data_mode
    res.provenance["data_mode"] = run_data_mode(res)
    return res


def _run_ecmwf_scenario(blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='ecmwf': fetch the current ECMWF NWP precipitation forecast (via Open-Meteo, module-level
    TTL-cached ECMWFForecastProvider) and run the same physics engine on it. Raises ProviderUnavailable
    (propagated to the caller as HTTP 503, see api/main.py) rather than silently substituting synthetic
    rainfall while still labelled ECMWF -- see docs/ECMWF_OPENMETEO_AUDIT.md. Mirrors _run_live_scenario
    exactly; kept as a separate function (not merged with it) because the two rainfall sources must never be
    conflated -- an ECMWF run must never be reported or displayed as an IMD live observation."""
    from ..rainfall.provider import list_providers, ECMWF_ID
    scen, meta = list_providers()[ECMWF_ID].get(ECMWF_ID)
    res = _run_physics(scen, blockage, horizon_min, get_pilot())
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = meta.to_dict()
    return res


def _run_radar_scenario(blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='imd_radar': the IMD Doppler Weather Radar boundary. In this build the provider ALWAYS
    raises `ProviderUnavailable` on the first line below (an "ACCESS PENDING -- ..." reason), which
    api/main.py turns into HTTP 503 exactly as it does for the live/ECMWF paths -- so a radar request fails
    honestly and is never quietly served by a scenario, the replay, the IMD observation or the ECMWF
    forecast wearing a radar label. See IMDRadarNowcastProvider's docstring, docs/LIVE_RAINFALL_AUDIT.md
    sections 8b/8c and decision D-14 for why radar is disabled.

    The physics call below is deliberately left in place (and deliberately unreachable today): it is the
    identical shape used by _run_live_scenario/_run_ecmwf_scenario, so if a real, licensed, documented radar
    endpoint ever exists, the only thing that has to change is the provider -- not this dispatch path.
    """
    from ..rainfall.provider import list_providers, RADAR_ID
    scen, meta = list_providers()[RADAR_ID].get(RADAR_ID)   # raises ProviderUnavailable in this build
    res = _run_physics(scen, blockage, horizon_min, get_pilot())
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = meta.to_dict()
    return res


def _run_gridded_scenario(scenario_id: str, blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='imerg' or 'synthetic_spatial': sources that produce a genuine [T, ny, nx] rainfall FIELD.

    Structurally identical to _run_live_scenario/_run_ecmwf_scenario, with one required difference: these
    providers need the model grid, because resampling a gridded source onto the model grid is the provider's
    responsibility, not the engine's (the engine refuses a field whose grid doesn't match -- see
    simulation/engine.py). Everything else -- ProviderUnavailable propagating to HTTP 503, never substituting
    another source under this label -- behaves exactly as the other real-source paths do.
    """
    from ..rainfall.provider import list_providers
    pilot = get_pilot()
    scen, meta = list_providers()[scenario_id].get(
        scenario_id, grid=pilot["terrain"].grid, horizon_s=int(horizon_min) * 60)
    res = _run_physics(scen, blockage, horizon_min, pilot)
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = meta.to_dict()
    res.provenance["pilot_data_mode"] = _data_mode
    res.provenance["data_mode"] = run_data_mode(res)
    return res


from ..rainfall.source_manager import SourceManager, AUTO_ID, DEMO_ID, GOLDEN_SCENARIO_ID  # noqa: E402

source_manager = SourceManager(cache_dir=config.STATE_DIR / "last_good_rainfall")


def _run_auto_scenario(blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='auto': best available rainfall with EXPLICIT failover (see rainfall/source_manager.py):
    IMD live -> radar-derived -> ECMWF -> last-good cache -> deterministic demo. The chosen source, every failed
    attempt and the resulting status label travel with the run; nothing is ever relabelled as live."""
    from ..rainfall.provider import list_providers, LIVE_ID, IMD_SRI_ID, ECMWF_ID
    from ..rainfall.source_manager import LIVE, RADAR, FORECAST
    pilot = get_pilot()
    scen, meta, _ = source_manager.resolve(
        list_providers(), [(LIVE_ID, LIVE), (IMD_SRI_ID, RADAR), (ECMWF_ID, FORECAST)],
        pilot["terrain"].grid, int(horizon_min) * 60, demo_scenario=pilot["scenarios"][GOLDEN_SCENARIO_ID])
    res = _run_physics(scen, blockage, horizon_min, pilot)
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = meta.to_dict()
    res.provenance["pilot_data_mode"] = _data_mode
    res.provenance["data_mode"] = run_data_mode(res)
    return res


def _run_demo_scenario(blockage: dict, horizon_min: int) -> SimulationResult:
    """scenario_id='demo': the deterministic golden walkthrough (the `cloudburst` design storm), labelled DEMO."""
    from dataclasses import replace
    pilot = get_pilot()
    scen = replace(pilot["scenarios"][GOLDEN_SCENARIO_ID], id=DEMO_ID)
    res = _run_physics(scen, blockage, horizon_min, pilot)
    res.provenance = dict(res.provenance)
    res.provenance["rainfall_source"] = {
        "source_type": "scenario", "source_name": scen.name, "timestamp": "N/A (demo scenario)",
        "forecast_horizon_min": int(horizon_min), "resolution_min": 5, "data_mode": "SYNTHETIC",
        "provenance": scen.provenance.to_dict(),
        "detail": {"source_status": {"requested": DEMO_ID, "label": "DEMO", "source": DEMO_ID, "is_live": False,
                                     "attempts": [], "cached": None, "fell_back": False}}}
    return res


def run_scenario(scenario_id: str, blockage: dict, horizon_min: int) -> SimulationResult:
    """Blocking; call via run_in_threadpool."""
    from ..rainfall.provider import LIVE_ID, ECMWF_ID, RADAR_ID, IMERG_ID, SPATIAL_SYNTHETIC_ID, IMD_SRI_ID
    if scenario_id == AUTO_ID:
        res = _run_auto_scenario(blockage or {"mode": "none"}, int(horizon_min))
    elif scenario_id == DEMO_ID:
        res = _run_demo_scenario(blockage or {"mode": "none"}, int(horizon_min))
    elif scenario_id == LIVE_ID:
        res = _run_live_scenario(blockage or {"mode": "none"}, int(horizon_min))
    elif scenario_id == ECMWF_ID:
        res = _run_ecmwf_scenario(blockage or {"mode": "none"}, int(horizon_min))
    elif scenario_id == RADAR_ID:
        res = _run_radar_scenario(blockage or {"mode": "none"}, int(horizon_min))
    elif scenario_id in (IMERG_ID, SPATIAL_SYNTHETIC_ID, IMD_SRI_ID):
        res = _run_gridded_scenario(scenario_id, blockage or {"mode": "none"}, int(horizon_min))
    else:
        cached = _run_scenario_cached(scenario_id, _canon_blockage(blockage), int(horizon_min))
        res = copy.copy(cached)  # fresh run_id/cache-slot per call; frames/mass_balance/provenance shared read-only
    res.run_id = uuid.uuid4().hex[:10]
    put_run(res)
    return res


def clear_scenario_cache() -> None:
    """Test hook: drop memoized simulation results (paired with set_pilot/reset_pilot in tests that swap
    pilot data but reuse scenario ids, which would otherwise return a stale cached run)."""
    _run_scenario_cached.cache_clear()
