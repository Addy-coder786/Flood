"""The shared incident picture in the ops store (D-38, D-41): the forecast the dispatcher published and the road-event
log merged from the dispatcher's browser and from drivers. Written before floodnet/ops/incident.py existed."""
import pytest

from floodnet.ops import incident as inc
from floodnet.ops.store import OpsStore


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def test_publish_forecast_versions_and_rebuilds_forecast(store):
    assert inc.get_forecast(store) is None
    d1 = inc.publish_forecast(store, run_id="r1", data_mode="SCENARIO", series_t_min=[0, 10, 20],
                              streets_cm={"a": [0, 5, 40]})
    assert d1["forecast_version"] == 1
    d2 = inc.publish_forecast(store, run_id="r2", data_mode="SCENARIO", series_t_min=[0, 10], streets_cm={"a": [1, 2]})
    assert d2["forecast_version"] == 2
    f, meta = inc.get_forecast(store)
    assert meta["run_id"] == "r2" and f.horizon_t_min == 10


def test_publish_forecast_rejects_ragged_series(store):
    with pytest.raises(ValueError, match="frames"):
        inc.publish_forecast(store, run_id="r", data_mode="X", series_t_min=[0, 10], streets_cm={"a": [1]})


def test_dispatcher_events_merge_by_id_and_are_immutable(store):
    ev = {"event_id": "C1", "seg_id": "s1", "kind": "closure", "received_t_min": 5, "source_tier": "OPERATOR"}
    out = inc.sync_dispatcher_events(store, [ev])
    assert out["added"] == ["C1"]
    again = inc.sync_dispatcher_events(store, [ev])
    assert again["added"] == [] and again["conflicts"] == []
    changed = inc.sync_dispatcher_events(store, [{**ev, "received_t_min": 9}])
    assert changed["conflicts"] == ["C1"]
    assert inc.list_road_events(store)[0]["received_t_min"] == 5        # the stored record wins


def test_server_origin_events_get_their_own_ids_and_origin(store):
    e = inc.add_field_road_event(store, {"seg_id": "s1", "kind": "closure", "received_t_min": 12,
                                         "event_id": "C1", "origin": "dispatcher"}, origin="driver:TR1", tier="FIELD_TEAM")
    assert e["event_id"].startswith("F") and e["event_id"] != "C1"      # client ids and origin are not trusted
    assert e["origin"] == "driver:TR1" and e["source_tier"] == "FIELD_TEAM"
    ids = [x["event_id"] for x in inc.list_road_events(store)]
    assert ids == [e["event_id"]]


def test_duplicate_field_report_with_same_client_ref_is_stored_once(store):
    body = {"seg_id": "s1", "kind": "closure", "received_t_min": 12, "client_ref": "abc"}
    a = inc.add_field_road_event(store, body, origin="driver:TR1", tier="FIELD_TEAM")
    b = inc.add_field_road_event(store, body, origin="driver:TR1", tier="FIELD_TEAM")
    assert a["event_id"] == b["event_id"] and len(inc.list_road_events(store)) == 1


def test_closures_use_only_events_known_at_now(store):
    inc.sync_dispatcher_events(store, [
        {"event_id": "C1", "seg_id": "s1", "kind": "closure", "received_t_min": 5},
        {"event_id": "R2", "seg_id": "s1", "kind": "reopen", "received_t_min": 30}])
    assert inc.closures(store, 10).closure_at("s1", 10).event_id == "C1"
    assert inc.closures(store, 31).closure_at("s1", 31) is None
