"""Resource registry (D-43): NIMS-inspired status, validated and timestamped updates from registered responders,
staleness, out-of-order handling and the planner connection. Written before floodnet/ops/registry.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.ops import registry as rg
from floodnet.ops.store import OpsStore
from floodnet.response import fixtures as fx
from floodnet.response.planner import build_state

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    s = OpsStore(tmp_path / "ops.sqlite3")
    rg.seed_from_fixtures(s)
    return s


def upd(store, rid, fields, *, actor="dispatcher", role="dispatcher", observed=None, ref=None, now=NOW):
    return rg.submit_update(store, actor=actor, role=role, resource_id=rid, fields=fields,
                            observed_at=(observed or now - timedelta(minutes=1)).isoformat(), now_wall=now, client_ref=ref)


def test_seed_is_idempotent_and_labelled_simulated(store):
    rg.seed_from_fixtures(store)
    res = rg.list_resources(store, now_wall=NOW)
    assert {r["id"] for r in res} >= {"T1", "T2", "V1", "V2", "SH1", "SH2"}
    assert len(res) == len(fx.TEAMS) + len(fx.VEHICLES) + len(fx.SHELTERS)
    v1 = next(r for r in res if r["id"] == "V1")
    assert v1["status"] == "AVAILABLE" and v1["verified"] is False and "SIMULATED" in v1["source"]


def test_out_of_service_needs_a_nims_reason(store):
    with pytest.raises(ValueError, match="reason"):
        upd(store, "V1", {"status": "OUT_OF_SERVICE"})
    with pytest.raises(ValueError, match="status"):
        upd(store, "V1", {"status": "BROKEN"})
    u = upd(store, "V1", {"status": "OUT_OF_SERVICE", "out_of_service_reason": "MECHANICAL"})
    assert u["applied"] is True
    v1 = rg.get_resource(store, "V1", now_wall=NOW)
    assert v1["status"] == "OUT_OF_SERVICE" and v1["verified"] is True and v1["last_update"]["by"] == "dispatcher"


def test_responder_scope_and_role_are_enforced(store):
    resp, token = rg.register_responder(store, name="Shelter desk SH1", role="shelter_staff", scope=["SH1"])
    assert token and token not in str(resp)
    who = rg.responder_for_token(store, token)
    assert who["responder_id"] == resp["responder_id"]
    actor = f"responder:{resp['responder_id']}"
    ok = upd(store, "SH1", {"occupancy": 70}, actor=actor, role="shelter_staff")
    assert ok["applied"]
    with pytest.raises(PermissionError):
        upd(store, "SH2", {"occupancy": 10}, actor=actor, role="shelter_staff")          # outside scope
    with pytest.raises(PermissionError):
        upd(store, "SH1", {"total_capacity": 500}, actor=actor, role="field_team")       # wrong role for shelters
    with pytest.raises(PermissionError):
        upd(store, "T1", {"capabilities": ["evacuation"]}, actor="responder:x", role="field_team")   # dispatcher only


def test_values_are_validated(store):
    with pytest.raises(ValueError, match="occupancy"):
        upd(store, "SH1", {"occupancy": -3})
    with pytest.raises(ValueError, match="total_capacity"):
        upd(store, "SH1", {"total_capacity": 0})
    with pytest.raises(ValueError, match="unknown resource"):
        upd(store, "ZZ9", {"status": "AVAILABLE"})
    with pytest.raises(ValueError, match="field"):
        upd(store, "V1", {"colour": "red"})


def test_future_and_out_of_order_updates(store):
    with pytest.raises(ValueError, match="future"):
        upd(store, "SH1", {"occupancy": 30}, observed=NOW + timedelta(minutes=10))
    upd(store, "SH1", {"occupancy": 60}, observed=NOW - timedelta(minutes=2))
    late = upd(store, "SH1", {"occupancy": 10}, observed=NOW - timedelta(minutes=20))
    assert late["applied"] is False and "older" in late["note"]
    assert rg.get_resource(store, "SH1", now_wall=NOW)["occupancy"] == 60


def test_duplicate_submission_is_stored_once(store):
    a = upd(store, "SH1", {"occupancy": 40}, ref="r-1")
    b = upd(store, "SH1", {"occupancy": 40}, ref="r-1")
    assert a["update_id"] == b["update_id"]
    assert len(store.list("resource_updates")) == 1


def test_staleness_is_a_flag_with_the_age(store):
    upd(store, "SH1", {"occupancy": 40}, observed=NOW - timedelta(minutes=5))
    later = NOW + timedelta(minutes=rg.STALE_AFTER_MIN + 1)
    r = rg.get_resource(store, "SH1", now_wall=later)
    assert r["stale"] is True and r["occupancy"] == 40 and r["age_min"] > rg.STALE_AFTER_MIN


def test_planner_resources_carry_registry_state_into_build_state(store):
    upd(store, "V2", {"status": "OUT_OF_SERVICE", "out_of_service_reason": "PERSONNEL"})
    upd(store, "SH2", {"occupancy": 150})
    upd(store, "T1", {"capabilities": ["evacuation"]})                 # water rescue no longer offered
    res = rg.planner_resources(store, now_wall=NOW)
    st = build_state(res, [], 0)
    assert st["vehicles"]["V2"]["available"] is False and "PERSONNEL" in st["vehicles"]["V2"]["unavailable_reason"]
    assert st["vehicles"]["V1"]["available"] is True
    assert st["shelters"]["SH2"]["occupancy"] == 150
    assert st["teams"]["T1"]["capabilities"] == ["evacuation"]
    assert "registry" in res["tag"]


def test_assigned_elsewhere_is_unavailable_to_the_planner(store):
    upd(store, "V1", {"status": "ASSIGNED"})
    st = build_state(rg.planner_resources(store, now_wall=NOW), [], 0)
    assert st["vehicles"]["V1"]["available"] is False and "assigned" in st["vehicles"]["V1"]["unavailable_reason"]


def test_an_out_of_service_specialist_team_leaves_the_need_unmet_with_the_reason():
    """Registry state reaches the allocation: the only water-rescue team is out of service, so the settlement that
    needs water rescue is unmet and the explanation names the unavailable team (test network, SIMULATED values)."""
    from floodnet.access.events import ClosureBook
    from floodnet.response import planner
    from tests.ops_net import G, LONLAT, NO_CTX, ROADS, forecast
    p0, p2, p5 = LONLAT[0].tolist(), LONLAT[2].tolist(), LONLAT[5].tolist()
    resources = {
        "settlements": [{"id": "S1", "name": "S1", "lonlat": p2, "people_needing_help": 5, "priority": "high",
                         "required_capabilities": ["water rescue"]}],
        "shelters": [{"id": "SH1", "name": "SH1", "lonlat": p5, "total_capacity": 50, "occupancy": 0, "status": "open"}],
        "teams": [{"id": "T1", "name": "T1", "lonlat": p0, "capabilities": ["water rescue", "evacuation"],
                   "available": False, "unavailable_reason": "out of service (MECHANICAL)"},
                  {"id": "T2", "name": "T2", "lonlat": p0, "capabilities": ["evacuation"]}],
        "vehicles": [{"id": "V1", "name": "V1", "type": "truck", "passenger_capacity": 10, "lonlat": p0}]}
    rt = planner.Router(ROADS, G, forecast(), NO_CTX, ClosureBook([], 0))
    out = planner.plan(resources, [], [], rt, 0.0)
    assert out["summary"]["assignments_proposed"] == 0
    r = out["unmet"]["S1"]["reasons"][0]
    assert r["reason"] == "no team with capability 'water rescue'" and "T1 (out of service" in r["detail"]
    resources["teams"][0].pop("available"); resources["teams"][0].pop("unavailable_reason")
    assert planner.plan(resources, [], [], rt, 0.0)["summary"]["assignments_proposed"] == 1
