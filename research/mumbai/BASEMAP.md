# Mumbai Base Map Layers — Evidence Report (Research Worker 5)

Scope: roads (routable), buildings, land cover/imperviousness, ward boundaries for a bounded Mumbai pilot.
Time-boxed evidence gathering only — no project decisions made here.

## Summary recommendation

**Roads + buildings: OpenStreetMap (via Overpass API for the pilot bbox, or Geofabrik `india-latest.osm.pbf` as a fallback/offline extract).**
**Ward boundaries: Bharatlas / OpenCity ward GeoJSON (BMC's 24 wards), cross-check against BMC's own ArcGIS REST endpoints if time allows.**
**Land cover / imperviousness: derive impervious proxy from OSM building + road + landuse polygons (best fit at street scale); use ESA WorldCover 10 m (CC BY 4.0) only as a coarse cross-check/fallback, not as the primary imperviousness signal in a dense city.**

Rationale and obligations below.

---

## Sources table

| Layer | Source | Format | Licence | Access method | VERIFIED? |
|---|---|---|---|---|---|
| Roads (named, routable) | OpenStreetMap (Overpass API) | JSON/XML (ways+nodes, shared topology) | ODbL 1.0 | Live Overpass query on bbox | YES — live query executed, see below |
| Roads (bulk/offline) | OpenStreetMap (Geofabrik extract) | `.osm.pbf` | ODbL 1.0 | Bulk download, no registration | YES — page fetched, sizes confirmed |
| Roads (govt, India) | Bhuvan (ISRO/NRSC) | WMS / portal, some downloads | Not confirmed as open/reusable | Requires registration for most products | PARTIAL — portal exists, licence/approval terms for a routable road layer NOT confirmed |
| Buildings | OpenStreetMap | vector polygons | ODbL 1.0 | Overpass / Geofabrik extract | Same as roads |
| Buildings (alt) | Microsoft Global ML Building Footprints | GeoJSON (quadkey-partitioned) | ODbL 1.0 (some sources say CDLA-Permissive-2.0 — conflicting) | Download from Planetary Computer / GitHub, no registration | PARTIAL — licence text conflicting between sources, not independently confirmed |
| Buildings (alt) | Google Open Buildings | CSV/GeoJSON, GEE FeatureCollection | CC BY 4.0 (also cited as ODbL v1.0 in some sources) | Google Earth Engine or direct download | PARTIAL — India/South Asia coverage confirmed by search, licence wording inconsistent across sources, not fetched directly |
| Buildings (alt, combined) | Overture Maps Foundation "buildings" theme | Parquet (GeoParquet) | ODbL (because it ingests OSM) | AWS Registry of Open Data / Azure, no registration | PARTIAL — theme licence confirmed via docs page, India-specific coverage NOT confirmed |
| Land cover | ESA WorldCover 2021 v200 | 10 m COG GeoTIFF, 3°x3° tiles | CC BY 4.0 | S3 (`s3://esa-worldcover`), AWS Open Data, GEE, or WorldCover downloader — no registration | YES — official pages confirm resolution, format, licence |
| Impervious/built-up | GHSL (JRC/EC) GHS-BUILT-S / GHS-BUILT-C | 10 m and 100 m GeoTIFF | Free/open (EC data policy) | human-settlement.emergency.copernicus.eu, GEE catalog | PARTIAL — resolution and open policy confirmed, exact licence text not fetched |
| Ward boundaries | Bharatlas (24 BMC wards) | Shapefile/GeoJSON/KML/Parquet | Listed as ODbL-1.0 for some cities, CC-BY-SA-4.0 for others (sourced from OpenCity/Oorvani Foundation) | Direct download, no registration | PARTIAL — page/listing seen, Mumbai's specific licence tag not individually confirmed (other cities on same site show ODbL-1.0 or CC-BY-SA-4.0) |
| Ward boundaries (alt) | BMC official ArcGIS REST services (`services8.arcgis.com/r6MmJtuWAzMawmJ8/...`, `mybmcid.mcgm.gov.in/server/rest/services`) | ArcGIS REST/JSON | UNSTATED | Public REST endpoint (per OpenCity explainer) | UNVERIFIED — endpoint not directly queried, licence/terms not seen |
| Ward boundaries (alt) | DataMeet `Municipal_Spatial_Data` GitHub repo | KML/GeoJSON | Repo-level (check GitHub repo licence file) | Direct GitHub download | UNVERIFIED — not opened directly |

---

## Roads — detail, including routing suitability

**Source: OpenStreetMap**, extracted either live via the Overpass API (good for a small pilot bbox) or as a bulk regional file via Geofabrik.

- **Overpass API (live query)**: no download/setup needed, ideal for a small bounding box during a hackathon. Trade-off: public Overpass instances (overpass-api.de) can be slow/rate-limited/queued under load — I hit one 504 Gateway Timeout on a first attempt before a smaller, tightened query succeeded. Not reliable for a live production demo without a fallback/cache; fine for a one-time data-prep step.
- **Geofabrik regional extract**: `india-latest.osm.pbf` — confirmed on `download.geofabrik.de/asia/india.html`, ~1.6 GB, updated to 2026-09-03, no registration required. Sub-regional zone extracts exist (e.g., central-zone ~331 MB) but Geofabrik's India zones are large multi-state regions, not city-level — still far smaller than the world file and easy to clip to a Mumbai bbox with `osmium extract`. Trade-off vs. Overpass: larger one-time download but fully offline/reproducible and not subject to live API rate limits.
- **Licence**: ODbL 1.0. Confirmed via OpenStreetMap Foundation's Licence/Attribution Guidelines page. For a public demo we must display attribution ("© OpenStreetMap contributors" or similar, linked to openstreetmap.org/copyright) unless the map shown has fewer than 100 features or covers under 10,000 m² — a full Mumbai pilot map will exceed both thresholds, so attribution is mandatory. If we redistribute a **derived database** (e.g., a processed routing graph) publicly, ODbL's share-alike clause requires making that derivative database available under ODbL too, or keeping the derived data itself non-public (only the rendered/produced work shown, with attribution) to avoid the share-alike obligation — this distinction (Produced Work vs. Derivative Database) matters and should be handled deliberately by whoever ships the demo.
- **Coverage/completeness check (VERIFIED, live)**: Overpass query for named highway ways in a ~1 km × 1.1 km bbox near Bandra (lat 19.075–19.085, lon 72.875–72.885) returned **78 named highway ways**. This confirms non-trivial named-road density in a dense Mumbai neighborhood, but is a single small-area sample — not a city-wide completeness statistic. General literature found (HeiGIT, Kontur, RMSI blog posts on OSM India road quality) indicates **attribute completeness is uneven**: e.g., one cited study found maxspeed tag completeness on a Hyderabad–Mumbai route at only ~1.9% vs. ~43% in Germany. Street *names* and *geometry* are generally present in dense urban Mumbai (consistent with the 78-way sample), but secondary attributes (speed limits, lane counts, one-way accuracy) should be assumed sparse and may need manual QA for the pilot bbox.
- **Routing suitability — genuinely routable, not display-only.** OSM's `highway=*` ways share `node` IDs at intersections by construction (the data model itself topologically connects ways at shared nodes), which is exactly the graph structure required by standard routing engines (OSRM, GraphHopper, Valhalla, pgRouting) — this is *how* those engines already build road graphs from OSM data worldwide, including in India (confirmed via search results referencing OSRM routing use over OSM data, and RMSI's explicit "geometry connectivity" QA work on India's high-priority road network in OSM). I did not build a full pilot-bbox connectivity graph myself, so I have not personally verified zero broken/disconnected segments in the exact Mumbai pilot area — that should be spot-checked once the actual extract is made (e.g., load into OSRM/pgRouting and check the largest connected component covers the pilot bbox).
- **Indian government road dataset**: Bhuvan (ISRO/NRSC) portal exists (`bhuvan.nrsc.gov.in`) with a documented API and various thematic layers, but **most datasets require registration and I could not confirm, within this time-box, that a routable/named road layer is available without approval**, nor its licence terms for reuse in a public demo. Treat as **UNVERIFIED — could not confirm** obtainability without approval; not recommended as the primary road source for a 12-hour build given this uncertainty, versus OSM's confirmed no-registration access.

---

## Buildings

- **Primary recommendation: OSM building polygons**, extracted alongside roads via the same Overpass/Geofabrik pipeline — same licence (ODbL) and access method already verified above, and keeps the pilot to a single data pipeline. Coverage in Mumbai was not separately building-counted in my Overpass test (only roads were queried); this should be spot-checked before committing (a simple `way["building"]` count over the same bbox would confirm).
- **Alternative: Microsoft Global ML Building Footprints.** Confirmed via GitHub repo (`microsoft/GlobalMLBuildingFootprints`) and Microsoft's own announcement of large India contributions (search result cites ~110M India edits/footprints added March 2024 — this specific figure came from a secondary summary, not a primary Microsoft source I fetched directly, so treat the number as **UNVERIFIED**, though the general claim of substantial India coverage is plausible and repeated across sources). Licence is inconsistently described across sources (CDLA-Permissive-2.0 in some, ODbL in others) — **this inconsistency itself is a flag**: confirm the exact licence file on the actual dataset release before using it in a public demo.
- **Alternative: Google Open Buildings.** Confirmed South Asia/India coverage claim and CC BY 4.0 licensing via search (also described elsewhere as ODbL v1.0 — same inconsistency issue as Microsoft's set). Access via Google Earth Engine or direct download, no registration barrier found. Only supplies footprint + confidence + area (no height/attributes), same as OSM's basic polygons.
- **Alternative: Overture Maps "buildings" theme** — combines OSM + Microsoft + Google building sources into GeoParquet, licensed ODbL because it ingests OSM. Available via AWS Registry of Open Data, no registration. India-specific coverage was not directly confirmed in the time available.
- Given the 12-hour constraint, pulling buildings from the **same OSM extract** as roads (rather than standing up a second pipeline for Microsoft/Google/Overture data) is the lowest-effort path and keeps a single, well-understood licence (ODbL) to manage.

---

## Land cover / imperviousness

Two real options, with an honest resolution caveat:

1. **ESA WorldCover 2021 v200** — 10 m resolution, global, **CC BY 4.0** (attribution only, no share-alike), Cloud-Optimized GeoTIFF, downloadable with no registration from AWS S3 (`s3://esa-worldcover`) or Google Earth Engine. Confirmed via ESA/VITO's own download and access pages. It has a "Built-up" class (and others: cropland, tree cover, water, etc.) that can be coarsely mapped to a runoff/imperviousness assumption (built-up ≈ high imperviousness, vegetation/water ≈ low).
   - **Honest limitation**: at 10 m resolution, a single pixel covers roughly a small building or a street segment plus adjoining ground — in a dense city like Mumbai this **cannot distinguish a paved road/roof from an adjacent open plot or narrow lane at the street-segment scale** needed for per-segment runoff generation. It is meaningful as a city-wide or ward-scale sanity check/coarse fallback, not as the primary input for street-level runoff modeling.
2. **GHSL (Global Human Settlement Layer), GHS-BUILT-S/GHS-BUILT-C** — offers both 100 m (older) and a newer 10 m sub-pixel built-up surface fraction product (2023 edition), free and openly published by the EU Joint Research Centre, accessible via the Copernicus GHSL portal and the Google Earth Engine catalog. The 10 m sub-pixel product is a genuine improvement (reported IoU 0.92 for built-up class at 10 m in cited literature) but I did not fetch/verify its exact licence text directly — treat licence as **PARTIAL/UNVERIFIED**, though EU JRC's stated policy is open/free.
3. **Recommended approach for the pilot: derive an imperviousness proxy directly from the OSM extract itself** — i.e., treat OSM `building=*` polygons and paved `highway=*`/`landuse=` (e.g., `landuse=commercial|industrial|residential` vs. `landuse=grass|forest`, `natural=water`) polygons as the imperviousness signal, since these are already being pulled for the roads/buildings layers, are at true vector/parcel resolution (not a 10 m pixel), and require no extra licence to manage (still ODbL, already covered). Use ESA WorldCover only as a coarse cross-check or gap-filler for areas where OSM landuse tagging is sparse.

---

## Boundaries

- **Bharatlas** lists a Mumbai ward map (24 BMC wards) downloadable as Shapefile/GeoJSON/KML/Parquet, sourced via OpenCity/Oorvani Foundation, no registration seen. Licence tagging on the Bharatlas site varies by city (some cities shown as ODbL-1.0, others CC-BY-SA-4.0) — **I did not open the Mumbai-specific page to confirm which licence tag applies to Mumbai**, so treat as PARTIAL/UNVERIFIED and confirm before use.
- **OpenCity** (`data.opencity.in`, `opencity.in`) also separately publishes Mumbai ward/Prabhag boundary data (56 Prabhags mentioned) with contact/office info — same provenance family as Bharatlas.
- **BMC's own ArcGIS REST endpoints** (`services8.arcgis.com/r6MmJtuWAzMawmJ8/ArcGIS/rest/services` and `mybmcid.mcgm.gov.in/server/rest/services`) were mentioned in an OpenCity explainer as a way governments expose GIS data publicly, but I did not query these endpoints directly or confirm their licence/terms of use — **UNVERIFIED**.
- **DataMeet `Municipal_Spatial_Data`** GitHub repo also carries Indian municipal ward data (KML/GeoJSON) — repo-level licence not opened/confirmed in this pass.
- For a 12-hour build, Bharatlas/OpenCity's ready-to-download GeoJSON is the lowest-effort option, but its exact licence for Mumbai specifically should be confirmed (a 30-second check of the actual download page) before shipping a public demo.

---

## Licence obligations for a public demo — what to honour

- **OSM (roads + buildings), ODbL 1.0**: Must display attribution ("© OpenStreetMap contributors", linked to openstreetmap.org/copyright) on any public map view, since the pilot will exceed the "under 100 features / under 10,000 m²" exemption. If a **derived database** (not just a rendered map) is redistributed publicly, share-alike applies — either publish that derivative under ODbL too, or restrict public exposure to the rendered/Produced Work only.
- **ESA WorldCover, CC BY 4.0**: Attribution required, no share-alike, no non-commercial restriction — straightforward to honour (credit ESA/WorldCover in a data-sources note).
- **Building footprint alternatives (Microsoft/Google/Overture)**: licence text is **inconsistent across secondary sources** (CDLA-Permissive-2.0 vs ODbL vs CC BY 4.0 depending on source cited) — this is a real trap. Before using any of these instead of/alongside OSM buildings, fetch the actual licence file from the dataset's own repo/release page rather than trusting a summary.
- **Ward boundaries (Bharatlas/OpenCity)**: licence varies by city on the same site (ODbL-1.0 vs CC-BY-SA-4.0 seen for different cities) — confirm Mumbai's specific tag; CC-BY-SA-4.0 carries a share-alike obligation similar in spirit to ODbL's.
- **No non-commercial or no-derivatives clauses were found** on any of the primary recommended sources (OSM/ODbL, WorldCover/CC BY 4.0) — good news for a hackathon demo that may later go further. The main traps are (a) forgetting attribution on OSM-derived rendered maps, (b) inadvertently redistributing a derived OSM database publicly without honoring share-alike, and (c) trusting an unverified licence claim for building-footprint alternatives.
- **Base-map tiles**: not separately investigated (out of this task's scope — this report covers vector layers: roads, buildings, land cover, boundaries — not basemap tile providers like Mapbox/OSM tile servers, whose tile-usage terms are a separate concern the routing/rendering team should check, e.g. OSM's own tile usage policy has strict rate/heavy-use limits unrelated to the ODbL data licence).

---

## What I could not verify (be honest)

- Exact building count from OSM for the Mumbai pilot bbox (only roads were counted in the live test).
- Whether Bhuvan/ISRO offers any road dataset obtainable **without** registration/approval, and its reuse licence — UNVERIFIED, treat as not viable for a 12-hour build.
- Which specific licence (CDLA-Permissive-2.0 vs ODbL vs CC BY 4.0) actually applies to the Microsoft and Google building-footprint datasets — sources conflict; not independently confirmed by opening the primary licence file.
- Exact licence tag on Bharatlas's Mumbai ward page specifically (site shows different licences per city; Mumbai's own page not opened).
- Terms of use / licence on BMC's own ArcGIS REST GIS endpoints — endpoint not queried, terms not read.
- City-wide (vs. single-neighbourhood) OSM road/building completeness statistics for Mumbai — only one small bbox (~1 km²) was sampled live (78 named highway ways); no systematic city-wide completeness audit was run given the time-box.
- Exact licence text for GHSL products (EC/JRC data policy is described as open/free by search results, but the specific licence document was not fetched).
- Full connectivity/topology validation of an actual Mumbai pilot-bbox road graph (e.g., loading into OSRM/pgRouting and checking for disconnected segments) — not performed; only the general OSM-topology-supports-routing argument is established.
