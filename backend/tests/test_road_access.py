"""Arrival-time road access (floodnet/access/): statuses, closures, no-future-information and the arrival-aware vs
current-status comparison, on a tiny hand-built network so every expected answer can be checked by hand.

Network (all segments 1000 m, residential = 15 km/h ASSUMED -> 4 min each), two ways from node 0 to node 3:
    north: 0 -n1-> 1 -n2-> 3        south: 0 -s1-> 2 -s2-> 3 -- but s2 is 1500 m (6 min), so north is quicker.
"""
from __future__ import annotations

import networkx as nx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from floodnet.access.arrival_router import plan_access, travel_min
from floodnet.access.events import ClosureBook, parse_events
from floodnet.access.road_status import Forecast, SegmentContext, build_context, classify
from floodnet.contracts import RoadGraph, RoadSegment
from floodnet.provenance import Provenance, Tag
from floodnet.api.main import app

T = tuple(float(x) for x in range(0, 65, 5))           # frames every 5 min, T+0 .. T+60


def _seg(sid, u, v, length):
    xy = np.array([[0.0, 0.0], [length, 0.0]])
    return RoadSegment(seg_id=sid, name=sid.upper(), highway="residential", osm_way_id=0, u=u, v=v,
                       length_m=length, oneway=True, xy=xy, lonlat=np.array([[72.84, 19.01], [72.85, 19.01]]))


ROADS = RoadGraph(node_xy=np.zeros((4, 2)), node_lonlat=np.array([[72.84, 19.01]] * 4),
                  segments=[_seg("n1", 0, 1, 1000), _seg("n2", 1, 3, 1000), _seg("s1", 0, 2, 1000),
                            _seg("s2", 2, 3, 1500)],
                  provenance=Provenance(tag=Tag.SYNTHETIC, source="hand-built test network"))
G = nx.DiGraph()
for s in ROADS.segments:
    G.add_edge(s.u, s.v, length_m=s.length_m, seg_id=s.seg_id, reversed=False)
NO_CTX = SegmentContext(frozenset(), frozenset())


def _forecast(**rows):
    return Forecast(T, {k: list(v) for k, v in rows.items()})


def _plan(forecast, dispatch=0.0, events=(), decision=None, ctx=NO_CTX, vehicle="car"):
    book = ClosureBook(parse_events(list(events)), dispatch if decision is None else decision)
    return plan_access(ROADS, G, 0, 3, vehicle=vehicle, dispatch_t_min=dispatch, forecast=forecast,
                       seg_ctx=ctx, closures=book)


def _ids(block):
    return [s["seg_id"] for s in block["segments"]]


def test_assumed_speed_gives_four_minutes_per_km_on_residential():
    assert travel_min(1000, "residential", "car") == pytest.approx(4.0)


def test_road_open_now_but_flooded_at_arrival_is_avoided():
    # n2 is dry at dispatch (T+0) but 50 cm from T+5: the car reaches n2 at T+4..T+8.
    out = _plan(_forecast(n2=[0] + [50] * 12))
    cur, arr = out["current_status"], out["arrival_aware"]
    assert _ids(cur) == ["n1", "n2"]                       # baseline sees n2 dry now and takes it
    assert cur["valid_at_arrival"] is False
    assert cur["blocked_at_arrival"][0]["seg_id"] == "n2"
    assert _ids(arr) == ["s1", "s2"] and arr["valid_at_arrival"] is True
    assert out["comparison"]["extra_travel_min"] == pytest.approx(2.0)
    assert "avoids them" in out["comparison"]["summary"]


def test_flooding_after_the_vehicle_has_passed_does_not_block():
    # n2 floods only from T+15, after the car has left it (T+8): both methods keep the quick route.
    out = _plan(_forecast(n2=[0, 0, 0, 50] + [50] * 9))
    assert _ids(out["arrival_aware"]) == ["n1", "n2"] and out["comparison"]["same_route"]


def test_confirmed_closure_is_never_used_and_dry_forecast_does_not_reopen_it():
    ev = [{"seg_id": "n1", "kind": "closure", "received_t_min": 0}]
    out = _plan(_forecast(), events=ev)                     # forecast: everything dry
    assert _ids(out["arrival_aware"]) == ["s1", "s2"]
    assert _ids(out["current_status"]) == ["s1", "s2"]


def test_reopen_event_restores_the_road():
    ev = [{"seg_id": "n1", "kind": "closure", "received_t_min": 0, "event_id": "c1"},
          {"seg_id": "n1", "kind": "reopen", "received_t_min": 2, "applies_from_t_min": 2, "event_id": "r1"}]
    assert _ids(_plan(_forecast(), dispatch=3, events=ev)["arrival_aware"]) == ["n1", "n2"]


