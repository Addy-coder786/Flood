"""Rainfall scenarios (5-min steps, 0-180 min).

SYNTHETIC: moderate, heavy, cloudburst (design-style storms, invented).
REAL:      july2005 - transcribed from the Chitale Committee report (Fact Finding Committee on Mumbai Floods, 2006),
           section 2.9.6 "Rainfall recorded by IMD at Santacruz by manual measurement on 26th July, 2005";
           peak 3-hour window 14.30-17.30: 100.2 / 190.3 / 90.3 mm (380.8 mm; the report itself cites "380 mm for
           3 hours between 14.30 PM to 17.30 PM", section 3.1). Hourly values held constant across each hour's 5-min steps.
"""
from __future__ import annotations

import numpy as np

from ..config import HORIZON_S, RAIN_DT_S
from ..contracts import RainfallScenario
from ..provenance import Provenance, Tag

T_S = np.arange(0, HORIZON_S + 1, RAIN_DT_S, dtype=float)      # 0..10800 s -> 37 steps

CHITALE_SANTACRUZ_26JUL2005_MM = [                              # (window, mm) verbatim, section 2.9.6
    ("08.30-11.30", 0.9), ("11.30-14.30", 18.4), ("14.30-15.30", 100.2), ("15.30-16.30", 190.3),
    ("16.30-17.30", 90.3), ("17.30-18.30", 100.4), ("18.30-19.30", 95.0), ("19.30-20.30", 72.2),
    ("20.30-21.30", 60.2), ("21.30-22.30", 22.5), ("22.30-23.30", 18.4), ("23.30-00.30", 40.0),
]
PEAK3H = [100.2, 190.3, 90.3]                                   # 14.30-17.30 IST


def _block(intensity: float, minutes: float) -> np.ndarray:
    v = np.zeros_like(T_S)
    v[T_S < minutes * 60] = intensity
    return v


def scenarios() -> dict[str, RainfallScenario]:
    syn = lambda note: Provenance(Tag.SYNTHETIC, "floodnet.data.scenarios", note)
    out = {}
    out["moderate"] = RainfallScenario(
        id="moderate", name="Moderate steady rain (20 mm/h x 2 h)", t_s=T_S, intensity_mm_h=_block(20.0, 120),
        provenance=syn("design-style constant storm, invented for testing; not an observed event"),
        description="20 mm/h for 120 min then dry. SYNTHETIC design storm.")
    out["heavy"] = RainfallScenario(
        id="heavy", name="Heavy steady rain (50 mm/h x 2 h)", t_s=T_S, intensity_mm_h=_block(50.0, 120),
        provenance=syn("design-style constant storm, invented for testing; not an observed event"),
        description="50 mm/h for 120 min then dry. SYNTHETIC design storm.")
    tmin = T_S / 60.0
    pulse = 120.0 * np.exp(-0.5 * ((tmin - 45.0) / 15.0) ** 2)
    pulse[pulse < 0.5] = 0.0
    out["cloudburst"] = RainfallScenario(
        id="cloudburst", name="Cloudburst pulse (peak 120 mm/h)", t_s=T_S, intensity_mm_h=pulse,
        provenance=syn("Gaussian pulse peak 120 mm/h, sigma 15 min, centred at 45 min; invented stress test"),
        description="Gaussian pulse, peak 120 mm/h at 45 min, sigma 15 min. SYNTHETIC stress test.")
    j = np.zeros_like(T_S)
    for h, mm in enumerate(PEAK3H):
        j[(tmin >= 60 * h) & (tmin < 60 * (h + 1))] = mm      # mm in 1 h == mm/h
    out["july2005"] = RainfallScenario(
        id="july2005", name="26 July 2005 replay (Santacruz gauge, hourly)", t_s=T_S, intensity_mm_h=j,
        provenance=Provenance(Tag.REAL, "Chitale Committee report (Fact Finding Committee on Mumbai Floods), section 2.9.6",
                              "IMD Santacruz manual measurement, 26 July 2005; peak 3-h window 14.30-17.30 IST = "
                              "100.2, 190.3, 90.3 mm; hourly resolution only (held constant within each hour); "
                              "Santacruz is ~10 km north of the Dadar pilot - local intensity at the pilot is not known"),
        description="Peak 3 hours (14.30-17.30 IST) of the 26 July 2005 storm as measured at IMD Santacruz, "
                    "transcribed from Chitale Committee report sec. 2.9.6. Hourly gauge values; total 380.8 mm.")
    return out
