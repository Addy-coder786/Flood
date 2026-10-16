"""Alert subscribers: consent per channel, encrypted numbers, STOP opt-out and DPDP erasure (DECISIONS D-44).

Where personal data lives
  * The audited document store (collection `subscribers`, append-only audit) holds ONLY non-identifying fields:
    consent records, language, verified/opted-out flags, the SIMULATED label and who created the record.
  * The phone number (Fernet-encrypted), its keyed HMAC (duplicate detection, STOP lookup), the last four digits and
    the subscriber's area (a location of interest) live in a separate `pii_vault` table in the same SQLite file. It is
    NOT audited, so erasure really deletes them: the append-only audit never saw them.
  Numbers never appear in logs, URLs or API responses; views show "•••• 1234" only.

Keys: FLOODNET_PII_KEY must be a Fernet key (`Fernet.generate_key()`); the HMAC key is derived from it. Without the key,
or without the optional `cryptography` package, nothing is stored (ValueError with the reason).

Numbers: E.164 (`+<country><number>`). An Indian (+91) number must have 10 digits starting 6-9 (a mobile number).
normalize_input() also accepts the forms people type for an Indian mobile (10 digits, a leading 0, 91 without '+', 0091,
spaces/dashes/brackets, Devanagari digits) and turns them into E.164; normalize_e164() stays strict (provider callbacks).
SIMULATED subscribers (demo/test data) must use the reserved-looking fake range +9100000xxxxx, which is NOT a valid Indian
mobile number, so simulated data can never address a real person.

Development test recipients (2026-10-11, messaging/devtest.py) are ordinary subscribers with `dev_test: True` and an
optional label kept in the vault; the flag can only be set from server configuration, never through the API body.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import unicodedata
from datetime import timedelta
from typing import Optional

from ..ops.store import OpsStore, iso, utc_now

COLL = "subscribers"
CHANNELS = ("sms", "whatsapp")
LANGUAGES = ("en", "hi", "mr")
RADIUS_MIN_M, RADIUS_MAX_M = 100, 2000
LOCALITY_RADIUS_M = 500          # ASSUMED: area radius for a subscriber who chose a locality instead of a point
NOTICE_VERSION = "floodnet-consent-notice-2026-10-11"   # the consent notice text version shown at sign-up
RETENTION_DAYS_AFTER_OPT_OUT = 30
RETENTION_NOTE = ("ASSUMED: personal data of opted-out subscribers is erased 30 days after opt-out (purge_expired). "
                  "Not a legal determination; the DPDP Rules' retention requirements were not reviewed by a lawyer.")
SIMULATED_PREFIX = "+9100000"    # fake range for SIMULATED subscribers (not a valid Indian mobile number)
STOP_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"})

# Pilot localities: REAL OpenStreetMap place nodes (ODbL, (c) OpenStreetMap contributors), copied verbatim from
# frontend-react/src/lib/terrain3d/place_labels.json (built by floodnet/data/place_labels.py; tiers primary + locality).
PILOT_LOCALITIES = [
    {"id": "matunga-east", "name": "Matunga East", "lonlat": [72.852085, 19.026158], "osm": "node/1288965355"},
    {"id": "dadar-west", "name": "Dadar West", "lonlat": [72.838224, 19.023889], "osm": "node/631537707"},
    {"id": "dadar-hindu-colony", "name": "Dadar Hindu Colony", "lonlat": [72.848184, 19.020417], "osm": "way/1149339606"},
    {"id": "bhavanishankar", "name": "Bhavanishankar", "lonlat": [72.835657, 19.022237], "osm": "node/1640461315"},
    {"id": "wadala-village", "name": "Wadala Village", "lonlat": [72.855073, 19.015878], "osm": "node/1288965326"},
    {"id": "shivaji-park", "name": "Shivaji Park", "lonlat": [72.839161, 19.028745], "osm": "node/1640461328"},
    {"id": "shindewadi", "name": "Shindewadi", "lonlat": [72.843607, 19.011684], "osm": "node/3766935667"},
    {"id": "kasarwadi", "name": "Kasarwadi", "lonlat": [72.842575, 19.025537], "osm": "node/631537718"},
    {"id": "rao-wadi", "name": "Rao Wadi", "lonlat": [72.841445, 19.031244], "osm": "node/11294890322"},
    {"id": "naigaon", "name": "Naigaon", "lonlat": [72.845815, 19.010716], "osm": "node/3806743196"},
    {"id": "dadar-parsee-colony", "name": "Dadar Parsee Colony", "lonlat": [72.851457, 19.018822], "osm": "node/1288965278"},
    {"id": "sahakar-nagar", "name": "Sahakar Nagar", "lonlat": [72.851632, 19.012668], "osm": "node/700687999"},
    {"id": "brhmanwada", "name": "Brhmanwada", "lonlat": [72.854611, 19.031196], "osm": "node/2446180073"},
]
LOCALITY_BY_ID = {loc["id"]: loc for loc in PILOT_LOCALITIES}

_VAULT_SQL = """CREATE TABLE IF NOT EXISTS pii_vault (
  subscriber_id TEXT PRIMARY KEY, number_hmac TEXT NOT NULL UNIQUE, number_enc TEXT NOT NULL,
  last4 TEXT NOT NULL, area TEXT NOT NULL, label TEXT)"""
EDITABLE = ("language", "area", "locality_id", "channels", "verified")


class PIIUnavailable(ValueError):
    """No usable FLOODNET_PII_KEY or no `cryptography`: nothing personal can be stored (HTTP 503)."""


class Conflict(ValueError):
    """The request is valid but not in the record's current state (HTTP 409)."""


