"""Report reconciliation (DECISION_MATRIX D-32; Agent C rule set 2.1-2.4, test cases T1-T13, T17).

Written BEFORE the implementation (Phase 3, tests first). Road-event cases run on the hand-built network of
tests/test_road_access.py (n1, n2 / s1, s2; 4 min per 1000 m); resource-event cases run the real planner on the
pilot network exactly as tests/test_response_planner.py does. Names used here are the ones fixed in D-32:
RoadEvent.source_tier, RoadEvent.references, TIER_RANK, STALE_AFTER_MIN, ClosureBook.annotated(),
ClosureBook.unconfirmed_during(seg, t0, t1), build_state keys events_unconfirmed / events_scheduled, shelter
fields last_report_t_min / last_report_event_id / stale.

New names are imported inside the tests so that every test reports its own failure reason.
"""
from __future__ import annotations

import copy

import pytest
from fastapi.testclient import TestClient

from floodnet import config
from floodnet.access.events import ClosureBook, RoadEvent, parse_events
from floodnet.access.road_status import classify
from floodnet.api import state
from floodnet.api.main import app
from floodnet.response import fixtures as fx
from floodnet.response.ledger import parse_response_events
from floodnet.response.planner import build_state
from tests.test_road_access import NO_CTX, T, _forecast, _ids, _plan
from tests.test_response_planner import DRY, run_plan

# ------------------------------------------------------------------ helpers
def ev(seg, kind, r, a=None, tier=None, eid=None, **extra):
    """One client-side event dict. tier omitted = OPERATOR (D-32 default)."""
    d = {"seg_id": seg, "kind": kind, "received_t_min": r, "applies_from_t_min": r if a is None else a}
    if tier is not None:
        d["source_tier"] = tier
    if eid is not None:
        d["event_id"] = eid
    d.update(extra)
    return d


def book(events, decision):
    return ClosureBook(parse_events(list(events)), decision)


def ann(bk, eid):
    rows = bk.annotated()
    return next(x for x in rows if x["event_id"] == eid)


def cls(bk, seg="n1", t0=0.0, t1=4.0, forecast=None, limit=30):
    return classify(seg, t0, t1, forecast=forecast or _forecast(), ctx=NO_CTX, closures=bk, limit_cm=limit)


@pytest.fixture(scope="module")
def roads():
    return state.get_pilot()["roads"]


# ------------------------------------------------------------------ back-compat (T10 part)
def test_legacy_event_json_parses_with_tier_and_reference_defaults():
    e = parse_events([{"seg_id": "n1", "kind": "closure", "received_t_min": 0}])[0]
    assert e.event_id == "ev1" and e.applies_from_t_min == 0.0 and e.note == ""
    assert e.source == "operator report (SIMULATED)"
    assert e.source_tier == "OPERATOR"                      # D-32 default tier
    assert list(e.references) == []


def test_to_dict_round_trips_through_the_constructor():
    # evaluate.prediction_quality rebuilds events with RoadEvent(**e): every field must be a constructor kwarg.
    e = parse_events([ev("n1", "closure", 0, eid="C1", note="x")])[0]
    assert RoadEvent(**e.to_dict()) == e


def test_to_dict_carries_source_tier_and_references():
    d = parse_events([ev("n1", "closure", 0, tier="FIELD_TEAM", references=["C0"])])[0].to_dict()
    assert d["source_tier"] == "FIELD_TEAM" and list(d["references"]) == ["C0"]


def test_unknown_keys_are_still_ignored():
    # the UI sends `name`; nothing may start rejecting unknown keys
    e = parse_events([{**ev("n1", "closure", 0, tier="FIELD_TEAM"), "name": "Tilak Road", "foo": 1}])[0]
    assert e.seg_id == "n1" and e.source_tier == "FIELD_TEAM"


@pytest.mark.parametrize("bad", ["SENSOR", "MODEL", "boss", 42])
def test_unknown_tier_is_rejected(bad):
    with pytest.raises(ValueError):
        parse_events([ev("n1", "closure", 0, tier=bad)])


def test_tier_constants_match_d32():
    from floodnet.access.events import STALE_AFTER_MIN, TIER_RANK
    assert TIER_RANK == {"AUTHORITY": 4, "OPERATOR": 3, "FIELD_TEAM": 3, "PUBLIC": 1}
    assert STALE_AFTER_MIN == {"AUTHORITY": 180, "OPERATOR": 90, "FIELD_TEAM": 90, "PUBLIC": 45}