def test_closure_received_after_the_decision_time_is_ignored():
    ev = [{"seg_id": "n1", "kind": "closure", "received_t_min": 20}]
    book = ClosureBook(parse_events(ev), decision_t_min=10)
    assert book.used == [] and len(book.ignored_future) == 1
    out = _plan(_forecast(), dispatch=10, events=ev)       # decision defaults to dispatch = T+10
    assert _ids(out["arrival_aware"]) == ["n1", "n2"]


def test_closure_known_now_that_starts_later_blocks_only_arrival_aware():
    # Reported at T+0, in force from T+5: n2 (reached T+4..T+8) is closed when the car gets there.
    ev = [{"seg_id": "n2", "kind": "closure", "received_t_min": 0, "applies_from_t_min": 5}]
    out = _plan(_forecast(), events=ev)
    assert _ids(out["current_status"]) == ["n1", "n2"] and out["current_status"]["valid_at_arrival"] is False
    assert _ids(out["arrival_aware"]) == ["s1", "s2"]


def test_status_categories():
    f = _forecast(n1=[25] * 13, n2=[5] * 13)
    book = ClosureBook([], 0)
    ctx = SegmentContext(unlinked=frozenset({"s1"}), dem_flagged=frozenset({"n2"}))
    st = lambda sid, t0=0, t1=4: classify(sid, t0, t1, forecast=f, ctx=ctx, closures=book, limit_cm=30)["status"]
    assert st("n1") == "UNCERTAIN"            # 25 cm is within 20 cm of the 30 cm limit
    assert st("n2") == "UNCERTAIN"            # DEM flagged
    assert st("s1") == "UNKNOWN"              # not linked to the model
    assert st("s2") == "USABLE"
    assert st("s2", 58, 62) == "UNKNOWN"      # beyond the T+60 horizon
    assert classify("n1", 0, 4, forecast=f, ctx=ctx, closures=book, limit_cm=25)["status"] == "AT_RISK"


def test_window_depth_takes_the_worst_bracketing_frame():
    f = _forecast(n1=[0, 0, 40] + [0] * 10)               # 40 cm only at T+10
    assert f.depth_cm("n1", 6, 9) == 40.0                  # frames T+5 and T+10 bracket 6..9
    assert f.depth_cm("n1", 0, 4) == 0.0


def test_unreachable_reasons():
    flooded = _forecast(n1=[50] * 13, s1=[50] * 13)
    assert "forecast depth" in _plan(flooded)["arrival_aware"]["unreachable_reason"]
    ev = [{"seg_id": "n1", "kind": "closure", "received_t_min": 0},
          {"seg_id": "s1", "kind": "closure", "received_t_min": 0}]
    assert _plan(_forecast(), events=ev)["arrival_aware"]["unreachable_reason"] == \
        "confirmed road closures block every path"


def test_bad_events_are_rejected():
    with pytest.raises(ValueError):
        parse_events([{"seg_id": "n1", "kind": "flood", "received_t_min": 0}])
    with pytest.raises(ValueError):
        parse_events([{"seg_id": "n1", "kind": "closure"}])


def test_build_context_flags_unlinked_and_unreliable_segments():
    mask = np.array([[False, True], [False, False]])
    ctx = build_context({"a": np.array([], dtype=np.int64), "b": np.array([1]), "c": np.array([2])}, mask)
    assert ctx.unlinked == {"a"} and ctx.dem_flagged == {"b"}


# ------------------------------------------------------------------ API, on the real pilot network
client = TestClient(app)
O, D = [72.8418, 19.0035], [72.8470, 19.0190]


def test_api_needs_a_forecast():
    assert client.post("/api/access/route", json={"origin": O, "dest": D}).status_code == 422


def test_api_with_client_supplied_series_and_closure():
    seg = client.get("/api/roads").json()["features"][0]["properties"]["seg_id"]
    body = {"origin": O, "dest": D, "vehicle": "ambulance", "dispatch_t_min": 10, "series_t_min": list(T),
            "streets_cm": {}, "events": [
                {"seg_id": seg, "kind": "closure", "received_t_min": 5},
                {"seg_id": seg, "kind": "closure", "received_t_min": 30, "event_id": "late"}]}
    r = client.post("/api/access/route", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["arrival_aware"]["reachable"] and j["current_status"]["reachable"]
    assert [e["event_id"] for e in j["events_ignored_future"]] == ["late"]
    assert j["assumptions"]["tag"] == "ASSUMED" and "not a guarantee" in j["disclaimer"]
    assert seg not in [s["seg_id"] for s in j["arrival_aware"]["segments"]]


def test_api_rejects_unknown_segment_and_decision_after_dispatch():
    base = {"origin": O, "dest": D, "series_t_min": list(T), "streets_cm": {}}
    r = client.post("/api/access/route", json={**base, "events": [{"seg_id": "nope", "kind": "closure",
                                                                   "received_t_min": 0}]})
    assert r.status_code == 422
    assert client.post("/api/access/route", json={**base, "dispatch_t_min": 5, "decision_t_min": 9}).status_code == 422
