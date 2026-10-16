"""Geographic place labels for the 3D terrain view -- REAL OpenStreetMap names, nothing invented.

The pilot road extract (data/raw/osm/pilot_osm.json) carries roads and buildings but no `place=*` nodes, so locality
names (Dadar West, Matunga East, Shivaji Park ...) are fetched once from the OSM Overpass API with the query below
and written, with their OSM ids, to frontend-react/src/lib/terrain3d/place_labels.json. Road and junction labels are
NOT stored here: the frontend takes them from the road network it already loads (GET /api/roads).

Only features inside the simulation DEM (with a small inset) are kept, so every label sits on the modelled terrain.
Names are copied verbatim from OSM; the one derived text is "Five Gardens", the common suffix of the five OSM
features named "Garden A, Five Gardens" ... "Garden E, Five Gardens" (placed at their mean position).

Data (c) OpenStreetMap contributors, ODbL 1.0 -- the same source and licence as the pilot road network.

Run (from backend/):  .venv/Scripts/python -m floodnet.data.place_labels            (fetches from Overpass)
                      .venv/Scripts/python -m floodnet.data.place_labels raw.json   (re-tier a saved response)
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import config

OUT = config.REPO_DIR / "frontend-react" / "src" / "lib" / "terrain3d" / "place_labels.json"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# DEM bbox (GET /api/terrain/dem -> bbox_lonlat) padded by ~500 m for the query; features are clipped to the DEM below.
QUERY_BBOX = (19.0034, 72.8283, 19.0367, 72.8618)            # south, west, north, east
DEM_BBOX = (72.83329779322314, 19.00839549961619, 72.85676394891016, 19.031695399908493)   # west, south, east, north
INSET_DEG = 0.0004                                            # ~40 m: no labels hanging off the terrain edge

QUERY = """[out:json][timeout:60];
(
  nwr["place"~"^(suburb|neighbourhood|quarter|locality|village|hamlet)$"]["name"]({s},{w},{n},{e});
  nwr["leisure"~"^(park|garden|stadium|pitch)$"]["name"]({s},{w},{n},{e});
  nwr["railway"~"^(station|halt)$"]["name"]({s},{w},{n},{e});
  nwr["public_transport"="station"]["name"]({s},{w},{n},{e});
);
out center tags;""".format(s=QUERY_BBOX[0], w=QUERY_BBOX[1], n=QUERY_BBOX[2], e=QUERY_BBOX[3])

# tier -> drawing class in the frontend; rank orders label placement (higher wins a collision)
TIERS = {
    "suburb": ("primary", 100),
    "neighbourhood": ("locality", 80), "quarter": ("locality", 80),
    "locality": ("locality", 60), "village": ("locality", 60), "hamlet": ("locality", 55),
    "station": ("landmark", 58), "halt": ("landmark", 50),
    "park": ("landmark", 48), "stadium": ("landmark", 46),
}
SKIP_NAMES = {"local park"}                                   # generic, not a place name
DEDUPE_M = 300.0                                              # near-duplicate names closer than this are one label


def _inside(lon: float, lat: float) -> bool:
    w, s, e, n = DEM_BBOX
    return w + INSET_DEG <= lon <= e - INSET_DEG and s + INSET_DEG <= lat <= n - INSET_DEG


def _metres(a: dict, b: dict) -> float:
    import math
    return math.hypot((a["lon"] - b["lon"]) * 105_300.0, (a["lat"] - b["lat"]) * 110_700.0)   # ~19 N


def _dedupe(ranked: list[dict]) -> list[dict]:
    """One label per place: drop a lower-ranked label within DEDUPE_M whose name is contained in, or spelled almost
    like, a kept one ("Hindu Colony" vs "Dadar Hindu Colony"; "Maheshwari udyan" vs "Maheshwari Udyaan")."""
    from difflib import SequenceMatcher
    norm = lambda n: "".join(ch for ch in n.lower() if ch.isalnum())  # noqa: E731
    kept: list[dict] = []
    for item in ranked:
        a = norm(item["name"])
        dup = next((k for k in kept if _metres(k, item) < DEDUPE_M and (
            a in norm(k["name"]) or norm(k["name"]) in a or SequenceMatcher(None, a, norm(k["name"])).ratio() > 0.8)), None)
        if dup is not None:
            dup["osm"].extend(item["osm"])
        else:
            kept.append(item)
    return kept


def build(raw: dict) -> dict:
    best: dict[str, dict] = {}
    gardens = []
    for el in raw.get("elements", []):
        tags = el.get("tags", {})
        name = (tags.get("name:en") or tags.get("name") or "").strip()
        c = el.get("center") or {"lat": el.get("lat"), "lon": el.get("lon")}
        if not name or c.get("lat") is None or name.lower() in SKIP_NAMES:
            continue
        # railway stations only (a bus stand tagged public_transport=station is not a map-level landmark);
        # gardens, pitches and names written in lower case are too minor for a locality map
        kind = tags.get("place") or tags.get("leisure") or tags.get("railway")
        if name.endswith(", Five Gardens") and _inside(c["lon"], c["lat"]):
            gardens.append((c["lon"], c["lat"], f"{el['type']}/{el['id']}"))
            continue
        if kind not in TIERS or not _inside(c["lon"], c["lat"]) or not name[0].isupper():
            continue
        tier, rank = TIERS[kind]
        item = {"name": name, "tier": tier, "kind": kind, "rank": rank, "lon": round(c["lon"], 6),
                "lat": round(c["lat"], 6), "osm": [f"{el['type']}/{el['id']}"]}
        key = name.lower().replace("'", "").replace(" ", "")
        prev = best.get(key)
        if prev is None or rank > prev["rank"]:
            item["osm"] = (prev["osm"] if prev else []) + item["osm"]
            best[key] = item
        else:
            prev["osm"].append(item["osm"][0])
    if len(gardens) >= 2:
        best["fivegardens"] = {"name": "Five Gardens", "tier": "landmark", "kind": "park", "rank": 47,
                               "lon": round(sum(g[0] for g in gardens) / len(gardens), 6),
                               "lat": round(sum(g[1] for g in gardens) / len(gardens), 6),
                               "osm": [g[2] for g in gardens]}
    labels = _dedupe(sorted(best.values(), key=lambda x: (-x["rank"], -len(x["name"]), x["name"])))
    return {
        "provenance": {
            "tag": "REAL", "source": "OpenStreetMap via Overpass API", "licence": "ODbL 1.0",
            "attribution": "(c) OpenStreetMap contributors",
            "osm_timestamp": raw.get("osm3s", {}).get("timestamp_osm_base"),
            "query": QUERY, "clipped_to_dem_bbox": DEM_BBOX, "inset_deg": INSET_DEG,
            "note": "Names verbatim from OSM, except 'Five Gardens' = common suffix of the five 'Garden A..E, Five "
                    "Gardens' features. Roads/junctions are labelled from GET /api/roads in the frontend.",
        },
        "labels": labels,
    }


def main(argv: list[str]) -> None:
    if len(argv) > 1:
        raw = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    else:
        import httpx
        r = httpx.post(OVERPASS_URL, data={"data": QUERY}, timeout=120,
                       headers={"User-Agent": "FloodNet/0.1 (research prototype)"})
        r.raise_for_status()
        raw = r.json()
    out = build(raw)
    OUT.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(out['labels'])} labels -> {OUT}")


if __name__ == "__main__":
    main(sys.argv)
