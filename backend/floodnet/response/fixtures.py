"""Demo response resources for the Hindmata / Dadar pilot.

LOCATIONS ARE REAL: settlements sit on active MCGM Flooding Spots (layer 344, data/raw/mcgm_gis/
flooding_spots_pilot.json, centroid of the MCGM polygon); shelters and team bases are OpenStreetMap buildings
(data/raw/osm/pilot_osm.json, centroid of the way; (c) OpenStreetMap contributors, ODbL).

EVERY NUMBER IS SIMULATED unless its provenance says otherwise: people needing help, priorities, capacities,
occupancy, team sizes and on-site times are invented for the demonstration and are not emergency-service data.
The one exception is marked: MCGM records AFFECT_POPULATION=90 / AFFECTED_HUT=30 for Vaccharaj Lane.

No real shelter designation is implied: a school or hall is used here only as a plausible location.

`required_capabilities` (D-34) names what a team must be able to do before it is sent to a settlement; the demo
requires only "evacuation", which both teams have, so the demo plan is the same as before capability matching.
"""
from __future__ import annotations

PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
PRIORITY_RULE = ("Declared priority first (critical > high > medium > low), then larger remaining need. The "
                 "declaration is an operator judgement of human risk, urgency and access, never flood depth alone.")

# ASSUMED on-site times (minutes); not measured.
TEAM_PICKUP_MIN = 2.0
LOAD_MIN = 10.0
UNLOAD_MIN = 5.0

MCGM = "MCGM Flooding Spots layer 344 (snapshot 2026-09-08), polygon centroid"
OSM = "OpenStreetMap way centroid, (c) OpenStreetMap contributors, ODbL"

SETTLEMENTS = [
    {"id": "S1", "name": "Dadar TT / Khodad Circle", "lonlat": [72.848752, 19.017141],
     "location_provenance": {"tag": "REAL", "source": MCGM, "ref": "OBJECTID 359"},
     "people_needing_help": 60, "priority": "critical",
     "priority_reason": "SIMULATED: low-lying junction, residents with limited mobility reported",
     "need": "evacuation to shelter", "required_capabilities": ["evacuation"], "numbers_provenance": {"tag": "SIMULATED"}},
    {"id": "S2", "name": "Vaccharaj Lane, Matunga East", "lonlat": [72.852454, 19.027101],
     "location_provenance": {"tag": "REAL", "source": MCGM, "ref": "OBJECTID 373"},
     "people_needing_help": 90, "priority": "high",
     "priority_reason": "SIMULATED declaration; MCGM attribute lists 30 affected huts",
     "need": "evacuation to shelter", "required_capabilities": ["evacuation"],
     "numbers_provenance": {"tag": "REAL", "source": "MCGM attributes AFFECT_POPULATION=90, AFFECTED_HUT=30",
                            "note": "an MCGM planning attribute, not a live headcount"}},
    {"id": "S3", "name": "Shakkar Panchayat, R.A. Kidwai Road", "lonlat": [72.854410, 19.011698],
     "location_provenance": {"tag": "REAL", "source": MCGM, "ref": "OBJECTID 2033"},
     "people_needing_help": 40, "priority": "medium", "priority_reason": "SIMULATED",
     "need": "evacuation to shelter", "required_capabilities": ["evacuation"], "numbers_provenance": {"tag": "SIMULATED"}},
    {"id": "S4", "name": "Dadar Railway Station area", "lonlat": [72.843691, 19.017613],
     "location_provenance": {"tag": "REAL", "source": MCGM, "ref": "OBJECTID 2035"},
     "people_needing_help": 25, "priority": "low", "priority_reason": "SIMULATED: stranded commuters",
     "need": "evacuation to shelter", "required_capabilities": ["evacuation"], "numbers_provenance": {"tag": "SIMULATED"}},
]

SHELTERS = [
    {"id": "SH1", "name": "Sharda Mangal Karyalaya (community hall)", "lonlat": [72.846461, 19.013704],
     "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/116072869"},
     "total_capacity": 80, "occupancy": 20, "status": "open", "numbers_provenance": {"tag": "SIMULATED"}},
    {"id": "SH2", "name": "IES V N Sule Guruji school", "lonlat": [72.848666, 19.021967],
     "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/685548839"},
     "total_capacity": 150, "occupancy": 0, "status": "open", "numbers_provenance": {"tag": "SIMULATED"}},
]

TEAMS = [
    {"id": "T1", "name": "Fire rescue team 1", "base": "Dadar Fire Brigade", "lonlat": [72.845747, 19.014145],
     "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/116072855"},
     "capabilities": ["evacuation", "water rescue"], "members": 6, "numbers_provenance": {"tag": "SIMULATED"}},
    {"id": "T2", "name": "Police rescue team 1", "base": "Dadar Police Station", "lonlat": [72.835465, 19.015061],
     "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/354066127"},
     "capabilities": ["evacuation"], "members": 4, "numbers_provenance": {"tag": "SIMULATED"}},
]

VEHICLES = [
    {"id": "V1", "name": "Fire brigade truck", "type": "truck", "passenger_capacity": 25,
     "lonlat": [72.845747, 19.014145], "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/116072855"},
     "numbers_provenance": {"tag": "SIMULATED"}},
    {"id": "V2", "name": "Police truck", "type": "truck", "passenger_capacity": 20,
     "lonlat": [72.835465, 19.015061], "location_provenance": {"tag": "REAL", "source": OSM, "ref": "way/354066127"},
     "numbers_provenance": {"tag": "SIMULATED"}},
]


def demo_resources() -> dict:
    return {"tag": "SIMULATED demo scenario on REAL locations", "priority_rule": PRIORITY_RULE,
            "assumed_minutes": {"team_pickup": TEAM_PICKUP_MIN, "load_at_settlement": LOAD_MIN,
                                "unload_at_shelter": UNLOAD_MIN},
            "settlements": SETTLEMENTS, "shelters": SHELTERS, "teams": TEAMS, "vehicles": VEHICLES}
