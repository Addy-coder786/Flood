"""Alert lifecycle, deliveries and callbacks (DECISIONS D-44, docs/LIVE_OPERATIONS.md §6, acceptance criteria 9, 10, 12).
Written before floodnet/messaging/alerts.py existed.

Road ids are REAL pilot segments; every depth series is a SYNTHETIC TEST FIXTURE. Subscribers are SIMULATED with FAKE
numbers (+9100000xxxxx is not a valid Indian mobile number). Only the SimulatedProvider is used: no network."""
import re
from datetime import datetime, timedelta, timezone

import pytest

from floodnet.messaging import alerts as al
from floodnet.messaging import providers as pv
from floodnet.messaging import subscribers as subs
from floodnet.ops import incident as inc
from floodnet.ops.store import OpsStore

KHODADAD_A, KHODADAD_B = "22848363_0", "1235236198_0"      # REAL: Khodadad Circle (trunk)
T = [0.0, 15.0, 30.0, 45.0, 60.0, 90.0]
WATCH = {KHODADAD_A: [0, 10, 20, 22, 20, 10]}                                      # SYNTHETIC: max 22 cm -> watch
ADVISORY = {KHODADAD_A: [0, 10, 20, 35, 40, 20]}                                   # SYNTHETIC: max 40 cm -> advisory
ADVISORY_WIDER = {**ADVISORY, KHODADAD_B: [0, 5, 16, 18, 18, 10]}                  # SYNTHETIC: one more road
BASE = "http://127.0.0.1:5173"
T0 = datetime(2026, 10, 11, 10, 0, tzinfo=timezone.utc)
NUMBER_RE = re.compile(r"\+?\d{8,}")
GSM_BASIC = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§"
                "¿abcdefghijklmnopqrstuvwxyzäöñüà")

PEOPLE = {   # name -> (fake number, area, channels, verified)
    "direct": ("+910000000011", {"lonlat": [72.848752, 19.017141], "radius_m": 300}, ("sms",), True),
    "nearby": ("+910000000012", {"lonlat": [72.8509328, 19.0177508], "radius_m": 100}, ("sms", "whatsapp"), True),
    "far": ("+910000000013", {"lonlat": [72.841445, 19.031244], "radius_m": 300}, ("sms",), True),
    "optout": ("+910000000014", {"lonlat": [72.848752, 19.017141], "radius_m": 300}, ("sms",), True),
    "unverified": ("+910000000015", {"lonlat": [72.848752, 19.017141], "radius_m": 300}, ("sms",), False),
}


def publish(store, streets):
    return inc.publish_forecast(store, run_id="test", data_mode="SYNTHETIC TEST FIXTURE", series_t_min=T,
                                streets_cm=streets, label="SYNTHETIC TEST FIXTURE")


def add_people(store) -> dict:
    ids = {}
    for name, (num, area, chans, verified) in PEOPLE.items():
        view, _ = subs.create(store, {"number": num, "area": area, "channels": {c: {"method": "test"} for c in chans},
                                      "verified": verified, "simulated": True}, actor="test")
        ids[name] = view["id"]
    subs.opt_out(store, ids["optout"], method="test", actor="test")
    return ids


def approved(store, streets=WATCH, **kw):
    publish(store, streets)
    a = al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher", now=T0, **kw)
    al.submit_review(store, a["alert_id"], actor="dispatcher")
    return al.approve(store, a["alert_id"], actor="dispatcher", approver_name="Test Approver", now=T0)


def factory(sim):
    return lambda channel, to, simulated: (sim, "SIMULATED", "test")


