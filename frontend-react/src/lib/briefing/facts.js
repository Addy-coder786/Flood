// FloodBriefingState — the ONE place that turns live FloodNet state into the facts a briefing may speak.
//
// WHY THIS FILE EXISTS
// Narration must never be free to invent a number, a street, a cause or a route. So narration templates
// (lib/briefing/narration.js) receive ONLY this object, and every field here is read straight out of data
// the app already fetched from the real API:
//
//   run     — POST /api/simulate summary          (peak depth, peak surcharging nodes, frames_t_min)
//   series  — GET  /api/simulation/{id}/series    (per-frame max_depth_cm, flooded_segments,
//                                                  surcharging_count, edges_at_capacity, streets{})
//   frame   — GET  /api/simulation/{id}/frame/{t} (per-segment depth_cm/severity/passability + geometry)
//   route / alternatives — POST /api/route, /api/route/alternatives
//   alerts  — lib/alerts.js, the same functions AlertsPanel itself renders
//
// Anything that is not present is returned as null and the corresponding briefing step is SKIPPED rather
// than narrated with filler. There is no fallback text with made-up values anywhere in this module.
import { computeAllAlerts } from '../alerts.js'
import { DEFAULT_BANDS_CM } from '../severity.js'

const SEVERE_CM = DEFAULT_BANDS_CM[2][0] // 30
const CRITICAL_CM = DEFAULT_BANDS_CM[3][0] // 60

function idxOfT(series, t) {
  if (!series?.t_min?.length) return -1
  let best = -1
  let bestD = Infinity
  series.t_min.forEach((v, i) => {
    const d = Math.abs(v - t)
    if (d < bestD) { bestD = d; best = i }
  })
  return best
}

/** Midpoint of a GeoJSON LineString, as {lat, lng}. Real geometry only — never a guessed coordinate. */
function midpointOf(feature) {
  const coords = feature?.geometry?.coordinates
  if (!Array.isArray(coords) || coords.length === 0) return null
  const mid = coords[Math.floor(coords.length / 2)]
  if (!Array.isArray(mid) || mid.length < 2) return null
  return { lat: Number(mid[1]), lng: Number(mid[0]) }
}

/** The deepest real street segment in the current frame, with its real name and real geometry. */
function deepestSegment(frame) {
  const feats = frame?.streets?.features
  if (!Array.isArray(feats) || !feats.length) return null
  let best = null
  for (const f of feats) {
    const d = Number(f?.properties?.depth_cm)
    if (!Number.isFinite(d)) continue
    if (!best || d > Number(best.properties.depth_cm)) best = f
  }
  if (!best) return null
  const p = best.properties
  return {
    segId: String(p.seg_id),
    // A segment may genuinely be unnamed in OSM. Say so rather than inventing a name.
    name: (p.name && String(p.name).trim()) || null,
    depthCm: Number(p.depth_cm),
    severity: p.severity ?? null,
    passableCar: p.passable_car ?? null,
    passableAmbulance: p.passable_ambulance ?? null,
    at: midpointOf(best),
  }
}

/** Top N deepest segments in the current frame (used to highlight a cluster rather than one line). */
function topSegments(frame, n = 6) {
  const feats = frame?.streets?.features
  if (!Array.isArray(feats)) return []
  return feats
    .filter((f) => Number.isFinite(Number(f?.properties?.depth_cm)))
    .sort((a, b) => Number(b.properties.depth_cm) - Number(a.properties.depth_cm))
    .slice(0, n)
    .map((f) => ({ segId: String(f.properties.seg_id), depthCm: Number(f.properties.depth_cm) }))
}

/** increasing / decreasing / stable, from the real max-depth series around the current time. */
function trendAt(series, currentT) {
  const i = idxOfT(series, currentT)
  const v = series?.max_depth_cm
  if (i < 0 || !Array.isArray(v) || v.length < 2) return null
  const prev = v[Math.max(0, i - 2)]
  const next = v[Math.min(v.length - 1, i + 2)]
  if (!Number.isFinite(prev) || !Number.isFinite(next)) return null
  const delta = next - prev
  if (Math.abs(delta) < 1.0) return 'stable' // < 1 cm across ~10 min is not a meaningful move
  return delta > 0 ? 'increasing' : 'decreasing'
}

/**
 * Pick a few MEANINGFUL forecast moments instead of narrating all 37 frames.
 * A moment qualifies when the pilot-wide max depth crosses a severity band, or when the flooded-segment
 * count changes by a large margin. The run's own peak is always included. All values are real series values.
 */
function forecastMoments(series, maxMoments = 4) {
  const t = series?.t_min
  const d = series?.max_depth_cm
  const segs = series?.flooded_segments
  if (!Array.isArray(t) || !Array.isArray(d) || t.length < 2) return []

  const moments = []
  const pushed = new Set()
  const add = (i, kind) => {
    if (i < 0 || i >= t.length || pushed.has(i)) return
    pushed.add(i)
    moments.push({
      tMin: t[i],
      depthCm: d[i],
      floodedSegments: Array.isArray(segs) ? segs[i] : null,
      kind,
    })
  }

  for (let i = 1; i < t.length; i++) {
    for (const band of [SEVERE_CM, CRITICAL_CM]) {
      if (d[i - 1] < band && d[i] >= band) add(i, 'crosses')
    }
  }

  let peakI = 0
  for (let i = 1; i < d.length; i++) if (d[i] > d[peakI]) peakI = i
  add(peakI, 'peak')

  moments.sort((a, b) => a.tMin - b.tMin)
  return moments.slice(0, maxMoments)
}

