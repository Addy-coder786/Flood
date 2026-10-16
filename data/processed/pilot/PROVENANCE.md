# Processed pilot dataset — Hindmata / Dadar pilot

Built 2026-09-08T18:35:40+00:00 by `floodnet.data.build_pilot`. bbox (lon/lat) (72.835, 19.01, 72.855, 19.03), margin 150 m, grid 244x255 @ 10.0 m (EPSG:32643).

Counts: grid_nx=244, grid_ny=255, res_m=10.0, nodes=1233, edges=1134, outfalls=116, contours=2678, road_segments=2978, road_nodes=2321, building_cells=13320, dem_flagged_cells=407, hotspots=21, hotspots_active=5, scenarios=4

| Item | Tag | Source | Note |
|---|---|---|---|
| Drainage geometry, lengths, shapes, W/H, inverts, status | **REAL** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | layer 7 Storm Water Drains + layer 6 Storm Water Manholes; EPSG:4326 -> 32643 via pyproj; clipped to pilot bbox + 150 m; status filter = Existing only; elevations mTHD (Town Hall Datum), offset to MSL UNVERIFIED |
| Manhole ground levels | **REAL** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | GROUND_LEV from layer 6, mTHD |
| Node inverts | **ESTIMATED** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | rule: min of US_INVERT/DS_INVERT of connected conduits (REAL inputs) |
| Outfalls | **ESTIMATED** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | out-degree-0 nodes of clipped graph = outfall; 19 of 116 are clip-boundary outfalls (conduit continues outside bbox); no tide/tailwater attribute in source |
| Conduit slope | **ESTIMATED** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | (US_INVERT-DS_INVERT)/CONDUIT_LE from REAL inverts; floored at 0.0001 for 18 flat/adverse conduits |
| Manning n | **ESTIMATED** | Chow (1959) Open-Channel Hydraulics, Table 5-6 | Manning n = 0.013 assumed for all shapes (concrete, range 0.012-0.015); MCGM data has no material field |
| Conduit capacity | **ESTIMATED** | Manning full-bore formula | Q = A R^(2/3) S^(1/2)/n with REAL W/H/inverts and ESTIMATED n; CIRC R=D/4, RECT/OREC/ARCH treated as rectangle W x H |
| Manhole storage area | **ESTIMATED** | assumption | 1.5 m2 plan area per manhole (typical chamber size); not in MCGM data |
| Inlet capacity | **ESTIMATED** | assumption | 0.05 m3/s per node, order-of-magnitude for a kerb inlet; not in MCGM data |
| Terrain z | **REAL** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | MCGM Contour_20CM (layer 301, 2678 polylines densified to 334852 pts @ 5.0 m) + 1233 manhole GROUND_LEV points, scipy griddata linear, nearest-fill outside hull; elevations mTHD (Town Hall Datum), MSL offset UNVERIFIED |
| Impervious fraction | **ESTIMATED** | floodnet.data.osm rule on OSM geometry | building footprint 1.0; within 6 m of an OSM road centreline 0.95; elsewhere 0.6 (assumed default for dense Mumbai urban fabric, not calibrated) |
| Building mask | **REAL** | OpenStreetMap contributors, ODbL 1.0 (Overpass API) | cell centroid inside an OSM building footprint (contains_xy) |
| Roads | **REAL** | OpenStreetMap contributors, ODbL 1.0 (Overpass API) | highway ways + building ways/relations (outer ways) in pilot bbox + margin; geometry as mapped by volunteers, completeness not audited |
| Flooding hotspots | **REAL** | MCGM ArcGIS REST MCGMGIS_Departments_Master_All_Layers, snapshot 2026-09-08 | layer 344 Flooding Spots (MCGM-designated chronic flooding locations); names with '(Delete)' or '(Tackled)' flagged inactive; DEPTH/STRETCH are MCGM free-text attributes |
| Scenario `moderate` | **SYNTHETIC** | floodnet.data.scenarios | design-style constant storm, invented for testing; not an observed event |
| Scenario `heavy` | **SYNTHETIC** | floodnet.data.scenarios | design-style constant storm, invented for testing; not an observed event |
| Scenario `cloudburst` | **SYNTHETIC** | floodnet.data.scenarios | Gaussian pulse peak 120 mm/h, sigma 15 min, centred at 45 min; invented stress test |
| Scenario `july2005` | **REAL** | Chitale Committee report (Fact Finding Committee on Mumbai Floods), section 2.9.6 | IMD Santacruz manual measurement, 26 July 2005; peak 3-h window 14.30-17.30 IST = 100.2, 190.3, 90.3 mm; hourly resolution only (held constant within each hour); Santacruz is ~10 km north of the Dadar pilot - local intensity at the pilot is not known |

Datum caveat: all elevations are mTHD (Town Hall Datum); the offset to MSL is UNVERIFIED. MCGM data: credit MCGM / Esri India, no explicit open licence. OSM: (c) OpenStreetMap contributors, ODbL.
