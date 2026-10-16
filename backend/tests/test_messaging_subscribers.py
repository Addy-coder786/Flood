"""Subscribers (DECISIONS D-44, docs/LIVE_OPERATIONS.md §6). Written before floodnet/messaging/subscribers.py existed.

Every number below is FAKE: "+9100000000NN" is not a valid Indian mobile number (those start with 6-9), so these
tests can never address a real person."""
import json

import pytest

from floodnet.messaging import subscribers as subs
from floodnet.ops.store import OpsStore

FAKE_1 = "+910000000001"
FAKE_2 = "+910000000002"
LONLAT = [72.848752, 19.017141]          # Dadar TT / Khodad Circle, REAL (floodnet/response/fixtures.py S1)
SMS = {"sms": {"method": "web_form"}}


@pytest.fixture
def pii_key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("FLOODNET_PII_KEY", Fernet.generate_key().decode())


@pytest.fixture
def store(tmp_path, pii_key):
    return OpsStore(tmp_path / "ops.sqlite3")


def _create(store, number=FAKE_1, **kw):
    body = {"number": number, "channels": SMS, "area": {"lonlat": LONLAT, "radius_m": 500},
            "language": "en", "verified": True, "simulated": True, **kw}
    return subs.create(store, body, actor="dispatcher")


def _all_text(store) -> str:
    """Everything the audited document store holds (documents + the append-only audit), as text."""
    docs = store._db.execute("SELECT body FROM docs").fetchall()
    audit = store._db.execute("SELECT body FROM audit").fetchall()
    return json.dumps([r[0] for r in docs] + [r[0] for r in audit])


def test_e164_validation():
    assert subs.normalize_e164("+91 00000 00001", simulated=True) == FAKE_1
    assert subs.normalize_e164("+1 (202) 555-0100") == "+12025550100"
    for bad in ("9876543210", "+91 12345", "+0123456789", "abc", "+91987654321012"):
        with pytest.raises(ValueError):
            subs.normalize_e164(bad)
    with pytest.raises(ValueError, match="Indian mobile"):
        subs.normalize_e164("+910000000001")                 # not a valid Indian mobile unless declared SIMULATED
    with pytest.raises(ValueError, match="SIMULATED"):
        subs.normalize_e164("+919876543210", simulated=True) # a real-looking number cannot be a simulated subscriber


def test_create_requires_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FLOODNET_PII_KEY", raising=False)
    store = OpsStore(tmp_path / "ops.sqlite3")
    with pytest.raises(ValueError, match="FLOODNET_PII_KEY"):
        _create(store)
    assert store.list(subs.COLL) == []


def test_create_refuses_without_cryptography(store, monkeypatch):
    def missing():
        raise ValueError("the 'cryptography' package is not installed")
    monkeypatch.setattr(subs, "_import_fernet", missing)
    with pytest.raises(ValueError, match="cryptography"):
        _create(store)


def test_number_is_encrypted_and_never_in_the_audited_store(store):
    view, created = _create(store)
    assert created and view["id"].startswith("SUB")
    assert view["number_masked"].endswith("0001") and "+91000000" not in json.dumps(view)
    text = _all_text(store)
    assert "0000000001" not in text and "0001" not in text        # neither the number nor last4 in docs or audit
    assert subs.reveal_number(store, view["id"]) == FAKE_1        # internal use only (provider call)


def test_duplicate_number_returns_existing(store):
    a, created_a = _create(store)
    b, created_b = _create(store, number="+91 0000000001")
    assert created_a and not created_b and a["id"] == b["id"]
    assert len(subs.list_subscribers(store)) == 1


def test_validation_of_channels_area_language(store):
    with pytest.raises(ValueError, match="consent"):
        _create(store, channels={})
    with pytest.raises(ValueError, match="channel"):
        _create(store, channels={"email": {"method": "web_form"}})
    with pytest.raises(ValueError, match="method"):
        _create(store, channels={"sms": {}})
    with pytest.raises(ValueError, match="radius"):
        _create(store, area={"lonlat": LONLAT, "radius_m": 50})
    with pytest.raises(ValueError, match="radius"):
        _create(store, area={"lonlat": LONLAT, "radius_m": 5000})
    with pytest.raises(ValueError, match="language"):
        _create(store, language="fr")
    with pytest.raises(ValueError, match="locality"):
        _create(store, area={"locality_id": "atlantis"})