/** Drainage stress at the current time — real solver output, no causal attribution. */
function drainageAt(series, currentT) {
  const i = idxOfT(series, currentT)
  if (i < 0) return null
  const num = (arr) => (Array.isArray(arr) && Number.isFinite(arr[i]) ? arr[i] : null)
  const surcharging = num(series.surcharging_count)
  const atCapacity = num(series.edges_at_capacity)
  const nodesAtCapacity = num(series.nodes_at_capacity)
  if (surcharging == null && atCapacity == null && nodesAtCapacity == null) return null
  return {
    surchargingNodes: surcharging,
    edgesAtCapacity: atCapacity,
    nodesAtCapacity,
    maxEdgeUtil: num(series.max_edge_util),
    // Peak over the whole run, so a briefing can say where this is heading without re-deriving it.
    peakSurchargingNodes: Array.isArray(series.surcharging_count)
      ? Math.max(...series.surcharging_count.filter(Number.isFinite))
      : null,
  }
}

/** Routing facts — only when a real route result exists. Never fabricates a recommendation. */
function routingFacts(route, alternatives) {
  const r = route?.result
  const alt = alternatives?.result
  if (!r && !alt) return null

  let recommended = null
  if (alt?.candidates?.length) {
    const rec = alt.recommended || {}
    const idx = rec.safest ?? rec.balanced ?? rec.fastest ?? null
    if (idx != null && alt.candidates[idx]) {
      const c = alt.candidates[idx]
      recommended = {
        index: idx,
        objectives: c.objectives || [],
        lengthM: Number.isFinite(c.length_m) ? c.length_m : null,
        maxDepthCm: Number.isFinite(c.max_depth_on_route_cm) ? c.max_depth_on_route_cm : null,
        segments: c.route_segments || [],
      }
    }
  }

  return {
    hasSingleRoute: Boolean(r),
    reachable: r ? Boolean(r.reachable) : null,
    vehicle: r?.vehicle ?? route?.vehicle ?? null,
    vehicleLimitCm: Number.isFinite(r?.vehicle_limit_cm) ? r.vehicle_limit_cm : null,
    lengthM: Number.isFinite(r?.length_m) ? r.length_m : null,
    baselineLengthM: Number.isFinite(r?.baseline_length_m) ? r.baseline_length_m : null,
    maxDepthOnRouteCm: Number.isFinite(r?.max_depth_on_route_cm) ? r.max_depth_on_route_cm : null,
    avoidedCount: Array.isArray(r?.avoided_segments) ? r.avoided_segments.length : null,
    candidateCount: alt?.candidates?.length ?? 0,
    recommended,
  }
}

/**
 * Build the complete briefing fact set. Returns `{ ready: false }` when there is no completed run — the
 * briefing then refuses to start rather than narrating an empty dashboard.
 */
export function buildBriefingFacts({ run, series, frame, route, alternatives, currentT, scenario, meta }) {
  if (!run || !series?.t_min?.length) {
    return { ready: false, reason: 'no-run' }
  }

  const deepest = deepestSegment(frame)
  const i = idxOfT(series, currentT)
  const currentMaxDepthCm = i >= 0 && Number.isFinite(series.max_depth_cm?.[i]) ? series.max_depth_cm[i] : null
  const currentFloodedSegments = i >= 0 && Number.isFinite(series.flooded_segments?.[i])
    ? series.flooded_segments[i]
    : null

  const alerts = computeAllAlerts({
    series,
    route,
    currentT,
    // The alert subject must be the REAL segment where the peak occurs, never a neighbourhood label.
    subjectLabel: deepest?.name || null,
  })

  return {
    ready: true,

    // context
    pilotName: meta?.pilot?.name ?? null,
    scenarioName: scenario?.name ?? null,
    dataMode: series?.data_mode ?? frame?.data_mode ?? null,
    // which rainfall source drove this run (e.g. 'radar_image_derived') -- straight from the run's provenance
    rainfallSourceType: run?.provenance?.rainfall_source?.source_type ?? null,
    horizonMin: series.t_min[series.t_min.length - 1] ?? null,
    currentT,

    // situation (step 1)
    currentMaxDepthCm,
    currentFloodedSegments,
    peakDepthCm: Number.isFinite(run?.summary?.max_depth_cm) ? run.summary.max_depth_cm : null,
    peakDepthTMin: Number.isFinite(run?.summary?.peak_depth_t_min) ? run.summary.peak_depth_t_min : null,
    trend: trendAt(series, currentT),

    // where (steps 1 & 4)
    deepest,
    topSegments: topSegments(frame),

    // alerts (step 2)
    alerts,
    topAlert: alerts[0] ?? null,

    // forecast (step 3)
    moments: forecastMoments(series),

    // drainage (step 5)
    drainage: drainageAt(series, currentT),

    // routing (step 6)
    routing: routingFacts(route, alternatives),
  }
}
