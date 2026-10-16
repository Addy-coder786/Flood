// Public alert page helpers (DECISIONS D-45; docs/LIVE_OPERATIONS.md §7; docs/LIVE_OPERATIONS_RESEARCH.md §8).
// Pure: no DOM, no fetch. src/main.jsx uses routeFor(); src/public/PublicAlertApp.jsx renders from the rest.
// Pinned by publicAlert.test.mjs.

// ---------------------------------------------------------------- routing
// Public ids are secrets.token_urlsafe(16) (22 chars); the server accepts up to 64 url-safe base64 characters.
export const PUBLIC_ID_RE = /^[A-Za-z0-9_-]{1,64}$/

function normaliseBase(base) {
  let b = String(base || '/')
  if (!b.startsWith('/')) b = `/${b}`
  return b.endsWith('/') ? b : `${b}/`
}

/** Which screen a path opens: {kind:'public', id} (id null when malformed), {kind:'driver'} or {kind:'app'}. */
export function routeFor(pathname, base) {
  const b = normaliseBase(base)
  const p = String(pathname || '')
  if (!p.startsWith(b)) return { kind: 'app' }
  const rest = p.slice(b.length).replace(/\/+$/, '')
  if (rest === 'driver') return { kind: 'driver' }
  if (rest === 'alerts') return { kind: 'public', id: null }
  if (rest.startsWith('alerts/')) {
    const id = rest.slice('alerts/'.length)
    return { kind: 'public', id: PUBLIC_ID_RE.test(id) ? id : null }
  }
  return { kind: 'app' }
}

// ---------------------------------------------------------------- time (Asia/Kolkata, UTC+5:30, no DST)
// Computed by arithmetic so the result never depends on the phone's timezone or its Intl data.
const IST_OFFSET_MS = 330 * 60 * 1000
const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function toMs(t) {
  if (t == null || t === '') return null
  const ms = typeof t === 'number' ? t : Date.parse(t)
  return Number.isFinite(ms) ? ms : null
}

function istClock(d) {
  const h = d.getUTCHours()
  return `${h % 12 || 12}:${String(d.getUTCMinutes()).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'} IST`
}

/** '7:40 pm IST, Sun 11 Oct 2026', or null for a missing/invalid time. */
export function formatIST(t) {
  const ms = toMs(t)
  if (ms == null) return null
  const d = new Date(ms + IST_OFFSET_MS)
  return `${istClock(d)}, ${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`
}

/** '7:40 pm IST', or null. */
export function formatISTTime(t) {
  const ms = toMs(t)
  return ms == null ? null : istClock(new Date(ms + IST_OFFSET_MS))
}

function span(sec) {
  const min = Math.floor(sec / 60)
  if (min < 60) return `${Math.max(1, min)} min`
  if (min < 24 * 60) {
    const h = Math.floor(min / 60)
    const m = min % 60
    return m ? `${h} h ${m} min` : `${h} h`
  }
  const days = Math.floor(min / (24 * 60))
  return `${days} day${days === 1 ? '' : 's'}`
}

/** '12 min ago', 'just now', 'in 1 h' ... ; null for an invalid time. */
export function relativeAge(t, nowMs) {
  const ms = toMs(t)
  if (ms == null) return null
  const sec = (nowMs - ms) / 1000
  if (Math.abs(sec) < 60) return 'just now'
  return sec > 0 ? `${span(sec)} ago` : `in ${span(-sec)}`
}

export function updatedLine(t, nowMs) {
  const when = formatIST(t)
  return when ? `Updated ${when} (${relativeAge(t, nowMs)})` : 'Update time not available'
}

// ---------------------------------------------------------------- status and state
// FloodNet's own status words come from the API (status_text); the glyph makes the level readable without colour.
const STATUS_GLYPH = { watch: '?', advisory: '!', ended: '–' }

export function statusDisplay(alert) {
  const s = alert?.status
  const known = Object.hasOwn(STATUS_GLYPH, s)
  return { glyph: known ? STATUS_GLYPH[s] : '•', text: alert?.status_text || 'Status not available', tone: known ? s : 'unknown' }
}

