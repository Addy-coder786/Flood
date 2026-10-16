"""QA audit L4: a `references` value given as one id string must become a one-item list, never a list of characters."""
from floodnet.access.events import parse_events
from floodnet.response.fixtures import demo_resources
from floodnet.response.ledger import parse_response_events


def _known():
    r = demo_resources()
    return {"vehicle_id": {v["id"] for v in r["vehicles"]}, "shelter_id": {s["id"] for s in r["shelters"]},
            "settlement_id": {s["id"] for s in r["settlements"]}}


def test_resource_event_reference_string_is_one_reference():
    ev = parse_response_events([{"kind": "shelter_update", "shelter_id": "SH1", "occupancy": 40, "received_t_min": 5,
                                 "references": "E12"}], _known())
    assert list(ev[0]["references"]) == ["E12"]


def test_road_event_reference_string_is_one_reference():
    ev = parse_events([{"seg_id": "x", "kind": "reopen", "received_t_min": 5, "references": "C12"}])
    assert list(ev[0].references) == ["C12"]