# ----------------------------------------------------------------------------- keys
def _import_fernet():
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        raise PIIUnavailable("the 'cryptography' package is not installed (optional extra 'ops'); subscriber numbers "
                         "cannot be encrypted, so none are stored") from None
    return Fernet


def _key() -> bytes:
    raw = (os.environ.get("FLOODNET_PII_KEY") or "").strip()
    if not raw:
        raise PIIUnavailable("FLOODNET_PII_KEY is not set: subscriber numbers are stored only encrypted, so no subscriber "
                         "can be added (generate one with cryptography.fernet.Fernet.generate_key())")
    return raw.encode("ascii")


def _fernet():
    Fernet = _import_fernet()
    try:
        return Fernet(_key())
    except PIIUnavailable:
        raise
    except ValueError:
        raise PIIUnavailable("FLOODNET_PII_KEY is not a valid Fernet key (32 url-safe base64-encoded bytes)") from None


def number_hmac(e164: str) -> str:
    """Keyed HMAC-SHA256 of a normalised number (key derived from FLOODNET_PII_KEY): de-duplication and STOP lookup
    without decrypting anything. Not reversible without the key."""
    k = hashlib.sha256(b"floodnet-subscriber-number-hmac-v1|" + _key()).digest()
    return hmac.new(k, e164.encode("ascii"), hashlib.sha256).hexdigest()


# ----------------------------------------------------------------------------- numbers
_E164 = re.compile(r"^\+[1-9]\d{7,14}$", re.ASCII)
_SEPARATORS = re.compile(r"[\s\-(). ‐-―−]")


