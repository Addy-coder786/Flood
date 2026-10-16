"""Rainfall source failover -- explicit, ordered, and never silent.

Scenario id `auto` asks for "the best rainfall FloodNet can honestly get right now":

    1. LIVE           IMD authenticated live observation            (needs key + unexpired token + bound IP)
    2. RADAR-DERIVED  IMD Mumbai-Veravali DWR SRI image-derived      (needs a fresh, decodable public image)
    3. FORECAST       ECMWF NWP via Open-Meteo                       (needs the network)
    4. CACHED         the last field any of 1-3 produced             (clearly aged; refused beyond MAX_CACHE_AGE_S)
    5. DEMO           the deterministic golden scenario              (always available; synthetic; never "live")

Rules:
  * Every attempt is recorded (source, ok, reason) and returned with the result -- fallback is never hidden.
  * A source that fails raises nothing to the caller; the next one is tried. Only `auto` behaves this way:
    asking for a SPECIFIC source (`live`, `imd_sri`, `ecmwf`) still fails loudly, as before.
  * CACHED carries the original source, its timestamp and its age; it is never relabelled LIVE.
  * Reasons come from the providers' own sanitised messages -- no credential ever appears in them.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..contracts import RainfallScenario
from ..provenance import Provenance, Tag

log = logging.getLogger(__name__)

AUTO_ID = "auto"
DEMO_ID = "demo"
GOLDEN_SCENARIO_ID = "cloudburst"          # deterministic design storm used for the judge walkthrough
LIVE, RADAR, FORECAST, CACHED, DEMO = "LIVE", "RADAR-DERIVED", "FORECAST", "CACHED", "DEMO"
MAX_CACHE_AGE_S = 6 * 3600


# source_type (or failover status) -> operator-facing label; the ONLY mapping the backend exposes
DISPLAY_LABEL = {LIVE: "IMD LIVE", RADAR: "IMD DWR RADAR-DERIVED", FORECAST: "ECMWF NWP", CACHED: "CACHED", DEMO: "DEMO",
                 "HISTORICAL": "HISTORICAL", "SCENARIO": "SCENARIO"}
_LABEL_BY_SOURCE_TYPE = {"live_observation": LIVE, "radar_image_derived": RADAR, "ecmwf_forecast": FORECAST,
                         "historical_replay": "HISTORICAL", "scenario": "SCENARIO"}


def active_source_metadata(rainfall_source: Optional[dict], now: Optional[float] = None) -> Optional[dict]:
    """{source, source_label, timestamp, age_seconds, status} for a run's `rainfall_source` block -- the only
    rainfall-source facts the API volunteers. `status` is "ok", "fallback" (auto run that did not get its first
    choice) or "cached" / "demo". Never contains a credential or an upstream error body."""
    if not rainfall_source:
        return None
    st = (rainfall_source.get("detail") or {}).get("source_status") or {}
    label = st.get("label") or _LABEL_BY_SOURCE_TYPE.get(rainfall_source.get("source_type"), "SCENARIO")
    ts = rainfall_source.get("timestamp")
    if st.get("cached"):
        ts = st["cached"].get("cached_at", ts)
    age = None
    try:
        age = round((now if now is not None else time.time()) - datetime.fromisoformat(str(ts)).timestamp(), 1)
    except (TypeError, ValueError):
        pass
    status = "cached" if label == CACHED else "demo" if label == DEMO else "fallback" if st.get("fell_back") else "ok"
    out = {"source": rainfall_source.get("source_name"), "source_label": DISPLAY_LABEL.get(label, label),
           "timestamp": ts if age is not None else None, "age_seconds": age, "status": status,
           "fallback_active": bool(st.get("fell_back"))}
    # D-36 P0-3: when the provider declared a timing block, report the input's CURRENT staleness (re-evaluated
    # now, from the block's own age basis and thresholds -- e.g. IMD: age of the observation, not of the run).
    from .provider import timing_staleness
    fresh = timing_staleness((rainfall_source.get("detail") or {}).get("timing"),
                             datetime.fromtimestamp(now, timezone.utc) if now is not None else None)
    if fresh is not None:
        out.update(fresh)
    return out


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).isoformat()


class SourceManager:
    def __init__(self, cache_dir: Optional[Path] = None, clock: Callable[[], float] = time.time):
        self._cache_dir, self._clock = cache_dir, clock
        self._lock = threading.Lock()
        self._last_good: Optional[dict] = None       # {"scen", "meta_dict", "label", "source", "at"}
        self._health: dict[str, dict] = {}           # source id -> last attempt
        self._current: Optional[dict] = None
        self._disk_checked = False

    # ------------------------------------------------------------------ public
    def resolve(self, providers: dict, priority: list[tuple[str, str]], grid, horizon_s: int,
                demo_scenario: RainfallScenario):
        """-> (RainfallScenario with id 'auto', RainfallSourceMeta-like dict, status dict)."""
        from .provider import ProviderUnavailable, RainfallSourceMeta
        attempts = []
        for sid, label in priority:
            try:
                scen, meta = providers[sid].get(sid, grid=grid, horizon_s=horizon_s)
            except (ProviderUnavailable, KeyError, NotImplementedError) as ex:
                self._note(sid, label, False, str(ex))
                attempts.append({"source": sid, "label": label, "ok": False, "reason": str(ex)[:300]})
                continue
            except Exception as ex:  # noqa: BLE001 -- a broken source must never take the forecast down
                log.exception("rainfall source %s failed unexpectedly", sid)
                self._note(sid, label, False, type(ex).__name__)
                attempts.append({"source": sid, "label": label, "ok": False, "reason": f"unexpected {type(ex).__name__}"})
                continue
            self._note(sid, label, True, None)
            attempts.append({"source": sid, "label": label, "ok": True, "reason": None})
            self._remember(scen, meta, label, sid)
            return self._finish(scen, meta, label, sid, attempts, cached=None)

        cached = self._load_last_good()
        now = self._clock()
        grid_ok = cached is not None and (cached["scen"].field_grid is None or cached["scen"].field_grid == grid)
        if cached is not None and grid_ok and now - cached["at"] <= MAX_CACHE_AGE_S:
            attempts.append({"source": "cache", "label": CACHED, "ok": True, "reason": None})
            meta = RainfallSourceMeta(**{**cached["meta_kwargs"], "provenance": cached["scen"].provenance})
            return self._finish(cached["scen"], meta, CACHED, cached["source"], attempts,
                                cached={"original_label": cached["label"], "cached_at": _iso(cached["at"]),
                                        "age_min": round((now - cached["at"]) / 60.0, 1)})
        attempts.append({"source": "cache", "label": CACHED, "ok": False,
                         "reason": "no cached field" if cached is None else
                         ("cached field older than 6 h" if grid_ok else "cached field is for a different grid")})

        attempts.append({"source": DEMO_ID, "label": DEMO, "ok": True, "reason": None})
        prov = Provenance(Tag.SYNTHETIC, demo_scenario.provenance.source,
                          "DEMO fallback: every live rainfall source was unavailable, so the deterministic golden "
                          "scenario is shown. It is synthetic and is not a forecast of current conditions. "
                          + (demo_scenario.provenance.note or ""))
        scen = replace(demo_scenario, provenance=prov)
        meta = RainfallSourceMeta(source_type="scenario", source_name=demo_scenario.name,
                                  timestamp="N/A (demo scenario)", forecast_horizon_min=horizon_s // 60,
                                  resolution_min=5, data_mode=Tag.SYNTHETIC.value, provenance=prov, detail={})
        return self._finish(scen, meta, DEMO, DEMO_ID, attempts, cached=None)

    def health(self) -> dict:
        with self._lock:
            out = {k: dict(v) for k, v in self._health.items()}
            current = dict(self._current) if self._current else None
        cached = self._load_last_good()
        now = self._clock()
        cache = ({"available": False} if cached is None else
                 {"available": now - cached["at"] <= MAX_CACHE_AGE_S, "original_label": cached["label"],
                  "source": cached["source"], "cached_at": _iso(cached["at"]),
                  "age_min": round((now - cached["at"]) / 60.0, 1)})
        if current and current.get("resolved_at_epoch"):
            current["age_min"] = round((now - current.pop("resolved_at_epoch")) / 60.0, 1)
        return {"sources": out, "cache": cache, "current": current}

    def reset(self) -> None:
        with self._lock:
            self._last_good, self._health, self._current, self._disk_checked = None, {}, None, True

    def note_attempt(self, sid: str, label: str, ok: bool, reason: Optional[str]) -> None:
        """Record an attempt made outside `resolve` (e.g. an explicitly chosen IMD-live run)."""
        self._note(sid, label, ok, reason)

    # ------------------------------------------------------------------ internals
    def _note(self, sid: str, label: str, ok: bool, reason: Optional[str]) -> None:
        with self._lock:
            self._health[sid] = {"label": label, "ok": ok, "checked_at": _iso(self._clock()),
                                 "reason": None if ok else (reason or "")[:300]}

    def _finish(self, scen, meta, label, sid, attempts, cached):
        now = self._clock()
        status = {"requested": AUTO_ID, "label": label, "source": sid, "is_live": label == LIVE,
                  "source_timestamp": meta.timestamp, "resolved_at": _iso(now), "attempts": attempts,
                  "cached": cached,
                  "fell_back": any(not a["ok"] for a in attempts)}
        with self._lock:
            self._current = {"label": label, "source": sid, "source_name": meta.source_name,
                             "source_timestamp": meta.timestamp, "resolved_at": _iso(now), "resolved_at_epoch": now}
        detail = {**(meta.detail or {}), "source_status": status}
        from .provider import RainfallSourceMeta
        meta = RainfallSourceMeta(source_type=meta.source_type, source_name=meta.source_name, timestamp=meta.timestamp,
                                  forecast_horizon_min=meta.forecast_horizon_min, resolution_min=meta.resolution_min,
                                  data_mode=meta.data_mode, provenance=meta.provenance, detail=detail)
        return replace(scen, id=AUTO_ID), meta, status

    def _remember(self, scen, meta, label, sid) -> None:
        entry = {"scen": scen, "label": label, "source": sid, "at": self._clock(),
                 "meta_kwargs": {"source_type": meta.source_type, "source_name": meta.source_name,
                                 "timestamp": meta.timestamp, "forecast_horizon_min": meta.forecast_horizon_min,
                                 "resolution_min": meta.resolution_min, "data_mode": meta.data_mode,
                                 "detail": meta.detail or {}}}
        with self._lock:
            self._last_good = entry
        if self._cache_dir is None:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            arrays = {"t_s": scen.t_s, "intensity_mm_h": scen.intensity_mm_h}
            if scen.intensity_field_mm_h is not None:
                arrays["field"] = scen.intensity_field_mm_h.astype(np.float32)
            np.savez_compressed(self._cache_dir / "last_good.npz", **arrays)
            (self._cache_dir / "last_good.json").write_text(json.dumps({
                "label": label, "source": sid, "at": entry["at"], "meta_kwargs": entry["meta_kwargs"],
                "scenario": {"id": scen.id, "name": scen.name, "description": scen.description},
                "provenance": scen.provenance.to_dict(),
                "grid": scen.field_grid.to_dict() if scen.field_grid is not None else None}, default=str), encoding="utf-8")
        except OSError as ex:
            log.warning("could not persist the last-good rainfall field: %s", ex)

    def _load_last_good(self) -> Optional[dict]:
        with self._lock:
            if self._last_good is not None or self._disk_checked or self._cache_dir is None:
                return self._last_good
            self._disk_checked = True
        try:
            doc = json.loads((self._cache_dir / "last_good.json").read_text(encoding="utf-8"))
            with np.load(self._cache_dir / "last_good.npz") as z:
                field = z["field"].astype(np.float64) if "field" in z.files else None
                t_s, inten = z["t_s"], z["intensity_mm_h"]
            from ..contracts import Grid
            g = doc.get("grid")
            grid = Grid(x0=g["x0"], y0=g["y0"], res=g["res"], nx=g["nx"], ny=g["ny"]) if g and field is not None else None
            p = doc["provenance"]
            scen = RainfallScenario(id=doc["scenario"]["id"], name=doc["scenario"]["name"], t_s=t_s, intensity_mm_h=inten,
                                    provenance=Provenance(Tag(p["tag"]), p["source"], p.get("note", "")),
                                    description=doc["scenario"].get("description", ""),
                                    intensity_field_mm_h=field, field_grid=grid)
            entry = {"scen": scen, "label": doc["label"], "source": doc["source"], "at": float(doc["at"]),
                     "meta_kwargs": doc["meta_kwargs"]}
            with self._lock:
                self._last_good = entry
        except (OSError, KeyError, ValueError) as ex:
            log.info("no usable last-good rainfall cache on disk (%s)", type(ex).__name__)
        return self._last_good
