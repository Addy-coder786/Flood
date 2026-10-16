"""Road status for one segment over the time window a vehicle is on it (DECISIONS.md D-25, docs/ROAD_ACCESS.md).

Checked in this order; the first match wins:
  CLOSED     a confirmed closure (events.py) is in force during the window. Never overridden by a dry forecast.
             The reason notes when the closure report is stale or contested by a lower-tier reopen (D-32).
  UNKNOWN    the segment overlaps no model grid cell, or the window ends beyond the last forecast frame.
  AT_RISK    forecast depth during the window reaches the vehicle's clearance limit. Not used for routing.
  UNCERTAIN  an unconfirmed (PUBLIC) closure report covers the window (never CLOSED without an operator), or the
             forecast depth is within ACCESS_UNCERTAINTY_BAND_CM below the limit (inside the DTM's ~0.2 m
             vertical precision), or the segment crosses DEM cells flagged unreliable (terrain/pits.py).
  USABLE     none of the above. Means "no known reason it is impassable", never "guaranteed safe".

The depth for a window [t0, t1] is the maximum over every forecast frame from the last frame at or before t0 to the
first frame at or after t1: conservative, so a rise between two 5-minute frames is not missed.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import numpy as np

from .. import config
from .events import ClosureBook

STATUSES = ("USABLE", "AT_RISK", "UNCERTAIN", "CLOSED", "UNKNOWN")
BLOCKING = frozenset({"AT_RISK", "CLOSED"})


@dataclass(frozen=True)
class Forecast:
    """Per-segment depth series of one simulation run. A segment missing from `streets_cm` is dry (0 cm); whether
    a segment is linked to the model at all is SegmentContext's job, not this one's."""
    t_min: tuple[float, ...]
    streets_cm: dict[str, list[float]]
    label: str = "simulation run"

    @property
    def horizon_t_min(self) -> float:
        return self.t_min[-1]

    def depth_cm(self, seg_id: str, t0: float, t1: float) -> Optional[float]:
        """Max depth over the frames spanning [t0, t1]; None if the window is outside the forecast."""
        if not self.t_min or t0 < self.t_min[0] - 1e-9 or t1 > self.t_min[-1] + 1e-9:
            return None
        row = self.streets_cm.get(seg_id)
        if row is None:
            return 0.0
        k0 = max(bisect_right(self.t_min, t0 + 1e-9) - 1, 0)
        k1 = min(bisect_left(self.t_min, t1 - 1e-9), len(row) - 1)
        return float(max(row[k0:k1 + 1]))


@dataclass(frozen=True)
class SegmentContext:
    unlinked: frozenset[str]        # no model grid cell within the street buffer: depth is not known
    dem_flagged: frozenset[str]     # crosses DEM cells flagged unreliable


def build_context(cells_by_seg: dict[str, np.ndarray], dem_mask: Optional[np.ndarray]) -> SegmentContext:
    flat = dem_mask.ravel() if dem_mask is not None else None
    unlinked = frozenset(s for s, idx in cells_by_seg.items() if idx.size == 0)
    flagged = frozenset(s for s, idx in cells_by_seg.items()
                        if flat is not None and idx.size and bool(flat[idx].any()))
    return SegmentContext(unlinked, flagged)


@lru_cache(maxsize=1)
def load_dem_mask() -> Optional[np.ndarray]:
    path = config.DATA_PROCESSED / "dem_reliability_mask.npz"
    return np.load(path)["mask"].astype(bool) if path.is_file() else None


def classify(seg_id: str, t0: float, t1: float, *, forecast: Forecast, ctx: SegmentContext,
             closures: ClosureBook, limit_cm: float,
             band_cm: float = config.ACCESS_UNCERTAINTY_BAND_CM, vehicle: Optional[str] = None) -> dict:
    closure = closures.closed_during(seg_id, t0, t1, vehicle=vehicle)
    if closure is not None:
        info = closures.annotate(closure)
        what = "restriction for " + ", ".join(closure.vehicle_types) if closure.vehicle_types else "closure"
        reason = (f"confirmed {what} {closure.event_id} (received T+{closure.received_t_min:g} min, "
                  f"in force from T+{closure.applies_from_t_min:g} min)")
        if info["stale"]:                      # a flag only: a stale closure is still a closure (D-32)
            reason += f"; stale {info['age_min']:g} min"
        if info["contested_by"]:
            reason += "; contested by " + ", ".join(info["contested_by"])
        return {"status": "CLOSED", "depth_cm": None, "closure_event_id": closure.event_id, "reason": reason}
    if seg_id in ctx.unlinked:
        return {"status": "UNKNOWN", "depth_cm": None, "reason": "road is not linked to any model grid cell"}
    depth = forecast.depth_cm(seg_id, t0, t1)
    if depth is None:
        return {"status": "UNKNOWN", "depth_cm": None,
                "reason": f"time on this road is beyond the forecast horizon (T+{forecast.horizon_t_min:g} min)"}
    if depth >= limit_cm:
        return {"status": "AT_RISK", "depth_cm": round(depth, 1),
                "reason": f"forecast depth {depth:.0f} cm reaches the {limit_cm:g} cm vehicle limit"}
    report = closures.unconfirmed_during(seg_id, t0, t1, vehicle=vehicle)
    if report is not None:                     # a PUBLIC closure report: UNCERTAIN, never CLOSED (D-32)
        return {"status": "UNCERTAIN", "depth_cm": round(depth, 1), "unconfirmed_event_id": report.event_id,
                "reason": f"unconfirmed PUBLIC report of closure {report.event_id} (received T+{report.received_t_min:g}); "
                          "not confirmed by an operator"}
    if depth >= limit_cm - band_cm:
        return {"status": "UNCERTAIN", "depth_cm": round(depth, 1),
                "reason": f"forecast depth {depth:.0f} cm is within {band_cm:g} cm of the {limit_cm:g} cm limit "
                          "(inside the terrain model's vertical precision)"}
    if seg_id in ctx.dem_flagged:
        return {"status": "UNCERTAIN", "depth_cm": round(depth, 1),
                "reason": "road crosses ground where the elevation model is flagged unreliable"}
    return {"status": "USABLE", "depth_cm": round(depth, 1), "reason": "no known reason it is impassable"}
