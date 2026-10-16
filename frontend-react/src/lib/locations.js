// Builds the searchable location index for the Route Planner's FROM/TO search fields.
//
// Two sources, both REAL data already available on the client -- no new backend endpoint, no fabricated
// results, no third-party geocoder:
//
//  1. PILOT_AREAS (frontend-react/src/components/LocationSelector/LocationSelector.jsx) -- the 4 hand-picked
//     landmarks already shown/used in Header's location selector. Reused verbatim (same coordinates), not
//     duplicated or re-derived.
//  2. Named OSM road segments from the `roads` FeatureCollection already fetched into FloodNetContext via
//     GET /api/roads (see backend/floodnet/data/osm.py -- geometry is REAL, (c) OpenStreetMap contributors,
//     ODbL 1.0; backend/floodnet/api/main.py::_seg_feature puts `name`/`highway` on every feature). A road
//     name can span many segments (OSM ways get split at intersections); segments sharing a name are
//     grouped into ONE search result whose point is the midpoint of that name's single longest segment --
//     a real point that lies on the real road centreline, never an invented/interpolated address.
//
// A third-party geocoder (Nominatim etc.) was deliberately NOT added: it would resolve queries anywhere on
// Earth, which would silently imply routing/simulation coverage far outside the Hindmata/Dadar pilot bbox
// where no real simulation data exists (see CLAUDE.md rule 1 "never fabricate" and rule 6). If broader
// coverage is ever wanted, it needs its own decision in docs/DECISIONS.md with a licence check first.

// The 4 hand-verified pilot landmarks, used by both Header's LocationSelector (the operational-area picker)
// and RoutePlanner's FROM/TO search below. Single source of truth so the two pickers can never drift apart.
export const PILOT_AREAS = [
  {
    id: 'hindmata',
    name: 'Hindmata Junction',
    zone: 'MCGM F/North Ward',
    status: 'ACTIVE PILOT',
    desc: 'Low-elevation natural bowl with high historical flood frequency.',
    coords: [72.8447, 19.0176],
  },
  {
    id: 'dadar-tt',
    name: 'Dadar TT Circle',
    zone: 'MCGM F/North Ward',
    status: 'ACTIVE PILOT',
    desc: 'Major transit corridor connecting Dr. B.A. Road and Tilak Bridge.',
    coords: [72.8465, 19.0205],
  },
  {
    id: 'parel-tt',
    name: 'Parel Junction',
    zone: 'MCGM F/South Ward (Adjacent)',
    status: 'IN PILOT BOUNDS',
    desc: 'Hospital district connector and railway underpass zone.',
    coords: [72.8415, 19.0085],
  },
  {
    id: 'matunga',
    name: 'Matunga East (King Circle)',
    zone: 'MCGM F/North Ward',
    status: 'ACTIVE PILOT',
    desc: 'Downstream receiving basin and chronic waterlogging hotspot.',
    coords: [72.8550, 19.0290],
  },
]

const HIGHWAY_LABELS = {
  trunk: 'Trunk road',
  primary: 'Primary road',
  secondary: 'Secondary road',
  tertiary: 'Tertiary road',
  residential: 'Residential road',
  living_street: 'Living street',
  service: 'Service road',
  unclassified: 'Road',
}

function highwayLabel(highway) {
  return HIGHWAY_LABELS[highway] || 'Road'
}

// Midpoint by cumulative distance along the polyline (not just the middle vertex index), so it stays a
// representative on-road point even when vertices are unevenly spaced.
function lineMidpoint(coords) {
  if (!coords || coords.length === 0) return null
  if (coords.length === 1) return coords[0]
  const dist = [0]
  for (let i = 1; i < coords.length; i++) {
    const [x1, y1] = coords[i - 1]
    const [x2, y2] = coords[i]
    dist.push(dist[i - 1] + Math.hypot(x2 - x1, y2 - y1))
  }
  const half = dist[dist.length - 1] / 2
  for (let i = 1; i < dist.length; i++) {
    if (dist[i] >= half) {
      const span = dist[i] - dist[i - 1]
      const frac = span === 0 ? 0 : (half - dist[i - 1]) / span
      const [x1, y1] = coords[i - 1]
      const [x2, y2] = coords[i]
      return [x1 + (x2 - x1) * frac, y1 + (y2 - y1) * frac]
    }
  }
  return coords[coords.length - 1]
}

/** roadsFeatureCollection: the GET /api/roads response already stored in FloodNetContext's `roads` state
 * (LineString features, properties {seg_id, name, highway, length_m, oneway}). landmarks: PILOT_AREAS
 * (imported by the caller so this module has no import-order coupling to LocationSelector). Returns []
 * while roads hasn't loaded yet -- callers must render "no results yet", never placeholder locations. */
export function buildLocationIndex(roadsFeatureCollection, landmarks = []) {
  const index = landmarks
    .filter((a) => Array.isArray(a.coords) && a.coords.length === 2)
    .map((a) => ({ id: `landmark:${a.id}`, name: a.name, context: a.zone || 'Pilot landmark', coords: a.coords, kind: 'landmark' }))

  const byName = new Map() // name -> { length, coords, highway, count }
  for (const f of roadsFeatureCollection?.features || []) {
    const name = (f.properties?.name || '').trim()
    if (!name) continue // unnamed segments (many `service` ways etc.) can't be searched by name -- skipped, never invented
    const coords = f.geometry?.coordinates
    if (!Array.isArray(coords) || coords.length < 2) continue
    const length = Number(f.properties?.length_m) || 0
    const prev = byName.get(name)
    if (!prev) {
      byName.set(name, { length, coords, highway: f.properties?.highway, count: 1 })
    } else {
      prev.count += 1
      if (length >= prev.length) {
        prev.length = length
        prev.coords = coords
        prev.highway = f.properties?.highway
      }
    }
  }

  for (const [name, entry] of byName) {
    const mid = lineMidpoint(entry.coords)
    if (!mid) continue
    index.push({
      id: `road:${name}`,
      name,
      context: entry.count > 1 ? `${highwayLabel(entry.highway)} · ${entry.count} segments` : highwayLabel(entry.highway),
      coords: mid,
      kind: 'road',
    })
  }

  return index
}

/** Case-insensitive substring match on name and context. Landmarks are surfaced first (they're the
 * highest-confidence, hand-verified points), then roads alphabetically. Capped at `limit` so the dropdown
 * never dumps all ~180 pilot road names at once. */
export function searchLocations(index, query, limit = 8) {
  const q = (query || '').trim().toLowerCase()
  const pool = q
    ? index.filter((loc) => loc.name.toLowerCase().includes(q) || (loc.context || '').toLowerCase().includes(q))
    : index
  return [...pool]
    .sort((a, b) => (a.kind !== b.kind ? (a.kind === 'landmark' ? -1 : 1) : a.name.localeCompare(b.name)))
    .slice(0, limit)
}

/** Reverse lookup used only to caption an already-picked point ("Selected: <name>") when it exactly matches
 * a search result's coordinate. A map click essentially never lands on the exact same float coordinate as a
 * pre-computed road midpoint/landmark, so this cannot mislabel an arbitrary map-clicked point. */
export function findLocationByCoords(index, lonlat) {
  if (!Array.isArray(lonlat) || lonlat.length !== 2) return null
  const [lon, lat] = lonlat
  return index.find((loc) => loc.coords[0] === lon && loc.coords[1] === lat) || null
}