/** Banners shown under the status header, in order. Never says an area or road is safe. */
export function stateBanners(alert) {
  if (!alert) return []
  const out = []
  if (alert.is_latest === false) {
    out.push({ kind: 'older', text: `You are viewing an older version (version ${alert.version} of ${alert.latest_version}).` })
  }
  const updated = formatIST(alert.updated_at) || 'the last update'
  if (alert.state === 'resolved' || alert.status === 'ended') {
    out.push({ kind: 'resolved', text: `No longer active as of ${updated}. Roads may still be waterlogged. Follow BMC instructions.` })
  } else if (alert.state === 'expired') {
    out.push({ kind: 'expired', text: `This advisory expired at ${formatIST(alert.expires_at) || 'its end time'} and is no `
      + 'longer kept up to date. Roads may still be waterlogged. Check BMC or IMD before you travel.' })
  } else if (alert.state === 'stale') {
    out.push({ kind: 'stale', text: `This page has not been updated since ${updated}. Conditions may have changed. `
      + 'Check BMC or IMD before you travel.' })
  }
  return out
}

const EVIDENCE_LABELS = { model_estimate: 'Model estimate', reported: 'Reported (unconfirmed)', confirmed_closure: 'Confirmed road closure' }

export function evidenceLabel(e) {
  return EVIDENCE_LABELS[e?.kind] || e?.label || 'Information'
}

// ---------------------------------------------------------------- map (inline SVG, no tiles)
// Each affected-road class differs by line pattern AND words (WCAG 1.4.1), all drawn in the brand burgundy.
export const ROAD_CLASSES = {
  MODEL_ESTIMATE: { label: 'Model estimate', aria: 'model estimate', dash: '9 6', cross: false,
    words: 'dashed line: the flood model expects water on this road' },
  CONFIRMED_CLOSURE: { label: 'Confirmed road closure', aria: 'confirmed closure', dash: null, cross: true,
    words: 'solid line with ✕: an operator confirmed this road is closed' },
  REPORTED: { label: 'Reported (unconfirmed)', aria: 'reported, unconfirmed', dash: '0.5 6', cross: false,
    words: 'dotted line: reported by the public, not confirmed' },
}

/** Equirectangular projection of [w, s, e, n] into a width-wide SVG box with `pad` px padding, north up. */
export function makeProjector(bbox, width = 360, pad = 12) {
  if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every(Number.isFinite)) return null
  const [w, s, e, n] = bbox
  if (!(e > w) || !(n > s)) return null
  const kx = Math.cos((((s + n) / 2) * Math.PI) / 180)
  const inner = width - 2 * pad
  const scale = inner / ((e - w) * kx)               // px per degree of latitude
  const height = (n - s) * scale + 2 * pad
  return {
    width,
    height,
    project: ([lon, lat]) => [pad + (lon - w) * kx * scale, pad + (n - lat) * scale],
  }
}

const r1 = (v) => Math.round(v * 10) / 10

export function pathD(coords, projector) {
  if (!Array.isArray(coords) || coords.length < 2) return ''
  return coords.map((c, i) => {
    const [x, y] = projector.project(c)
    return `${i ? 'L' : 'M'}${r1(x)} ${r1(y)}`
  }).join('')
}

export function polygonD(rings, projector) {
  if (!Array.isArray(rings) || !rings.length) return ''
  return rings.map((ring) => {
    const d = pathD(ring, projector)
    return d ? `${d}Z` : ''
  }).join('')
}

/** The point halfway along a line (by length in projected pixels), for the closure ✕ marker. */
export function midPoint(coords, projector) {
  if (!Array.isArray(coords) || coords.length < 2) return null
  const pts = coords.map((c) => projector.project(c))
  const lens = pts.slice(1).map((p, i) => Math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]))
  let half = lens.reduce((a, b) => a + b, 0) / 2
  for (let i = 0; i < lens.length; i += 1) {
    if (half <= lens[i] || i === lens.length - 1) {
      const f = lens[i] ? half / lens[i] : 0
      return [r1(pts[i][0] + f * (pts[i + 1][0] - pts[i][0])), r1(pts[i][1] + f * (pts[i + 1][1] - pts[i][1]))]
    }
    half -= lens[i]
  }
  return null
}

const roadName = (r) => (r?.name || '').trim() || 'unnamed road'

export function mapAriaLabel(map) {
  const roads = map?.affected_roads || []
  if (!roads.length) return 'Map of the area. No affected roads in this version.'
  const seen = new Set()
  const parts = []
  for (const r of roads) {
    const part = `${roadName(r)} (${ROAD_CLASSES[r.class]?.aria || 'affected'})`
    if (!seen.has(part)) { seen.add(part); parts.push(part) }
  }
  const shown = parts.slice(0, 10)
  const more = parts.length - shown.length
  return `Map of the affected area, north up. Affected roads: ${shown.join(', ')}${more > 0 ? ` and ${more} more` : ''}.`
}

