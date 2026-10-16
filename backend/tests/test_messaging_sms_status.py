"""Real-SMS readiness for the dispatcher's test send (2026-10-11, owner's request for a real SMS test).
Written before the code. Every number is a DUMMY ("+9160000000NN"), every credential FAKE, every HTTP call a fake:
nothing leaves this process.
- A test SMS carries no link, so it does not need FLOODNET_PUBLIC_BASE_URL (advisories still do: their link).
- With no public URL the provider cannot call FloodNet back, so the dispatcher can ASK the provider for the status
  (Twilio GET Messages/{sid}.json); the answer moves the record forward only, with Twilio's error code kept.
- The text is the owner's: an explicitly labelled test, not an emergency alert."""
import pytest

from floodnet.messaging import devtest as dt
from floodnet.messaging import providers as pv
from tests.test_messaging_devtest import DEV_1, FakeHTTP, env, key_hash, seeded, store  # noqa: F401  (fixtures)

SID = "SM" + "b" * 32


def _live_no_base(monkeypatch):
    monkeypatch.setenv("FLOODNET_LIVE_MESSAGING", "1")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC" + "0" * 32)
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "fake-token")
    monkeypatch.setenv("TWILIO_SMS_FROM", "+15550000000")


def test_test_sms_needs_no_public_base_url_but_advisories_still_do(monkeypatch):
    _live_no_base(monkeypatch)
    ok, why = pv.live_sending_allowed("sms", DEV_1, purpose="test")
    assert ok and why.startswith("LIVE")
    monkeypatch.setenv("FLOODNET_MESSAGING_ALLOWLIST", DEV_1)
    ok, why = pv.live_sending_allowed("sms", DEV_1, purpose="alert")
    assert not ok and "FLOODNET_PUBLIC_BASE_URL" in why


def test_twilio_status_lookup_maps_status_and_keeps_the_error_code():
    http = FakeHTTP((200, {"sid": SID, "status": "undelivered", "error_code": 30008, "error_message": "Unknown error"}))
    p = pv.TwilioProvider(account_sid="AC" + "0" * 32, auth=("AC" + "0" * 32, "fake"), sms_from="+15550000000",
                          http_get=http)
    got = p.fetch_status(SID)
    assert got == {"status": "FAILED", "raw_status": "undelivered", "error": {"code": 30008, "message": "Unknown error"}}
    assert http.calls[0]["url"].endswith(f"/Messages/{SID}.json")
    missing = pv.TwilioProvider(account_sid="AC" + "0" * 32, auth=("x", "y"), http_get=FakeHTTP((404, {"code": 20404})))
    assert missing.fetch_status(SID)["status"] is None


def test_refresh_moves_a_live_test_send_forward_only(store, monkeypatch):
    _live_no_base(monkeypatch)
    dev = seeded(store)[DEV_1]
    post = FakeHTTP((201, {"sid": SID, "status": "queued"}))
    factory = lambda ch, to, simulated: pv.choose_provider(ch, to, simulated=simulated, http_post=post,  # noqa: E731
                                                           purpose="test")
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0101-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=factory, expected_mode="LIVE")
    assert out["mode"] == "LIVE" and out["status"] == "ACCEPTED"
    did = out["delivery_id"]
    sent = dt.refresh_status(store, did, provider=pv.TwilioProvider.from_env(http_get=FakeHTTP((200, {"sid": SID, "status": "sent"}))))
    assert sent["status"] == "SENT" and sent["provider_status"] == "sent"
    done = dt.refresh_status(store, did, provider=pv.TwilioProvider.from_env(http_get=FakeHTTP((200, {"sid": SID, "status": "delivered"}))))
    assert done["status"] == "DELIVERED"
    back = dt.refresh_status(store, did, provider=pv.TwilioProvider.from_env(http_get=FakeHTTP((200, {"sid": SID, "status": "sent"}))))
    assert back["status"] == "DELIVERED"                                # never moves back
    assert [h["status"] for h in done["status_history"]][-2:] == ["SENT", "DELIVERED"]


def test_refresh_is_refused_for_a_simulated_send(store):
    dev = seeded(store)[DEV_1]
    sim = pv.SimulatedProvider(store=store)
    f = lambda ch, to, simulated: pv.choose_provider(ch, to, simulated=simulated, simulated_provider=sim, purpose="test")  # noqa: E731
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0102-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=f)
    with pytest.raises(dt.Conflict, match="SIMULATED"):
        dt.refresh_status(store, out["delivery_id"], provider=None)


def test_the_test_text_is_the_owners_labelled_test_message():
    assert dt.TEST_TEXT == ("FloodNet SMS Test: Your SMS integration is working. This is a test message, "
                            "not an emergency alert.")


def test_a_send_must_match_the_mode_its_preview_showed(store, monkeypatch):
    """QA 2026-10-11 M8: a preview shown as SIMULATED must not become a real send because the server's configuration
    changed in between; a LIVE send needs the dispatcher to have seen LIVE."""
    dev = seeded(store)[DEV_1]
    post = FakeHTTP((201, {"sid": SID, "status": "queued"}))
    factory = lambda ch, to, simulated: pv.choose_provider(ch, to, simulated=simulated, http_post=post,  # noqa: E731
                                                           purpose="test")
    _live_no_base(monkeypatch)                                        # the server went LIVE after the preview
    with pytest.raises(dt.Conflict, match="preview"):
        dt.send(store, dev["id"], channel="sms", idempotency_key="click-0201-abcdef", text_sha256=key_hash(),
                actor="dispatcher", factory=factory, expected_mode="SIMULATED")
    with pytest.raises(dt.Conflict, match="preview"):                  # no mode given: never LIVE
        dt.send(store, dev["id"], channel="sms", idempotency_key="click-0202-abcdef", text_sha256=key_hash(),
                actor="dispatcher", factory=factory)
    assert post.calls == []
    out = dt.send(store, dev["id"], channel="sms", idempotency_key="click-0203-abcdef", text_sha256=key_hash(),
                  actor="dispatcher", factory=factory, expected_mode="LIVE")
    assert out["mode"] == "LIVE" and len(post.calls) == 1
