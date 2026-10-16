"""Volunteer-submitted candidate shelters (2026-10-11): SUBMITTED -> PERMISSION_CONFIRMED -> SUITABILITY_CHECKED ->
CONFIRMED (a registry shelter the planner can use) or REJECTED. A candidate never reaches the planner or the shelter
list before CONFIRMED; every step is validated and audited. The modelled flood depth at the site comes from the
forecast published to live operations, on the nearest road, or is UNKNOWN. Synthetic test network (tests/ops_net.py).
Written before the candidate code in floodnet/ops/registry.py existed."""
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.access.road_status import SegmentContext
from floodnet.ops import incident as inc
from floodnet.ops import registry as rg
from floodnet.ops.store import OpsStore
from floodnet.response.planner import build_state
from tests.ops_net import NO_CTX, ROADS, SEG_IDS, T

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
NEAR_S01 = [72.8425, 19.0102]          # about 22 m north of South Rd (s01)
FAR = [72.8425, 19.0125]               # more than 250 m from every test road


@pytest.fixture
def store(tmp_path):
    s = OpsStore(tmp_path / "ops.sqlite3")
    rg.seed_from_fixtures(s)
    return s


def submit(store, **kw):
    data = {"name": "Gurudwara community hall", "lonlat": NEAR_S01, "address": "Lane 3, near the market",
            "description": "Ground floor hall, owner willing to host", "capacity_estimate": 60, **kw}
    return rg.submit_candidate(store, data, actor="responder:RP1", submitted_by="volunteer Asha (RP1)", now_wall=NOW)


def permission(store, cid, **kw):
    data = {"granted_by_role": "hall manager", "granted_by_name": "Mr Patil", "method": "IN_PERSON",
            "granted_at": (NOW - timedelta(minutes=20)).isoformat(), **kw}
    return rg.record_permission(store, cid, data, actor="dispatcher", now_wall=NOW)


CHECK = {"access": "YES", "water_sanitation": "YES", "accessibility": "UNKNOWN", "capacity_estimate": 55,
         "notes": "ramp at side entrance not seen"}
SITE_UNKNOWN = {"status": "UNKNOWN", "reason": "no forecast has been published to live operations"}


def publish(store, **depths):
    streets = {sid: depths[sid] for sid in depths}
    inc.publish_forecast(store, run_id="t", data_mode="SYNTHETIC TEST", series_t_min=list(T), streets_cm=streets)


def shelters(store):
    return {r["id"]: r for r in rg.list_resources(store, NOW) if r["kind"] == "shelter"}