/** One legend row per affected-road class, with how many lines of that class this version has (0 = none). */
export function legendRows(map) {
  const roads = map?.affected_roads || []
  return Object.entries(ROAD_CLASSES).map(([key, c]) => ({ key, ...c, count: roads.filter((r) => r.class === key).length }))
}

// ---------------------------------------------------------------- API path, versions, titles, updates
export function versionFromSearch(search) {
  const v = new URLSearchParams(search || '').get('v')
  return v && /^\d{1,6}$/.test(v) ? v : null
}

export function apiPathFor(id, version) {
  const v = version != null && /^\d{1,6}$/.test(String(version)) ? `?v=${version}` : ''
  return `/api/public/alerts/${encodeURIComponent(id)}${v}`
}

/** The link to the latest version: the same page without ?v=. */
export function latestHref(pathname) {
  return String(pathname || '')
}

const MAX_SHORT = 40

export function shortLocality(alert) {
  if (!alert) return ''
  const h = String(alert.headline || '')
  const i = h.indexOf(': ')
  let s = i >= 0 ? h.slice(i + 2) : String(alert.locality_text || '').split('. ')[0]
  s = s.trim().replace(/\.$/, '')
  return s.length > MAX_SHORT ? `${s.slice(0, MAX_SHORT - 1).trimEnd()}…` : s
}

export function docTitle(alert) {
  const s = shortLocality(alert)
  return s ? `FloodNet advisory – ${s}` : 'FloodNet advisory'
}

export function updatesNewestFirst(updates) {
  return Array.isArray(updates) ? [...updates].sort((a, b) => (b.version || 0) - (a.version || 0)) : []
}

// ---------------------------------------------------------------- interactive map, plain risk, official feed (2026-10-11)
// The optional street map uses exactly the dashboard's tile source and attribution (components/MapView/MapView.jsx);
// it is loaded only when the reader asks for it (src/public/TileMap.jsx, a separate chunk).
export const TILE_URL = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png'
export const TILE_ATTRIBUTION = '&copy; OpenStreetMap contributors'
export const TILE_MAX_ZOOM = 19
export const MAX_ZOOM = 8

const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
const uniqueNames = (roads) => [...new Set(roads.map(roadName))]

/** Names of roads an operator confirmed closed, once each. */
export function closedRoads(map) {
  return uniqueNames((map?.affected_roads || []).filter((r) => r.class === 'CONFIRMED_CLOSURE'))
}

/**
 * What the advisory says in plain words, built only from the API data, and the time horizon.
 * {lines: [...], horizon: 'Until 8:40 pm IST (the next 60 min from 7:40 pm IST)' | text | null}
 */
export function plainRisk(alert) {
  if (alert?.status === 'ended') {
    return { lines: ['FloodNet no longer expects flooding from this event.',
      'Roads may still be waterlogged. Follow BMC instructions.'], horizon: null }
  }
  const roads = alert?.map?.affected_roads || []
  const model = roads.filter((r) => r.class === 'MODEL_ESTIMATE')
  const depths = model.map((r) => r.depth_cm).filter(Number.isFinite)
  const closed = closedRoads(alert?.map)
  const reported = uniqueNames(roads.filter((r) => r.class === 'REPORTED'))
  const lines = []
  if (model.length) {
    lines.push(`The flood model estimates water ${depths.length ? `up to ${Math.max(...depths)} cm deep` : ''} on `
      + `${plural(model.length, 'road section')} (a computer-model estimate, not a measurement).`.replace(/ {2}/g, ' '))
  }
  if (closed.length) lines.push(`${plural(closed.length, 'road')} confirmed closed by an operator.`)
  if (reported.length) lines.push(`${plural(reported.length, 'road')} reported closed by the public, not confirmed.`)
  if (alert?.status === 'advisory') lines.push('Flooding is likely here: avoid the area if you can.')
  else if (alert?.status === 'watch') lines.push('Flooding is possible here: plan ahead and check again before you travel.')
  const tw = alert?.time_window || {}
  const from = toMs(tw.from)
  const to = toMs(tw.to)
  let horizon = tw.text || null
  if (from != null && to != null && to > from) {
    horizon = `Until ${formatISTTime(to)} (the next ${Math.round((to - from) / 60000)} min from ${formatISTTime(from)})`
  }
  return { lines, horizon }
}