# ------------------------------------------------------------------ T1 .. T9 (road events, tiny network)
def test_t1_reopen_without_references_lifts_the_closure():
    bk = book([ev("n1", "closure", 10, eid="C1"), ev("n1", "reopen", 60, eid="R2")], decision=61)
    assert bk.closure_at("n1", 59).event_id == "C1"
    assert bk.closure_at("n1", 61) is None
    assert ann(bk, "R2")["status"] == "APPLIED" and list(ann(bk, "R2")["lifts"]) == ["C1"]
    assert ann(bk, "C1")["status"] == "LIFTED" and ann(bk, "C1")["lifted_by"] == "R2"


def test_t2_reopen_recorded_before_the_closure_has_no_effect():
    bk = book([ev("n1", "closure", 10, eid="C1"), ev("n1", "reopen", 8, eid="R2")], decision=30)
    assert bk.closure_at("n1", 30).event_id == "C1"
    assert ann(bk, "R2")["status"] == "NO_EFFECT"
    assert "recorded before closure C1" in ann(bk, "R2")["explanation"]


def test_t3_late_backdated_closure_after_an_unrelated_reopen_is_in_force():
    evs = [ev("n1", "closure", 0, eid="C0"), ev("n1", "reopen", 20, eid="R1"), ev("n1", "closure", 30, 5, eid="C1")]
    bk = book(evs, decision=35)
    hit = bk.closure_at("n1", 35)
    assert hit is not None and hit.event_id == "C1"
    assert list(ann(bk, "R1")["lifts"]) == ["C0"]
    out = _plan(_forecast(), dispatch=35, events=evs)
    assert _ids(out["arrival_aware"]) == ["s1", "s2"]


@pytest.mark.parametrize("order", [(0, 1), (1, 0)])
def test_t4_full_tie_closure_wins_in_both_array_orders(order):
    base = [ev("n1", "closure", 10, eid="C1"), ev("n1", "reopen", 10, eid="R2")]
    bk = book([base[i] for i in order], decision=30)
    hit = bk.closure_at("n1", 30)
    assert hit is not None and hit.event_id == "C1"
    assert ann(bk, "R2")["status"] == "NO_EFFECT"


def test_t5_public_reopen_is_rejected_and_the_closure_is_contested():
    bk = book([ev("n1", "closure", 10, eid="C1", tier="OPERATOR"), ev("n1", "reopen", 12, eid="R2", tier="PUBLIC")], 15)
    hit = bk.closure_at("n1", 15)
    assert hit is not None and hit.event_id == "C1"
    assert ann(bk, "R2")["status"] == "REJECTED_LOWER_TIER"
    assert list(ann(bk, "C1")["contested_by"]) == ["R2"]
    st = cls(bk, t0=15, t1=19)
    assert st["status"] == "CLOSED" and st["reason"].endswith("; contested by R2")


def test_t6_public_closure_is_uncertain_not_closed():
    evs = [ev("n1", "closure", 0, eid="C1", tier="PUBLIC")]
    bk = book(evs, decision=0)
    st = cls(bk)
    assert st["status"] == "UNCERTAIN" and "unconfirmed PUBLIC report" in st["reason"]
    assert bk.closure_at("n1", 0) is None                        # never a confirmed closure
    assert bk.unconfirmed_during("n1", 0, 4).event_id == "C1"
    assert ann(bk, "C1")["status"] == "UNCONFIRMED"
    out = _plan(_forecast(), events=evs)
    for blk in (out["arrival_aware"], out["current_status"]):
        assert blk["reachable"] and not blk["status_counts_at_arrival"].get("CLOSED")
    # expected route from the cost rule, not hard-coded: n1 (4 min x UNCERTAIN factor) + n2 (4) vs s1 (4) + s2 (6)
    via_north = 4.0 * config.ACCESS_COST_FACTOR["UNCERTAIN"] + 4.0
    assert _ids(out["arrival_aware"]) == (["s1", "s2"] if via_north > 10.0 else ["n1", "n2"])


def test_t7_scheduled_reopen_lifts_only_from_its_applies_time():
    bk = book([ev("n1", "closure", 10, eid="C1"), ev("n1", "reopen", 20, 40, eid="R2")], decision=25)
    assert bk.closure_at("n1", 25).event_id == "C1"
    assert bk.closure_at("n1", 45) is None
    r2 = ann(bk, "R2")
    assert r2["scheduled"] is True and "scheduled" in r2["explanation"]


