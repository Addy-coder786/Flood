"""Assignment ledger: states, history, response events and the invariants a plan must satisfy.

The ledger is held by the client (like the run bundle and the road-event log) and sent with every planning
request, so planning works on any server instance (docs/DEPLOYMENT.md). The server validates it, never trusts it
blindly: unknown ids, impossible status transitions and overlapping bookings are rejected or reported.

Assignment states:  PROPOSED -> ACCEPTED -> DISPATCHED -> IN_PROGRESS -> COMPLETED;  any non-final -> CANCELLED.
History is append-only: every status change records {status, t_min, reason}; nothing is ever edited or erased.
"""
from __future__ import annotations

from collections import defaultdict

from ..access.events import parse_tier

STATUSES = ("PROPOSED", "ACCEPTED", "DISPATCHED", "IN_PROGRESS", "COMPLETED", "CANCELLED")
TRANSITIONS = {"PROPOSED": {"ACCEPTED", "CANCELLED"}, "ACCEPTED": {"DISPATCHED", "CANCELLED"},
               "DISPATCHED": {"IN_PROGRESS", "CANCELLED"}, "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
               "COMPLETED": set(), "CANCELLED": set()}
UNDERWAY = ("DISPATCHED", "IN_PROGRESS")                  # the vehicle has left: kept and re-routed, never re-planned
COMMITTED = ("ACCEPTED",) + UNDERWAY                      # occupy resources and shelter places
FINAL = ("COMPLETED", "CANCELLED")

RESPONSE_EVENT_KINDS = ("vehicle_unavailable", "vehicle_available", "shelter_update", "settlement_update")


def _refs(value) -> list[str]:
    """`references` as a list of ids; one id given as a plain string is one reference (not its characters)."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    return [str(r) for r in value]


def parse_response_events(raw: list[dict] | None, known: dict[str, set[str]]) -> list[dict]:
    """Resource events: {kind, received_t_min, applies_from_t_min?, vehicle_id | shelter_id | settlement_id,
    reason?, total_capacity? / occupancy? / people_needing_help?, source_tier? (default OPERATOR; PUBLIC reports are
    listed but never applied, D-32), references?}. Road events are handled by access/events.py."""
    out = []
    for i, e in enumerate(raw or []):
        kind = str(e.get("kind", ""))
        if kind not in RESPONSE_EVENT_KINDS:
            raise ValueError(f"response event {i}: kind must be one of {RESPONSE_EVENT_KINDS}, got {kind!r}")
        try:
            received = float(e["received_t_min"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"response event {i}: received_t_min is required") from None
        applies = e.get("applies_from_t_min")
        ev = {"event_id": str(e.get("event_id") or f"rev{i + 1}"), "kind": kind, "received_t_min": received,
              "applies_from_t_min": received if applies is None else float(applies),
              "reason": str(e.get("reason") or ""), "source": str(e.get("source") or "operator report (SIMULATED)"),
              "source_tier": parse_tier(e.get("source_tier"), f"response event {i}"),
              "references": _refs(e.get("references"))}
        key = {"vehicle_unavailable": "vehicle_id", "vehicle_available": "vehicle_id", "shelter_update": "shelter_id",
               "settlement_update": "settlement_id"}[kind]
        ref = str(e.get(key) or "")
        if ref not in known[key]:
            raise ValueError(f"response event {i}: unknown {key} {ref!r}")
        ev[key] = ref
        for f in ("total_capacity", "occupancy", "people_needing_help", "status"):
            if e.get(f) is not None:
                ev[f] = e[f] if f == "status" else int(e[f])
        out.append(ev)
    return out


def parse_ledger(raw: list[dict] | None, known: dict[str, set[str]], plan_version: int | None = None) -> list[dict]:
    """Validates the client's ledger. Every assignment carries the `plan_version` that last wrote it (default 0 for
    ledgers from before D-33); when the request's `plan_version` is given, a ledger written by a newer plan is
    rejected so a late or out-of-order replan never builds on state it has not seen (D-33)."""
    out = []
    for i, a in enumerate(raw or []):
        st = a.get("status")
        if st not in STATUSES:
            raise ValueError(f"assignment {i}: status must be one of {STATUSES}, got {st!r}")
        for key, field in (("team_id", "teams"), ("vehicle_id", "vehicles"), ("settlement_id", "settlements"),
                           ("shelter_id", "shelters")):
            if str(a.get(key) or "") not in known[field]:
                raise ValueError(f"assignment {i}: unknown {key} {a.get(key)!r}")
        hist = list(a.get("history") or [])
        seq = [h.get("status") for h in hist]
        for prev, nxt in zip(seq[:-1], seq[1:]):
            if nxt not in TRANSITIONS.get(prev, set()):
                raise ValueError(f"assignment {a.get('assignment_id')}: illegal status change {prev} -> {nxt}")
        if seq and seq[-1] != st:
            raise ValueError(f"assignment {a.get('assignment_id')}: status {st} does not match its history")
        if not a.get("legs") and st != "PROPOSED":
            raise ValueError(f"assignment {a.get('assignment_id')}: a {st} assignment must carry its legs")
        try:
            written_by = int(a.get("plan_version") or 0)
        except (TypeError, ValueError):
            raise ValueError(f"assignment {a.get('assignment_id')}: plan_version must be an integer") from None
        out.append({**a, "assignment_id": str(a.get("assignment_id") or f"A{i + 1}"), "people": int(a.get("people", 0)),
                    "history": hist, "plan_version": written_by})
    newest = max((a["plan_version"] for a in out), default=0)
    if plan_version is not None and newest > plan_version:
        raise ValueError(f"ledger is newer (v{newest}) than plan_version {plan_version}")
    return out


def check_invariants(assignments: list[dict], shelters: dict[str, dict]) -> list[str]:
    """Violations the planner must never produce: a team or vehicle booked for two overlapping missions, or more
    people sent to a shelter than it has free places. Empty list = sound."""
    problems = []
    busy: dict[tuple[str, str], list[tuple[float, float, str]]] = defaultdict(list)
    reserved: dict[str, int] = defaultdict(int)
    for a in assignments:
        if a["status"] == "CANCELLED":
            continue
        if a["status"] != "COMPLETED" and not a.get("attention"):     # flagged ones await an operator decision
            reserved[a["shelter_id"]] += a["people"]
        if not a.get("legs"):
            continue
        t0, t1 = a["legs"][0]["depart_t_min"], a["legs"][-1]["done_t_min"]
        for kind, rid in (("team", a["team_id"]), ("vehicle", a["vehicle_id"])):
            busy[(kind, rid)].append((t0, t1, a["assignment_id"]))
    for (kind, rid), windows in busy.items():
        windows.sort()
        for (s0, e0, i0), (s1, e1, i1) in zip(windows[:-1], windows[1:]):
            if s1 < e0 - 1e-9:
                problems.append(f"{kind} {rid} double-booked: {i0} until T+{e0:.1f} overlaps {i1} from T+{s1:.1f}")
    for sid, n in reserved.items():
        sh = shelters[sid]
        free = sh["total_capacity"] - sh["occupancy"]
        if n > free:
            problems.append(f"shelter {sid} over capacity: {n} reserved, {free} free")
    return problems