# ------------------------------------------------------------------ workflow
def test_full_workflow_creates_a_dispatcher_verified_shelter_the_planner_can_use(store):
    c = submit(store)
    cid = c["candidate_id"]
    assert c["state"] == "SUBMITTED" and c["submitted_by"] == "volunteer Asha (RP1)"
    assert c["received_at"] == "2026-10-11T10:00:00Z"
    before = set(shelters(store))
    c = permission(store, cid)
    assert c["state"] == "PERMISSION_CONFIRMED"
    assert c["permission"] == {"granted_by_role": "hall manager", "granted_by_name": "Mr Patil", "method": "IN_PERSON",
                               "granted_at": "2026-10-11T09:40:00Z", "note": "", "recorded_by": "dispatcher",
                               "recorded_at": "2026-10-11T10:00:00Z"}
    c = rg.record_suitability(store, cid, CHECK, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    assert c["state"] == "SUITABILITY_CHECKED"
    assert c["suitability"]["modelled_flood_depth"]["status"] == "UNKNOWN"
    assert c["suitability"]["access"] == "YES" and c["suitability"]["capacity_estimate"] == 55
    assert set(shelters(store)) == before                          # still not a shelter
    c = rg.confirm_candidate(store, cid, {}, actor="dispatcher", now_wall=NOW)
    assert c["state"] == "CONFIRMED"
    sh = rg.get_resource(store, c["shelter_id"], now_wall=NOW)
    assert sh["kind"] == "shelter" and sh["total_capacity"] == 55 and sh["occupancy"] == 0 and sh["status"] == "open"
    assert sh["source"].startswith("volunteer-submitted, verified by dispatcher") and cid in sh["source"]
    assert sh["verified"] is True and sh["freshness"] == "CURRENT"
    st = build_state(rg.planner_resources(store, now_wall=NOW), [], 0)
    assert st["shelters"][c["shelter_id"]]["total_capacity"] == 55
    assert [h["state"] for h in c["history"]] == ["SUBMITTED", "PERMISSION_CONFIRMED", "SUITABILITY_CHECKED", "CONFIRMED"]
    actions = [a["action"] for a in store.audit("shelter_candidates", cid)]
    assert actions == ["candidate_submit", "candidate_permission", "candidate_suitability", "candidate_confirm"]


def test_a_candidate_is_never_a_shelter_or_planner_input_before_confirmation(store):
    c = submit(store)
    cid = c["candidate_id"]
    for step in (lambda: None, lambda: permission(store, cid),
                 lambda: rg.record_suitability(store, cid, CHECK, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)):
        step()
        res = rg.planner_resources(store, now_wall=NOW)
        assert all(s["name"] != "Gurudwara community hall" for s in res["shelters"])
        assert all(r.get("name") != "Gurudwara community hall" for r in rg.list_resources(store, NOW))
    rg.reject_candidate(store, cid, "owner withdrew permission", actor="dispatcher", now_wall=NOW)
    assert all(s["name"] != "Gurudwara community hall" for s in rg.planner_resources(store, now_wall=NOW)["shelters"])


def test_steps_must_happen_in_order(store):
    cid = submit(store)["candidate_id"]
    with pytest.raises(ValueError, match="PERMISSION_CONFIRMED"):
        rg.record_suitability(store, cid, CHECK, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    with pytest.raises(ValueError, match="SUITABILITY_CHECKED"):
        rg.confirm_candidate(store, cid, {}, actor="dispatcher", now_wall=NOW)
    permission(store, cid)
    with pytest.raises(ValueError, match="SUBMITTED"):
        permission(store, cid)
    with pytest.raises(ValueError, match="reason"):
        rg.reject_candidate(store, cid, "  ", actor="dispatcher", now_wall=NOW)
    c = rg.reject_candidate(store, cid, "building is below road level", actor="dispatcher", now_wall=NOW)
    assert c["state"] == "REJECTED" and c["rejection"]["reason"] == "building is below road level"
    with pytest.raises(ValueError):
        rg.record_suitability(store, cid, CHECK, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    with pytest.raises(ValueError):
        rg.reject_candidate(store, cid, "again", actor="dispatcher", now_wall=NOW)
    with pytest.raises(ValueError, match="unknown candidate"):
        permission(store, "CS999")


@pytest.mark.parametrize("bad, match", [
    ({"lonlat": [72.90, 19.10]}, "pilot area"),
    ({"lonlat": "here"}, "lonlat"),
    ({"name": ""}, "name"),
    ({"name": "x" * 121}, "name"),
    ({"address": "a" * 201}, "address"),
    ({"description": "d" * 501}, "description"),
    ({"capacity_estimate": 0}, "capacity_estimate"),
    ({"description": "call me on 98200 12345"}, "phone"),
])
def test_submissions_are_validated(store, bad, match):
    with pytest.raises(ValueError, match=match):
        submit(store, **bad)
    assert store.list("shelter_candidates") == []


@pytest.mark.parametrize("bad, match", [
    ({"granted_by_role": ""}, "granted_by_role"),
    ({"method": "telepathy"}, "method"),
    ({"granted_at": (NOW + timedelta(hours=1)).isoformat()}, "future"),
    ({"granted_at": None}, "granted_at"),
    ({"granted_by_name": "Patil +91 98200 12345"}, "phone"),
])
def test_permission_is_validated(store, bad, match):
    cid = submit(store)["candidate_id"]
    with pytest.raises(ValueError, match=match):
        permission(store, cid, **bad)
    assert rg.get_candidate(store, cid)["state"] == "SUBMITTED"


def test_suitability_checklist_is_validated_and_confirm_capacity_overrides(store):
    cid = submit(store)["candidate_id"]
    permission(store, cid)
    with pytest.raises(ValueError, match="access"):
        rg.record_suitability(store, cid, {**CHECK, "access": "maybe"}, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    with pytest.raises(ValueError, match="capacity_estimate"):
        rg.record_suitability(store, cid, {**CHECK, "capacity_estimate": -1}, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    rg.record_suitability(store, cid, CHECK, actor="dispatcher", site_check=SITE_UNKNOWN, now_wall=NOW)
    with pytest.raises(ValueError, match="occupancy"):
        rg.confirm_candidate(store, cid, {"total_capacity": 40, "occupancy": 41}, actor="dispatcher", now_wall=NOW)
    c = rg.confirm_candidate(store, cid, {"total_capacity": 40, "occupancy": 5}, actor="dispatcher", now_wall=NOW)
    sh = rg.get_resource(store, c["shelter_id"], now_wall=NOW)
    assert (sh["total_capacity"], sh["occupancy"]) == (40, 5)


def test_open_submissions_per_volunteer_are_capped(store):
    for i in range(rg.MAX_OPEN_CANDIDATES_PER_SUBMITTER):
        submit(store, name=f"Hall {i}")
    with pytest.raises(ValueError, match="open"):
        submit(store, name="One too many")
    assert len(rg.list_candidates(store, actor="responder:RP1")) == rg.MAX_OPEN_CANDIDATES_PER_SUBMITTER
    assert rg.list_candidates(store, actor="responder:RP2") == []


# ------------------------------------------------------------------ modelled depth at the site
def test_site_check_is_unknown_without_a_published_forecast(store):
    s = rg.site_flood_check(store, NEAR_S01, ROADS, NO_CTX)
    assert s["status"] == "UNKNOWN" and "published" in s["reason"]
    assert rg.site_flood_check(store, NEAR_S01, None, NO_CTX)["status"] == "UNKNOWN"


def test_site_check_reports_the_nearest_road_peak_from_the_published_forecast(store):
    peak = [0.0] * len(T)
    peak[6] = 45.0                                                 # T+30
    publish(store, s01=peak, n34=[90.0] * len(T))
    s = rg.site_flood_check(store, NEAR_S01, ROADS, NO_CTX)
    assert s["status"] == "MODELLED" and s["seg_id"] == "s01" and s["road_name"] == "South Rd"
    assert s["max_depth_cm"] == 45.0 and s["peak_t_min"] == 30.0 and 15 < s["distance_m"] < 30
    assert s["forecast_version"] == 1 and s["data_mode"] == "SYNTHETIC TEST"
    assert "not a measurement" in s["note"]
    far = rg.site_flood_check(store, FAR, ROADS, NO_CTX)
    assert far["status"] == "UNKNOWN" and "road" in far["reason"]
    unlinked = rg.site_flood_check(store, NEAR_S01, ROADS, SegmentContext(frozenset({"s01"}), frozenset()))
    assert unlinked["status"] == "UNKNOWN" and "not linked" in unlinked["reason"]


def test_site_check_on_a_road_without_modelled_water_is_zero_not_unknown(store):
    publish(store, n34=[90.0] * len(T))                            # s01 absent: the model has it dry
    s = rg.site_flood_check(store, NEAR_S01, ROADS, NO_CTX)
    assert s["status"] == "MODELLED" and s["max_depth_cm"] == 0.0
    assert SEG_IDS  # the network is the synthetic test network
