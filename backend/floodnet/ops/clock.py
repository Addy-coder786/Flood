"""The incident clock (docs/LIVE_OPERATIONS.md §2).

FloodNet times decisions in incident minutes (T+min from the forecast start). Live operations also need a "now" for
reports and GPS fixes that arrive from phones. The store keeps one clock document:
  REPLAY  a scenario demonstration: now is the dispatcher's decision time and does not move by itself
  LIVE    a real incident: now advances with the wall clock from the moment the clock was set
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .store import OpsStore, iso, utc_now

MODES = ("REPLAY", "LIVE")
_ID = ("clock", "incident")


def get_clock(store: OpsStore) -> dict:
    c = store.get(*_ID)
    return c or {"mode": "REPLAY", "t_min": 0.0, "set_at": iso(utc_now()), "_version": 0}


def set_clock(store: OpsStore, mode: str, t_min: float, *, now: Optional[datetime] = None, actor: str = "dispatcher") -> dict:
    mode = str(mode).upper()
    if mode not in MODES:
        raise ValueError(f"clock mode must be one of {MODES}, got {mode!r}")
    t = float(t_min)
    if not (t >= 0 and t < 1e6):
        raise ValueError("clock t_min must be a finite number of minutes >= 0")
    return store.put(*_ID, {"mode": mode, "t_min": t, "set_at": iso(now or utc_now())}, actor=actor, action="set_clock")


def now_t_min(store: OpsStore, *, now: Optional[datetime] = None) -> float:
    c = get_clock(store)
    if c["mode"] != "LIVE":
        return float(c["t_min"])
    set_at = datetime.fromisoformat(c["set_at"].replace("Z", "+00:00"))
    elapsed = ((now or utc_now()) - set_at).total_seconds() / 60.0
    return round(float(c["t_min"]) + max(0.0, elapsed), 3)
