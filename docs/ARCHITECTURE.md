# ARCHITECTURE — source of truth for all agents (v0.1, 2026-09-08)

Decisions: `docs/DECISIONS.md`. Contracts: `backend/floodnet/contracts.py` (dataclasses + Protocols). Constants: `backend/floodnet/config.py`.
**Rule: agents implement the protocols and fill the dataclasses. Nobody redefines them. The integrator owns `contracts.py`, `config.py`, `simulation/engine.py`.**

## Pipeline (one loop, `simulation/engine.py`)
```
RainfallScenario (mm/h)  -> runoff_fn (terrain.runoff) -> SurfaceModel.add_runoff
SurfaceModel.take_volume at inlet cells  <= DrainageModel.inlet_capacity_m3
DrainageModel.add_inflow -> DrainageModel.step -> surcharge m3 -> SurfaceModel.add_volume at node cells
SurfaceModel.step  -> every 300 s: Frame(depth grid, node states, edge util, street depths)
```
Units: s, m, m3, m3/s internally. mm/h for rain input. cm and minutes only at the API/UI.
CRS: EPSG:32643 everywhere; lon/lat only when serialising for the map.
Grid: `z[j,i]`, row 0 = south, `x = x0+(i+.5)res`.

## Ownership
| Dir (`backend/floodnet/`) | Agent | Delivers |
|---|---|---|
| `data/` | C Data | `mcgm.py` (snapshot → `DrainageNetwork` for pilot, Existing only, node inverts, outfalls=sinks), `contours.py` (layer 301 → DTM `Terrain.z`), `osm.py` (Overpass → `RoadGraph`, building mask, impervious), `hotspots.py` (layer 344), `scenarios.py` (4 `RainfallScenario`s), `build_pilot.py` (writes `data/processed/pilot/`), `load.py` (loaders used by API) |
| `terrain/` | A Hydrology | `runoff.py` (`runoff_fn`), `surface.py` (`SurfaceModel` impl, mass-conserving storage-cell scheme), `dem_provider.py` (`DEMProvider`: contour-interpolated / local NPZ / labelled synthetic) |
| `drainage/` | B Drainage | `hydraulics.py` (`DrainageModel` impl: Manning capacity, storage, HGL, surcharge, blockage, inlet cap, cause), `attributes.py` (ESTIMATED n / inlet / storage rules with citations), `scenarios.py` (blockage scenarios) |
| `streets/`, `routing/` | F Routing+QA | `aggregate.py` (`street_fn`: grid depth → per-segment max), `router.py` (flood-aware Dijkstra, vehicle limits, time-aware), tests |
| `api/` | D Backend | FastAPI: `/`, `/api/topology`, `/api/terrain`, `/api/scenarios`, `/api/simulate`, `/api/nowcast`, `/api/simulation/{id}/frame/{t_min}`, `/api/route`, `/api/storm/replay`, static mount of `frontend/` |
| `frontend/` (repo root) | E Frontend | Leaflet dashboard, vanilla JS, provenance badges |
| `tests/` | F + everyone | pytest; F owns `test_e2e.py` and sanity checks (mass balance, blocked > normal) |

## Fixtures rule
Every agent must be runnable on a tiny SYNTHETIC fixture (`backend/tests/fixtures/`), labelled `Tag.SYNTHETIC`, before real data lands. When `data/processed/pilot/` exists, `floodnet.data.load` is the only way to get real inputs.

## Provenance rule
Every payload the API returns includes `provenance` blocks. REAL: MCGM geometry/inverts/ground levels, contours, OSM roads, Chitale-derived rainfall. ESTIMATED: Manning n, node invert rule, outfall inference, inlet capacity, storage area, impervious fraction from OSM. SYNTHETIC: design/user storms, fixtures. NWP: Open-Meteo. Never mix labels.

## Environment
`backend/.venv` via `uv` (already created). Deps fixed in `pyproject.toml` — do **not** add packages; report needs to the integrator. Run: `cd backend && .venv/Scripts/python -m uvicorn floodnet.api.main:app --port 8000`.