@pytest.fixture
def store(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("FLOODNET_LIVE_MESSAGING", raising=False)
    from floodnet.api import state
    if KHODADAD_A not in {s.seg_id for s in state.get_pilot()["roads"].segments}:
        pytest.skip("REAL pilot road network not loaded")
    return OpsStore(tmp_path / "ops.sqlite3")


def deliveries(store, alert_id):
    return {(d["subscriber_id"], d["channel"], d["version"]): d for d in al.list_deliveries(store, alert_id)}


# ----------------------------------------------------------------------------- drafting and text
def test_draft_needs_a_published_forecast(store):
    with pytest.raises(al.NoForecast):
        al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher")


def test_draft_with_nothing_affected_is_refused(store):
    publish(store, {KHODADAD_A: [0, 1, 2, 3, 2, 1]})
    with pytest.raises(ValueError, match="no affected"):
        al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher")


def test_draft_texts_link_stop_banner_gsm_and_length(store):
    publish(store, ADVISORY)
    a = al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher", now=T0)
    assert a["state"] == "DRAFT" and len(a["public_id"]) >= 22
    v = a["versions"][-1]
    link = f"{BASE}/alerts/{a['public_id']}"
    for ch in ("sms", "whatsapp"):
        text = v["texts"][ch]
        assert link in text and "Reply STOP to unsubscribe" in text
        assert "student prototype, not an official warning" in text
        assert not NUMBER_RE.search(text.replace(link, "")), text
    assert len(v["texts"]["sms"]) <= 300 and set(v["texts"]["sms"]) <= GSM_BASIC
    assert "Flooding likely - avoid the area" in v["texts"]["sms"]
    assert v["level"] == "advisory" and v["zone"]["segments"]
    assert "translation" in al.TRANSLATION_NOTE.lower()


def test_review_is_required_before_approval_and_send(store):
    publish(store, WATCH)
    a = al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher", now=T0)
    with pytest.raises(al.StateError):
        al.approve(store, a["alert_id"], actor="dispatcher")
    with pytest.raises(al.StateError):
        al.send(store, a["alert_id"], actor="dispatcher")
    al.submit_review(store, a["alert_id"], actor="dispatcher")
    ok = al.approve(store, a["alert_id"], actor="dispatcher", approver_name="A. Person", now=T0)
    assert ok["state"] == "APPROVED" and ok["approved_by"] == "dispatcher" and ok["approver_name"] == "A. Person"
    assert ok["approved_at"] and ok["approved_version"] == 1


# ----------------------------------------------------------------------------- targeting and idempotency
def test_only_eligible_recipients_get_messages(store):
    ids = add_people(store)
    a = approved(store)
    sim = pv.SimulatedProvider(store=store)
    out = al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    assert out["state"] == "SENT"
    d = deliveries(store, a["alert_id"])
    assert set(d) == {(ids["direct"], "sms", 1), (ids["nearby"], "sms", 1), (ids["nearby"], "whatsapp", 1)}
    assert all(x["status"] == "ACCEPTED" and x["mode"] == "SIMULATED" for x in d.values())
    assert len(sim.outbox) == 3
    reasons = {t["subscriber_id"]: t for t in al.get_targets(store, a["alert_id"], 1)}
    assert set(reasons) == set(ids.values())
    assert "opted out" in reasons[ids["optout"]]["reason"] and "not verified" in reasons[ids["unverified"]]["reason"]
    assert "outside" in reasons[ids["far"]]["reason"]
    for x in d.values():
        assert not NUMBER_RE.search(str({k: v for k, v in x.items() if k != "status_history"}))


def test_double_send_creates_no_duplicate_deliveries(store):
    add_people(store)
    a = approved(store)
    sim = pv.SimulatedProvider(store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    again = al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    assert again["already_sent"] is True
    doc = store.get("alerts", a["alert_id"])                          # simulate a replayed/duplicated send event
    store.put("alerts", a["alert_id"], {**doc, "state": "APPROVED"}, actor="test", action="replay")
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    assert len(al.list_deliveries(store, a["alert_id"])) == 3 and len(sim.outbox) == 3


def test_provider_failures_are_recorded(store):
    ids = add_people(store)
    a = approved(store)
    keys = {f"{a['alert_id']}:v1:{ids['direct']}:sms": "REJECTED", f"{a['alert_id']}:v1:{ids['nearby']}:sms": "TIMEOUT"}
    sim = pv.SimulatedProvider(failures=keys, store=store)
    out = al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    d = deliveries(store, a["alert_id"])
    assert d[(ids["direct"], "sms", 1)]["status"] == "REJECTED" and d[(ids["direct"], "sms", 1)]["error"]
    assert d[(ids["nearby"], "sms", 1)]["status"] == "UNKNOWN"
    assert d[(ids["nearby"], "whatsapp", 1)]["status"] == "ACCEPTED"
    assert out["state"] == "PARTIAL"


def test_all_failed_marks_alert_failed(store):
    add_people(store)
    a = approved(store)
    sim = pv.SimulatedProvider(failures=lambda ch, to, key: "FAILED", store=store)
    out = al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    assert out["state"] == "FAILED"
    assert {d["status"] for d in al.list_deliveries(store, a["alert_id"])} == {"FAILED"}


def test_exception_in_provider_becomes_unknown_never_retried(store):
    add_people(store)
    a = approved(store)

    class Boom:
        name, mode = "boom", "SIMULATED"

        def __init__(self):
            self.calls = 0

        def send(self, *a, **k):
            self.calls += 1
            raise TimeoutError("simulated timeout")
    b = Boom()
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=lambda c, t, s: (b, "SIMULATED", "test"), now=T0)
    assert {d["status"] for d in al.list_deliveries(store, a["alert_id"])} == {"UNKNOWN"} and b.calls == 3


# ----------------------------------------------------------------------------- callbacks
def test_callbacks_forward_only_and_deduplicated(store):
    ids = add_people(store)
    a = approved(store)
    sim = pv.SimulatedProvider(store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    d = deliveries(store, a["alert_id"])[(ids["direct"], "sms", 1)]
    mid = d["provider_message_id"]
    r1 = al.apply_callback(store, provider="simulated", provider_message_id=mid, status="DELIVERED", raw_status="delivered")
    assert r1["applied"] and r1["status"] == "DELIVERED"
    late = al.apply_callback(store, provider="simulated", provider_message_id=mid, status="SENT", raw_status="sent")
    assert not late["applied"] and late["status"] == "DELIVERED"
    dup = al.apply_callback(store, provider="simulated", provider_message_id=mid, status="DELIVERED", raw_status="delivered")
    assert dup["duplicate"] is True
    unknown = al.apply_callback(store, provider="simulated", provider_message_id="SIM-nope", status="SENT", raw_status="sent")
    assert unknown["matched"] is False
    hist = [h["status"] for h in deliveries(store, a["alert_id"])[(ids["direct"], "sms", 1)]["status_history"]]
    assert hist[-1] == "DELIVERED" and "SENT" not in hist[hist.index("DELIVERED"):]


def test_simulated_callbacks_drive_deliveries(store):
    ids = add_people(store)
    a = approved(store)
    late_key = f"{a['alert_id']}:v1:{ids['direct']}:sms"
    sim = pv.SimulatedProvider(failures={late_key: "FAIL_AFTER_ACCEPT"}, store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    res = al.simulate_callbacks(store, a["alert_id"], sim)
    assert res["applied"] >= 3
    d = deliveries(store, a["alert_id"])
    assert d[(ids["direct"], "sms", 1)]["status"] == "FAILED"
    assert d[(ids["nearby"], "sms", 1)]["status"] == "DELIVERED"


# ----------------------------------------------------------------------------- material change and cooldown
def test_material_change_versions_and_cooldown(store):
    ids = add_people(store)
    a = approved(store, WATCH)
    sim = pv.SimulatedProvider(store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)

    same = al.refresh(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=5))
    assert same["changed"] is False and "no material change" in same["reason"]

    publish(store, ADVISORY)                                        # level rises: new version, cooldown bypassed
    up = al.refresh(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=10))
    assert up["changed"] and up["alert"]["state"] == "REVIEW_REQUIRED" and up["alert"]["current_version"] == 2
    assert "Flooding likely" in up["alert"]["versions"][-1]["change_summary"]
    al.approve(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=11))
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0 + timedelta(minutes=11))
    d = deliveries(store, a["alert_id"])
    assert d[(ids["direct"], "sms", 2)]["status"] == "ACCEPTED"

    publish(store, ADVISORY_WIDER)                                  # same level, more roads: new version, cooldown
    wider = al.refresh(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=20))
    assert wider["changed"] and wider["alert"]["current_version"] == 3
    al.approve(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=20))
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0 + timedelta(minutes=21))
    d = deliveries(store, a["alert_id"])
    assert d[(ids["direct"], "sms", 3)]["status"] == "SUPPRESSED" and "cooldown" in d[(ids["direct"], "sms", 3)]["reason"]


