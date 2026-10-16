"""3D place labels: OSM names are copied, tiered, clipped to the DEM and de-duplicated -- never invented."""
from floodnet.data.place_labels import DEM_BBOX, build


def _node(i, lon, lat, **tags):
    return {"type": "node", "id": i, "lon": lon, "lat": lat, "tags": tags}


def test_tiers_clip_and_dedupe_without_inventing_names():
    mid_lon, mid_lat = (DEM_BBOX[0] + DEM_BBOX[2]) / 2, (DEM_BBOX[1] + DEM_BBOX[3]) / 2
    raw = {"osm3s": {"timestamp_osm_base": "2026-09-21T00:00:00Z"}, "elements": [
        _node(1, mid_lon, mid_lat, place="suburb", name="Dadar West"),
        _node(2, mid_lon + 0.004, mid_lat, place="neighbourhood", name="Dadar Hindu Colony"),
        _node(3, mid_lon + 0.0041, mid_lat, place="locality", name="Hindu Colony"),        # ~10 m away: same place
        _node(4, mid_lon, mid_lat + 0.003, railway="station", name="Dadar"),
        _node(5, mid_lon, mid_lat - 0.003, public_transport="station", name="Asiad Bus Stand"),  # not rail: dropped
        _node(6, mid_lon - 0.003, mid_lat, leisure="park", name="hoppers ground"),          # lower-case: dropped
        _node(7, DEM_BBOX[2] + 0.01, mid_lat, place="suburb", name="Wadala"),                # outside the DEM
        _node(8, mid_lon + 0.002, mid_lat + 0.002, leisure="park", name="Garden A, Five Gardens"),
        _node(9, mid_lon + 0.0022, mid_lat + 0.002, leisure="park", name="Garden B, Five Gardens"),
    ]}
    out = build(raw)
    names = [l["name"] for l in out["labels"]]
    assert names[0] == "Dadar West" and out["labels"][0]["tier"] == "primary"
    assert "Dadar Hindu Colony" in names and "Hindu Colony" not in names
    assert "Dadar" in names and "Asiad Bus Stand" not in names
    assert "hoppers ground" not in names and "Wadala" not in names
    assert "Five Gardens" in names                                         # the one documented derived name
    assert out["provenance"]["licence"] == "ODbL 1.0" and out["provenance"]["osm_timestamp"]
    assert all(l["osm"] for l in out["labels"])
