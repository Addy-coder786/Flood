"""Regression tests for the independent QA review of the live-operations code (2026-10-11). Each test reproduces one
finding (H = high, M = medium, L = low) and was written before its fix."""
import math
import threading
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.access.events import ClosureBook, parse_events
from floodnet.ops import auth, clock, impact as im, incident as inc, registry as rg, trips as tr
from floodnet.ops.store import OpsStore
from tests.ops_net import G, LONLAT, NO_CTX, ROADS, forecast, mid
from tests.test_ops_trips import assignment

NOW = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def book(*events, now=1.0):
    return ClosureBook(parse_events(list(events)), now)


def closure(seg, t=1.0, eid="C1", **kw):
    return {"event_id": eid, "seg_id": seg, "kind": "closure", "received_t_min": t, **kw}


def assess(trip, now_t=1.0, f=None, closures=None, fix=None, **kw):
    return im.assess(trip, now_t=now_t, now_wall=NOW, roads=ROADS, G=G, forecast=f or forecast(), seg_ctx=NO_CTX,
                     closures=closures or book(now=now_t), fix=fix, **kw)


# ------------------------------------------------------------------ H1: a web page on the dispatcher's machine
def test_h1_loopback_dispatcher_refuses_foreign_origins_and_hosts(monkeypatch):
    monkeypatch.delenv("FLOODNET_DISPATCHER_TOKEN", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    ok = dict(client_host="127.0.0.1", host="127.0.0.1:8000")
    assert auth.dispatcher_allowed(None, **ok, origin="http://localhost:5173") is True
    assert auth.dispatcher_allowed(None, **ok, origin=None) is True
    assert auth.dispatcher_allowed(None, **ok, origin="https://evil.example") is False          # cross-site page
    assert auth.dispatcher_allowed(None, "127.0.0.1", host="rebind.evil.example", origin=None) is False   # DNS rebinding
    assert auth.dispatcher_allowed(None, "127.0.0.1", host="abc.ngrok.app", origin="https://abc.ngrok.app") is False


# ------------------------------------------------------------------ H2: a late vehicle must not skip the pickup
def test_h2_late_vehicle_before_loading_keeps_the_settlement_leg(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    t = tr.set_stage(store, t["trip_id"], "EN_ROUTE", actor=f"driver:{t['trip_id']}", now_t=1)
    # the schedule says the truck is loading at the settlement (T+4.2 .. T+14.2); the driver has not reported arriving
    a = assess(t, now_t=12.0, closures=book(closure("s12", t=11), now=12.0))
    assert a["position"]["leg_index"] == 0, a["position"]
    assert a["decision"] == "REQUIRED"
    assert a["candidate"]["legs"][0]["kind"] == "to_settlement"
    # a fresh GPS fix on the first road is snapped to the first leg, not reported as off the route
    fix = {"lon": mid(0, 1)[0], "lat": mid(0, 1)[1], "recorded_at": (NOW - timedelta(seconds=3)).isoformat()}
    b = assess(t, now_t=12.0, fix=fix)
    assert b["position"]["source"] == "GPS_ON_ROUTE" and b["position"]["leg_index"] == 0


def test_h2_loaded_vehicle_is_never_sent_back_to_the_settlement(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    t = tr.set_stage(store, t["trip_id"], "LOADED", actor=f"driver:{t['trip_id']}", now_t=3)
    a = assess(t, now_t=3.0)                         # schedule still says leg 0; people are on board
    assert a["position"]["leg_index"] == 1


# ------------------------------------------------------------------ M1: rejected or open proposals while moving
def test_m1_rejected_required_reroute_is_not_reproposed_as_the_vehicle_moves(store):
    """The closure is on the shelter leg (e25). At T+0.5 the truck is before node 1; at T+2.5 it is on s12, so the
    remaining route (and the candidate's road list) is different, but the finding the dispatcher rejected is the same."""
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    shut = lambda now: book(closure("e25", t=0.5), now=now)
    p = im.apply(store, tid, assess(t, now_t=0.5, closures=shut(0.5)), trigger={"kind": "x"}, now_t=0.5)["proposal"]
    assert p["kind"] == "REQUIRED"
    im.decide(store, tid, p["proposal_id"], "REJECT", actor="dispatcher", now_t=0.6, reason="water gone, team can see it")
    later = assess(store.get("trips", tid), now_t=2.5, closures=shut(2.5))
    assert later["decision"] == "REQUIRED" and later["candidate"]["signature"] != p["signature"]
    assert im.apply(store, tid, later, trigger={"kind": "gps"}, now_t=2.5)["action"] == "suppressed_rejected"
    # a NEW blocking finding (worse situation) is proposed again
    worse = assess(store.get("trips", tid), now_t=2.6,
                   closures=book(closure("e25", t=0.5), closure("n45", eid="C2", t=2.6), now=2.6))
    assert im.apply(store, tid, worse, trigger={"kind": "road_event"}, now_t=2.6)["action"] in ("created", "escalated")


def test_m1_open_proposal_is_not_superseded_just_because_the_vehicle_moved(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    shut = lambda now: book(closure("e25", t=0.5), now=now)
    im.apply(store, tid, assess(t, now_t=0.5, closures=shut(0.5)), trigger={"kind": "x"}, now_t=0.5)
    again = assess(store.get("trips", tid), now_t=2.5, closures=shut(2.5))
    assert im.apply(store, tid, again, trigger={"kind": "gps"}, now_t=2.5)["action"] == "deduplicated"
    assert [p["status"] for p in store.get("trips", tid)["proposals"]] == ["OPEN"]


# ------------------------------------------------------------------ M2: a failed BEGIN must not leave the store locked
def test_m2_store_recovers_after_a_failed_begin(store, monkeypatch):
    real = store._db

    class Flaky:
        def __init__(self):
            self.fail = True

        def execute(self, sql, *a):
            if sql.startswith("BEGIN") and self.fail:
                self.fail = False
                import sqlite3
                raise sqlite3.OperationalError("database is locked")
            return real.execute(sql, *a)

        def __getattr__(self, k):
            return getattr(real, k)

    store._db = Flaky()
    with pytest.raises(Exception):
        store.put("x", "1", {"a": 1}, actor="t", action="t")
    store._db = real
    done = []
    th = threading.Thread(target=lambda: done.append(store.put("x", "2", {"a": 2}, actor="t", action="t")), daemon=True)
    th.start(); th.join(timeout=5)
    assert done, "the store stayed locked after one failed BEGIN"


# ------------------------------------------------------------------ M3: stale assessments, lost updates
def test_m3_assessment_is_not_applied_to_a_finished_or_rerouted_trip(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    a = assess(t, closures=book(closure("s12")))
    tr.set_stage(store, tid, "COMPLETED", actor="dispatcher", now_t=1.2)
    assert im.apply(store, tid, a, trigger={"kind": "x"}, now_t=1.3)["action"] == "stale_assessment"
    assert store.get("trips", tid)["proposals"] == []


def test_m3_rotating_the_driver_link_keeps_the_last_position(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    tr.record_position(store, tid, {"lon": 72.841, "lat": 19.010}, actor=f"driver:{tid}", now_wall=NOW)
    tr.rotate_driver_token(store, tid)
    assert store.get("trips", tid)["last_position"]["lon"] == 72.841


# ------------------------------------------------------------------ M4: finished trips and duplicate reports
def test_m4_driver_reports_after_abort_are_refused_and_client_ref_deduplicates(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    a = tr.report(store, tid, "delay", actor=f"driver:{tid}", now_t=1, known_segments={"s12"}, client_ref="r1")
    b = tr.report(store, tid, "delay", actor=f"driver:{tid}", now_t=1, known_segments={"s12"}, client_ref="r1")
    assert a["report"]["report_id"] == b["report"]["report_id"]
    tr.set_stage(store, tid, "ABORTED", actor="dispatcher", now_t=2, reason="test")
    with pytest.raises(ValueError, match="finished"):
        tr.report(store, tid, "blockage", actor=f"driver:{tid}", now_t=3, seg_id="s12", known_segments={"s12"})
    assert inc.list_road_events(store) == []


# ------------------------------------------------------------------ M5: field-event ids are reserved
def test_m5_dispatcher_cannot_sync_an_event_in_the_field_id_range(store):
    with pytest.raises(ValueError, match="reserved"):
        inc.sync_dispatcher_events(store, [closure("n34", eid="F2", source_tier="AUTHORITY")])


# ------------------------------------------------------------------ L: input validation (no 500s), token hashes, escalation
@pytest.mark.parametrize("value", ["1e20", 1e20, "not a time"])
def test_l_absurd_gps_times_are_rejected_cleanly(store, value):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    with pytest.raises(ValueError):
        tr.record_position(store, t["trip_id"], {"lon": 72.84, "lat": 19.01, "recorded_at": value},
                           actor=f"driver:{t['trip_id']}", now_wall=NOW)


def test_l_bad_values_are_value_errors(store):
    rg.seed_from_fixtures(store)
    with pytest.raises(ValueError):
        rg.submit_update(store, actor="dispatcher", role="dispatcher", resource_id="V1", fields={"lonlat": [None, 1]},
                         observed_at=None, now_wall=NOW)
    with pytest.raises(ValueError):
        parse_events([{"seg_id": "s", "kind": "closure", "received_t_min": 0, "vehicle_types": 5}])
    with pytest.raises(ValueError):
        clock.set_clock(store, "REPLAY", math.inf)


def test_l_escalation_closes_when_a_route_becomes_feasible_again(store):
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]
    blocked = book(closure("s12"), closure("e25", eid="C2"))
    assert im.apply(store, tid, assess(t, closures=blocked), trigger={"kind": "x"}, now_t=1)["action"] == "escalated"
    im.apply(store, tid, assess(store.get("trips", tid), now_t=1.5), trigger={"kind": "x"}, now_t=1.5)
    esc = store.get("trips", tid)["escalation"]
    assert esc["open"] is False and "feasible" in esc["closed_reason"]


# ------------------------------------------------------------------ messaging findings (pilot road network needed)
from floodnet.messaging import alerts as al, providers as pv, targeting as tg   # noqa: E402
from tests.test_messaging_targeting import FORECAST, KHODADAD_A, S1, roads      # noqa: E402,F401


def test_m6_a_truck_restriction_is_not_a_public_closure(roads):
    dry = FORECAST.__class__((0.0, 30.0, 60.0), {}, label="SYNTHETIC TEST FIXTURE (dry)")
    r = book(closure(KHODADAD_A, t=0, vehicle_types=["truck"]), now=5)
    zone = tg.impact_zone(dry, r, 5, 60, roads=roads)
    assert zone["level"] is None
    assert not [s for s in zone["segments"] if s["basis"] == "CONFIRMED_CLOSURE"]


def test_m7_simulated_outbox_keeps_no_digits_of_a_real_number():
    p = pv.SimulatedProvider()
    p.send("sms", "+919876543210", "test body", "k-1")
    assert not any(ch.isdigit() for ch in p.outbox[0]["to"]), p.outbox[0]["to"]


def test_m7_targeting_reasons_do_not_describe_a_persons_location(roads):
    zone = tg.impact_zone(FORECAST, book(now=30), 30, 60, roads=roads)
    sub = {"id": "SUB1", "channels": {"sms": {"granted_at": "x"}}, "verified": True, "status": "ACTIVE",
           "opted_out_at": None, "simulated": True, "language": "en",
           "area": {"kind": "point", "lonlat": S1, "radius_m": 300}}
    d = tg.decide(zone, [sub], roads=roads)[0]
    assert d["eligible"]
    import re
    assert not re.search(r"\d+ m\b", d["reason"]), d["reason"]


def test_m8_approval_is_for_the_version_the_reviewer_saw(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    s = OpsStore(tmp_path / "ops.sqlite3")
    inc.publish_forecast(s, run_id="t", data_mode="SYNTHETIC TEST FIXTURE", series_t_min=list(FORECAST.t_min),
                         streets_cm={k: list(v) for k, v in FORECAST.streets_cm.items()})
    a = al.create_draft(s, window_min=60, public_base_url="http://localhost:5173/static", actor="dispatcher")
    al.submit_review(s, a["alert_id"], actor="dispatcher")
    with pytest.raises(al.StateError, match="version"):
        al.approve(s, a["alert_id"], actor="dispatcher", approver_name="R", version=a["current_version"] + 1)
    ok = al.approve(s, a["alert_id"], actor="dispatcher", approver_name="R", version=a["current_version"])
    assert ok["state"] == "APPROVED"


@pytest.fixture
def ops_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from floodnet.api.main import app
    from floodnet.ops.store import reset_store_for_tests
    monkeypatch.setenv("FLOODNET_OPS_DB", str(tmp_path / "ops.sqlite3"))
    monkeypatch.setenv("FLOODNET_DISPATCHER_TOKEN", "qa-token")
    monkeypatch.delenv("FLOODNET_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("VERCEL", raising=False)
    reset_store_for_tests()
    yield TestClient(app, headers={"X-FloodNet-Token": "qa-token"})
    reset_store_for_tests()


@pytest.mark.parametrize("v", ["²", "9" * 5000, "-1"])
def test_l_odd_public_versions_are_404_not_500(ops_client, v):
    assert ops_client.get(f"/api/public/alerts/abc?v={v}").status_code == 404


def test_l_audit_never_returns_token_hashes(ops_client, monkeypatch):
    from floodnet.api import ops_api
    from tests.ops_net import SEG_IDS
    from tests.test_ops_trips import assignment as make
    monkeypatch.setattr(ops_api, "_ctx", lambda: (ROADS, G, NO_CTX, set(SEG_IDS)))
    ops_client.post("/api/ops/trips", json={"assignment": make()})
    ops_client.post("/api/ops/responders", json={"name": "x", "role": "shelter_staff", "scope": ["SH1"]})
    body = ops_client.get("/api/ops/audit").text
    assert "token_hash" not in body


def test_l_approve_api_requires_the_version(ops_client):
    inc.publish_forecast(__import__("floodnet.ops.store", fromlist=["get_store"]).get_store(), run_id="t",
                         data_mode="SYNTHETIC TEST FIXTURE", series_t_min=list(FORECAST.t_min),
                         streets_cm={k: list(v) for k, v in FORECAST.streets_cm.items()})
    a = ops_client.post("/api/ops/alerts/draft", json={"window_min": 60}).json()
    ops_client.post(f"/api/ops/alerts/{a['alert_id']}/submit-review")
    assert ops_client.post(f"/api/ops/alerts/{a['alert_id']}/approve", json={"approver_name": "R"}).status_code == 422


def test_l_draft_links_only_follow_a_local_dashboard_origin(ops_client):
    from floodnet.ops.store import get_store
    inc.publish_forecast(get_store(), run_id="t", data_mode="SYNTHETIC TEST FIXTURE", series_t_min=list(FORECAST.t_min),
                         streets_cm={k: list(v) for k, v in FORECAST.streets_cm.items()})
    ok = ops_client.post("/api/ops/alerts/draft", headers={"Origin": "http://localhost:5173"},
                         json={"window_min": 60, "public_base": "http://localhost:5173/static"})
    assert ok.status_code == 201, ok.text
    r = ops_client.post("/api/ops/alerts/draft", headers={"Origin": "https://evil.example"},
                        json={"window_min": 60, "public_base": "https://evil.example/static"})
    assert r.status_code == 422


def test_e2e_finding_deciding_on_a_replaced_proposal_names_the_newer_one(store):
    """Found in the browser walkthrough: a new road report replaced P1 while the dispatcher looked at it."""
    t, _ = tr.create_trip(store, assignment(), vehicle_type="truck", now_t=0)
    tid = t["trip_id"]

    def required(issues, sig):
        return {"decision": "REQUIRED", "reason": "blocked", "issues": [{"seg_id": x} for x in issues],
                "candidate": {"legs": [], "signature": sig}, "comparison": None, "position": {"source": "PLANNED_ESTIMATE"}}
    assert im.apply(store, tid, required(["e25"], ["a"]), trigger={"kind": "x"}, now_t=0.5)["action"] == "created"
    assert im.apply(store, tid, required(["e25", "s12"], ["b"]), trigger={"kind": "road_event"}, now_t=0.6)["action"] == "created"
    with pytest.raises(ValueError, match="replaced by P2"):
        im.decide(store, tid, "P1", "ACCEPT", actor="dispatcher", now_t=0.7)


def test_e2e_finding_locality_text_stays_readable():
    from floodnet.messaging import public as pubm
    names = [f"Area {i}" for i in range(13)]
    text = pubm.locality_phrase(names)
    assert text == "Near Area 0, Area 1 and Area 2 (+10 more areas)."
    assert pubm.locality_phrase(["A"]) == "Near A." and pubm.locality_phrase([]) == ""


@pytest.mark.parametrize("mode,demo", [("SYNTHETIC rainfall: heavy design storm on REAL pilot data", True),
                                       ("IMD LIVE rainfall on REAL pilot data", False)])
def test_e2e_finding_demonstration_storms_are_flagged_on_the_public_page(mode, demo):
    from floodnet.messaging import public as pubm
    assert bool(pubm.demonstration_note(mode)) is demo
