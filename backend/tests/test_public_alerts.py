"""The public alert page contract (DECISIONS D-45, docs/LIVE_OPERATIONS.md §7, acceptance criterion 11).
Written before floodnet/messaging/public.py existed. Depths are SYNTHETIC TEST FIXTURES on REAL pilot road ids;
subscribers are SIMULATED with FAKE numbers."""
import json
import re
from datetime import timedelta

import pytest

from floodnet.messaging import alerts as al
from floodnet.messaging import providers as pv
from floodnet.messaging import public as pub
from floodnet.ops import incident as inc
from tests.test_messaging_alerts import (ADVISORY, BASE, T0, WATCH, add_people, approved, factory, publish,  # noqa: F401
                                         store)

TILAK = "22848583_0"
EXPECTED_KEYS = {"public_id", "version", "latest_version", "is_latest", "issuer", "headline", "locality_text",
                 "status", "status_text", "state", "evidence", "time_window", "uncertainty", "precautions", "contacts",
                 "official_links", "map", "updates", "updated_at", "expires_at", "disclaimer", "attribution",
                 # added after the browser walkthrough (2026-10-11): a demonstration storm is said so on the page
                 "demonstration"}


def sent_alert(store, streets=WATCH, events=()):
    add_people(store)
    if events:
        inc.sync_dispatcher_events(store, list(events))
    a = approved(store, streets)
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(pv.SimulatedProvider(store=store)), now=T0)
    return al.get_alert(store, a["alert_id"])


def test_unpublished_alert_has_no_public_page(store):
    publish(store, WATCH)
    a = al.create_draft(store, window_min=60, public_base_url=BASE, actor="dispatcher", now=T0)
    assert pub.snapshot_for_public_id(store, a["public_id"]) is None
    al.cancel(store, a["alert_id"], actor="dispatcher")
    assert pub.snapshot_for_public_id(store, a["public_id"]) is None
    assert pub.snapshot_for_public_id(store, "no-such-id") is None


def test_snapshot_contract_and_wording(store):
    a = sent_alert(store, ADVISORY)
    s = pub.snapshot_for_public_id(store, a["public_id"], now=T0 + timedelta(minutes=5))
    assert set(s) == EXPECTED_KEYS
    assert s["status"] == "advisory" and s["status_text"] == "Flooding likely – avoid the area"
    assert s["state"] == "active" and s["version"] == 1 and s["is_latest"]
    assert s["demonstration"] and "Demonstration" in s["demonstration"]      # fixture data mode is SYNTHETIC
    for field in (s["status_text"], s["headline"]):
        assert not re.search(r"\b(alert|warning|red|level)\b", field, re.I), field
    assert [c["number"] for c in s["contacts"]] == ["112", "1916"]
    assert all(c["source_url"].startswith("https://") for c in s["contacts"])
    assert s["precautions"]["items"] == ["Don't walk through flowing water",
                                         "only half a metre of flood water can carry a car away",
                                         "Avoid contact with flood water", "Stay away from power lines"]
    assert s["precautions"]["source"] == "Source: BMC Disaster Management"
    assert {l["url"] for l in s["official_links"]} == {"https://sachet.ndma.gov.in/", "https://dm.mcgm.gov.in/"}
    assert "not an official warning" in s["disclaimer"]
    ev = {e["kind"]: e for e in s["evidence"]}
    assert ev["model_estimate"]["time"] and ev["model_estimate"]["source"]
    assert s["time_window"]["from"] and s["time_window"]["to"]


def test_snapshot_never_contains_private_fields(store):
    ev = [{"event_id": "OPSCLOSUREXYZ", "seg_id": TILAK, "kind": "closure", "received_t_min": 0,
           "source_tier": "OPERATOR", "note": "INTERNAL-NOTE-ABC"}]
    a = sent_alert(store, ADVISORY, events=ev)
    s = pub.snapshot_for_public_id(store, a["public_id"], now=T0)
    text = json.dumps(s, ensure_ascii=False).replace(a["public_id"], "<public-id>")   # random id: no false hits
    assert "confirmed_closure" in {e["kind"] for e in s["evidence"]}
    assert not re.search(r"\+?\d{8,}", text)                                       # no phone numbers
    for forbidden in ("SUB1", "subscriber", "recipient", "deliver", "_actor", "trip", "vehicle", "OPSCLOSUREXYZ",
                      "INTERNAL-NOTE-ABC", "Test Approver", "approved_by", "created_by", "dispatcher", "AL1",
                      "number", "signature", "idempotency", "seg_id"):
        if forbidden == "number":
            assert all(set(c) == {"number", "name", "source", "source_url"} for c in s["contacts"])
            continue
        assert forbidden not in text, forbidden


