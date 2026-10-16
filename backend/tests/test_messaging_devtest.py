"""Designated development test recipients and the "Send test SMS" workflow (2026-10-11).
Written before floodnet/messaging/devtest.py existed.

Recipients come ONLY from server configuration (FLOODNET_DEV_TEST_RECIPIENTS). Every number here is a DUMMY made of
zeros ("+9160000000NN"); every credential is FAKE. Providers are the SimulatedProvider or the Twilio adapter with an
injected fake HTTP function: no test makes a network call or sends a real message."""
import hashlib
import json
from datetime import timedelta

import pytest

from floodnet.messaging import alerts as al
from floodnet.messaging import devtest as dt
from floodnet.messaging import providers as pv
from floodnet.messaging import subscribers as subs
from floodnet.ops.store import OpsStore

DEV_1, DEV_2 = "+916000000001", "+916000000002"
ENV_VALUE = "+91 60000 00001:Owner, 06000000002 , not-a-number:Bad, +91 50000 00003"
def leaks(text) -> bool:
    """A dummy number (full or national part) or a +91 prefix in the text. A generic '8+ digits' check would
    flag hex hashes and provider message ids by chance."""
    return "+91" in str(text) or "600000000" in str(text)
LIVE_ENV = ("FLOODNET_LIVE_MESSAGING", "FLOODNET_MESSAGING_ALLOWLIST", "FLOODNET_PUBLIC_BASE_URL", "TWILIO_ACCOUNT_SID",
            "TWILIO_AUTH_TOKEN", "TWILIO_API_KEY_SID", "TWILIO_API_KEY_SECRET", "TWILIO_SMS_FROM", "TWILIO_WHATSAPP_FROM",
            "MSG91_AUTHKEY", "MSG91_TEMPLATE_ID", "FLOODNET_SMS_PROVIDER", "VERCEL", "FLOODNET_ENV",
            "FLOODNET_DEV_TEST_RECIPIENTS", "FLOODNET_DEV_TEST_CHANNELS")


class FakeHTTP:
    def __init__(self, response):
        self.response, self.calls = response, []

    def __call__(self, url, **kw):
        self.calls.append({"url": url, **kw})
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


@pytest.fixture(autouse=True)
def env(monkeypatch):
    from cryptography.fernet import Fernet
    for n in LIVE_ENV:
        monkeypatch.delenv(n, raising=False)
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("FLOODNET_DEV_TEST_RECIPIENTS", ENV_VALUE)

    def no_network(*a, **k):
        raise AssertionError("a test tried to reach a real provider")
    monkeypatch.setattr(pv, "_httpx_post", no_network)


@pytest.fixture
def store(tmp_path):
    return OpsStore(tmp_path / "ops.sqlite3")


def sim_factory(store, failures=None):
    sim = pv.SimulatedProvider(failures=failures, store=store)
    return lambda ch, to, simulated: pv.choose_provider(ch, to, simulated=simulated, simulated_provider=sim,
                                                        purpose="test")


def seeded(store, **kw):
    out = dt.seed(store, actor="dispatcher", locality_id="dadar-west", **kw)
    return {subs.reveal_number(store, v["id"]): v for v in out["seeded"] + out["existing"]}


def all_text(store) -> str:
    docs = store._db.execute("SELECT body FROM docs").fetchall()
    audit = store._db.execute("SELECT body FROM audit").fetchall()
    return json.dumps([r[0] for r in docs] + [r[0] for r in audit])


def key_hash():
    return hashlib.sha256(dt.TEST_TEXT.encode("utf-8")).hexdigest()


# ----------------------------------------------------------------------------- configuration
def test_status_reports_counts_never_numbers(monkeypatch):
    st = dt.status()
    assert st["configured"] is True and st["valid"] == 2 and st["invalid"] == 2
    assert st["channels"] == ["sms"] and st["production"] is False and st["seeding_allowed"] is True
    assert not leaks(json.dumps(st))
    monkeypatch.delenv("FLOODNET_DEV_TEST_RECIPIENTS")
    st = dt.status()
    assert st["configured"] is False and st["valid"] == 0


