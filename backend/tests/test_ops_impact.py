"""Route-impact reassessment of active trips (D-42). Written before floodnet/ops/impact.py existed.

Trip on the test network (tests/ops_net.py): truck at node 0 -> settlement at node 2 along South Rd (s01 then s12,
about T+0..T+4.2), load 10 min, then to the shelter at node 5 (East Ln e25)."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.access.events import ClosureBook, parse_events
from floodnet.ops import impact as im
from floodnet.ops import trips as tr
from floodnet.ops.store import OpsStore
from tests.ops_net import G, LONLAT, NO_CTX, ROADS, flood_from, forecast, mid
from tests.test_ops_trips import assignment

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def trip_doc(store, vehicle_type="truck", aid="A1"):
    t, _ = tr.create_trip(store, assignment(vehicle_type=vehicle_type, aid=aid), vehicle_type=vehicle_type, now_t=0)
    return t


def book(*events, now=1.0):
    return ClosureBook(parse_events(list(events)), now)


def closure(seg, t=1.0, eid="C1", **kw):
    return {"event_id": eid, "seg_id": seg, "kind": "closure", "received_t_min": t, **kw}


def assess(trip, now_t=1.0, f=None, closures=None, fix=None, **kw):
    return im.assess(trip, now_t=now_t, now_wall=NOW, roads=ROADS, G=G, forecast=f or forecast(), seg_ctx=NO_CTX,
                     closures=closures or book(now=now_t), fix=fix, **kw)


def remaining_ids(a):
    return [s["seg_id"] for leg in a["candidate"]["legs"] for s in leg["segments"]] if a.get("candidate") else None


def test_nothing_changed_gives_no_change(store):
    a = assess(trip_doc(store))
    assert a["decision"] == "NO_CHANGE" and a["position"]["source"] == "PLANNED_ESTIMATE"


def test_confirmed_closure_on_the_remaining_route_gives_a_required_reroute(store):
    a = assess(trip_doc(store), closures=book(closure("s12")))
    assert a["decision"] == "REQUIRED"
    assert any(i["seg_id"] == "s12" and i["status"] == "CLOSED" for i in a["issues"])
    assert "s12" not in remaining_ids(a)
    assert "C1" in a["reason"]


def test_road_becoming_dangerous_while_en_route(store):
    a = assess(trip_doc(store), f=forecast(s12=flood_from(3)))
    assert a["decision"] == "REQUIRED" and a["issues"][0]["status"] == "AT_RISK"


def test_candidates_are_compared_for_the_selected_vehicle(store):
    a = assess(trip_doc(store), closures=book(closure("s12")))
    cur, cand = a["comparison"]["current"], a["comparison"]["candidate"]
    assert cur["blocking"] == 1 and cand["blocking"] == 0
    assert cand["eta_t_min"] > cur["eta_t_min"]                    # the detour is longer, but usable
    assert {"uncertain", "closures"} <= set(cand)


def test_no_feasible_route_escalates_and_never_invents_one(store):
    a = assess(trip_doc(store), closures=book(closure("s12"), closure("e25", eid="C2")))
    assert a["decision"] == "ESCALATE" and a["candidate"] is None
    assert "no feasible route" in a["reason"]


def test_destination_unavailable_escalates(store):
    a = assess(trip_doc(store), destination_ok=(False, "shelter SH1 reported full"))
    assert a["decision"] == "ESCALATE" and "SH1" in a["reason"]


def test_flooding_behind_the_vehicle_changes_nothing(store):
    # at T+3 the truck has left s01 (T+0..T+2.1); s01 floods from T+3
    a = assess(trip_doc(store), now_t=3.0, f=forecast(s01=flood_from(3)))
    assert a["decision"] == "NO_CHANGE"
    assert all(i["seg_id"] != "s01" for i in a["issues"])


def test_a_truck_restriction_reroutes_the_truck_but_not_a_car(store):
    r = book(closure("s12", vehicle_types=["truck"]))
    assert assess(trip_doc(store), closures=r)["decision"] == "REQUIRED"
    assert assess(trip_doc(store, vehicle_type="car", aid="A2"), closures=r)["decision"] == "NO_CHANGE"


def test_fresh_on_route_gps_is_used(store):
    fix = {"lon": mid(1, 2)[0], "lat": mid(1, 2)[1], "accuracy_m": 5, "recorded_at": (NOW - timedelta(seconds=5)).isoformat()}
    a = assess(trip_doc(store), now_t=3.0, fix=fix)
    assert a["position"]["source"] == "GPS_ON_ROUTE" and a["position"]["freshness"] == "FRESH"


def test_stale_gps_falls_back_to_the_planned_estimate_and_says_so(store):
    fix = {"lon": mid(1, 2)[0], "lat": mid(1, 2)[1], "recorded_at": (NOW - timedelta(seconds=60)).isoformat()}
    a = assess(trip_doc(store), now_t=3.0, fix=fix)
    assert a["position"]["source"] == "PLANNED_ESTIMATE" and "STALE" in a["position"]["note"]


def test_gps_far_off_the_pilot_network_is_not_used(store):
    fix = {"lon": 72.95, "lat": 19.20, "recorded_at": (NOW - timedelta(seconds=3)).isoformat()}
    a = assess(trip_doc(store), now_t=3.0, fix=fix)
    assert a["position"]["source"] == "PLANNED_ESTIMATE" and "off the pilot road network" in a["position"]["note"]


def test_off_route_vehicle_gets_a_required_route_from_where_it_is(store):
    fix = {"lon": float(LONLAT[4][0]), "lat": float(LONLAT[4][1]), "recorded_at": (NOW - timedelta(seconds=3)).isoformat()}
    a = assess(trip_doc(store), now_t=3.0, fix=fix)
    assert a["position"]["source"] == "GPS_OFF_ROUTE" and a["decision"] == "REQUIRED"
    assert a["candidate"]["legs"][0]["from_node"] == 4


# ------------------------------------------------------------------ applying assessments: dedup, rejection, cooldown
def test_the_same_required_finding_is_proposed_once(store):
    t = trip_doc(store)
    a = assess(t, closures=book(closure("s12")))
    first = im.apply(store, t["trip_id"], a, trigger={"kind": "road_event", "id": "C1"}, now_t=1)
    again = im.apply(store, t["trip_id"], assess(store.get("trips", t["trip_id"]), closures=book(closure("s12"))),
                     trigger={"kind": "gps"}, now_t=1.5)
    assert first["action"] == "created" and again["action"] == "deduplicated"
    assert len(store.get("trips", t["trip_id"])["proposals"]) == 1


def test_accept_replaces_the_route_and_waits_for_the_driver(store):
    t = trip_doc(store)
    p = im.apply(store, t["trip_id"], assess(t, closures=book(closure("s12"))), trigger={"kind": "road_event"}, now_t=1)["proposal"]
    trip = im.decide(store, t["trip_id"], p["proposal_id"], "ACCEPT", actor="dispatcher", now_t=1.2)
    assert trip["route_version"] == 2 and "s12" not in [s["seg_id"] for l in trip["legs"] for s in l["segments"]]
    assert trip["proposals"][0]["status"] == "ACCEPTED"
    trip = im.acknowledge(store, t["trip_id"], p["proposal_id"], actor=f"driver:{t['trip_id']}")
    assert trip["proposals"][0]["status"] == "ACKNOWLEDGED"
    # the accepted route avoids the closure, so reassessment finds nothing more to do
    assert assess(trip, now_t=1.5, closures=book(closure("s12")))["decision"] == "NO_CHANGE"


def test_a_rejected_proposal_is_not_reproposed_and_the_trip_needs_attention(store):
    t = trip_doc(store)
    p = im.apply(store, t["trip_id"], assess(t, closures=book(closure("s12"))), trigger={"kind": "x"}, now_t=1)["proposal"]
    im.decide(store, t["trip_id"], p["proposal_id"], "REJECT", actor="dispatcher", now_t=1.1, reason="driver can see it is passable")
    out = im.apply(store, t["trip_id"], assess(store.get("trips", t["trip_id"]), closures=book(closure("s12"))),
                   trigger={"kind": "gps"}, now_t=2)
    assert out["action"] == "suppressed_rejected"
    assert "rejected" in store.get("trips", t["trip_id"])["attention"]


def _optional(sig, eta=10.0):
    return {"decision": "OPTIONAL", "reason": "faster", "issues": [], "candidate": {"legs": [], "signature": sig},
            "comparison": {"current": {"eta_t_min": eta + 4}, "candidate": {"eta_t_min": eta}},
            "position": {"source": "PLANNED_ESTIMATE"}}


def test_optional_proposals_have_a_cooldown(store):
    t = trip_doc(store)
    tid = t["trip_id"]
    assert im.apply(store, tid, _optional(["a"]), trigger={"kind": "x"}, now_t=1)["action"] == "created"
    p = store.get("trips", tid)["proposals"][0]
    im.decide(store, tid, p["proposal_id"], "REJECT", actor="dispatcher", now_t=1.5, reason="no")
    assert im.apply(store, tid, _optional(["b"]), trigger={"kind": "x"}, now_t=3)["action"] == "suppressed_cooldown"
    assert im.apply(store, tid, _optional(["b"]), trigger={"kind": "x"}, now_t=7)["action"] == "created"


def test_escalation_is_recorded_once(store):
    t = trip_doc(store)
    a = assess(t, closures=book(closure("s12"), closure("e25", eid="C2")))
    assert im.apply(store, t["trip_id"], a, trigger={"kind": "road_event"}, now_t=1)["action"] == "escalated"
    assert im.apply(store, t["trip_id"], a, trigger={"kind": "gps"}, now_t=2)["action"] == "deduplicated"
    trip = store.get("trips", t["trip_id"])
    assert trip["escalation"]["reason"].startswith("no feasible route")
    assert [x["action"] for x in store.audit("trips", t["trip_id"])].count("escalate") == 1