def test_t8_staleness_is_a_flag_and_never_lifts_a_closure():
    bk = book([ev("n1", "closure", 0, eid="C1", tier="FIELD_TEAM")], decision=100)
    assert bk.closure_at("n1", 100).event_id == "C1"
    c1 = ann(bk, "C1")
    assert c1["stale"] is True and c1["age_min"] == pytest.approx(100.0)
    assert "stale 100 min" in cls(bk, t0=100, t1=104, forecast=_forecast())["reason"] or \
        "stale 100 min" in c1["explanation"]


def test_t9_authority_closure_is_lifted_only_by_an_authority_reopen():
    evs = [ev("n1", "closure", 0, eid="C1", tier="AUTHORITY"), ev("n1", "reopen", 5, eid="R2", tier="FIELD_TEAM")]
    bk = book(evs, decision=10)
    assert bk.closure_at("n1", 10).event_id == "C1"
    assert ann(bk, "R2")["status"] == "REJECTED_LOWER_TIER"
    bk2 = book(evs + [ev("n1", "reopen", 6, eid="R3", tier="AUTHORITY")], decision=10)
    assert bk2.closure_at("n1", 10) is None
    assert ann(bk2, "R3")["status"] == "APPLIED" and ann(bk2, "C1")["status"] == "LIFTED"


def test_future_events_are_annotated_as_future_and_not_used():
    bk = book([ev("n1", "closure", 20, eid="C1")], decision=10)
    assert bk.used == [] and [e.event_id for e in bk.ignored_future] == ["C1"]
    rows = bk.annotated()
    assert all(r["status"] != "FUTURE" for r in rows if r["event_id"] != "C1")


def test_explanations_are_plain_strings_in_the_d32_format():
    bk = book([ev("n1", "closure", 10, eid="C1"), ev("n1", "reopen", 12, eid="R2", tier="PUBLIC")], decision=15)
    for row in bk.annotated():
        x = row["explanation"]
        assert isinstance(x, str) and "RoadEvent(" not in x and "{'" not in x
        assert x.startswith(f"{row['event_id']} {row['kind']} {row['seg_id']} by {row['source_tier']}: {row['status']}; received T+")


# ------------------------------------------------------------------ API (T10: superset of old keys)
client = TestClient(app)
O, D = [72.8418, 19.0035], [72.8470, 19.0190]


