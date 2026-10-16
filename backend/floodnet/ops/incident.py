"""The shared incident picture (D-38, D-41): the forecast the dispatcher published and the road-event log.

Road events reach the store two ways:
  * the dispatcher's browser syncs its log (ids C1, R2 ... made in the browser); records are immutable, so the same id
    with different content is reported as a conflict and the stored record is kept;
  * drivers and registered responders report from the field; the server gives those events their own ids (F1, F2 ...)
    and records the origin, so a client can never impersonate the dispatcher or overwrite one of its events.
Every event keeps the D-32 fields (received / applies_from / source_tier) and is reconciled by access/events.py.
"""
from __future__ import annotations

from typing import Optional

from ..access.events import ClosureBook, parse_events
from ..access.road_status import Forecast
from .store import OpsStore

_FORECAST = ("forecasts", "active")


def publish_forecast(store: OpsStore, *, run_id: Optional[str], data_mode: str, series_t_min: list[float],
                     streets_cm: dict[str, list[float]], label: Optional[str] = None, actor: str = "dispatcher") -> dict:
    t = [float(x) for x in series_t_min]
    if not t:
        raise ValueError("the forecast has no frames")
    if any(b <= a for a, b in zip(t[:-1], t[1:])):
        raise ValueError("forecast frame times must increase")
    bad = [k for k, v in streets_cm.items() if len(v) != len(t)]
    if bad:
        raise ValueError(f"forecast frames: {len(bad)} road series do not match the {len(t)} frame times (e.g. {bad[0]})")
    with store.transaction():                     # version read and write together (QA M3)
        return _publish(store, run_id, data_mode, t, streets_cm, label, actor)


def _publish(store, run_id, data_mode, t, streets_cm, label, actor):
    prev = store.get(*_FORECAST)
    doc = {"run_id": run_id, "data_mode": str(data_mode), "label": label or f"simulation run {run_id} ({data_mode})",
           "series_t_min": t, "streets_cm": {str(k): [float(x) for x in v] for k, v in streets_cm.items()},
           "forecast_version": (prev["forecast_version"] if prev else 0) + 1}
    return store.put(*_FORECAST, doc, actor=actor, action="publish_forecast")


def get_forecast(store: OpsStore) -> Optional[tuple[Forecast, dict]]:
    d = store.get(*_FORECAST)
    if d is None:
        return None
    meta = {k: d[k] for k in ("run_id", "data_mode", "label", "forecast_version", "_updated_at")}
    return Forecast(tuple(d["series_t_min"]), d["streets_cm"], label=d["label"]), meta


def _canonical(e: dict) -> dict:
    return parse_events([e])[0].to_dict()


def sync_dispatcher_events(store: OpsStore, events: list[dict], actor: str = "dispatcher") -> dict:
    """Merge the browser's road-event log by event_id. Validates every event first (ValueError, nothing stored)."""
    parsed = parse_events(events)
    reserved = [e.event_id for e in parsed if _is_field_id(e.event_id)]
    if reserved:                                  # F1, F2 ... belong to field reports (QA M5)
        raise ValueError(f"event ids {reserved[:3]} are reserved for field reports; use the browser's own ids")
    added, conflicts = [], []
    with store.transaction():
        for raw, ev in zip(events, parsed):
            d = {**ev.to_dict(), "references": list(ev.references), "vehicle_types": list(ev.vehicle_types), "origin": "dispatcher"}
            if raw.get("name"):
                d["name"] = str(raw["name"])[:120]
            old = store.get("road_events", ev.event_id)
            if old is None:
                store.put("road_events", ev.event_id, d, actor=actor, action="sync")
                added.append(ev.event_id)
            elif _canonical(old) != _canonical(d):
                conflicts.append(ev.event_id)
    return {"added": added, "conflicts": conflicts}


def add_field_road_event(store: OpsStore, body: dict, *, origin: str, tier: str) -> dict:
    """A road report from a driver or responder. The server sets id, origin and tier; `client_ref` makes a resend
    of the same report idempotent."""
    ref = str(body.get("client_ref") or "")[:80]
    with store.transaction():
        if ref:
            dup = store.list("road_events", lambda d: d.get("origin") == origin and d.get("client_ref") == ref)
            if dup:
                return dup[0]
        event_id = f"F{store.next_seq('field_road_event')}"
        while store.get("road_events", event_id) is not None:
            event_id = f"F{store.next_seq('field_road_event')}"
        clean = {k: body[k] for k in ("seg_id", "kind", "received_t_min", "applies_from_t_min", "note", "vehicle_types")
                 if k in body}
        ev = parse_events([{**clean, "event_id": event_id, "source_tier": tier,
                            "source": f"{origin} report"}])[0]
        d = {**ev.to_dict(), "references": list(ev.references), "vehicle_types": list(ev.vehicle_types), "origin": origin,
             "client_ref": ref}
        if body.get("name"):
            d["name"] = str(body["name"])[:120]
        if body.get("incident_id"):
            d["incident_id"] = str(body["incident_id"])[:64]
        return store.put("road_events", event_id, d, actor=origin, action="field_report")


def _is_field_id(event_id: str) -> bool:
    return len(event_id) > 1 and event_id[0] == "F" and event_id[1:].isdigit()


def list_road_events(store: OpsStore) -> list[dict]:
    return store.list("road_events")


def closures(store: OpsStore, now_t_min: float) -> ClosureBook:
    return ClosureBook(parse_events(list_road_events(store)), now_t_min)
