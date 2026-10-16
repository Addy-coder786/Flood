"""Data provenance tags. EVERY dataset, parameter table and API payload carries one.

REAL          - observed/official data used as published (e.g. MCGM conduit inverts).
ESTIMATED     - derived from real data by a stated rule or a cited table (e.g. Manning n by shape).
SYNTHETIC     - invented for a scenario or stress test; never presented as observed.
DEMONSTRATION - placeholder that exists only so the UI can render; must never reach a scientific claim.
NWP           - numerical-weather-prediction model output (NOT a radar nowcast).
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from enum import Enum


class Tag(str, Enum):
    REAL = "REAL"
    ESTIMATED = "ESTIMATED"
    SYNTHETIC = "SYNTHETIC"
    DEMONSTRATION = "DEMONSTRATION"
    NWP = "NWP"


@dataclass(frozen=True)
class Provenance:
    tag: Tag
    source: str          # e.g. "MCGM ArcGIS REST layer 7 (Storm Water Drains), snapshot 2026-09-08"
    note: str = ""       # e.g. "Manning n assumed 0.013 for concrete box sections (Chow 1959)"

    def to_dict(self) -> dict:
        d = asdict(self); d["tag"] = self.tag.value; return d


MCGM_SNAPSHOT = "MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08"
