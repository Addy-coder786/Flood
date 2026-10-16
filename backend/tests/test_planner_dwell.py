"""Regression (found in the 2026-10-11 browser check): a mission that has reached its shelter and is unloading
(arrive <= now < done of its LAST leg) was flagged "planned completion T+18 has passed" at T+15. Only a mission whose
planned completion is actually in the past is overdue; one still in its final dwell needs no re-route and no flag."""
from floodnet.response.planner import _reroute_underway


class _NoRouter:
    def leg(self, *a, **k):          # must not be called: nothing is left to route
        raise AssertionError("no leg should be re-routed during the final dwell")


def _mission():
    seg = lambda a, b: {"seg_id": "x", "name": "", "enter_t_min": a, "exit_t_min": b, "to_node": 2, "status": "USABLE", "depth_cm": 0}
    return {"assignment_id": "A1", "status": "IN_PROGRESS", "vehicle_id": "V1", "shelter_id": "SH1", "legs": [
        {"kind": "to_settlement", "to": "S1", "from_node": 1, "to_node": 2, "depart_t_min": 0, "arrive_t_min": 4, "done_t_min": 8, "segments": [seg(0, 4)]},
        {"kind": "to_shelter", "to": "SH1", "from_node": 2, "to_node": 3, "depart_t_min": 8, "arrive_t_min": 13, "done_t_min": 18, "segments": [seg(8, 13)]},
    ]}


def test_mission_unloading_at_its_shelter_is_not_overdue():
    r = _reroute_underway(_NoRouter(), _mission(), {"shelters": {}, "vehicles": {}}, now=15)
    assert r["ok"] is True
    assert not r.get("attention"), r.get("attention")
    assert r["changed"] is False


def test_mission_past_its_planned_completion_is_still_flagged():
    r = _reroute_underway(_NoRouter(), _mission(), {"shelters": {}, "vehicles": {}}, now=19)
    assert r["ok"] is True
    assert "has passed" in r["attention"]