def test_consent_record_and_locality_area(store):
    view, _ = _create(store, area={"locality_id": "dadar-parsee-colony"},
                      channels={"sms": {"method": "paper_form"}, "whatsapp": {"method": "web_form"}})
    c = view["channels"]["sms"]["consent"]
    assert c["method"] == "paper_form" and c["notice_version"] == subs.NOTICE_VERSION and c["granted_at"]
    assert set(view["channels"]) == {"sms", "whatsapp"}
    assert view["area"]["locality_id"] == "dadar-parsee-colony"
    internal = subs.targeting_records(store)[0]
    assert internal["area"]["lonlat"] == [72.851457, 19.018822] and internal["area"]["radius_m"] == subs.LOCALITY_RADIUS_M


def test_public_view_has_no_number_or_location(store):
    view, _ = _create(store)
    flat = json.dumps(subs.get_public_view(store, view["id"]))
    assert "number_enc" not in flat and "number_hmac" not in flat and FAKE_1 not in flat
    assert view["simulated"] is True and view["verified"] is True


def test_opt_out_by_id_and_by_inbound_stop(store):
    a, _ = _create(store)
    b, _ = _create(store, number=FAKE_2)
    out = subs.opt_out(store, a["id"], method="dispatcher", actor="dispatcher")
    assert out["opted_out_at"] and out["status"] == "OPTED_OUT"
    r = subs.opt_out_by_number(store, "whatsapp:" + FAKE_2, method="sms_stop")
    assert r == {"matched": True}
    assert subs.get_public_view(store, b["id"])["status"] == "OPTED_OUT"
    assert subs.opt_out_by_number(store, "+910000000099", method="sms_stop") == {"matched": False}


def test_erase_removes_pii_and_keeps_tombstone(store):
    a, _ = _create(store)
    tomb = subs.erase(store, a["id"], actor="dispatcher")
    assert tomb["status"] == "ERASED" and tomb["erased_at"]
    assert tomb.get("number_masked") is None and tomb.get("area") is None
    assert subs.reveal_number(store, a["id"]) is None
    assert subs.targeting_records(store) == []
    assert subs.opt_out_by_number(store, FAKE_1, method="sms_stop") == {"matched": False}
    again, created = _create(store)                     # the number can subscribe again with fresh consent
    assert created and again["id"] != a["id"]


def test_retention_constant_is_labelled():
    assert subs.RETENTION_DAYS_AFTER_OPT_OUT > 0
    assert "ASSUMED" in subs.RETENTION_NOTE


def test_simulated_seed_is_labelled(store):
    seeded = subs.seed_simulated(store, actor="dispatcher")
    assert seeded and all(s["simulated"] for s in seeded)
    assert all(subs.reveal_number(store, s["id"]).startswith("+9100000") for s in seeded)
    assert len(subs.seed_simulated(store, actor="dispatcher")) == len(seeded)     # idempotent
    assert len(subs.list_subscribers(store)) == len(seeded)


# ----------------------------------------------------------------------------- Indian number input (2026-10-11)
# Every number below is a DUMMY made of zeros after the first digit ("6000000001"); none is stored or sent.
def test_indian_number_input_forms_normalise_to_e164():
    want = "+916000000001"
    for raw in ("+91 60000 00001", "+91-60000-00001", "6000000001", "60000 00001", "600-000-0001", "06000000001",
                "0 6000000001", "916000000001", "0091 6000000001", "+91 (0) 60000 00001", " +916000000001 ",
                "tel:+916000000001", "whatsapp:+916000000001", "+91 60000 00001", "+91‑60000‑00001",
                "+९१६००००००००१"):                      # Devanagari digits typed on a Hindi/Marathi keyboard
        assert subs.normalize_input(raw) == want, repr(raw)
    assert subs.normalize_input("+1 (202) 555-0100") == "+12025550100"        # other countries stay E.164