const EVIDENCE_TYPES = [
  { kind: 'model_estimate', label: 'Model estimate', text: 'Computed by FloodNet’s flood model (student prototype); not a measurement.' },
  { kind: 'reported', label: 'Reported (unconfirmed)', text: 'Reported by members of the public; not confirmed by an operator.' },
  { kind: 'confirmed_closure', label: 'Confirmed road closure', text: 'Confirmed by a FloodNet operator.' },
  { kind: 'official', label: 'Authorised official warning',
    text: 'FloodNet does not issue official warnings. Official warnings come from authorised agencies through SACHET (NDMA); see “Official warnings” below.' },
]

/** The four kinds of evidence, each marked present or not; FloodNet never marks “official” as present. */
export function evidenceTypes(alert) {
  const kinds = new Set((alert?.evidence || []).map((e) => e.kind))
  return EVIDENCE_TYPES.map((t) => ({ ...t, present: t.kind !== 'official' && kinds.has(t.kind) }))
}

/** The newest update, when there has been one after the first issue; otherwise null. */
export function latestChange(alert) {
  const newest = updatesNewestFirst(alert?.updates)[0]
  return newest && newest.version > 1 ? newest : null
}

/** Tap-to-see text for one affected road. */
export function roadInfo(r) {
  const d = Number.isFinite(r?.depth_cm) ? r.depth_cm : null
  const lines = []
  if (r?.class === 'CONFIRMED_CLOSURE') {
    lines.push('Confirmed closed by an operator.')
    if (d != null) lines.push(`The flood model also estimates water up to ${d} cm (an estimate, not a measurement).`)
  } else if (r?.class === 'REPORTED') {
    lines.push('Reported closed by the public; not confirmed by an operator.')
  } else {
    lines.push('Model estimate.')
    lines.push(d != null ? `Water up to ${d} cm in the time window (computer estimate, not a measurement).`
      : 'Water expected in the time window (computer estimate, not a measurement).')
  }
  return { title: roadName(r), label: ROAD_CLASSES[r?.class]?.label || 'Affected road', lines }
}

const CLASS_ORDER = { CONFIRMED_CLOSURE: 0, MODEL_ESTIMATE: 1, REPORTED: 2 }

/** Affected roads grouped by name and kind (a road is often several map sections): closures first, then depth. */
export function affectedRoadList(map) {
  const groups = new Map()
  ;(map?.affected_roads || []).forEach((r, i) => {
    const key = `${r.class}|${roadName(r)}`
    const g = groups.get(key) || { key, name: roadName(r), cls: r.class, indices: [], depth_cm: null }
    g.indices.push(i)
    if (Number.isFinite(r.depth_cm)) g.depth_cm = Math.max(g.depth_cm ?? -Infinity, r.depth_cm)
    groups.set(key, g)
  })
  return [...groups.values()].sort((a, b) => (CLASS_ORDER[a.cls] ?? 9) - (CLASS_ORDER[b.cls] ?? 9)
    || (b.depth_cm ?? -1) - (a.depth_cm ?? -1) || a.name.localeCompare(b.name))
}

const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi)

/** Pan inside the full map: the view never leaves it. */
export function panView(view, full, dx, dy) {
  return { x: clamp(view.x + dx, full.x, full.x + full.w - view.w), y: clamp(view.y + dy, full.y, full.y + full.h - view.h),
    w: view.w, h: view.h }
}

/** Zoom by `factor` (>1 in) around the point [cx, cy] (map units), between the whole map and MAX_ZOOM. */
export function zoomView(view, full, factor, [cx, cy]) {
  const w = clamp(view.w / factor, full.w / MAX_ZOOM, full.w)
  const k = w / view.w
  const h = (w * full.h) / full.w
  return panView({ x: cx - (cx - view.x) * k, y: cy - (cy - view.y) * k, w, h }, full, 0, 0)
}

const OFFICIAL_AREA = /maharashtra|mumbai/i

/** Items of FloodNet's cached copy of the SACHET feed that name Maharashtra or Mumbai, newest first. */
export function officialItemsFor(items, max = 5) {
  const all = Array.isArray(items) ? items : []
  const hits = all.filter((i) => OFFICIAL_AREA.test(`${i?.title || ''} ${i?.author || ''}`))
  const t = (i) => { const ms = Date.parse(i?.pub_date || ''); return Number.isFinite(ms) ? ms : -Infinity }
  return { items: hits.sort((a, b) => t(b) - t(a)).slice(0, max), cached: all.length }
}
