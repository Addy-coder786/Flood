// Rescue trips started on the Roads page (DECISIONS D-49). Pure helpers for the trip panel and the map: no React,
// no fetch. Server: backend/floodnet/ops/trips.py (direct trips), ops/eta.py (ETA parts), api/ops_api.py (preview).

// The selected route must not blend into flood, status or alert colours (all warm, green or grey), nor into the
// teal-blue water layer: a deep blue core inside a dark navy outline and a white halo. Alternatives are thin, muted
// and dashed. Checked in rescueTrip.test.mjs (hue gap and contrast).
export const ROUTE_STYLE = {
  halo: { color: '#FFFFFF', weight: 13, opacity: 0.95 },
  outline: { color: '#0B1F4D', weight: 9, opacity: 1 },
  core: { color: '#2F6BFF', weight: 5, opacity: 1 },
  next: { color: '#FFFFFF', weight: 3, opacity: 1, dashArray: '1 9' },
  alternative: { color: '#5B6472', weight: 3, opacity: 0.75, dashArray: '6 8' },
  previous: { color: '#5B6472', weight: 3, opacity: 0.5, dashArray: '2 8' },
}

export const DIRECT_STAGES = ['PLANNED', 'ACCEPTED', 'EN_ROUTE', 'ARRIVED', 'COMPLETED']
export const DIRECT_STAGE_LABEL = {
  PLANNED: 'Planned', ACCEPTED: 'Accepted', EN_ROUTE: 'En route', ARRIVED: 'Arrived', COMPLETED: 'Completed', ABORTED: 'Aborted',
}

export const isDirect = (trip) => trip?.kind === 'direct'

export function tripTitle(trip) {
  const n = trip?.names || {}
  if (isDirect(trip)) return `${trip.trip_id} · ${n.origin || 'Start'} → ${n.destination || 'Destination'}`
  return `${trip.trip_id} · ${n.settlement || trip.settlement_id} → ${n.shelter || trip.shelter_id}`
}

/** The five lifecycle steps with done / current / todo, plus an exception line (aborted, escalated) when there is one. */
export function lifecycle(trip) {
  const stage = trip?.stage
  const reached = stage === 'ABORTED'
    ? Math.max(0, ...(trip.stage_history || []).map((h) => DIRECT_STAGES.indexOf(h.stage)))
    : DIRECT_STAGES.indexOf(stage)
  const steps = DIRECT_STAGES.map((s, i) => ({
    stage: s, label: DIRECT_STAGE_LABEL[s],
    state: i < reached || (i === reached && s === 'COMPLETED') ? 'done' : i === reached && stage !== 'ABORTED' ? 'current' : i === reached ? 'done' : 'todo',
  }))
  let exception = null
  if (stage === 'ABORTED') {
    const why = (trip.stage_history || []).filter((h) => h.stage === 'ABORTED').slice(-1)[0]?.reason
    exception = `Aborted by the dispatcher${why ? `: ${why}` : ''}`
  } else if (trip?.escalation?.open) {
    exception = `Escalated to a person: ${trip.escalation.reason}`
  }
  return { steps, exception }
}

const TONE = { SIMULATED: 'sim', MODELLED: 'ok', 'OPERATIONS LOG': 'ok', REGISTRY: 'ok', STALE: 'warn', UNKNOWN: 'warn', 'NOT TRACKED': 'muted', LIVE: 'live', UNAVAILABLE: 'warn' }

/** One row per ETA part, in the server's order. Unknown parts get no number. */
export function etaRows(eta, forecastFreshness = null) {
  if (!eta?.components) return []
  return eta.components.map((c) => {
    const value = c.minutes == null ? 'not included' : `${c.key === 'base' ? '' : '+'}${c.minutes.toFixed(1)} min`
    const note = c.key === 'flood' && c.minutes != null && forecastFreshness?.label
      ? `${c.detail}; from a ${forecastFreshness.label} forecast` : c.detail
    return { key: c.key, label: c.label, value, note, tone: c.label === 'UNKNOWN' || c.label === 'NOT MODELLED' ? 'warn' : c.key === 'flood' ? 'modelled' : 'base' }
  })
}

