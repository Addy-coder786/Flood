"""Read-only consumer of the official SACHET (NDMA) CAP RSS feed (DECISIONS D-46).

FloodNet only READS official alerts and shows them apart from its own advisories, each labelled
"OFFICIAL (SACHET/NDMA)". It never originates or contributes alerts. SACHET's integration guide asks consumers to use
ETag / If-None-Match (docs/LIVE_OPERATIONS_RESEARCH.md §7; reported by a research agent, not re-fetched by the lead).
The feed URL is configuration (SACHET_RSS_URL); none is hard-coded because the exact path was not verified here.

XML safety: defusedxml is not installed, so any document containing a DOCTYPE or ENTITY declaration is rejected before
parsing (entity-expansion attacks need a DTD); xml.etree.ElementTree does not fetch external resources.
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from typing import Callable, Optional

from ..ops.store import OpsStore, iso, utc_now

LABEL = "OFFICIAL (SACHET/NDMA)"
COLL, META = "official_alerts", "official_alerts_meta"
MAX_BYTES = 2_000_000
_DTD = re.compile(r"<!\s*(DOCTYPE|ENTITY)", re.I)


def _httpx_get(url: str, headers: dict):
    import httpx
    r = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
    return r.status_code, dict(r.headers), r.text


def _text(el, tag: str) -> Optional[str]:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text.strip()[:500] or None


def _safe_link(url: Optional[str]) -> Optional[str]:
    return url if url and url.lower().startswith(("https://", "http://")) else None


def parse(xml_text: str) -> list[dict]:
    if len(xml_text) > MAX_BYTES:
        raise ValueError("feed too large")
    if _DTD.search(xml_text):
        raise ValueError("feed contains a DOCTYPE/ENTITY declaration; rejected (entity-expansion guard)")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as ex:
        raise ValueError(f"feed is not well-formed XML ({ex})") from None
    items = []
    for it in root.iter("item"):
        guid = _text(it, "guid") or _text(it, "link") or hashlib.sha256(ET.tostring(it)).hexdigest()[:24]
        items.append({"guid": guid, "title": _text(it, "title"), "link": _safe_link(_text(it, "link")),
                      "author": _text(it, "author"), "pub_date": _text(it, "pubDate"), "label": LABEL,
                      "source": "SACHET (NDMA) public CAP RSS feed"})
    return items


def fetch(feed_url: str, http_get: Callable = _httpx_get, etag: Optional[str] = None) -> dict:
    """http_get(url, headers) -> (status, headers, text). 304 -> unchanged. Raises ValueError on any other failure."""
    headers = {"Accept": "application/rss+xml, application/xml, text/xml"}
    if etag:
        headers["If-None-Match"] = etag
    status, resp_headers, text = http_get(feed_url, headers)
    resp_headers = {str(k).lower(): v for k, v in (resp_headers or {}).items()}
    if status == 304:
        return {"unchanged": True, "etag": etag, "items": []}
    if status != 200:
        raise ValueError(f"SACHET feed answered HTTP {status}")
    return {"unchanged": False, "etag": resp_headers.get("etag"), "items": parse(text or "")}


def refresh(store: OpsStore, feed_url: str, http_get: Callable = _httpx_get) -> dict:
    meta_id = hashlib.sha256(feed_url.encode()).hexdigest()[:16]
    meta = store.get(META, meta_id) or {}
    out = fetch(feed_url, http_get, etag=meta.get("etag"))
    now = iso(utc_now())
    stored = 0
    with store.transaction():
        for item in out["items"]:
            key = hashlib.sha256(item["guid"].encode()).hexdigest()[:24]
            old = store.get(COLL, key)
            if old is None or any(old.get(k) != v for k, v in item.items()):
                store.put(COLL, key, {**item, "fetched_at": now, "feed": meta_id}, actor="sachet", action="fetch")
                stored += 1
        store.put(META, meta_id, {"feed_url": feed_url, "etag": out["etag"] if not out["unchanged"] else meta.get("etag"),
                                  "checked_at": now}, actor="sachet", action="check")
    return {"unchanged": out["unchanged"], "stored": stored, "checked_at": now}


def list_cached(store: OpsStore) -> list[dict]:
    keys = ("guid", "title", "link", "author", "pub_date", "label", "source", "fetched_at")
    return [{k: d.get(k) for k in keys} for d in store.list(COLL)]
