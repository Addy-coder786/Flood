"""Loaders for data/processed/pilot/. The only sanctioned way to get real pilot inputs."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..config import DATA_PROCESSED
from ..contracts import Grid, Terrain, DrainageNetwork, RainfallScenario, RoadGraph
from ..provenance import Provenance, Tag
from .mcgm import network_from_dict
from .osm import roads_from_dict

REQUIRED = ["network.json", "terrain.npz", "terrain.json", "roads.json", "hotspots.json", "scenarios.json"]


class PilotDataMissing(FileNotFoundError):
    pass


def _check(d: Path):
    missing = [f for f in REQUIRED if not (d / f).exists()]
    if missing:
        raise PilotDataMissing(
            f"Processed pilot data missing in {d}: {missing}. "
            "Run: backend/.venv/Scripts/python -m floodnet.data.build_pilot")


def _prov(p: dict) -> Provenance:
    return Provenance(Tag(p["tag"]), p["source"], p.get("note", ""))


def load_grid(d: Path = DATA_PROCESSED) -> Grid:
    with open(d / "terrain.json", "r", encoding="utf-8") as fh:
        g = json.load(fh)["grid"]
    return Grid(x0=g["x0"], y0=g["y0"], res=g["res"], nx=g["nx"], ny=g["ny"])


def load_terrain(d: Path = DATA_PROCESSED) -> Terrain:
    with open(d / "terrain.json", "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    g = meta["grid"]
    arr = np.load(d / "terrain.npz")
    return Terrain(grid=Grid(x0=g["x0"], y0=g["y0"], res=g["res"], nx=g["nx"], ny=g["ny"]),
                   z=arr["z"].astype(np.float32), impervious=arr["impervious"].astype(np.float32),
                   building=arr["building"].astype(bool), provenance=_prov(meta["provenance"]),
                   impervious_provenance=_prov(meta["impervious_provenance"]),
                   building_provenance=_prov(meta["building_provenance"]))


def load_network(d: Path = DATA_PROCESSED) -> DrainageNetwork:
    with open(d / "network.json", "r", encoding="utf-8") as fh:
        js = json.load(fh)
    js.pop("grid", None)
    return network_from_dict(js)


def load_roads(d: Path = DATA_PROCESSED) -> RoadGraph:
    with open(d / "roads.json", "r", encoding="utf-8") as fh:
        return roads_from_dict(json.load(fh))


def load_hotspots(d: Path = DATA_PROCESSED) -> list[dict]:
    with open(d / "hotspots.json", "r", encoding="utf-8") as fh:
        return json.load(fh)["hotspots"]


def load_scenarios(d: Path = DATA_PROCESSED) -> dict[str, RainfallScenario]:
    with open(d / "scenarios.json", "r", encoding="utf-8") as fh:
        js = json.load(fh)
    out = {}
    for k, v in js.items():
        out[k] = RainfallScenario(id=v["id"], name=v["name"], t_s=np.array(v["t_min"], dtype=float) * 60.0,
                                  intensity_mm_h=np.array(v["intensity_mm_h"], dtype=float),
                                  provenance=_prov(v["provenance"]), description=v.get("description", ""))
    return out


def load_pilot(d: Path = DATA_PROCESSED) -> dict:
    """Process-wide cached by resolved directory: parsing the real network/terrain/roads JSON is the
    dominant cost of a pilot load (tens of thousands of manhole/conduit records), and nothing in this
    codebase mutates the returned dataclasses in place (blockage etc. all go through dataclasses.replace),
    so repeat calls -- from tests, validation scripts, or API state -- can safely share one parse."""
    return _load_pilot_cached(Path(d).resolve())


@lru_cache(maxsize=4)
def _load_pilot_cached(d: Path) -> dict:
    _check(d)
    return {"terrain": load_terrain(d), "net": load_network(d), "roads": load_roads(d),
            "hotspots": load_hotspots(d), "scenarios": load_scenarios(d)}
