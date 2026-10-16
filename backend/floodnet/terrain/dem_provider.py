"""DEM / terrain providers -> contracts.Terrain.

LocalNPZProvider      reads data/processed/pilot/*.npz + *.json written by floodnet.data.build_pilot (Agent C).
SyntheticBowlProvider labelled SYNTHETIC test terrain (a bowl with a low point) for unit tests / demos.
OpenTopographyProvider stub: not implemented (needs an API key; no credentials in this repo).
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

from ..contracts import Grid, Terrain
from ..provenance import Provenance, Tag


class DEMProvider(ABC):
    @abstractmethod
    def get_terrain(self, grid: Grid) -> Terrain: ...


def _prov_from_dict(d, default: Provenance) -> Provenance:
    if not isinstance(d, dict):
        return default
    try:
        return Provenance(Tag(d.get("tag", default.tag.value)), d.get("source", default.source), d.get("note", ""))
    except Exception:
        return default


def _grid_from_dict(d: dict) -> Grid:
    return Grid(x0=float(d["x0"]), y0=float(d["y0"]), res=float(d["res"]), nx=int(d["nx"]), ny=int(d["ny"]))


class LocalNPZProvider(DEMProvider):
    """npz keys: z, impervious, building (any of the latter two may be missing -> defaults).
    json: {"grid": {...}, "provenance"|"terrain_provenance": {...}, "impervious_provenance": {...},
           "building_provenance": {...}} - all optional, tolerated."""

    def __init__(self, npz_path: str | Path, json_path: Optional[str | Path] = None):
        self.npz_path = Path(npz_path)
        self.json_path = Path(json_path) if json_path else self.npz_path.with_suffix(".json")

    def stored_grid(self) -> Optional[Grid]:
        if self.json_path.exists():
            meta = json.loads(self.json_path.read_text(encoding="utf-8"))
            if "grid" in meta:
                return _grid_from_dict(meta["grid"])
        return None

    def get_terrain(self, grid: Optional[Grid] = None) -> Terrain:
        data = np.load(self.npz_path, allow_pickle=False)
        keys = set(data.files)
        zkey = "z" if "z" in keys else ("dem" if "dem" in keys else None)
        if zkey is None:
            raise ValueError(f"{self.npz_path}: no 'z' array (keys: {sorted(keys)})")
        z = np.asarray(data[zkey], dtype=np.float32)
        ny, nx = z.shape
        meta = {}
        if self.json_path.exists():
            meta = json.loads(self.json_path.read_text(encoding="utf-8"))
        if grid is None:
            if "grid" in meta:
                grid = _grid_from_dict(meta["grid"])
            else:
                grid = Grid(0.0, 0.0, 10.0, nx, ny)
        if (grid.ny, grid.nx) != (ny, nx):
            raise ValueError(f"grid {grid.ny}x{grid.nx} does not match DEM {ny}x{nx} in {self.npz_path}")
        imp = np.asarray(data["impervious"], dtype=np.float32) if "impervious" in keys \
            else np.full((ny, nx), 0.7, dtype=np.float32)
        bld = np.asarray(data["building"], dtype=bool) if "building" in keys \
            else np.zeros((ny, nx), dtype=bool)
        default = Provenance(Tag.ESTIMATED, str(self.npz_path), "provenance missing in sidecar json")
        prov = _prov_from_dict(meta.get("provenance", meta.get("terrain_provenance", meta.get("z_provenance"))), default)
        imp_prov = _prov_from_dict(meta.get("impervious_provenance"),
                                   default if "impervious" in keys else
                                   Provenance(Tag.ESTIMATED, "default", "impervious missing; 0.7 assumed"))
        bld_prov = _prov_from_dict(meta.get("building_provenance"),
                                   default if "building" in keys else
                                   Provenance(Tag.ESTIMATED, "default", "building mask missing; none assumed"))
        return Terrain(grid=grid, z=z, impervious=imp, building=bld,
                       provenance=prov, impervious_provenance=imp_prov, building_provenance=bld_prov)


class SyntheticBowlProvider(DEMProvider):
    """Parabolic bowl with its low point at the grid centre plus small random noise. SYNTHETIC - not Mumbai."""

    def __init__(self, depth_m: float = 3.0, noise: float = 0.1, seed: int = 0, impervious: float = 0.6,
                 building_mask: Optional[np.ndarray] = None):
        self.depth_m = float(depth_m)
        self.noise = float(noise)
        self.seed = int(seed)
        self.impervious = float(impervious)
        self.building_mask = building_mask

    def get_terrain(self, grid: Grid) -> Terrain:
        rng = np.random.default_rng(self.seed)
        j = np.arange(grid.ny)[:, None]; i = np.arange(grid.nx)[None, :]
        cj, ci = (grid.ny - 1) / 2.0, (grid.nx - 1) / 2.0
        rmax = max(np.hypot(cj, ci), 1.0)
        r = np.hypot(j - cj, i - ci) / rmax
        z = self.depth_m * r ** 2 + rng.normal(0.0, self.noise, size=(grid.ny, grid.nx))
        z = z.astype(np.float32)
        note = "synthetic test terrain - not Mumbai"
        bld = np.zeros((grid.ny, grid.nx), dtype=bool) if self.building_mask is None \
            else np.asarray(self.building_mask, dtype=bool)
        return Terrain(grid=grid, z=z,
                       impervious=np.full((grid.ny, grid.nx), self.impervious, dtype=np.float32),
                       building=bld,
                       provenance=Provenance(Tag.SYNTHETIC, "SyntheticBowlProvider", note),
                       impervious_provenance=Provenance(Tag.SYNTHETIC, "SyntheticBowlProvider", note),
                       building_provenance=Provenance(Tag.SYNTHETIC, "SyntheticBowlProvider", note))


class OpenTopographyProvider(DEMProvider):
    """Stub for OpenTopography global DEM API (SRTM/Copernicus). Requires an API key; not wired in this repo."""

    def __init__(self, api_key: Optional[str] = None, dem_type: str = "COP30"):
        self.api_key = api_key
        self.dem_type = dem_type

    def get_terrain(self, grid: Grid) -> Terrain:
        raise NotImplementedError(
            "OpenTopographyProvider is a stub: no API credentials are available in this repo and 30 m global DEMs "
            "are too coarse for a 10 m street-scale pilot. Use LocalNPZProvider (MCGM contour-derived DTM) or "
            "SyntheticBowlProvider (labelled SYNTHETIC) instead.")