def test_opted_out_after_first_send_gets_nothing_more(store):
    ids = add_people(store)
    a = approved(store, WATCH)
    sim = pv.SimulatedProvider(store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    subs.opt_out_by_number(store, PEOPLE["direct"][0], method="sms_stop")
    publish(store, ADVISORY)
    al.refresh(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=40))
    al.approve(store, a["alert_id"], actor="dispatcher")
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0 + timedelta(minutes=40))
    assert (ids["direct"], "sms", 2) not in deliveries(store, a["alert_id"])
    al.resolve(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0 + timedelta(minutes=50))
    res = [d for d in al.list_deliveries(store, a["alert_id"]) if d["kind"] == "resolution"]
    mine = [d for d in res if d["subscriber_id"] == ids["direct"]]
    assert mine and all(d["status"] == "SUPPRESSED" for d in mine)
    assert all(o["to"] != "•••• 0011" for o in sim.outbox[3:])        # nothing sent to the opted-out number


def test_resolve_follows_up_only_previous_recipients(store):
    ids = add_people(store)
    a = approved(store)
    keys = {f"{a['alert_id']}:v1:{ids['direct']}:sms": "REJECTED"}
    sim = pv.SimulatedProvider(failures=keys, store=store)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0)
    out = al.resolve(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim), now=T0 + timedelta(minutes=5))
    assert out["state"] == "RESOLVED" and out["versions"][-1]["level"] == "ended"
    res = [d for d in al.list_deliveries(store, a["alert_id"]) if d["kind"] == "resolution"]
    assert {(d["subscriber_id"], d["channel"]) for d in res} == {(ids["nearby"], "sms"), (ids["nearby"], "whatsapp")}
    text = out["versions"][-1]["texts"]["sms"]
    assert "No longer active" in text and "Reply STOP to unsubscribe" in text and f"/alerts/{a['public_id']}" in text
    with pytest.raises(al.StateError):
        al.resolve(store, a["alert_id"], actor="dispatcher", provider_factory=factory(sim))


def test_cancel_only_before_publication(store):
    publish(store, WATCH)
    a = al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher", now=T0)
    assert al.cancel(store, a["alert_id"], actor="dispatcher")["state"] == "CANCELLED"
    b = approved(store)
    al.send(store, b["alert_id"], actor="dispatcher", provider_factory=factory(pv.SimulatedProvider()), now=T0)
    with pytest.raises(al.StateError):
        al.cancel(store, b["alert_id"], actor="dispatcher")


def test_inbound_stop_opts_out(store):
    ids = add_people(store)
    assert al.handle_inbound(store, "whatsapp:" + PEOPLE["nearby"][0], " stop ")["opted_out"] is True
    assert subs.get_public_view(store, ids["nearby"])["status"] == "OPTED_OUT"
    assert al.handle_inbound(store, PEOPLE["direct"][0], "hello")["opted_out"] is False