def test_channels_come_from_configuration(monkeypatch):
    monkeypatch.setenv("FLOODNET_DEV_TEST_CHANNELS", "sms, whatsapp, email")
    assert dt.status()["channels"] == ["sms", "whatsapp"]


@pytest.mark.parametrize("var,value", [("VERCEL", "1"), ("FLOODNET_ENV", "production"), ("FLOODNET_ENV", "Prod")])
def test_seeding_refused_in_production(store, monkeypatch, var, value):
    monkeypatch.setenv(var, value)
    with pytest.raises(dt.NotAllowed, match="production"):
        dt.seed(store, actor="dispatcher", locality_id="dadar-west")
    assert subs.list_subscribers(store) == []
    assert dt.status()["seeding_allowed"] is False
    assert dt.dev_hmacs() == set()                       # and never allow-listed there


def test_seed_creates_encrypted_masked_dev_subscribers_idempotently(store):
    out = dt.seed(store, actor="dispatcher", locality_id="dadar-west")
    assert len(out["seeded"]) == 2 and out["invalid"] == 2 and out["existing"] == []
    by_num = {subs.reveal_number(store, v["id"]): v for v in out["seeded"]}
    assert set(by_num) == {DEV_1, DEV_2}
    owner = by_num[DEV_1]
    assert owner["dev_test"] is True and owner["label"] == "Owner" and owner["number_masked"] == "•••• 0001"
    assert owner["simulated"] is False and owner["verified"] is True and owner["status"] == "ACTIVE"
    assert owner["channels"]["sms"]["consent"]["method"] == dt.CONSENT_METHOD
    assert owner["area"]["locality_id"] == "dadar-west"
    assert by_num[DEV_2]["label"] is None
    assert not leaks(json.dumps(out))
    assert "600000000" not in all_text(store) and "Owner" not in all_text(store)   # number and label only in the vault
    again = dt.seed(store, actor="dispatcher", locality_id="dadar-west")
    assert again["seeded"] == [] and len(again["existing"]) == 2
    assert len(subs.list_subscribers(store)) == 2


def test_seed_marks_an_existing_active_subscriber_and_leaves_an_opted_out_one(store):
    a, _ = subs.create(store, {"number": DEV_1, "channels": {"sms": {"method": "web_form"}},
                               "area": {"locality_id": "naigaon"}}, actor="dispatcher")
    b, _ = subs.create(store, {"number": DEV_2, "channels": {"sms": {"method": "web_form"}},
                               "area": {"locality_id": "naigaon"}}, actor="dispatcher")
    subs.opt_out(store, b["id"], method="inbound_stop", actor="subscriber:inbound")
    out = dt.seed(store, actor="dispatcher", locality_id="dadar-west")
    assert out["seeded"] == []
    ex = {v["id"]: v for v in out["existing"]}
    assert ex[a["id"]]["dev_test"] is True and ex[a["id"]]["area"]["locality_id"] == "naigaon"
    assert ex[b["id"]]["status"] == "OPTED_OUT" and ex[b["id"]]["dev_test"] is False
    assert any("opted out" in s["reason"] for s in out["skipped"])


def test_seed_needs_a_known_locality(store):
    with pytest.raises(ValueError, match="locality"):
        dt.seed(store, actor="dispatcher", locality_id="atlantis")


# ----------------------------------------------------------------------------- the live gate
def _live_env(monkeypatch):
    monkeypatch.setenv("FLOODNET_LIVE_MESSAGING", "1")                 # monkeypatched for this test process only
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+15550000000")
    monkeypatch.setenv("FLOODNET_PUBLIC_BASE_URL", "https://floodnet.example.test")


def test_dev_recipients_are_allow_listed_for_test_sends_only(monkeypatch):
    _live_env(monkeypatch)
    ok, why = pv.live_sending_allowed("sms", DEV_1, purpose="test")
    assert ok and "development test recipient" in why and DEV_1 not in why
    ok, why = pv.live_sending_allowed("sms", DEV_1)                     # alerts: the ordinary allow-list only
    assert not ok and "allow-list" in why
    ok, _ = pv.live_sending_allowed("sms", "+916000000009", purpose="test")
    assert not ok
    monkeypatch.setenv("FLOODNET_ENV", "production")
    ok, why = pv.live_sending_allowed("sms", DEV_1, purpose="test")
    assert not ok