def normalize_e164(raw: str, *, simulated: bool = False) -> str:
    """'+91 00000 00001' -> '+910000000001'. Accepts a 'whatsapp:' prefix. Raises ValueError (without echoing the
    number) when it is not E.164, not an Indian mobile for +91, or a real-looking number declared SIMULATED."""
    s = str(raw or "").strip()
    if s.lower().startswith("whatsapp:"):
        s = s[9:]
    s = re.sub(r"[\s\-().]", "", s)
    if not _E164.match(s):
        raise ValueError("number must be in E.164 format: '+' then country code and number, 8-15 digits")
    if s.startswith("+91"):
        national = s[3:]
        if len(national) != 10:
            raise ValueError("an Indian (+91) number must have 10 digits after +91")
        if simulated:
            if not s.startswith(SIMULATED_PREFIX):
                raise ValueError(f"a SIMULATED subscriber must use the fake range {SIMULATED_PREFIX}xxxxx, never a "
                                 "real-looking number")
        elif national[0] not in "6789":
            raise ValueError("not an Indian mobile number (must start with 6-9 after +91); fake numbers are only "
                             "allowed for SIMULATED subscribers")
    elif simulated:
        raise ValueError(f"a SIMULATED subscriber must use the fake range {SIMULATED_PREFIX}xxxxx")
    return s


def normalize_input(raw: str, *, simulated: bool = False) -> str:
    """A number as a person types it -> E.164. Indian forms: '98xxxxxxxx', '098xxxxxxxx', '9198xxxxxxxx',
    '0091 98xxx xxxxx', '+91 (0) 98xxx-xxxxx', any Unicode decimal digits (e.g. Devanagari); a 'tel:' or 'whatsapp:'
    prefix is dropped. Other countries need '+' or '00'. The result is checked by normalize_e164 (Indian numbers must
    be mobiles starting 6-9). Errors never echo the number."""
    s = str(raw or "").strip()
    low = s.lower()
    for prefix in ("tel:", "whatsapp:"):
        if low.startswith(prefix):
            s = s[len(prefix):]
            break
    s = "".join(str(unicodedata.digit(c)) if c.isdigit() and not c.isascii() else c for c in s)
    s = _SEPARATORS.sub("", s)
    if s.startswith("00") and len(s) >= 12:          # international dialling prefix (0091...); never a national 10
        s = "+" + s[2:]
    if s.startswith("+"):
        if s.startswith("+910") and len(s) == 14:      # '+91 (0) 98...': the trunk 0 written after the country code
            s = "+91" + s[4:]
        return normalize_e164(s, simulated=simulated)
    if not (s.isascii() and s.isdigit()):
        raise ValueError("enter a 10-digit Indian mobile number, or the number with its country code (+...)")
    if len(s) == 10:
        return normalize_e164("+91" + s, simulated=simulated)
    if len(s) == 11 and s[0] == "0":
        return normalize_e164("+91" + s[1:], simulated=simulated)
    if len(s) == 12 and s.startswith("91"):
        return normalize_e164("+" + s, simulated=simulated)
    raise ValueError("enter a 10-digit Indian mobile number, or the number with its country code (+...)")


def mask_last4(last4: Optional[str]) -> Optional[str]:
    return f"•••• {last4}" if last4 else None


# ----------------------------------------------------------------------------- vault (not audited)
def _vault(store: OpsStore):
    with store._lock:
        if not getattr(store, "_pii_vault_ready", False):
            store._db.execute(_VAULT_SQL)
            cols = {r[1] for r in store._db.execute("PRAGMA table_info(pii_vault)").fetchall()}
            if "label" not in cols:                 # vaults created before 2026-10-11 (dev test recipient label)
                store._db.execute("ALTER TABLE pii_vault ADD COLUMN label TEXT")
            store._pii_vault_ready = True
    return store._db


def _vault_get(store: OpsStore, subscriber_id: str) -> Optional[dict]:
    db = _vault(store)
    with store._lock:
        row = db.execute("SELECT number_hmac, number_enc, last4, area, label FROM pii_vault WHERE subscriber_id=?",
                         (subscriber_id,)).fetchone()
    if not row:
        return None
    return {"number_hmac": row[0], "number_enc": row[1], "last4": row[2], "area": json.loads(row[3]), "label": row[4]}


def _vault_find(store: OpsStore, h: str) -> Optional[str]:
    db = _vault(store)
    with store._lock:
        row = db.execute("SELECT subscriber_id FROM pii_vault WHERE number_hmac=?", (h,)).fetchone()
    return row[0] if row else None


