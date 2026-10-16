"""Turn instructions from FloodNet's own path geometry, GPS snapping and GPS freshness (D-40, D-42).
Written before floodnet/ops/navigation.py existed."""
import pytest

from floodnet.ops import navigation as nav
from tests.ops_net import ROADS, mid


def _segs(*pairs):
    """Path segments in the shape trips store them: seg_id and the node the vehicle reaches."""
    by = {(s.u, s.v): s.seg_id for s in ROADS.segments}
    by.update({(s.v, s.u): s.seg_id for s in ROADS.segments})
    return [{"seg_id": by[(a, b)], "to_node": b} for a, b in pairs]


def test_straight_on_one_street_is_one_instruction_then_arrival():
    m = nav.maneuvers(ROADS, 0, _segs((0, 1), (1, 2)), destination="Shelter A")
    assert [x["type"] for x in m] == ["depart", "arrive"]
    assert "South Rd" in m[0]["instruction"] and m[0]["instruction"].startswith("Head east")
    assert m[-1]["instruction"] == "Arrive at Shelter A"
    assert m[0]["distance_m"] == pytest.approx(1052, abs=15)


def test_turns_are_named_left_or_right_with_the_new_street():
    # 0 -> 1 east on South Rd, turn north (left) onto Mid Ln, then right (east) onto North Rd
    m = nav.maneuvers(ROADS, 0, _segs((0, 1), (1, 4), (4, 5)), destination="X")
    turns = [x for x in m if x["type"] == "turn"]
    assert [t["modifier"] for t in turns] == ["left", "right"]
    assert turns[0]["instruction"] == "Turn left onto Mid Ln"
    assert turns[1]["instruction"] == "Turn right onto North Rd"


def test_bearing_classes():
    assert nav.turn_modifier(5) == "straight"
    assert nav.turn_modifier(-35) == "slight left"
    assert nav.turn_modifier(90) == "right"
    assert nav.turn_modifier(170) == "u-turn"


def test_snap_to_route_reports_distance_and_segment():
    segs = _segs((0, 1), (1, 2))
    on = nav.snap_to_route(ROADS, 0, segs, *mid(1, 2, north_m=20))
    assert on["seg_index"] == 1 and on["distance_m"] == pytest.approx(20, abs=2)
    far = nav.snap_to_route(ROADS, 0, segs, *mid(3, 4))
    assert far["distance_m"] > 500


def test_gps_freshness_bands():
    assert nav.gps_freshness(10) == "FRESH"
    assert nav.gps_freshness(31) == "STALE"
    assert nav.gps_freshness(121) == "LOST"
    assert nav.gps_freshness(None) == "NONE"
