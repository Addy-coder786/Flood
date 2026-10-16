"""Registry registration and import (2026-10-11): authorised records entered by the dispatcher or imported from a
file, validated against the existing vocabularies (capabilities from the fixtures, vehicle types from
config.VEHICLE_LIMIT_CM), inside the pilot area, with source, observed/received time and a freshness flag.
Written before the registration code in floodnet/ops/registry.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet import config
from floodnet.ops import registry as rg
from floodnet.ops.store import OpsStore
from floodnet.response import fixtures as fx
from floodnet.response.planner import build_state

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
IN_PILOT = [72.8460, 19.0140]


@pytest.fixture
def store(tmp_path):
    s = OpsStore(tmp_path / "ops.sqlite3")
    rg.seed_from_fixtures(s)
    return s


def team(**kw):
    return {"name": "Ward rescue team A", "capabilities": ["Evacuation", "water rescue"], "members": 5,
            "base": "Dadar ward office", "lonlat": IN_PILOT, **kw}


def vehicle(**kw):
    return {"name": "Ward ambulance 1", "type": "ambulance", "passenger_capacity": 6, "base": "Ward office",
            "lonlat": IN_PILOT, **kw}


def shelter(**kw):
    return {"name": "Municipal school hall", "lonlat": IN_PILOT, "total_capacity": 120, "occupancy": 10,
            "status": "open", **kw}


def reg(store, kind, data, observed=None, **kw):
    return rg.register_resource(store, kind, data, actor="dispatcher", now_wall=NOW,
                                observed_at=(observed or NOW - timedelta(minutes=1)).isoformat(), **kw)


# ------------------------------------------------------------------ vocabularies
def test_vocabularies_come_from_the_existing_fixtures_and_config():
    fixture_caps = {c for t in fx.TEAMS for c in t["capabilities"]} | {c for s in fx.SETTLEMENTS for c in s["required_capabilities"]}
    assert set(rg.CAPABILITIES) == fixture_caps == {"evacuation", "water rescue"}
    assert "pedestrian" not in rg.VEHICLE_TYPES
    for t, limit in rg.VEHICLE_TYPES.items():
        assert config.VEHICLE_LIMIT_CM[t] == limit
    assert rg.PILOT_BBOX == config.PILOT_BBOX_LONLAT


def test_seed_records_stay_simulated_with_unknown_freshness(store):
    t1 = rg.get_resource(store, "T1", now_wall=NOW)
    assert "SIMULATED" in t1["source"] and t1["verified"] is False
    assert t1["freshness"] == "UNKNOWN" and t1["observed_at"] is None


# ------------------------------------------------------------------ dispatcher entry
def test_dispatcher_registers_a_team_with_source_times_and_freshness(store):
    r = reg(store, "team", team())
    assert r["id"] == "T3" and r["kind"] == "team"                  # T1, T2 are the seed
    assert r["source"] == "dispatcher entry" and r["verified"] is True
    assert r["capabilities"] == ["evacuation", "water rescue"]      # normalised like the planner does
    assert r["status"] == "AVAILABLE" and r["out_of_service_reason"] is None
    assert r["observed_at"] == "2026-10-11T09:59:00Z" and r["received_at"] == "2026-10-11T10:00:00Z"
    assert r["freshness"] == "CURRENT" and r["stale"] is False
    assert r["entered_by"] == "dispatcher"
    assert "SIMULATED" not in str(r["numbers_provenance"])
    assert store.audit("resources", "T3")[0]["action"] == "register"


@pytest.mark.parametrize("bad, match", [
    ({"capabilities": ["boat"]}, "capabilit"),
    ({"capabilities": []}, "capabilit"),
    ({"lonlat": [72.90, 19.10]}, "pilot area"),
    ({"name": "  "}, "name"),
    ({"name": "x" * 121}, "name"),
    ({"members": 0}, "members"),
    ({"base": ""}, "base"),
    ({"status": "OUT_OF_SERVICE"}, "reason"),
    ({"colour": "red"}, "colour"),
])
def test_team_registration_is_validated(store, bad, match):
    with pytest.raises(ValueError, match=match):
        reg(store, "team", team(**bad))
    assert len(rg.list_resources(store, NOW)) == 6                  # nothing stored


def test_vehicle_type_must_be_known_and_carries_its_assumed_depth_limit(store):
    with pytest.raises(ValueError, match="type"):
        reg(store, "vehicle", vehicle(type="boat"))
    with pytest.raises(ValueError, match="type"):
        reg(store, "vehicle", vehicle(type="pedestrian"))
    with pytest.raises(ValueError, match="passenger_capacity"):
        reg(store, "vehicle", {k: v for k, v in vehicle().items() if k != "passenger_capacity"})
    v = reg(store, "vehicle", vehicle(type="Truck"))
    assert v["id"] == "V3" and v["type"] == "truck" and v["depth_limit_cm"] == 60
    assert "ASSUMED" in v["depth_limit_source"]


def test_shelter_registration_checks_occupancy_and_status(store):
    with pytest.raises(ValueError, match="occupancy"):
        reg(store, "shelter", shelter(occupancy=130))
    with pytest.raises(ValueError, match="status"):
        reg(store, "shelter", shelter(status="full"))
    s = reg(store, "shelter", shelter())
    assert s["id"] == "SH3" and s["total_capacity"] == 120 and s["occupancy"] == 10 and s["status"] == "open"


def test_duplicate_names_are_refused_and_client_ref_is_idempotent(store):
    with pytest.raises(ValueError, match="already"):
        reg(store, "team", team(name="fire rescue team 1"))        # the seed T1, case-insensitive
    a = reg(store, "shelter", shelter(), client_ref="form-1")
    b = reg(store, "shelter", shelter(), client_ref="form-1")
    assert a["id"] == b["id"] and len(store.list("resources", lambda r: r["kind"] == "shelter")) == 3


def test_observed_time_rules(store):
    with pytest.raises(ValueError, match="future"):
        reg(store, "team", team(), observed=NOW + timedelta(minutes=10))
    old = reg(store, "team", team(), observed=NOW - timedelta(minutes=rg.STALE_AFTER_MIN + 5))
    assert old["stale"] is True and old["freshness"] == "STALE"


# ------------------------------------------------------------------ import
CSV_MIXED = """kind,name,lon,lat,base,capabilities,members,type,passenger_capacity,total_capacity,occupancy,status,observed_at
team,Volunteer rescue team,72.8461,19.0141,Ward office,evacuation,4,,,,,,2026-10-11T09:30:00Z
vehicle,Hired boat,72.8462,19.0142,Jetty,,,boat,8,,,,
shelter,Community hall B,72.8463,19.0143,,,,,,90,0,open,
"""


def test_import_dry_run_previews_every_row_and_stores_nothing(store):
    rows = rg.parse_import(CSV_MIXED, "csv")
    out = rg.import_records(store, rows, label="ward list.csv", now_wall=NOW, commit=False)
    assert out["committed"] is False and out["valid"] == 2 and out["invalid"] == 1
    bad = [r for r in out["rows"] if r["errors"]]
    assert [r["row"] for r in bad] == [2] and "type" in bad[0]["errors"][0]
    assert out["rows"][0]["kind"] == "team" and out["rows"][0]["record"]["capabilities"] == ["evacuation"]
    assert len(rg.list_resources(store, NOW)) == 6


def test_import_commit_is_all_or_nothing(store):
    rows = rg.parse_import(CSV_MIXED, "csv")
    with pytest.raises(ValueError, match="nothing was imported"):
        rg.import_records(store, rows, label="ward list.csv", now_wall=NOW, commit=True)
    assert len(rg.list_resources(store, NOW)) == 6
    good = rg.parse_import(CSV_MIXED.replace(",boat,", ",truck,"), "csv")
    out = rg.import_records(store, good, label="ward list.csv", now_wall=NOW, commit=True)
    assert out["committed"] is True and len(out["ids"]) == 3
    recs = [rg.get_resource(store, i, now_wall=NOW) for i in out["ids"]]
    assert all(r["source"] == "import: ward list.csv" for r in recs)
    assert recs[0]["freshness"] == "CURRENT" and recs[0]["observed_at"] == "2026-10-11T09:30:00Z"
    assert recs[1]["freshness"] == "UNKNOWN" and recs[1]["observed_at"] is None     # the file did not say when
    assert recs[1]["received_at"] == "2026-10-11T10:00:00Z"


def test_import_commit_rolls_back_when_a_write_fails_midway(store, monkeypatch):
    rows = rg.parse_import(CSV_MIXED.replace(",boat,", ",truck,"), "csv")
    real_put, calls = store.put, {"n": 0}

    def flaky(collection, *a, **kw):
        if collection == "resources":
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("disk full (simulated)")
        return real_put(collection, *a, **kw)
    monkeypatch.setattr(store, "put", flaky)
    with pytest.raises(RuntimeError):
        rg.import_records(store, rows, label="x.csv", now_wall=NOW, commit=True)
    monkeypatch.setattr(store, "put", real_put)
    assert len(rg.list_resources(store, NOW)) == 6


def test_import_json_forms_and_file_level_errors(store):
    a = rg.parse_import('[{"kind": "shelter", "name": "Hall C", "lonlat": [72.846, 19.014], "total_capacity": 50}]', "json")
    b = rg.parse_import('{"records": [{"kind": "shelter", "name": "Hall C", "lon": 72.846, "lat": 19.014, "total_capacity": 50}]}', "json")
    assert rg.import_records(store, a, label="a", now_wall=NOW)["valid"] == 1
    assert rg.import_records(store, b, label="b", now_wall=NOW)["valid"] == 1
    with pytest.raises(ValueError, match="JSON"):
        rg.parse_import("{not json", "json")
    with pytest.raises(ValueError, match="kind"):
        rg.parse_import("name,lon,lat\nx,72.84,19.01\n", "csv")
    with pytest.raises(ValueError, match="rows"):
        rg.parse_import("[" + ",".join(['{"kind": "team"}'] * (rg.IMPORT_MAX_ROWS + 1)) + "]", "json")
    dup = rg.parse_import('[{"kind": "shelter", "name": "Hall D", "lonlat": [72.846, 19.014], "total_capacity": 5},'
                          ' {"kind": "shelter", "name": "hall d", "lonlat": [72.846, 19.014], "total_capacity": 5}]', "json")
    out = rg.import_records(store, dup, label="d", now_wall=NOW)
    assert out["invalid"] == 1 and "already" in out["rows"][1]["errors"][0]
    unknown = rg.import_records(store, [{"kind": "boat", "name": "x"}], label="u", now_wall=NOW)
    assert "kind" in unknown["rows"][0]["errors"][0]


# ------------------------------------------------------------------ updates, responders, planner
def test_a_record_with_unknown_observation_time_can_still_be_updated(store):
    rows = rg.parse_import("kind,name,lon,lat,total_capacity\nshelter,Hall E,72.846,19.014,40\n", "csv")
    sid = rg.import_records(store, rows, label="e.csv", now_wall=NOW, commit=True)["ids"][0]
    u = rg.submit_update(store, actor="dispatcher", role="dispatcher", resource_id=sid, fields={"occupancy": 12},
                         observed_at=(NOW - timedelta(minutes=2)).isoformat(), now_wall=NOW)
    assert u["applied"] is True
    r = rg.get_resource(store, sid, now_wall=NOW)
    assert r["occupancy"] == 12 and r["freshness"] == "CURRENT"


def test_volunteer_role_has_no_resource_scope_and_cannot_update_resources(store):
    v, token = rg.register_responder(store, name="Volunteer Asha", role="volunteer", scope=[])
    assert v["role"] == "volunteer" and v["scope"] == []
    with pytest.raises(ValueError, match="volunteer"):
        rg.register_responder(store, name="Volunteer B", role="volunteer", scope=["SH1"])
    with pytest.raises(ValueError, match="scope"):
        rg.register_responder(store, name="Team", role="field_team", scope=[])
    with pytest.raises(PermissionError):
        rg.submit_update(store, actor=f"responder:{v['responder_id']}", role="volunteer", resource_id="SH1",
                         fields={"occupancy": 3}, observed_at=None, now_wall=NOW)


def test_revoked_responder_tokens_stop_working(store):
    r, token = rg.register_responder(store, name="Desk", role="shelter_staff", scope=["SH1"])
    assert rg.responder_for_token(store, token) is not None
    rg.revoke_responder(store, r["responder_id"])
    assert rg.responder_for_token(store, token) is None
    with pytest.raises(ValueError, match="unknown responder"):
        rg.revoke_responder(store, "RP999")


def test_registered_records_reach_the_planner_with_their_state(store):
    t = reg(store, "team", team(capabilities=["evacuation"]))
    v = reg(store, "vehicle", vehicle(status="OUT_OF_SERVICE", out_of_service_reason="mechanical"))
    s = reg(store, "shelter", shelter())
    res = rg.planner_resources(store, now_wall=NOW)
    st = build_state(res, [], 0)
    assert st["teams"][t["id"]]["capabilities"] == ["evacuation"] and st["teams"][t["id"]]["available"] is True
    assert st["vehicles"][v["id"]]["available"] is False and "MECHANICAL" in st["vehicles"][v["id"]]["unavailable_reason"]
    assert st["vehicles"][v["id"]]["type"] == "ambulance"
    assert st["shelters"][s["id"]]["total_capacity"] == 120 and st["shelters"][s["id"]]["status"] == "open"
    assert set(st["teams"]) >= {"T1", "T2"}                         # the seed stays


def test_a_vehicle_on_an_active_direct_trip_is_unavailable_to_the_planner(store):
    """Lead integration 2026-10-11: direct trips (started on the Roads page, ops/trips.py kind "direct") commit a
    registry vehicle outside the planner's ledger. Mission trips are already in the ledger and are left alone."""
    store.put("trips", "TR7", {"trip_id": "TR7", "kind": "direct", "vehicle_id": "V1", "stage": "EN_ROUTE"},
              actor="dispatcher", action="create")
    store.put("trips", "TR8", {"trip_id": "TR8", "kind": "mission", "vehicle_id": "V2", "stage": "EN_ROUTE"},
              actor="dispatcher", action="create")
    store.put("trips", "TR9", {"trip_id": "TR9", "vehicle_id": "V2", "stage": "ASSIGNED"}, actor="dispatcher", action="create")
    store.put("trips", "TR10", {"trip_id": "TR10", "kind": "direct", "vehicle_id": None, "stage": "PLANNED"},
              actor="dispatcher", action="create")
    v = {r["id"]: r for r in rg.planner_resources(store, now_wall=NOW)["vehicles"]}
    assert v["V1"]["available"] is False
    assert v["V1"]["unavailable_reason"] == "on rescue trip TR7 (started on the Roads page)"
    assert v["V2"].get("available", True) is True                   # mission trips: the ledger handles them
    st = build_state(rg.planner_resources(store, now_wall=NOW), [], 0)
    assert st["vehicles"]["V1"]["available"] is False and "TR7" in st["vehicles"]["V1"]["unavailable_reason"]
    trip = store.get("trips", "TR7")
    store.put("trips", "TR7", {**trip, "stage": "COMPLETED"}, actor="dispatcher", action="stage")
    v = {r["id"]: r for r in rg.planner_resources(store, now_wall=NOW)["vehicles"]}
    assert v["V1"].get("available", True) is True and not v["V1"].get("unavailable_reason")


def test_an_out_of_service_vehicle_keeps_its_own_reason_even_on_a_direct_trip(store):
    upd = rg.submit_update(store, actor="dispatcher", role="dispatcher", resource_id="V1",
                           fields={"status": "OUT_OF_SERVICE", "out_of_service_reason": "MECHANICAL"},
                           observed_at=(NOW - timedelta(minutes=1)).isoformat(), now_wall=NOW)
    assert upd["applied"]
    store.put("trips", "TR7", {"trip_id": "TR7", "kind": "direct", "vehicle_id": "V1", "stage": "ARRIVED"},
              actor="dispatcher", action="create")
    v1 = next(r for r in rg.planner_resources(store, now_wall=NOW)["vehicles"] if r["id"] == "V1")
    assert v1["available"] is False and "MECHANICAL" in v1["unavailable_reason"] and "TR7" in v1["unavailable_reason"]