def reveal_number(store: OpsStore, subscriber_id: str) -> Optional[str]:
    """Decrypts a number for the provider call only. Never return this from an API or write it to a log."""
    v = _vault_get(store, subscriber_id)
    if v is None:
        return None
    return _fernet().decrypt(v["number_enc"].encode("ascii")).decode("ascii")


# ----------------------------------------------------------------------------- validation
def _parse_channels(raw, now: str) -> dict:
    if not isinstance(raw, dict) or not raw:
        raise ValueError("at least one channel with recorded consent is required, e.g. "
                         "{'sms': {'method': 'web_form'}}")
    out = {}
    for ch, rec in raw.items():
        if ch not in CHANNELS:
            raise ValueError(f"channel must be one of {CHANNELS}, got {ch!r}")
        rec = rec if isinstance(rec, dict) else {}
        method = str(rec.get("method") or "").strip()[:40]
        if not method:
            raise ValueError(f"consent for {ch}: 'method' (how consent was obtained, e.g. web_form) is required")
        out[ch] = {"consent": {"granted_at": str(rec.get("granted_at") or now)[:40], "method": method,
                               "notice_version": str(rec.get("notice_version") or NOTICE_VERSION)[:80]}}
    return out


def _parse_area(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("area must be {'lonlat': [lon, lat], 'radius_m': 100..2000} or {'locality_id': ...}")
    if raw.get("locality_id") is not None:
        loc = LOCALITY_BY_ID.get(str(raw["locality_id"]))
        if loc is None:
            raise ValueError(f"unknown pilot locality; choose one of {sorted(LOCALITY_BY_ID)}")
        return {"kind": "locality", "locality_id": loc["id"], "locality_name": loc["name"],
                "lonlat": list(loc["lonlat"]), "radius_m": LOCALITY_RADIUS_M}
    try:
        lon, lat = (float(v) for v in raw["lonlat"])
        radius = float(raw.get("radius_m"))
    except (KeyError, TypeError, ValueError):
        raise ValueError("area needs lonlat [lon, lat] and radius_m (100..2000 m)") from None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        raise ValueError("area lonlat out of range")
    if not RADIUS_MIN_M <= radius <= RADIUS_MAX_M:
        raise ValueError(f"area radius_m must be between {RADIUS_MIN_M} and {RADIUS_MAX_M} m")
    return {"kind": "point", "lonlat": [round(lon, 6), round(lat, 6)], "radius_m": radius}


# ----------------------------------------------------------------------------- views
def _view(doc: dict, vault: Optional[dict]) -> dict:
    """The dispatcher's view: never the number, its HMAC or ciphertext; masked last four digits only."""
    area = None
    if vault is not None:
        a = vault["area"]
        area = ({"kind": "locality", "locality_id": a["locality_id"], "locality_name": a["locality_name"]}
                if a.get("kind") == "locality" else {"kind": "point", "lonlat": a["lonlat"], "radius_m": a["radius_m"]})
    return {"id": doc["id"], "number_masked": mask_last4(vault["last4"]) if vault else None,
            "channels": doc.get("channels", {}), "area": area, "language": doc.get("language"),
            "verified": bool(doc.get("verified")), "status": doc.get("status"), "opted_out_at": doc.get("opted_out_at"),
            "erased_at": doc.get("erased_at"), "simulated": bool(doc.get("simulated")),
            "label": "SIMULATED" if doc.get("simulated") else (vault or {}).get("label"),
            "dev_test": bool(doc.get("dev_test")), "consent_withdrawn": doc.get("consent_withdrawn", []),
            "created_by": doc.get("created_by"), "created_at": doc.get("_created_at"),
            "updated_at": doc.get("_updated_at")}


def get_public_view(store: OpsStore, subscriber_id: str) -> Optional[dict]:
    doc = store.get(COLL, subscriber_id)
    return _view(doc, _vault_get(store, subscriber_id)) if doc else None


def list_subscribers(store: OpsStore) -> list[dict]:
    return [_view(d, _vault_get(store, d["id"])) for d in store.list(COLL)]


def targeting_records(store: OpsStore) -> list[dict]:
    """Internal records for targeting (area included, number excluded). Erased subscribers are left out."""
    out = []
    for d in store.list(COLL):
        v = _vault_get(store, d["id"])
        if v is None or d.get("status") == "ERASED":
            continue
        out.append({"id": d["id"], "channels": d.get("channels", {}), "language": d.get("language"),
                    "verified": bool(d.get("verified")), "status": d.get("status"),
                    "opted_out_at": d.get("opted_out_at"), "simulated": bool(d.get("simulated")), "area": v["area"]})
    return out


# ----------------------------------------------------------------------------- lifecycle
def _clean_label(label) -> Optional[str]:
    text = "".join(c for c in str(label or "") if c.isprintable()).strip()[:40]
    return text or None


def create(store: OpsStore, body: dict, *, actor: str, dev_test: bool = False,
           label: Optional[str] = None) -> tuple[dict, bool]:
    """Adds a subscriber. A number already subscribed returns the existing record and created=False.
    `dev_test` and `label` are keyword-only on purpose: they come from server configuration (messaging/devtest.py),
    never from a request body (a body's 'dev_test' / 'label' keys are ignored)."""
    simulated = bool(body.get("simulated"))
    number = normalize_input(body.get("number"), simulated=simulated)
    fernet = _fernet()                            # raises before anything is written
    h = number_hmac(number)
    now = iso(utc_now())
    channels = _parse_channels(body.get("channels"), now)
    area = _parse_area(body.get("area") if body.get("area") is not None else {"locality_id": body.get("locality_id")})
    language = str(body.get("language") or "en").lower()
    if language not in LANGUAGES:
        raise ValueError(f"language must be one of {LANGUAGES}")
    db = _vault(store)
    with store.transaction():
        existing = _vault_find(store, h)
        if existing:
            return get_public_view(store, existing), False
        sid = f"SUB{store.next_seq('subscriber')}"
        doc = {"id": sid, "channels": channels, "language": language, "verified": bool(body.get("verified")),
               "status": "ACTIVE", "opted_out_at": None, "opt_out_method": None, "erased_at": None,
               "simulated": simulated, "created_by": str(actor)[:60]}
        if dev_test and not simulated:
            doc["dev_test"] = True
        store.put(COLL, sid, doc, actor=actor, action="subscribe")
        db.execute("INSERT INTO pii_vault (subscriber_id, number_hmac, number_enc, last4, area, label) "
                   "VALUES (?,?,?,?,?,?)",
                   (sid, h, fernet.encrypt(number.encode("ascii")).decode("ascii"), number[-4:], json.dumps(area),
                    _clean_label(label) if dev_test else None))
    return get_public_view(store, sid), True


def find_by_number(store: OpsStore, e164: str) -> Optional[str]:
    """Subscriber id for a normalised number (keyed-HMAC lookup), or None. Internal use only."""
    return _vault_find(store, number_hmac(e164))


def number_hmac_of(store: OpsStore, subscriber_id: str) -> Optional[str]:
    v = _vault_get(store, subscriber_id)
    return v["number_hmac"] if v else None


def mark_dev_test(store: OpsStore, subscriber_id: str, *, actor: str, label: Optional[str] = None) -> dict:
    """Designates an existing ACTIVE subscriber as a development test recipient (server configuration only)."""
    db = _vault(store)
    with store.transaction():
        doc = store.get(COLL, subscriber_id)
        if doc is None:
            raise KeyError(subscriber_id)
        if doc.get("status") != "ACTIVE" or doc.get("simulated"):
            raise Conflict(f"subscriber is {doc.get('status')}; not designated")
        if not doc.get("dev_test"):
            doc["dev_test"] = True
            store.put(COLL, subscriber_id, doc, actor=actor, action="dev_test_designate")
        if label is not None:
            db.execute("UPDATE pii_vault SET label=? WHERE subscriber_id=?", (_clean_label(label), subscriber_id))
    return get_public_view(store, subscriber_id)


def update(store: OpsStore, subscriber_id: str, body: dict, *, actor: str) -> dict:
    """Edits language, area (point or pilot locality), verified, and the permitted channels with their consent
    records. A channel kept without a new 'method' keeps its consent record; a new channel needs one; a channel left
    out is a withdrawal of its consent, recorded in consent_withdrawn. The number cannot be edited (erase and add the
    new number with fresh consent). Only ACTIVE subscribers can be edited (opted-out people are not re-subscribed by an
    edit)."""
    body = body if isinstance(body, dict) else {}
    unknown = sorted(set(body) - set(EDITABLE))
    if unknown:
        if "number" in unknown:
            raise ValueError("the number cannot be edited: erase this subscriber and add the new number with fresh "
                             "consent")
        raise ValueError(f"fields that cannot be edited: {', '.join(unknown)} (editable: {', '.join(EDITABLE)})")
    now = iso(utc_now())
    db = _vault(store)
    with store.transaction():
        doc = store.get(COLL, subscriber_id)
        if doc is None:
            raise KeyError(subscriber_id)
        if doc.get("status") != "ACTIVE":
            raise Conflict(f"only an ACTIVE subscriber can be edited (status {doc.get('status')}); an opted-out person "
                           "must ask again and be added with fresh consent")
        vault = _vault_get(store, subscriber_id)
        if vault is None:
            raise Conflict("no personal data held for this subscriber (erased)")
        if "language" in body:
            language = str(body.get("language") or "").lower()
            if language not in LANGUAGES:
                raise ValueError(f"language must be one of {LANGUAGES}")
            doc["language"] = language
        if "verified" in body:
            if not isinstance(body["verified"], bool):
                raise ValueError("verified must be true or false")
            doc["verified"] = body["verified"]
        area = None
        if body.get("area") is not None or body.get("locality_id") is not None:
            area = _parse_area(body["area"] if body.get("area") is not None else {"locality_id": body["locality_id"]})
        if "channels" in body:
            raw = body["channels"]
            if not isinstance(raw, dict):
                raise ValueError("channels must be an object, e.g. {'sms': {}, 'whatsapp': {'method': 'phone_call'}}")
            if not raw:
                raise ValueError("at least one channel must stay; to stop all messages use opt-out")
            old = doc.get("channels", {})
            new = {}
            for ch, rec in raw.items():
                if ch not in CHANNELS:
                    raise ValueError(f"channel must be one of {CHANNELS}, got {ch!r}")
                rec = rec if isinstance(rec, dict) else {}
                if ch in old and not rec.get("method"):
                    new[ch] = old[ch]
                else:
                    new.update(_parse_channels({ch: rec}, now))
            withdrawn = doc.setdefault("consent_withdrawn", [])
            for ch in sorted(set(old) - set(new)):
                withdrawn.append({"channel": ch, "at": now, "by": str(actor)[:60], "method": "dispatcher edit"})
            doc["channels"] = new
        store.put(COLL, subscriber_id, doc, actor=actor, action="subscriber_update")
        if area is not None:
            db.execute("UPDATE pii_vault SET area=? WHERE subscriber_id=?", (json.dumps(area), subscriber_id))
    return get_public_view(store, subscriber_id)


def opt_out(store: OpsStore, subscriber_id: str, *, method: str, actor: str) -> dict:
    with store.transaction():
        doc = store.get(COLL, subscriber_id)
        if doc is None:
            raise KeyError(subscriber_id)
        if doc.get("status") == "ACTIVE":
            doc.update(status="OPTED_OUT", opted_out_at=iso(utc_now()), opt_out_method=str(method)[:40])
            store.put(COLL, subscriber_id, doc, actor=actor, action="opt_out")
    return get_public_view(store, subscriber_id)


def opt_out_by_number(store: OpsStore, raw_number: str, *, method: str) -> dict:
    """Inbound STOP from a phone. The reply never says whether the number was known (only `matched` internally)."""
    try:
        number = normalize_e164(raw_number, simulated=str(raw_number).replace("whatsapp:", "").strip()
                                .startswith(SIMULATED_PREFIX))
    except ValueError:
        return {"matched": False}
    sid = _vault_find(store, number_hmac(number))
    if sid is None:
        return {"matched": False}
    opt_out(store, sid, method=method, actor="subscriber:inbound")
    return {"matched": True}


def is_stop_keyword(text: str) -> bool:
    return str(text or "").strip().upper().rstrip(".!") in STOP_WORDS


def erase(store: OpsStore, subscriber_id: str, *, actor: str) -> dict:
    """DPDP withdrawal/erasure: deletes number, HMAC, last four digits and area; keeps a tombstone without PII
    (id, consent history, timestamps) so delivery records stay explainable."""
    db = _vault(store)
    with store.transaction():
        doc = store.get(COLL, subscriber_id)
        if doc is None:
            raise KeyError(subscriber_id)
        db.execute("DELETE FROM pii_vault WHERE subscriber_id=?", (subscriber_id,))
        now = iso(utc_now())
        doc.update(status="ERASED", erased_at=now, opted_out_at=doc.get("opted_out_at") or now,
                   opt_out_method=doc.get("opt_out_method") or "erasure")
        store.put(COLL, subscriber_id, doc, actor=actor, action="erase")
    try:                                    # move the WAL into the main file so deleted bytes do not linger there
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:                       # noqa: BLE001 - best effort; secure_delete already zeroes freed pages
        pass
    return get_public_view(store, subscriber_id)


def purge_expired(store: OpsStore, *, now=None) -> list[str]:
    """Erases subscribers opted out more than RETENTION_DAYS_AFTER_OPT_OUT days ago (ASSUMED period)."""
    from datetime import datetime
    cutoff = (now or utc_now()) - timedelta(days=RETENTION_DAYS_AFTER_OPT_OUT)
    done = []
    for d in store.list(COLL, lambda d: d.get("status") == "OPTED_OUT" and d.get("opted_out_at")):
        if datetime.fromisoformat(d["opted_out_at"].replace("Z", "+00:00")) < cutoff:
            erase(store, d["id"], actor="retention")
            done.append(d["id"])
    return done


def seed_simulated(store: OpsStore, *, actor: str) -> list[dict]:
    """Demo subscribers, labelled SIMULATED, with FAKE numbers (+9100000xxxxx is not a valid Indian mobile).
    Areas are REAL places from floodnet/response/fixtures.py (S1-S4 settlements) and PILOT_LOCALITIES."""
    seeds = [
        ("+910000000001", {"lonlat": [72.848752, 19.017141], "radius_m": 500}, {"sms": {"method": "demo_seed"}}, True),
        ("+910000000002", {"lonlat": [72.852454, 19.027101], "radius_m": 300},
         {"sms": {"method": "demo_seed"}, "whatsapp": {"method": "demo_seed"}}, True),
        ("+910000000003", {"lonlat": [72.854410, 19.011698], "radius_m": 400}, {"whatsapp": {"method": "demo_seed"}}, True),
        ("+910000000004", {"locality_id": "dadar-parsee-colony"}, {"sms": {"method": "demo_seed"}}, False),
    ]
    out = []
    for number, area, channels, verified in seeds:
        view, _ = create(store, {"number": number, "area": area, "channels": channels, "language": "en",
                                 "verified": verified, "simulated": True}, actor=actor)
        out.append(view)
    return out