const FRESH_NAME = { forecast: 'Forecast', closures: 'Closures', traffic: 'Traffic', vehicle: 'Vehicle status' }

export function freshnessChips(freshness) {
  return Object.keys(FRESH_NAME).filter((k) => freshness?.[k]).map((k) => {
    const f = freshness[k]
    const extra = k === 'closures' ? `${f.events_known ?? 0} report(s) known` : k === 'vehicle' && f.age_min != null ? `${Math.round(f.age_min)} min since last update` : ''
    return { key: k, name: FRESH_NAME[k], label: f.label, tone: TONE[f.label] || 'warn', detail: [f.detail, extra].filter(Boolean).join(' · ') }
  })
}

/** Registry vehicles (disabled with a reason when unavailable or already on a trip) and, separately, bare vehicle
 * types for a vehicle FloodNet does not track. */
export function vehicleOptions(resources, trips, limits, ledger = []) {
  const busy = new Map((trips || []).filter((t) => !['COMPLETED', 'ABORTED'].includes(t.stage) && t.vehicle_id).map((t) => [t.vehicle_id, t.trip_id]))
  // committed in the response plan (QA 2026-10-11 H2): accepted, dispatched or under way
  const planned = new Map((ledger || []).filter((a) => ['ACCEPTED', 'DISPATCHED', 'IN_PROGRESS'].includes(a.status) && a.vehicle_id).map((a) => [a.vehicle_id, a.assignment_id]))
  const registry = (resources || []).filter((r) => r.kind === 'vehicle').map((r) => {
    const reasons = []
    if (r.status !== 'AVAILABLE') reasons.push(`${r.status}${r.out_of_service_reason ? ` (${r.out_of_service_reason})` : ''}`)
    if (busy.has(r.id)) reasons.push(`on trip ${busy.get(r.id)}`)
    else if (planned.has(r.id)) reasons.push(`assigned in plan ${planned.get(r.id)}`)
    const tags = [/SIMULATED/.test(r.source || '') ? 'SIMULATED record' : null, r.stale ? `stale ${Math.round(r.age_min)} min` : null].filter(Boolean)
    return {
      value: r.id, type: r.type, lonlat: r.lonlat || null, disabled: reasons.length > 0, reason: reasons.join(', '),
      label: `${r.id} ${r.name} · ${r.type}, ${limits?.[r.type] ?? '?'} cm limit${reasons.length ? ` · ${reasons.join(', ')}` : ''}${tags.length ? ` · ${tags.join(', ')}` : ''}`,
    }
  })
  const types = Object.entries(limits || {}).map(([t, cm]) => ({ value: `type:${t}`, type: t, label: `${t} (${cm} cm limit) – not tracked`, disabled: false }))
  return { registry, types }
}

export function nextSegment(trip) {
  const s = trip?.remaining?.[0]?.segments?.[0]
  return s ? { seg_id: s.seg_id, name: s.name, status: s.status } : null
}

const IST = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })

export function positionText(p) {
  if (!p) return 'No GPS shared yet'
  const at = p.recorded_at ? ` · recorded ${IST.format(new Date(p.recorded_at))} IST` : ''
  const src = p.source === 'SIMULATED' ? ' (SIMULATED)' : ''
  const acc = p.accuracy_m != null ? ` · ±${Math.round(p.accuracy_m)} m` : ''
  if (p.freshness === 'FRESH') return `GPS ${Math.round(p.age_s)} s old${src}${acc}${at}`
  if (p.freshness === 'STALE') return `GPS stale: ${Math.round(p.age_s)} s old${src}${at}`
  return `GPS lost: last fix ${Math.round(p.age_s / 60)} min ago${src}${at}`
}

