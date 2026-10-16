"""Timestamped road events: confirmed closures and reopenings, reconciled by trust tier (DECISIONS D-32).

Each event carries three different times (MISC-04 asks for all three to be kept apart):
  received_t_min      when the report reached the system (simulation minutes from the forecast start, T0)
  applies_from_t_min  the time the report describes, i.e. from when the road is closed / open again
  processed_at        wall-clock UTC time this server handled the request (set by the API, never by the client)

A decision made at `decision_t_min` may only use events with received_t_min <= decision_t_min. Later events are
set aside and reported, never used, so a historical replay cannot see the future.

Reports carry a trust tier (`source_tier`; the labels are ours and SIMULATED in the demo):
  AUTHORITY (rank 4) > OPERATOR = FIELD_TEAM (rank 3) > PUBLIC (rank 1); default OPERATOR.
A confirmed closure (tier rank >= OPERATOR) is authoritative: it stays in force until a reopen for the same
segment LIFTS it. Reopen R lifts closure C iff R was recorded no earlier than C, describes a later moment
((applies, received) lexicographically greater; a full tie goes to the closure) and R's tier rank >= C's.
PUBLIC closures never close a road for routing (they make it UNCERTAIN); a PUBLIC reopen never lifts a confirmed
closure (REJECTED_LOWER_TIER, the closure is marked contested). Nothing ever expires: staleness
(STALE_AFTER_MIN per tier, ASSUMED and uncalibrated) is a flag and a log line only. A dry flood forecast never
reopens a closed road (road_status.py checks closures before depths).

Restrictions (D-42): a closure may name `vehicle_types`; it then binds only those vehicles (e.g. a weak bridge closed
to trucks). Routing passes the vehicle type; with no vehicle (map display) every closure is shown. A reopen always
names no vehicle type and lifts the closure for every vehicle.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

from .. import config

KINDS = ("closure", "reopen")
TIERS = ("AUTHORITY", "OPERATOR", "FIELD_TEAM", "PUBLIC")
TIER_RANK = {"AUTHORITY": 4, "OPERATOR": 3, "FIELD_TEAM": 3, "PUBLIC": 1}
STALE_AFTER_MIN = {"AUTHORITY": 180, "OPERATOR": 90, "FIELD_TEAM": 90, "PUBLIC": 45}     # ASSUMED, not calibrated
DEFAULT_TIER = "OPERATOR"
CONFIRMED_RANK = TIER_RANK["OPERATOR"]          # a closure below this rank is a report, not a confirmed closure
STATUSES = ("ACTIVE", "LIFTED", "APPLIED", "NO_EFFECT", "REJECTED_LOWER_TIER", "UNCONFIRMED", "FUTURE")


def parse_tier(value, where: str = "event") -> str:
    """Validates a client-supplied tier (case and surrounding blanks ignored); absent = OPERATOR."""
    if value is None or value == "":
        return DEFAULT_TIER
    tier = str(value).strip().upper()
    if tier not in TIER_RANK:
        raise ValueError(f"{where}: source_tier must be one of {TIERS}, got {value!r}")
    return tier


@dataclass(frozen=True)
class RoadEvent:
    event_id: str
    seg_id: str
    kind: str                       # "closure" | "reopen"
    received_t_min: float
    applies_from_t_min: float
    source: str = "operator report (SIMULATED)"
    note: str = ""
    source_tier: str = DEFAULT_TIER
    references: tuple[str, ...] = field(default_factory=tuple)    # explanatory only; never changes precedence
    vehicle_types: tuple[str, ...] = field(default_factory=tuple)  # empty = every vehicle (D-42)

    def __post_init__(self):
        # keep the record hashable and round-trippable: RoadEvent(**e.to_dict()) == e (evaluate.py relies on it)
        object.__setattr__(self, "references", tuple(str(r) for r in (self.references or ())))
        object.__setattr__(self, "vehicle_types", tuple(str(v) for v in (self.vehicle_types or ())))

    def applies_to(self, vehicle: Optional[str]) -> bool:
        return not self.vehicle_types or vehicle is None or vehicle in self.vehicle_types

    @property
    def rank(self) -> int:
        return TIER_RANK.get(self.source_tier, 0)

    @property
    def confirmed(self) -> bool:
        return self.rank >= CONFIRMED_RANK

    def to_dict(self) -> dict:
        return asdict(self)


def parse_events(raw: list[dict] | None) -> list[RoadEvent]:
    """Validates client-supplied events. Raises ValueError with a readable message on bad input. Unknown keys
    (the UI sends `name`) are ignored."""
    out: list[RoadEvent] = []
    for i, e in enumerate(raw or []):
        kind = str(e.get("kind", "")).lower()
        if kind not in KINDS:
            raise ValueError(f"event {i}: kind must be one of {KINDS}, got {e.get('kind')!r}")
        if not e.get("seg_id"):
            raise ValueError(f"event {i}: seg_id is required")
        try:
            received = float(e["received_t_min"])
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"event {i}: received_t_min (number, minutes from T0) is required") from None
        applies = e.get("applies_from_t_min")
        applies = received if applies is None else float(applies)
        refs = e.get("references") or ()
        if isinstance(refs, str):
            refs = (refs,)
        vtypes = e.get("vehicle_types") or ()
        if isinstance(vtypes, str):
            vtypes = (vtypes,)
        if not isinstance(vtypes, (list, tuple)):
            raise ValueError(f"event {i}: vehicle_types must be a list of vehicle types")
        vtypes = tuple(dict.fromkeys(str(v).strip().lower() for v in vtypes if str(v).strip()))
        unknown = [v for v in vtypes if v not in config.VEHICLE_LIMIT_CM]
        if unknown:
            raise ValueError(f"event {i}: vehicle_types must be among {sorted(config.VEHICLE_LIMIT_CM)}, got {unknown}")
        if vtypes and kind == "reopen":
            raise ValueError(f"event {i}: a reopen applies to every vehicle; vehicle_types is only for closures")
        out.append(RoadEvent(event_id=str(e.get("event_id") or f"ev{i + 1}"), seg_id=str(e["seg_id"]), kind=kind,
                             received_t_min=received, applies_from_t_min=applies,
                             source=str(e.get("source") or RoadEvent.source), note=str(e.get("note") or ""),
                             source_tier=parse_tier(e.get("source_tier"), f"event {i}"),
                             references=tuple(str(r) for r in refs), vehicle_types=vtypes))
    return out


def _key(e: RoadEvent) -> tuple:
    return (e.applies_from_t_min, e.received_t_min, e.event_id)


class ClosureBook:
    """The closure state of every segment, built only from events known at the decision time (D-32 rules)."""

    def __init__(self, events: list[RoadEvent], decision_t_min: float):
        self.decision_t_min = float(decision_t_min)
        self.used = [e for e in events if e.received_t_min <= self.decision_t_min]
        self.ignored_future = [e for e in events if e.received_t_min > self.decision_t_min]
        self._by_seg: dict[str, list[RoadEvent]] = {}
        for e in sorted(self.used, key=_key):
            self._by_seg.setdefault(e.seg_id, []).append(e)
        # derived relations (event ids); computed once per segment
        self._lifted_by: dict[str, list[RoadEvent]] = {}       # closure id -> reopens that lift it (by applies order)
        self._lifts: dict[str, list[str]] = {}                  # reopen id -> closure ids it lifts
        self._contested: dict[str, list[str]] = {}              # closure id -> rejected lower-tier reopen ids
        self._rejected: dict[str, list[str]] = {}               # reopen id -> why it could not lift a closure (tier)
        self._no_effect: dict[str, list[str]] = {}              # reopen id -> why it did not lift a closure (time)
        for seg_events in self._by_seg.values():
            self._reconcile(seg_events)

    # ------------------------------------------------------------ the lifting relation (D-32)
    def _reconcile(self, seg_events: list[RoadEvent]) -> None:
        closures = [e for e in seg_events if e.kind == "closure"]
        for r in (e for e in seg_events if e.kind == "reopen"):
            for c in closures:
                if r.received_t_min < c.received_t_min:
                    self._no_effect.setdefault(r.event_id, []).append(
                        f"recorded before closure {c.event_id} (received T+{c.received_t_min:g})")
                elif (r.applies_from_t_min, r.received_t_min) <= (c.applies_from_t_min, c.received_t_min):
                    self._no_effect.setdefault(r.event_id, []).append(
                        f"describes no later moment than closure {c.event_id} (applies T+{c.applies_from_t_min:g}); "
                        "on a tie the closure wins")
                elif r.rank < c.rank:
                    self._rejected.setdefault(r.event_id, []).append(
                        f"{r.source_tier} cannot lift closure {c.event_id} reported by {c.source_tier}; "
                        f"a reopen by {c.source_tier} or higher is needed")
                    self._contested.setdefault(c.event_id, []).append(r.event_id)
                else:
                    self._lifted_by.setdefault(c.event_id, []).append(r)
                    self._lifts.setdefault(r.event_id, []).append(c.event_id)
        for cid in self._lifted_by:
            self._lifted_by[cid].sort(key=_key)

    def _in_force(self, c: RoadEvent, t_min: float) -> bool:
        if c.applies_from_t_min > t_min:
            return False
        return not any(r.applies_from_t_min <= t_min for r in self._lifted_by.get(c.event_id, ()))

    def _closure_at(self, seg_id: str, t_min: float, confirmed: bool, vehicle: Optional[str] = None) -> Optional[RoadEvent]:
        hits = [e for e in self._by_seg.get(seg_id, ()) if e.kind == "closure" and e.confirmed == confirmed
                and e.applies_to(vehicle) and self._in_force(e, t_min)]
        return max(hits, key=_key) if hits else None

    def _during(self, seg_id: str, t0: float, t1: float, confirmed: bool, vehicle: Optional[str] = None) -> Optional[RoadEvent]:
        hit = self._closure_at(seg_id, t0, confirmed, vehicle)
        if hit:
            return hit
        for e in self._by_seg.get(seg_id, ()):          # sorted by (applies, received, id)
            if e.kind == "closure" and e.confirmed == confirmed and e.applies_to(vehicle) and t0 < e.applies_from_t_min <= t1 \
                    and self._in_force(e, e.applies_from_t_min):
                return e
        return None

    # ------------------------------------------------------------ public queries (same shape as before)
    def closure_at(self, seg_id: str, t_min: float, vehicle: Optional[str] = None) -> Optional[RoadEvent]:
        """The confirmed closure in force on `seg_id` at time t_min (for `vehicle`, if given), else None."""
        return self._closure_at(seg_id, t_min, True, vehicle)

    def closed_during(self, seg_id: str, t0: float, t1: float, vehicle: Optional[str] = None) -> Optional[RoadEvent]:
        """A confirmed closure in force at any moment of [t0, t1] (at t0 or taking effect inside the window)."""
        return self._during(seg_id, t0, t1, True, vehicle)

    def unconfirmed_during(self, seg_id: str, t0: float, t1: float, vehicle: Optional[str] = None) -> Optional[RoadEvent]:
        """An unconfirmed (PUBLIC) closure report, not lifted, covering any moment of [t0, t1]."""
        return self._during(seg_id, t0, t1, False, vehicle)

    # ------------------------------------------------------------ per-event derived status for the API / UI
    def status_of(self, e: RoadEvent) -> str:
        if e.received_t_min > self.decision_t_min:
            return "FUTURE"
        if e.kind == "closure":
            lifted = any(r.applies_from_t_min <= self.decision_t_min for r in self._lifted_by.get(e.event_id, ()))
            return "LIFTED" if lifted else ("ACTIVE" if e.confirmed else "UNCONFIRMED")
        if self._lifts.get(e.event_id):
            return "APPLIED"
        if self._rejected.get(e.event_id):
            return "REJECTED_LOWER_TIER"
        return "NO_EFFECT"

    def age_min(self, e: RoadEvent) -> float:
        return max(0.0, self.decision_t_min - e.received_t_min)

    def is_stale(self, e: RoadEvent) -> bool:
        return self.age_min(e) > STALE_AFTER_MIN.get(e.source_tier, STALE_AFTER_MIN[DEFAULT_TIER])

    def annotate(self, e: RoadEvent) -> dict:
        """The event dict plus status, stale, age_min, scheduled, lifts, lifted_by, contested_by, explanation."""
        status = self.status_of(e)
        stale, age = (self.is_stale(e), self.age_min(e)) if status != "FUTURE" else (False, 0.0)
        scheduled = e.applies_from_t_min > e.received_t_min
        lifters = self._lifted_by.get(e.event_id, ())
        lifted_by = lifters[0] if lifters else None
        lifts = list(self._lifts.get(e.event_id, ()))
        contested = list(self._contested.get(e.event_id, ()))
        head = (f"{e.event_id} {e.kind} {e.seg_id} by {e.source_tier}: {status}; "
                f"received T+{e.received_t_min:g}, applies T+{e.applies_from_t_min:g}")
        if scheduled:
            head += ", scheduled"
        if stale:
            head += f", stale {age:g} min"
        parts = [head]
        if e.vehicle_types:
            parts.append("restriction: binds only " + ", ".join(e.vehicle_types))
        if status == "FUTURE":
            parts.append(f"received after the decision time T+{self.decision_t_min:g}; not used")
        if lifts:
            parts.append("lifts " + ", ".join(lifts))
        if lifted_by is not None:
            parts.append(f"lifted by {lifted_by.event_id} at T+{lifted_by.applies_from_t_min:g}")
        if contested:
            parts.append("contested by " + ", ".join(contested))
        if status == "UNCONFIRMED":
            parts.append("unconfirmed: a PUBLIC report makes the road UNCERTAIN, never CLOSED; "
                         "record an operator closure to confirm")
        if status == "NO_EFFECT":
            why = self._no_effect.get(e.event_id) or ["nothing was closed on this road"]
            parts.append("no effect: " + why[0])
        if status == "REJECTED_LOWER_TIER":
            parts.append("rejected: " + self._rejected[e.event_id][0])
        return {**e.to_dict(), "references": list(e.references), "vehicle_types": list(e.vehicle_types), "status": status, "stale": stale,
                "age_min": round(age, 2), "scheduled": scheduled, "lifts": lifts,
                "lifted_by": lifted_by.event_id if lifted_by is not None else None, "contested_by": contested,
                "explanation": "; ".join(parts)}

    def annotated(self) -> list[dict]:
        """Every event known to the book (used ones first, then those received after the decision time)."""
        return [self.annotate(e) for e in self.used] + [self.annotate(e) for e in self.ignored_future]

    def annotated_used(self) -> list[dict]:
        return [self.annotate(e) for e in self.used]
