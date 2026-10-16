"""Rescue trips are dispatched assignments (D-41): lifecycle, ledger mapping, driver token, GPS fixes, field
reports. Written before floodnet/ops/trips.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.access.events import ClosureBook
from floodnet.ops import incident as inc
from floodnet.ops import trips as tr
from floodnet.ops.store import OpsStore
from floodnet.response import planner
from tests.ops_net import G, LONLAT, NO_CTX, ROADS, forecast

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def assignment(status="ACCEPTED", vehicle_type="truck", aid="A1"):
    """A planner mission on the test network: vehicle and team at node 0, settlement at node 2, shelter at node 5."""
    rt = planner.Router(ROADS, G, forecast(), NO_CTX, ClosureBook([], 0))
    here = {"lonlat": LONLAT[0].tolist(), "name": "base"}
    m = planner._mission_legs(rt, {**here, "name": "Team"}, {**here, "type": vehicle_type},
                              {"lonlat": LONLAT[2].tolist(), "name": "Settlement"},
                              {"lonlat": LONLAT[5].tolist(), "name": "Shelter"}, 0.0)
    assert m["feasible"]
    return {"assignment_id": aid, "settlement_id": "S1", "team_id": "T1", "vehicle_id": "V1", "shelter_id": "SH1",
            "people": 10, "status": status, "legs": m["legs"], "plan_version": 3, "history": []}


def test_create_trip_from_accepted_assignment_returns_the_token_once(store):
    trip, token = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    assert trip["stage"] == "ASSIGNED" and trip["assignment_id"] == "A1" and trip["plan_version"] == 3
    assert token and token not in str(trip)                    # only the hash is stored
    assert tr.trip_for_token(store, token)["trip_id"] == trip["trip_id"]
    assert tr.trip_for_token(store, "not-the-token") is None
    again, token2 = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=1)
    assert again["trip_id"] == trip["trip_id"] and token2 is None


@pytest.mark.parametrize("status", ["PROPOSED", "COMPLETED", "CANCELLED"])
def test_only_committed_assignments_become_trips(store, status):
    with pytest.raises(ValueError, match="ACCEPTED or DISPATCHED"):
        tr.create_trip(store, assignment(status=status), vehicle_type="truck", now_t=0)


def test_stages_only_move_forward_and_map_to_the_ledger(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = trip["trip_id"]
    assert tr.ledger_status_for_stage("ASSIGNED") == "DISPATCHED"
    tr.set_stage(store, tid, "EN_ROUTE", actor=f"driver:{tid}", now_t=1)
    t = tr.set_stage(store, tid, "LOADED", actor=f"driver:{tid}", now_t=12)      # skipping AT_SETTLEMENT is allowed
    assert tr.ledger_status_for_stage(t["stage"]) == "IN_PROGRESS"
    with pytest.raises(ValueError, match="backwards"):
        tr.set_stage(store, tid, "EN_ROUTE", actor=f"driver:{tid}", now_t=13)
    done = tr.set_stage(store, tid, "COMPLETED", actor=f"driver:{tid}", now_t=20)
    assert tr.ledger_status_for_stage(done["stage"]) == "COMPLETED"
    with pytest.raises(ValueError, match="finished"):
        tr.set_stage(store, tid, "ABORTED", actor="dispatcher", now_t=21, reason="x")
    assert [h["stage"] for h in done["stage_history"]] == ["ASSIGNED", "EN_ROUTE", "LOADED", "COMPLETED"]


def test_abort_needs_the_dispatcher_and_a_reason(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    with pytest.raises(ValueError, match="dispatcher"):
        tr.set_stage(store, trip["trip_id"], "ABORTED", actor=f"driver:{trip['trip_id']}", now_t=1, reason="x")
    with pytest.raises(ValueError, match="reason"):
        tr.set_stage(store, trip["trip_id"], "ABORTED", actor="dispatcher", now_t=1)
    t = tr.set_stage(store, trip["trip_id"], "ABORTED", actor="dispatcher", now_t=1, reason="road impassable")
    assert tr.ledger_status_for_stage(t["stage"]) == "CANCELLED"


def test_gps_fix_is_validated_and_kept_with_both_times(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = trip["trip_id"]
    fix = {"lon": 72.845, "lat": 19.010, "accuracy_m": 8, "recorded_at": (NOW - timedelta(seconds=5)).isoformat()}
    p = tr.record_position(store, tid, fix, actor=f"driver:{tid}", now_wall=NOW)
    assert p["source"] == "DEVICE_GPS" and p["received_at"].startswith("2026-10-11T10:00:00")
    assert store.get("trips", tid)["last_position"]["lat"] == 19.010
    with pytest.raises(ValueError, match="latitude"):
        tr.record_position(store, tid, {**fix, "lat": 95}, actor=f"driver:{tid}", now_wall=NOW)
    with pytest.raises(ValueError, match="future"):
        tr.record_position(store, tid, {**fix, "recorded_at": (NOW + timedelta(minutes=5)).isoformat()},
                           actor=f"driver:{tid}", now_wall=NOW)
    with pytest.raises(ValueError, match="SIMULATED"):
        tr.record_position(store, tid, {**fix, "source": "SIMULATED"}, actor=f"driver:{tid}", now_wall=NOW)
    sim = tr.record_position(store, tid, {**fix, "source": "SIMULATED"}, actor="dispatcher", now_wall=NOW)
    assert sim["source"] == "SIMULATED"


def test_an_older_fix_does_not_replace_a_newer_one(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = trip["trip_id"]
    new = {"lon": 72.846, "lat": 19.010, "recorded_at": (NOW - timedelta(seconds=2)).isoformat()}
    old = {"lon": 72.841, "lat": 19.010, "recorded_at": (NOW - timedelta(seconds=40)).isoformat()}
    tr.record_position(store, tid, new, actor=f"driver:{tid}", now_wall=NOW)
    tr.record_position(store, tid, old, actor=f"driver:{tid}", now_wall=NOW)
    assert store.get("trips", tid)["last_position"]["lon"] == 72.846


def test_blockage_report_becomes_a_binding_field_team_closure(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = trip["trip_id"]
    out = tr.report(store, tid, "blockage", actor=f"driver:{tid}", now_t=3, seg_id="s12", note="tree down",
                    known_segments={"s12"})
    ev = out["road_event"]
    assert ev["kind"] == "closure" and ev["source_tier"] == "FIELD_TEAM" and ev["origin"] == f"driver:{tid}"
    assert ev["vehicle_types"] == [] and ev["received_t_min"] == 3
    assert inc.closures(store, 3).closure_at("s12", 3, vehicle="car").event_id == ev["event_id"]


def test_high_water_binds_vehicles_no_taller_than_the_reporting_one(store):
    trip, _ = tr.create_trip(store, assignment(vehicle_type="car"), vehicle_type="car", now_t=0)
    tid = trip["trip_id"]
    ev = tr.report(store, tid, "high_water", actor=f"driver:{tid}", now_t=3, seg_id="s12", known_segments={"s12"})["road_event"]
    assert set(ev["vehicle_types"]) == {"car", "motorcycle"}                     # limits <= 30 cm (ASSUMED limits)
    book = inc.closures(store, 3)
    assert book.closure_at("s12", 3, vehicle="truck") is None


def test_report_on_an_unknown_road_or_kind_is_rejected(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = trip["trip_id"]
    with pytest.raises(ValueError, match="road"):
        tr.report(store, tid, "blockage", actor=f"driver:{tid}", now_t=3, seg_id="nope", known_segments={"s12"})
    with pytest.raises(ValueError, match="kind"):
        tr.report(store, tid, "alien", actor=f"driver:{tid}", now_t=3, known_segments={"s12"})
    delay = tr.report(store, tid, "delay", actor=f"driver:{tid}", now_t=3, note="crowd", known_segments={"s12"})
    assert delay["road_event"] is None and delay["report"]["kind"] == "delay"


def test_trips_belong_to_one_incident_assignment_ids_repeat_across_incidents(store):
    """Found in the browser walkthrough: a new incident's plan reuses A1; an old incident's active trip for A1 must
    neither be returned for it nor block a new trip."""
    old, tok_old = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0, incident_id="INC-old")
    new, tok_new = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0, incident_id="INC-new")
    assert new["trip_id"] != old["trip_id"] and tok_new is not None
    assert new["incident_id"] == "INC-new"
    again, tok = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=1, incident_id="INC-new")
    assert again["trip_id"] == new["trip_id"] and tok is None


def test_a_driver_report_carries_its_trip_incident(store):
    trip, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0, incident_id="INC-x")
    ev = tr.report(store, trip["trip_id"], "blockage", actor=f"driver:{trip['trip_id']}", now_t=2, seg_id="s12",
                   known_segments={"s12"})["road_event"]
    assert ev["incident_id"] == "INC-x"