// ASSUMED wading limits, same values as the backend's config.VEHICLE_LIMIT_CM (the server checks them again).
export const VEHICLE_LIMIT_CM = { car: 30, motorcycle: 30, ambulance: 40, truck: 60, pedestrian: 60 }
// "Flooded road" reported from the Roads page closes the road to vehicles rated 30 cm or less (ASSUMED, D-49):
// depth was not measured, so the report binds the lowest vehicles; taller ones see the forecast check.
export const FLOODED_ROAD_VEHICLES = Object.entries(VEHICLE_LIMIT_CM).filter(([, cm]) => cm <= 30).map(([v]) => v).sort()

// Rescue layers on the map (owner request 2026-10-11): the response plan's mission routes and the active trips are
// hidden until a rescue view is chosen -- the Response or Trips page, or the "Rescue missions" map layer. On the Roads
// page only the trips show (that is where a rescue trip is started and followed). The "Emergency route" layer switch
// still hides trips everywhere.
const RESCUE_PAGES = ['response', 'trips']
export function rescueLayerVisibility(page, layers) {
  const rescue = RESCUE_PAGES.includes(page) || Boolean(layers?.missions)
  return { missions: rescue, trips: Boolean(layers?.route) && (rescue || page === 'access') }
}
// Map legend rows for mission routes, in plan order (colours: lib/response.js STATUS_COLOR)
export const MISSION_LEGEND = [
  { status: 'PROPOSED', label: 'Proposed', dashed: true },
  { status: 'ACCEPTED', label: 'Accepted' },
  { status: 'DISPATCHED', label: 'Dispatched' },
  { status: 'IN_PROGRESS', label: 'Under way' },
]

// Live traffic (2026-10-11): TomTom Flow Segment Data sampled for the roads of the route only (backend
// floodnet/traffic/tomtom.py). Levels use ASSUMED thresholds on current / free-flow speed. On the map traffic is a thin
// DOTTED line over the route so it never hides the flood status colours; both are drawn when both apply.
export const TRAFFIC_LEVELS = [
  { level: 'normal', label: 'Normal traffic', color: '#16a34a' },
  { level: 'moderate', label: 'Moderate congestion', color: '#eab308' },
  { level: 'heavy', label: 'Heavy congestion', color: '#dc2626' },
]
const LEVEL_COLOR = Object.fromEntries(TRAFFIC_LEVELS.map((l) => [l.level, l.color]))

/** The sampled roads with a known level, as [{seg_id, level, color}]; nothing for unknown or unsampled roads. */
export function trafficLines(traffic) {
  if (!traffic || traffic.mode !== 'LIVE') return []
  return Object.entries(traffic.segments || {})
    .filter(([, v]) => LEVEL_COLOR[v?.level])
    .map(([seg_id, v]) => ({ seg_id, level: v.level, color: LEVEL_COLOR[v.level] }))
}

const HM = new Intl.DateTimeFormat('en-GB', { timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hour12: false })
const min = (m) => (m == null || !Number.isFinite(Number(m)) ? null : `${Number(m).toFixed(1)} min`)

/** The panel's live-traffic line: what it is, when it was fetched, how much of the route it covers, the two ETAs. */
export function trafficSummary(traffic, eta) {
  const live = traffic?.mode === 'LIVE'
  if (!live) {
    return { mode: 'UNAVAILABLE', text: 'Live traffic unavailable — using estimated travel time.', detail: traffic?.note || '',
      withTraffic: null, withoutTraffic: min(eta?.eta_min), updated: null, coverage: null }
  }
  return {
    mode: 'LIVE',
    text: `Live traffic (TomTom)${eta?.traffic_delay_min != null ? `: +${Number(eta.traffic_delay_min).toFixed(1)} min on the sampled roads` : ''}`,
    detail: traffic.note || '',
    withTraffic: min(eta?.eta_min),
    withoutTraffic: min(eta?.eta_without_traffic_min),
    updated: traffic.fetched_at ? `${HM.format(new Date(traffic.fetched_at))} IST` : null,
    coverage: `${traffic.sampled ?? 0} of ${traffic.total ?? 0} roads sampled`,
  }
}