def test_versions_history_and_requested_version(store):
    a = sent_alert(store, WATCH)
    publish(store, ADVISORY)
    al.refresh(store, a["alert_id"], actor="dispatcher", now=T0 + timedelta(minutes=10))
    assert pub.snapshot_for_public_id(store, a["public_id"], v=2) is None          # under review: not public yet
    assert pub.snapshot_for_public_id(store, a["public_id"])["version"] == 1
    al.approve(store, a["alert_id"], actor="dispatcher")
    al.send(store, a["alert_id"], actor="dispatcher", provider_factory=factory(pv.SimulatedProvider()),
            now=T0 + timedelta(minutes=11))
    latest = pub.snapshot_for_public_id(store, a["public_id"], now=T0 + timedelta(minutes=12))
    first = pub.snapshot_for_public_id(store, a["public_id"], v=1, now=T0 + timedelta(minutes=12))
    assert latest["version"] == 2 and latest["status"] == "advisory" and latest["is_latest"]
    assert first["version"] == 1 and first["status"] == "watch" and not first["is_latest"] and first["latest_version"] == 2
    assert [u["version"] for u in latest["updates"]] == [1, 2]
    assert "Flooding likely" in latest["updates"][1]["what_changed"]
    assert pub.snapshot_for_public_id(store, a["public_id"], v=9) is None


def test_stale_expired_and_resolved(store):
    a = sent_alert(store, WATCH)
    pid = a["public_id"]
    assert pub.snapshot_for_public_id(store, pid, now=T0 + timedelta(minutes=30))["state"] == "active"
    assert pub.snapshot_for_public_id(store, pid, now=T0 + timedelta(minutes=61))["state"] == "stale"
    s = pub.snapshot_for_public_id(store, pid, now=T0 + timedelta(hours=7))
    assert s["state"] == "expired"
    assert s["expires_at"] == "2026-10-11T16:00:00Z"
    al.resolve(store, a["alert_id"], actor="dispatcher", provider_factory=factory(pv.SimulatedProvider()),
               now=T0 + timedelta(minutes=20))
    r = pub.snapshot_for_public_id(store, pid, now=T0 + timedelta(minutes=21))
    assert r["state"] == "resolved" and r["status"] == "ended" and r["status_text"] == "No longer active"
    assert r["updates"][-1]["what_changed"] == "No longer active"


def test_map_data_is_small_rounded_and_classified(store):
    ev = [{"event_id": "OPSCLOSUREXYZ", "seg_id": TILAK, "kind": "closure", "received_t_min": 0}]
    a = sent_alert(store, ADVISORY, events=ev)
    m = pub.snapshot_for_public_id(store, a["public_id"], now=T0)["map"]
    classes = {r["class"] for r in m["affected_roads"]}
    assert classes == {"MODEL_ESTIMATE", "CONFIRMED_CLOSURE"}
    for r in m["affected_roads"]:
        assert r["type"] == "LineString" and r["name"]
        for lon, lat in r["coordinates"]:
            assert round(lon, 5) == lon and round(lat, 5) == lat
    assert m["area_outline"]["type"] == "Polygon" and len(m["area_outline"]["coordinates"][0]) >= 4
    w, s_, e, n = m["bbox"]
    assert w < e and s_ < n
    assert m["context_roads"] and all(r["class"] in {"primary", "secondary", "tertiary", "trunk"}
                                      for r in m["context_roads"])
    assert len(json.dumps(m)) < 60_000