def test_indian_number_input_rejects_non_mobiles_and_garbage():
    for bad in ("", "abc", "12", "5000000001", "1000000001", "600000000", "60000000012", "+91 50000 00001",
                "+91 600000000", "0600000000", "91600000000", "+91 6000000001 ext 2", "6000000001x", "++916000000001",
                "+910000000001", "0000000001"):
        with pytest.raises(ValueError):
            subs.normalize_input(bad)
    with pytest.raises(ValueError) as e:
        subs.normalize_input("5000000001")
    assert "5000000001" not in str(e.value)                  # the error never echoes the number
    assert subs.normalize_input("0000000001", simulated=True) == FAKE_1     # SIMULATED range only when declared


def test_strict_e164_rejects_non_ascii_digits():
    for raw in ("+९१६००००००००१", "+1२०२५५५०१००"):           # strict E.164 is ASCII only (normalize_input maps)
        with pytest.raises(ValueError):
            subs.normalize_e164(raw)


def test_create_accepts_national_indian_form(store):
    view, created = _create(store, number="06000000001", simulated=False)
    assert created and view["number_masked"] == "•••• 0001"
    assert subs.reveal_number(store, view["id"]) == "+916000000001"
    again, created2 = _create(store, number="+91 60000 00001", simulated=False)
    assert not created2 and again["id"] == view["id"]                        # same person, same record


# ----------------------------------------------------------------------------- editing a subscriber (2026-10-11)
def test_update_language_area_channels_and_consent_history(store):
    view, _ = _create(store)
    out = subs.update(store, view["id"], {"language": "mr", "locality_id": "naigaon"}, actor="dispatcher")
    assert out["language"] == "mr" and out["area"]["locality_id"] == "naigaon"
    assert subs.targeting_records(store)[0]["area"]["radius_m"] == subs.LOCALITY_RADIUS_M
    sms_consent = out["channels"]["sms"]["consent"]
    # adding WhatsApp needs its own consent record; the SMS consent is kept as it was
    with pytest.raises(ValueError, match="method"):
        subs.update(store, view["id"], {"channels": {"sms": {}, "whatsapp": {}}}, actor="dispatcher")
    out = subs.update(store, view["id"], {"channels": {"sms": {}, "whatsapp": {"method": "phone_call"}}},
                      actor="dispatcher")
    assert out["channels"]["sms"]["consent"] == sms_consent
    assert out["channels"]["whatsapp"]["consent"]["method"] == "phone_call"
    # removing a channel withdraws its consent and is recorded
    out = subs.update(store, view["id"], {"channels": {"whatsapp": {}}}, actor="dispatcher")
    assert set(out["channels"]) == {"whatsapp"}
    assert [w["channel"] for w in out["consent_withdrawn"]] == ["sms"] and out["consent_withdrawn"][0]["at"]
    with pytest.raises(ValueError, match="opt"):
        subs.update(store, view["id"], {"channels": {}}, actor="dispatcher")      # all channels -> use opt-out
    out = subs.update(store, view["id"], {"area": {"lonlat": LONLAT, "radius_m": 300}, "verified": False},
                      actor="dispatcher")
    assert out["area"] == {"kind": "point", "lonlat": LONLAT, "radius_m": 300.0} and out["verified"] is False
    assert "0001" not in _all_text(store)                     # the area and number stay in the vault only
    actions = [r["action"] for r in store.audit(subs.COLL, view["id"])]
    assert actions.count("subscriber_update") == 4


def test_update_refuses_number_unknown_fields_and_inactive(store):
    view, _ = _create(store)
    with pytest.raises(ValueError, match="number"):
        subs.update(store, view["id"], {"number": FAKE_2}, actor="dispatcher")
    with pytest.raises(ValueError, match="dev_test"):
        subs.update(store, view["id"], {"dev_test": True}, actor="dispatcher")   # designation comes from config only
    with pytest.raises(ValueError, match="language"):
        subs.update(store, view["id"], {"language": "fr"}, actor="dispatcher")
    with pytest.raises(KeyError):
        subs.update(store, "SUB999", {"language": "en"}, actor="dispatcher")
    subs.opt_out(store, view["id"], method="dispatcher", actor="dispatcher")
    with pytest.raises(subs.Conflict):
        subs.update(store, view["id"], {"language": "hi"}, actor="dispatcher")


def test_create_ignores_a_dev_test_flag_in_the_request_body(store):
    view, _ = _create(store, number="+916000000003", simulated=False, dev_test=True, label="Someone")
    assert view["dev_test"] is False and view.get("label") is None