def test_live_gate_still_needs_the_flag_for_dev_recipients(monkeypatch):
    ok, why = pv.live_sending_allowed("sms", DEV_1, purpose="test")
    assert not ok and "FLOODNET_LIVE_MESSAGING" in why


# ----------------------------------------------------------------------------- preview
def test_sms_info_counts_gsm_and_unicode_segments():
    assert dt.sms_info("a" * 160) == {"characters": 160, "encoding": "GSM-7", "units": 160, "segments": 1,
                                      "per_segment": 160}
    two = dt.sms_info("a" * 161)
    assert two["segments"] == 2 and two["per_segment"] == 153               # concatenation header: 153 per part
    assert dt.sms_info("a" * 306)["segments"] == 2 and dt.sms_info("a" * 307)["segments"] == 3
    ext = dt.sms_info("{" * 80)                                              # extension characters take 2 septets
    assert ext["characters"] == 80 and ext["units"] == 160 and ext["segments"] == 1
    assert dt.sms_info("{" * 81)["segments"] == 2
    u = dt.sms_info("बाढ़" * 20)                                              # Devanagari: UCS-2, 70 / 67 per part
    assert u["encoding"] == "UCS-2" and u["units"] == 80 and u["per_segment"] == 67 and u["segments"] == 2
    assert dt.sms_info("बाढ़" * 17)["segments"] == 1
    assert dt.sms_info("")["segments"] == 0


def test_preview_is_the_exact_text_with_mode_and_no_number(store):
    dev = seeded(store)[DEV_1]
    p = dt.preview(store, dev["id"], "sms", sim_factory(store))
    assert p["text"] == dt.TEST_TEXT and p["text_sha256"] == key_hash()
    assert p["segments"] == 1 and p["encoding"] == "GSM-7" and p["characters"] == len(dt.TEST_TEXT)
    assert p["mode"] == "SIMULATED" and "FLOODNET_LIVE_MESSAGING" in p["mode_reason"]
    assert p["number_masked"] == "•••• 0001" and p["recipient_label"] == "Owner"
    assert not leaks(json.dumps(p))
    # the owner's wording (2026-10-11): still explicitly a test and explicitly not a warning
    assert "test message" in dt.TEST_TEXT.lower() and "not an emergency alert" in dt.TEST_TEXT.lower()
    assert al.gsm_safe(dt.TEST_TEXT) == dt.TEST_TEXT


def test_preview_and_send_refused_for_ordinary_subscribers(store):
    plain, _ = subs.create(store, {"number": "+916000000005", "channels": {"sms": {"method": "web_form"}},
                                   "area": {"locality_id": "naigaon"}, "verified": True}, actor="dispatcher")
    with pytest.raises(dt.NotAllowed, match="development test recipient"):
        dt.preview(store, plain["id"], "sms", sim_factory(store))
    with pytest.raises(dt.NotAllowed):
        dt.send(store, plain["id"], channel="sms", idempotency_key="k" * 16, text_sha256=key_hash(),
                actor="dispatcher", factory=sim_factory(store))
    with pytest.raises(KeyError):
        dt.preview(store, "SUB999", "sms", sim_factory(store))


def test_removed_from_configuration_means_no_more_test_sends(store, monkeypatch):
    dev = seeded(store)[DEV_1]
    monkeypatch.setenv("FLOODNET_DEV_TEST_RECIPIENTS", "06000000002")
    with pytest.raises(dt.NotAllowed):
        dt.preview(store, dev["id"], "sms", sim_factory(store))


def test_channel_without_consent_is_refused(store):
    dev = seeded(store)[DEV_1]
    with pytest.raises(ValueError, match="consent"):
        dt.preview(store, dev["id"], "whatsapp", sim_factory(store))


