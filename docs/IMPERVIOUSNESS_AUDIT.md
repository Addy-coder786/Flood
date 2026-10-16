# Imperviousness data audit

**Status: investigated, real citable improvement identified, NOT implemented this pass.** Documented here so
the next pass can implement it directly rather than re-investigate.

## 1. Current method

`backend/floodnet/data/osm.py::impervious_fraction()` (called once, at pilot-build time, from
`floodnet/data/build_pilot.py` — not recomputed per request):

```python
IMPERV_BUILDING, IMPERV_ROAD, IMPERV_DEFAULT = 1.0, 0.95, 0.6
```

Rule: OSM building footprint → 1.0; within 6 m of an OSM road centreline → 0.95; elsewhere → a flat **0.6
default for dense Mumbai urban fabric, explicitly labelled "not calibrated"** (provenance tag `ESTIMATED`).

## 2. What was investigated

Verified live (2026-09-09) against the same real MCGM ArcGIS REST service already used for the drainage
network (`data/raw/mcgm_gis/PROVENANCE.md`), layer 281 ("Landuse"), bbox-filtered to the actual Hindmata/Dadar
pilot: **400 real features returned**, 22 distinct `TYPE` values present, e.g. Industrial Area, Bus Depot,
Storage Area, Parking Area, Slum Pockets, Educational Facility, Park, Play grounds, Open Space, Water bodies,
Cemetery, Sport Complex.

**Important limitation confirmed, not assumed:** this layer only covers named special/institutional parcels
(grounds, depots, institutions, named gardens) — it is **not** a complete land-use zoning layer. Ordinary
residential/commercial street blocks in the pilot are not covered by it, so it can only ever refine
imperviousness for the specific parcels it covers, never replace the flat 0.6 default everywhere.

## 3. Citable coefficients found

**USDA NRCS TR-55 (1986), *Urban Hydrology for Small Watersheds*** — a standard, globally-cited urban
hydrology reference. Verified (2026-09-09) entries: **Commercial and business: 85% impervious. Industrial:
72% impervious. Residential ≤1/8 acre (townhouses): 65% impervious.**

Of the 22 real TYPE values present in the pilot, exactly **one** maps cleanly and directly to a TR-55 line
item with no analogy required: **`Industrial Area` → 0.72**.

`Bus Depot` / `Storage Area` / `Parking Area` could plausibly be treated as "commercial and business" (0.85)
by analogy, but that mapping was not found as a direct citation this pass — it is an inference, not a sourced
number. Per this project's rule against inventing scientific values, **that analogy was deliberately not
used.** No citable numeric source was found for Park / Play grounds / Open Space / Cemetery / Water bodies /
Educational Facility / Sport Complex — these are left unmapped.

## 4. Why this was not implemented this pass

`impervious_fraction()` output is baked into the cached pilot snapshot (`data/processed/pilot/`) at build
time, not recomputed per request. Changing it means: (a) writing a new MCGM-layer-281 fetch (following the
existing `data/raw/mcgm_gis/` snapshot pattern), (b) rasterising the real Industrial Area polygons onto the
grid (same pattern as `building_mask`), (c) re-running the full pilot build, and (d) re-validating the entire
test suite plus mass-balance/physics output against the new terrain array, since imperviousness feeds runoff
generation project-wide. Given the real-world impact is small (Industrial Area covers a small fraction of the
62,220-cell pilot grid, and the coefficient only moves 0.6→0.72 for those cells), the engineering cost of a
full pilot rebuild + exhaustive re-validation was judged not worth rushing inside this already-large pass.
Per the explicit project rule ("if no defensible mapping is possible within the current time budget, leave
the existing method intact and document the limitation") — the mapping ­*is* defensible for one category, but
implementing it safely needs a dedicated pass with its own validation, not a rushed addition here.

## 5. Ready-to-implement mapping for the next pass

```python
# Additive precedence, applied BEFORE the existing road-buffer/building overrides (which still win where
# they overlap an Industrial Area polygon, e.g. a warehouse building inside an industrial parcel stays 1.0):
IMPERV_INDUSTRIAL = 0.72   # USDA NRCS TR-55 (1986), "Industrial" land use, cited directly, not an analogy
# Rasterise MCGM ArcGIS layer 281 (Landuse) polygons where TYPE == 'Industrial Area', pilot bbox + margin,
# same `contains_xy` rasterisation pattern already used by `building_mask`/`impervious_fraction`'s road buffer.
```

No other TYPE category should be reclassified without first finding a direct (non-analogy) citation for it.