def test_api_access_route_legacy_body_still_works_and_events_used_are_annotated():
    seg = client.get("/api/roads").json()["features"][0]["properties"]["seg_id"]
    body = {"origin": O, "dest": D, "vehicle": "ambulance", "dispatch_t_min": 10, "series_t_min": list(T),
            "streets_cm": {}, "events": [{"seg_id": seg, "kind": "closure", "received_t_min": 5},
                                         {"seg_id": seg, "kind": "closure", "received_t_min": 30, "event_id": "late"}]}
    r = client.post("/api/access/route", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert [e["event_id"] for e in j["events_ignored_future"]] == ["late"]
    used = j["events_used"][0]
    assert {"event_id", "seg_id", "kind", "received_t_min", "applies_from_t_min", "source", "note"} <= set(used)
    assert used["status"] == "ACTIVE" and used["stale"] is False and used["source_tier"] == "OPERATOR"
    assert isinstance(used["explanation"], str) and used["explanation"].startswith(used["event_id"])


def test_api_rejects_an_unknown_tier_with_422():
    seg = client.get("/api/roads").json()["features"][0]["properties"]["seg_id"]      # a real segment: only the tier is wrong
    body = {"origin": O, "dest": D, "series_t_min": list(T), "streets_cm": {},
            "events": [{"seg_id": seg, "kind": "closure", "received_t_min": 0, "source_tier": "SENSOR"}]}
    r = client.post("/api/access/route", json=body)
    assert r.status_code == 422, r.status_code
    assert "tier" in str(r.json().get("detail", "")).lower()


# ------------------------------------------------------------------ resource events (T11 .. T13), real pilot
KNOWN = {k: {r["id"] for r in fx.demo_resources()[k]} for k in ("settlements", "shelters", "teams", "vehicles")}
KNOWN.update({"vehicle_id": KNOWN["vehicles"], "shelter_id": KNOWN["shelters"], "settlement_id": KNOWN["settlements"]})


def _strip(out: dict) -> dict:
    out = copy.deepcopy(out)
    out["summary"].pop("replan_ms", None)
    return out


def test_t11_same_moment_shelter_conflict_resolves_pessimistically_in_both_orders(roads):
    e1 = {"event_id": "E1", "kind": "shelter_update", "shelter_id": "SH1", "received_t_min": 30, "occupancy": 50}
    e2 = {"event_id": "E2", "kind": "shelter_update", "shelter_id": "SH1", "received_t_min": 30, "occupancy": 80}
    for order in ([e1, e2], [e2, e1]):
        st = build_state(fx.demo_resources(), parse_response_events(order, KNOWN), now=30)
        assert st["shelters"]["SH1"]["occupancy"] == 80, order
    a = run_plan(roads, now=30, response_events=[e1, e2])
    b = run_plan(roads, now=30, response_events=[e2, e1])
    assert any("conflict between E1 and E2" in line for line in a["log"])
    assert _strip(a) == _strip(b)


def test_t12_public_shelter_report_is_listed_but_not_applied(roads):
    e1 = {"event_id": "E1", "kind": "shelter_update", "shelter_id": "SH2", "received_t_min": 0, "occupancy": 150,
          "source_tier": "PUBLIC"}
    out = run_plan(roads, response_events=[e1])
    assert out["summary"]["covered_proposed"] == 210 and out["summary"]["unmet"] == 5     # as with no events
    assert [e["event_id"] for e in out["state"]["events_unconfirmed"]] == ["E1"]
    assert out["state"]["events_used"] == []


def test_t13_stale_shelter_report_keeps_the_last_known_value_and_is_flagged(roads):
    e1 = {"event_id": "E1", "kind": "shelter_update", "shelter_id": "SH1", "received_t_min": 0, "occupancy": 60}
    out = run_plan(roads, now=120, response_events=[e1], forecast=DRY)
    sh = next(s for s in out["state"]["shelters"] if s["id"] == "SH1")
    assert sh["occupancy"] == 60                                     # last known value kept, never zeroed
    assert sh["stale"] is True and sh["last_report_t_min"] == 0 and sh["last_report_event_id"] == "E1"
    assert sh["free"] == 80 - 60 - sh["reserved"]
    assert any("SH1" in line and "stale" in line.lower() for line in out["log"])


def test_scheduled_resource_events_are_listed_apart_from_future_received_ones():
    evs = [{"event_id": "E1", "kind": "vehicle_unavailable", "vehicle_id": "V1", "received_t_min": 5, "applies_from_t_min": 30},
           {"event_id": "E2", "kind": "vehicle_unavailable", "vehicle_id": "V2", "received_t_min": 40}]
    st = build_state(fx.demo_resources(), parse_response_events(evs, KNOWN), now=10)
    assert st["vehicles"]["V1"]["available"] and st["vehicles"]["V2"]["available"]
    assert [e["event_id"] for e in st["events_scheduled"]] == ["E1"]
    assert [e["event_id"] for e in st["events_pending"]] == ["E2"]


def test_resource_event_unknown_tier_is_rejected():
    with pytest.raises(ValueError):
        parse_response_events([{"kind": "vehicle_unavailable", "vehicle_id": "V1", "received_t_min": 0,
                                "source_tier": "SENSOR"}], KNOWN)


# ------------------------------------------------------------------ T17: deterministic replay order
def test_t17_replay_is_independent_of_the_script_event_order(roads):
    from floodnet.response.evaluate import DEMO_SCRIPT, replay
    # Two SH1 occupancy reports at T+0 (before any mission exists): "full" and "as in the fixture". Under the
    # pessimistic rule the first plan must treat SH1 as full whichever report comes first in the array.
    events = [{"t": 0, "kind": "shelter_update", "shelter_id": "SH1", "occupancy": 80, "reason": "full (SIMULATED)"},
              {"t": 0, "kind": "shelter_update", "shelter_id": "SH1", "occupancy": 20, "reason": "20 in (SIMULATED)"}] + DEMO_SCRIPT["events"]
    fwd = {**DEMO_SCRIPT, "replan_t_min": [0, 30, 60], "events": events}
    rev = {**fwd, "events": list(reversed(events))}
    a = replay(roads, NO_CTX, DRY, DRY, "arrival", fwd)
    b = replay(roads, NO_CTX, DRY, DRY, "arrival", rev)
    key = lambda r: [(s["t_min"], s["plan_version"], s["served"], s["unmet"], s["dispatched"]) for s in r["steps"]]
    assert key(a) == key(b)
    assert [(x["assignment_id"], x["status"], x["shelter_id"]) for x in a["ledger"]] == \
           [(x["assignment_id"], x["status"], x["shelter_id"]) for x in b["ledger"]]