# ----------------------------------------------------------------------------- send
def test_send_is_simulated_labelled_audited_and_idempotent(store):
    dev = seeded(store)[DEV_1]
    f = sim_factory(store)
    first = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0001-abcdef", text_sha256=key_hash(),
                    actor="dispatcher", factory=f)
    assert first["duplicate"] is False and first["status"] == "ACCEPTED" and first["mode"] == "SIMULATED"
    assert first["label"] == "SIMULATED" and first["kind"] == "test" and first["provider"] == "simulated"
    assert first["status_meaning"].startswith("Provider accepted")
    again = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0001-abcdef", text_sha256=key_hash(),
                    actor="dispatcher", factory=f)                        # the double click
    assert again["duplicate"] is True and again["delivery_id"] == first["delivery_id"]
    outbox = store.list("sim_outbox")
    assert len(outbox) == 1 and outbox[0]["body"] == dt.TEST_TEXT and outbox[0]["to"] == "(real number withheld)"
    sends = dt.list_sends(store)
    assert len(sends) == 1 and sends[0]["number_masked"] == "•••• 0001"
    assert not leaks(json.dumps(sends))
    audit = store.audit(al.DELIVERIES, first["delivery_id"])
    assert audit[0]["actor"] == "dispatcher" and audit[0]["action"] == "test_send_requested"
    assert "600000000" not in all_text(store)


def test_same_key_for_another_recipient_is_a_conflict(store):
    devs = seeded(store)
    dt.send(store, devs[DEV_1]["id"], channel="sms", idempotency_key="click-0002-abcdef", text_sha256=key_hash(),
            actor="dispatcher", factory=sim_factory(store))
    with pytest.raises(dt.Conflict):
        dt.send(store, devs[DEV_2]["id"], channel="sms", idempotency_key="click-0002-abcdef",
                text_sha256=key_hash(), actor="dispatcher", factory=sim_factory(store))


def test_stale_preview_and_bad_keys_are_refused(store):
    dev = seeded(store)[DEV_1]
    with pytest.raises(dt.Conflict, match="preview"):
        dt.send(store, dev["id"], channel="sms", idempotency_key="click-0003-abcdef", text_sha256="0" * 64,
                actor="dispatcher", factory=sim_factory(store))
    for bad in ("", "short", "has space in it!", "x" * 81):
        with pytest.raises(ValueError, match="idempotency"):
            dt.send(store, dev["id"], channel="sms", idempotency_key=bad, text_sha256=key_hash(),
                    actor="dispatcher", factory=sim_factory(store))
    assert store.list("sim_outbox") == []


def test_opted_out_dev_recipient_gets_nothing(store):
    dev = seeded(store)[DEV_1]
    subs.opt_out(store, dev["id"], method="inbound_stop", actor="subscriber:inbound")
    with pytest.raises(dt.NotAllowed, match="opted out"):
        dt.send(store, dev["id"], channel="sms", idempotency_key="click-0004-abcdef", text_sha256=key_hash(),
                actor="dispatcher", factory=sim_factory(store))


def test_live_path_uses_the_provider_once_with_a_fake_http(store, monkeypatch):
    """With every live condition met (monkeypatched, fake credentials) the Twilio adapter is used, through a FAKE
    HTTP function: nothing leaves this process."""
    _live_env(monkeypatch)
    dev = seeded(store)[DEV_1]
    http = FakeHTTP((201, {"sid": "SM" + "a" * 32, "status": "queued"}))
    factory = lambda ch, to, simulated: pv.choose_provider(ch, to, simulated=simulated, http_post=http,  # noqa: E731
                                                           purpose="test")
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0005-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=factory, expected_mode="LIVE")
    assert out["mode"] == "LIVE" and out["label"] == "LIVE" and out["status"] == "ACCEPTED"
    assert len(http.calls) == 1 and http.calls[0]["data"]["Body"] == dt.TEST_TEXT
    dt.send(store, dev["id"], channel="sms", idempotency_key="click-0005-abcdef", text_sha256=key_hash(),
            actor="dispatcher", factory=factory, expected_mode="LIVE")
    assert len(http.calls) == 1                                          # the double click sent nothing more
    assert not leaks(json.dumps(out))
    # the provider's callbacks then move the record forward: SENT, DELIVERED (and never back)
    for raw in ("sent", "delivered", "sent"):
        al.apply_callback(store, provider="twilio", provider_message_id="SM" + "a" * 32,
                          status=pv.map_twilio_status(raw), raw_status=raw)
    got = dt.list_sends(store)[0]
    assert got["status"] == "DELIVERED" and got["status_meaning"].startswith("Delivered")


