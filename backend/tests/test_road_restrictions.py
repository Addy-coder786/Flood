"""Vehicle-specific road restrictions (DECISIONS D-42): a confirmed closure may name `vehicle_types`; it then binds
only those vehicles. Same hand-built network as test_road_access.py (north n1-n2 is quicker than south s1-s2).
Written before RoadEvent had a vehicle_types field."""
from __future__ import annotations

import pytest

from floodnet.access.events import ClosureBook, parse_events
from floodnet.access.road_status import classify
from tests.test_road_access import NO_CTX, _forecast, _ids, _plan


def _restriction(seg="n1", types=("truck",), t=0, tier="OPERATOR", eid="C1"):
    return {"event_id": eid, "seg_id": seg, "kind": "closure", "received_t_min": t, "source_tier": tier,
            "vehicle_types": list(types)}


def test_restriction_is_parsed_and_validated():
    e = parse_events([_restriction()])[0]
    assert e.vehicle_types == ("truck",)
    assert parse_events([{"seg_id": "n1", "kind": "closure", "received_t_min": 0}])[0].vehicle_types == ()
    with pytest.raises(ValueError, match="vehicle_types"):
        parse_events([_restriction(types=("hovercraft",))])
    with pytest.raises(ValueError, match="reopen"):
        parse_events([{"seg_id": "n1", "kind": "reopen", "received_t_min": 0, "vehicle_types": ["truck"]}])


def test_restriction_binds_only_the_listed_vehicle_types():
    book = ClosureBook(parse_events([_restriction()]), 10)
    assert book.closed_during("n1", 0, 5, vehicle="truck").event_id == "C1"
    assert book.closed_during("n1", 0, 5, vehicle="car") is None
    assert book.closed_during("n1", 0, 5).event_id == "C1"           # no vehicle given (map display): shown


def test_classify_uses_the_vehicle():
    book = ClosureBook(parse_events([_restriction()]), 10)
    f = _forecast()
    assert classify("n1", 0, 4, forecast=f, ctx=NO_CTX, closures=book, limit_cm=60, vehicle="truck")["status"] == "CLOSED"
    assert classify("n1", 0, 4, forecast=f, ctx=NO_CTX, closures=book, limit_cm=30, vehicle="car")["status"] == "USABLE"


def test_a_truck_restriction_removes_the_faster_route_for_a_truck_only():
    ev = [_restriction()]
    truck = _plan(_forecast(), events=ev, vehicle="truck")["arrival_aware"]
    car = _plan(_forecast(), events=ev, vehicle="car")["arrival_aware"]
    assert _ids(truck) == ["s1", "s2"]                       # slower, but the only route the truck may use
    assert _ids(car) == ["n1", "n2"]                         # the car keeps the faster road
    assert truck["travel_time_min"] > car["travel_time_min"]


def test_an_unrestricted_closure_still_binds_every_vehicle_and_a_reopen_lifts_a_restriction():
    both = [_restriction(), {"event_id": "C2", "seg_id": "n1", "kind": "closure", "received_t_min": 1}]
    assert _ids(_plan(_forecast(), events=both, vehicle="car", dispatch=2)["arrival_aware"]) == ["s1", "s2"]
    lifted = [_restriction(), {"event_id": "R2", "seg_id": "n1", "kind": "reopen", "received_t_min": 1}]
    assert _ids(_plan(_forecast(), events=lifted, vehicle="truck", dispatch=2)["arrival_aware"]) == ["n1", "n2"]


def test_annotation_describes_the_restriction():
    book = ClosureBook(parse_events([_restriction()]), 10)
    a = book.annotated_used()[0]
    assert a["vehicle_types"] == ["truck"]
    assert "only truck" in a["explanation"]