# ----------------------------------------------------------------------------- SACHET feed (read-only consumer)
# HAND-WRITTEN TEST FIXTURE that mirrors the RSS 2.0 structure. It is NOT real SACHET/NDMA data and was not fetched.
RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>HAND-WRITTEN FIXTURE feed</title>
    <link>https://example.test/</link>
    <description>test fixture, not real data</description>
    <item>
      <title>FIXTURE item one</title>
      <link>https://example.test/cap/one.xml</link>
      <author>fixture-author@example.test</author>
      <pubDate>Sun, 11 Oct 2026 08:00:00 GMT</pubDate>
      <guid>fixture-guid-1</guid>
    </item>
    <item>
      <title>FIXTURE item two</title>
      <link>javascript:alert(1)</link>
      <guid>fixture-guid-2</guid>
    </item>
  </channel>
</rss>"""


class FakeFeed:
    def __init__(self, status=200, text=RSS_FIXTURE, etag='"v1"'):
        self.status, self.text, self.etag, self.calls = status, text, etag, []

    def __call__(self, url, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        if headers.get("If-None-Match") == self.etag:
            return 304, {"ETag": self.etag}, ""
        return self.status, {"ETag": self.etag}, self.text


def test_sachet_parse_label_and_etag():
    from floodnet.messaging import sachet
    feed = FakeFeed()
    out = sachet.fetch("https://example.test/feed.xml", feed)
    assert out["unchanged"] is False and out["etag"] == '"v1"'
    assert [i["guid"] for i in out["items"]] == ["fixture-guid-1", "fixture-guid-2"]
    one, two = out["items"]
    assert one["label"] == "OFFICIAL (SACHET/NDMA)" and one["title"] == "FIXTURE item one"
    assert one["link"] == "https://example.test/cap/one.xml" and one["author"] and one["pub_date"]
    assert two["link"] is None                                       # only http(s) links are passed on
    again = sachet.fetch("https://example.test/feed.xml", feed, etag='"v1"')
    assert again["unchanged"] is True and feed.calls[-1]["headers"]["If-None-Match"] == '"v1"'


def test_sachet_rejects_doctype_and_entities():
    from floodnet.messaging import sachet
    bomb = '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]><rss><channel></channel></rss>'
    with pytest.raises(ValueError, match="DOCTYPE"):
        sachet.fetch("https://example.test/feed.xml", FakeFeed(text=bomb))
    with pytest.raises(ValueError):
        sachet.fetch("https://example.test/feed.xml", FakeFeed(status=500))


def test_sachet_refresh_caches_in_store(store):
    from floodnet.messaging import sachet
    feed = FakeFeed()
    r1 = sachet.refresh(store, "https://example.test/feed.xml", feed)
    assert r1["stored"] == 2
    r2 = sachet.refresh(store, "https://example.test/feed.xml", feed)
    assert r2["unchanged"] is True and feed.calls[-1]["headers"]["If-None-Match"] == '"v1"'
    cached = sachet.list_cached(store)
    assert len(cached) == 2 and all(i["label"] == "OFFICIAL (SACHET/NDMA)" for i in cached)
    assert all(not k.startswith("_") for i in cached for k in i)


def test_map_roads_carry_their_modelled_depth_for_tap_to_see(store):
    """Added 2026-10-11 (interactive public map): each affected road says what is known about it -- the modelled
    depth in the time window for a model estimate (whole cm), nothing invented for a closure without a model depth."""
    ev = [{"event_id": "OPSCLOSUREXYZ", "seg_id": TILAK, "kind": "closure", "received_t_min": 0}]
    a = sent_alert(store, ADVISORY, events=ev)
    s = pub.snapshot_for_public_id(store, a["public_id"], now=T0)
    roads = s["map"]["affected_roads"]
    model = [r for r in roads if r["class"] == "MODEL_ESTIMATE"]
    assert model and all(isinstance(r["depth_cm"], int) and r["depth_cm"] >= 15 for r in model)
    assert max(r["depth_cm"] for r in model) == 40                       # SYNTHETIC fixture peak on Khodadad Circle
    closure = [r for r in roads if r["class"] == "CONFIRMED_CLOSURE"]
    assert closure and all(r["depth_cm"] is None or isinstance(r["depth_cm"], int) for r in closure)
    assert set(s) == EXPECTED_KEYS                                       # no new top-level fields