# ----------------------------------------------------------------------------- retry
def test_retry_only_after_a_failure_that_never_reached_the_provider(store):
    dev = seeded(store)[DEV_1]
    outcomes = {"TEST:click-0006-abcdef": "FAILED"}
    f = sim_factory(store, failures=lambda ch, to, key: outcomes.get(key))
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0006-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    assert out["status"] == "FAILED" and out["retryable"] is True and out["provider_message_id"] is None
    again = dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)
    assert again["status"] == "ACCEPTED" and len(again["attempts"]) == 2 and again["retryable"] is False
    assert [a["idempotency_key"] for a in again["attempts"]] == ["TEST:click-0006-abcdef", "TEST:click-0006-abcdef:a2"]
    with pytest.raises(dt.Conflict, match="retry"):
        dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)
    assert [r["action"] for r in store.audit(al.DELIVERIES, out["delivery_id"])].count("test_send_retry") == 1


@pytest.mark.parametrize("outcome,status", [("TIMEOUT", "UNKNOWN"), ("REJECTED", "REJECTED")])
def test_unknown_and_rejected_are_never_retried(store, outcome, status):
    dev = seeded(store)[DEV_1]
    f = sim_factory(store, failures=lambda ch, to, key: outcome)
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0007-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    assert out["status"] == status and out["retryable"] is False
    with pytest.raises(dt.Conflict):
        dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)


def test_failure_after_acceptance_is_not_retried(store):
    dev = seeded(store)[DEV_1]
    f = sim_factory(store, failures=lambda ch, to, key: "FAIL_AFTER_ACCEPT")
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0008-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    r = dt.simulate_callbacks(store, out["delivery_id"])
    assert r["label"] == "SIMULATED" and r["applied"] == 2
    got = dt.list_sends(store)[0]
    assert got["status"] == "FAILED" and got["provider_message_id"] and got["retryable"] is False
    with pytest.raises(dt.Conflict):
        dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)


def test_retries_are_capped(store):
    dev = seeded(store)[DEV_1]
    f = sim_factory(store, failures=lambda ch, to, key: "FAILED")
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0009-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    for _ in range(dt.MAX_ATTEMPTS - 1):
        out = dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)
    assert out["status"] == "FAILED" and out["retryable"] is False and len(out["attempts"]) == dt.MAX_ATTEMPTS
    with pytest.raises(dt.Conflict):
        dt.retry(store, out["delivery_id"], actor="dispatcher", factory=f)


def test_an_interrupted_send_is_reported_unknown_not_resent(store):
    dev = seeded(store)[DEV_1]
    f = sim_factory(store)
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0010-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    d = store.get(al.DELIVERIES, out["delivery_id"])
    d["status"] = "SUBMITTED"                                      # as if the process died during the provider call
    store.put(al.DELIVERIES, d["delivery_id"], d, actor="test", action="test")
    again = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0010-abcdef", text_sha256=key_hash(),
                    actor="dispatcher", factory=f)
    assert again["duplicate"] is True and again["status"] == "SUBMITTED" and again["retryable"] is False
    later = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0010-abcdef", text_sha256=key_hash(),
                    actor="dispatcher", factory=f, now=dt.utc_now() + timedelta(minutes=5))
    assert later["status"] == "UNKNOWN" and later["retryable"] is False          # never re-sent blindly
    assert len(store.list("sim_outbox")) == 1


def test_status_meanings_cover_every_delivery_status():
    for s in pv.DELIVERY_STATUSES:
        assert pv.STATUS_MEANING[s]
    assert "not" in pv.STATUS_MEANING["ACCEPTED"].lower() and "unknown" in pv.STATUS_MEANING["UNKNOWN"].lower()
