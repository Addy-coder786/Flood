// Driver trip page helpers (DECISIONS D-40, D-41, D-42; docs/LIVE_OPERATIONS.md §4-§5; research §5).
// Pure: no DOM, no fetch. src/driver/DriverApp.jsx renders from these; pinned by driver.test.mjs.
import { ACCESS_STATUS } from './roadAccess.js'
import { formatISTTime } from './publicAlert.js'

// ---------------------------------------------------------------- token
// The dispatcher hands out `<base>driver#t=<token>` (secrets.token_urlsafe(24), 32 chars). The page moves it to
// sessionStorage under this key and removes it from the address bar.
export const DRIVER_TOKEN_KEY = 'floodnet.driverToken'
const TOKEN_RE = /^[A-Za-z0-9_-]{16,256}$/

export function tokenFromHash(hash) {
  const raw = String(hash || '').replace(/^#/, '')
  if (!raw) return null
  const t = new URLSearchParams(raw).get('t')
  return t && TOKEN_RE.test(t) ? t : null
}

// ---------------------------------------------------------------- stages (server: backend/floodnet/ops/trips.py)
export const STAGE_LABELS = {
  ACCEPTED: 'Accept trip',                       // direct trips from the Roads page (D-49)
  EN_ROUTE: 'Start trip',
  AT_PICKUP: 'Arrived at pickup point',
  AT_SETTLEMENT: 'Arrived at settlement',
  LOADED: 'People on board',
  AT_SHELTER: 'Arrived at shelter',
  COMPLETED: 'Trip completed',
  ARRIVED: 'Arrived at destination',
}

const STAGE_STATUS = {
  PLANNED: 'New trip – accept it to begin',
  ACCEPTED: 'Accepted – not started',
  ARRIVED: 'At the destination',
  ASSIGNED: 'Assigned – not started',
  EN_ROUTE: 'On the way',
  AT_PICKUP: 'At the pickup point',
  AT_SETTLEMENT: 'At the settlement',
  LOADED: 'People on board',
  AT_SHELTER: 'At the shelter',
  COMPLETED: 'Trip completed',
  ABORTED: 'Trip stopped by the dispatcher',
}

export function stageStatus(stage) {
  return STAGE_STATUS[stage] || String(stage || '')
}

/** The confirmation asked before COMPLETED: a mission ends at the shelter, a direct trip at its destination. */
export function completionQuestion(view) {
  return view?.kind === 'direct'
    ? 'Mark this trip as completed? Do this only when the task at the destination is done.'
    : 'Mark this trip as completed? Do this only when everyone has been handed over at the shelter.'
}

/** One button per stage the server allows next (driver stages only); the first is the expected one. */
export function stageButtons(view) {
  const next = (view?.next_stages || []).filter((s) => Object.hasOwn(STAGE_LABELS, s))
  return next.map((stage, i) => ({ stage, label: STAGE_LABELS[stage], primary: i === 0, confirm: stage === 'COMPLETED' }))
}

// ---------------------------------------------------------------- formatting
export function formatDistance(m) {
  if (m == null || !Number.isFinite(Number(m)) || Number(m) < 0) return '–'
  const r = Math.round(Number(m) / 10) * 10
  if (r < 1000) return `${r} m`
  const km = r / 1000
  return km >= 10 ? `${Math.round(km)} km` : `${km.toFixed(1)} km`
}

function ageWords(sec) {
  const s = Math.max(0, Math.round(Number(sec) || 0))
  if (s < 60) return `${s} s`
  const m = Math.floor(s / 60)
  if (m >= 5) return `${m} min`
  const rest = s % 60
  return rest ? `${m} min ${rest} s` : `${m} min`
}

/** What the dispatcher sees of this phone's location: words + glyph, never colour alone. */
export function freshnessText(position) {
  if (!position) return { glyph: '○', tone: 'none', text: 'No location received by the dispatcher yet' }
  const age = ageWords(position.age_s)
  if (position.freshness === 'FRESH') return { glyph: '●', tone: 'fresh', text: `Location current (${age} old)` }
  if (position.freshness === 'STALE') return { glyph: '◐', tone: 'stale', text: `Location getting old (${age} old)` }
  return { glyph: '○', tone: 'lost',
    text: `Location lost (last fix ${age} ago); the dispatcher sees an estimated position from the plan` }
}

export function offlineText(lastOkMs) {
  const t = formatISTTime(lastOkMs)
  return t ? `Offline – last update at ${t}; instructions may be out of date`
    : 'Offline – no update received yet; instructions may be out of date'
}

// ---------------------------------------------------------------- location sharing
export function shouldPost(lastPostMs, nowMs, intervalS) {
  const s = Number(intervalS) > 0 ? Number(intervalS) : 15
  return lastPostMs == null || nowMs - lastPostMs >= s * 1000
}

export function positionPayload(pos) {
  const c = pos?.coords
  if (!c || !Number.isFinite(c.longitude) || !Number.isFinite(c.latitude)) return null
  return {
    lon: c.longitude,
    lat: c.latitude,
    accuracy_m: Number.isFinite(c.accuracy) ? Math.round(c.accuracy) : null,
    recorded_at: Number.isFinite(pos.timestamp) ? pos.timestamp : Date.now(),
  }
}

export function geoErrorMessage(err) {
  switch (err?.code) {
    case 1: return 'Location permission was refused. Allow location for this site in the browser settings, then press Share again.'
    case 2: return 'Your location is not available right now (no GPS signal). Sharing will continue when the phone finds it.'
    case 3: return 'Finding your location took too long. Sharing will keep trying while this page is open.'
    default: return 'The phone could not provide a location.'
  }
}

/** Whether this browser can share location at all. Geolocation needs a secure (https) page. */
export function geoSupport({ isSecureContext, hasGeolocation }) {
  if (!isSecureContext) return { ok: false, reason: 'Location sharing needs a secure link (https). Ask the dispatcher for an https driver link.' }
  if (!hasGeolocation) return { ok: false, reason: 'This browser cannot share location.' }
  return { ok: true, reason: null }
}

// ---------------------------------------------------------------- instructions and voice
export function nextInstruction(view) {
  const leg = view?.remaining?.[0]
  if (!leg) return null
  const m = leg.maneuvers || []
  if (!m.length) return { instruction: `Go to ${leg.to || 'the destination'}`, distance: null, then: null, to: leg.to || null, kind: leg.kind }
  return {
    instruction: m[0].instruction,
    distance: m[0].type === 'arrive' ? null : formatDistance(m[0].distance_m),
    then: m[1]?.instruction || null,
    to: leg.to || null,
    kind: leg.kind,
  }
}

/** The maneuvers after the current one on the first remaining leg. */
export function upcomingManeuvers(view) {
  return (view?.remaining?.[0]?.maneuvers || []).slice(1)
}

export function isNewInstruction(prev, next) {
  if (!next) return false
  if (!prev) return true
  return prev.instruction !== next.instruction || prev.to !== next.to
}

// ---------------------------------------------------------------- reports
export const REPORT_KINDS = [
  { kind: 'blockage', label: 'Road blocked', needsRoad: true },
  { kind: 'high_water', label: 'Water too deep', needsRoad: true },
  { kind: 'delay', label: 'Delay', needsRoad: false },
  { kind: 'other', label: 'Other', needsRoad: false },
]
const NOTE_MAX = 300           // backend ReportReq.note max_length

const ORD = (n) => `${n}${n % 10 === 1 && n % 100 !== 11 ? 'st' : n % 10 === 2 && n % 100 !== 12 ? 'nd' : n % 10 === 3 && n % 100 !== 13 ? 'rd' : 'th'}`

/** Upcoming roads (unique by segment, in driving order) for the report form. */
export function upcomingRoads(view) {
  const out = []
  const seen = new Set()
  for (const leg of view?.remaining || []) {
    for (const s of leg.segments || []) {
      if (!s?.seg_id || seen.has(s.seg_id)) continue
      seen.add(s.seg_id)
      const name = (s.name || '').trim()
      out.push({ seg_id: s.seg_id, label: name || `unnamed road (${ORD(out.length + 1)} ahead)` })
    }
  }
  return out
}

export function reportPayload({ kind, segId, note, clientRef }) {
  const k = REPORT_KINDS.find((x) => x.kind === kind)
  if (!k) return { ok: false, error: 'Choose what happened.' }
  if (k.needsRoad && !segId) return { ok: false, error: 'Choose the road this is about.' }
  return { ok: true, body: { kind, seg_id: segId || null, note: String(note || '').trim().slice(0, NOTE_MAX), client_ref: clientRef } }
}

// ---------------------------------------------------------------- hazards and route geometry
export function hazardRows(view) {
  return (view?.hazards_ahead || []).map((h) => {
    const st = ACCESS_STATUS[h.status] || { label: String(h.status || 'Unknown'), glyph: '–' }
    const bits = []
    if (Number.isFinite(h.depth_cm)) bits.push(`modelled water ${Math.round(h.depth_cm)} cm`)
    if (h.reason) bits.push(h.reason)
    return { seg_id: h.seg_id, name: (h.name || '').trim() || 'unnamed road', label: st.label, glyph: st.glyph,
      status: h.status, detail: bits.join('; ') }
  })
}

function lineCoords(g) {
  if (!g) return []
  if (g.type === 'Feature') return lineCoords(g.geometry)
  if (g.type === 'LineString') return [g.coordinates || []]
  if (g.type === 'MultiLineString') return g.coordinates || []
  return []
}

/** Coordinate arrays of the remaining route, one or more per leg. */
export function routeLines(view) {
  return (view?.remaining || []).flatMap((leg) => lineCoords(leg.route)).filter((c) => Array.isArray(c) && c.length >= 2)
}
