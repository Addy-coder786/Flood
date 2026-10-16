// Resource registry page helpers (2026-10-11). Pure functions only: the page (components/Pages/ResourcesPage.jsx)
// calls the API (api/registry.js); these decide what a record looks like on screen and check forms before they are
// sent. The server (backend/floodnet/ops/registry.py) validates again and is the authority.
//
// Honesty rules kept here: a record nobody has confirmed is UNKNOWN, never "current"; a seed record is SIMULATED; a
// site depth that could not be modelled stays UNKNOWN and is never shown as a number; candidate shelters are not
// shelters until CONFIRMED.

/** Ten or more digits, spaces or hyphens allowed: a phone number (same rule as the server). */
export const PHONE_RE = /(?:\+?\d[\s-]?){10,}/

export const CANDIDATE_FLOW = ['SUBMITTED', 'PERMISSION_CONFIRMED', 'SUITABILITY_CHECKED', 'CONFIRMED']
export const CANDIDATE_LABEL = {
  SUBMITTED: 'Submitted', PERMISSION_CONFIRMED: 'Permission', SUITABILITY_CHECKED: 'Suitability',
  CONFIRMED: 'Confirmed shelter', REJECTED: 'Rejected',
}
export const PERMISSION_METHOD_LABEL = { IN_PERSON: 'In person', PHONE: 'Phone call', WRITTEN: 'Written', MESSAGE: 'Message', OTHER: 'Other' }
export const CHECK_LABEL = {
  access: 'Vehicle access from the road', water_sanitation: 'Drinking water and toilets', accessibility: 'Step-free entry',
}
export const STATUS_LABEL = { AVAILABLE: 'Available', ASSIGNED: 'Assigned elsewhere', OUT_OF_SERVICE: 'Out of service', open: 'Open', closed: 'Closed' }

/** "never confirmed" / "just now" / "12 min ago" / "3 h 5 min ago". */
export function ageText(ageMin) {
  if (ageMin == null || !Number.isFinite(Number(ageMin))) return 'never confirmed'
  const m = Number(ageMin)
  if (m < 1) return 'just now'
  if (m < 60) return `${Math.round(m)} min ago`
  const h = Math.floor(m / 60)
  return `${h} h ${Math.round(m - h * 60)} min ago`
}

/** Freshness of a registry record as a chip: CURRENT / STALE / UNKNOWN (anything else is UNKNOWN). */
export function freshnessChip(r) {
  const f = r?.freshness
  if (f === 'CURRENT') return { text: 'CURRENT', tone: 'ok', glyph: '✓', title: `Last observed ${ageText(r.age_min)}` }
  if (f === 'STALE') {
    return { text: 'STALE', tone: 'warn', glyph: '↻', title: `Last observed ${ageText(r.age_min)}: older than the stale threshold; confirm it again` }
  }
  return { text: 'UNKNOWN', tone: 'muted', glyph: '?', title: r?.freshness_note || 'Nobody has said when this was last true' }
}

/** Where a record came from: SIMULATED seed, dispatcher entry, import, volunteer (verified) or a later update. */
export function sourceChip(r) {
  const reg = String(r?.registered_source || '')
  const src = String(r?.source || '')
  const title = [reg && `Registered from: ${reg}`, src && src !== reg && `Latest: ${src}`].filter(Boolean).join(' · ') || 'source not recorded'
  if (reg.startsWith('volunteer-submitted')) return { text: 'VOLUNTEER · VERIFIED', tone: 'ok', title }
  if (reg.startsWith('import:')) return { text: 'IMPORTED', tone: 'muted', title }
  if (reg === 'dispatcher entry') return { text: 'DISPATCHER ENTRY', tone: 'muted', title }
  if (src.includes('SIMULATED')) return { text: 'SIMULATED', tone: 'sim', title }
  if (src.startsWith('update')) return { text: 'UPDATED', tone: 'ok', title }
  return { text: 'SOURCE UNKNOWN', tone: 'muted', title }
}

/** Shelter occupancy for the bar and its text. level: ok < 80 % <= warn < 100 % <= full (display bands only). */
export function occupancy(s) {
  const used = Number(s?.occupancy)
  const total = Number(s?.total_capacity)
  if (!Number.isFinite(used) || !Number.isFinite(total) || total <= 0) return { used: null, total: null, free: null, pct: 0, level: 'unknown', text: 'capacity not known' }
  const free = Math.max(0, total - used)
  const pct = Math.min(100, Math.round((used / total) * 100))
  const level = used >= total ? 'full' : pct >= 80 ? 'warn' : 'ok'
  const text = used > total ? `${used} / ${total} occupied · over capacity` : `${used} / ${total} occupied · ${free} free`
  return { used, total, free, pct, level, text }
}

// ---------------------------------------------------------------- form validation (mirrors the server)
const str = (v) => (v == null ? '' : String(v)).trim()
const intIn = (v, lo, hi) => {
  const s = str(v)
  if (!/^-?\d+$/.test(s)) return null
  const n = Number(s)
  return n >= lo && n <= hi ? n : null
}

/** [lon, lat] inside the pilot box, or an error message. */
export function pilotLonLat(lon, lat, bbox) {
  const a = str(lon); const b = str(lat)
  if (!a || !b || !Number.isFinite(Number(a)) || !Number.isFinite(Number(b))) return { error: 'Enter longitude and latitude as numbers (lon, lat).' }
  const x = Number(a); const y = Number(b)
  const [w, s, e, n] = bbox || [-180, -90, 180, 90]
  if (x < w || x > e || y < s || y > n) return { error: `Outside the pilot area (lon ${w}–${e}, lat ${s}–${n}).` }
  return { lonlat: [x, y] }
}

function textField(errors, key, value, max, { required = false, phone = false, label = key } = {}) {
  const v = str(value)
  if (required && !v) errors[key] = `${label} is required.`
  else if (v.length > max) errors[key] = `${label}: at most ${max} characters.`
  else if (phone && PHONE_RE.test(v)) errors[key] = `${label}: do not record phone numbers here.`
  return v
}

/** A new team / vehicle / shelter from form strings -> { errors: {field: message}, record }. */
export function validateRecord(kind, form, vocab) {
  const errors = {}
  const record = {}
  const f = form || {}
  record.name = textField(errors, 'name', f.name, 120, { required: true, label: 'Name' })
  const ll = pilotLonLat(f.lon, f.lat, vocab?.pilot_bbox)
  if (ll.error) errors.location = ll.error
  else record.lonlat = ll.lonlat
  if (kind === 'team' || kind === 'vehicle') {
    record.base = textField(errors, 'base', f.base, 120, { required: true, label: 'Base' })
    const org = textField(errors, 'organisation', f.organisation, 80, { label: 'Organisation' })
    if (org) record.organisation = org
    const status = str(f.status).toUpperCase()
    if (status && status !== 'AVAILABLE') {
      record.status = status
      if (status === 'OUT_OF_SERVICE') {
        if (!str(f.out_of_service_reason)) errors.out_of_service_reason = 'Out of service needs a reason.'
        else record.out_of_service_reason = str(f.out_of_service_reason).toUpperCase()
      }
    }
  }
  if (kind === 'team') {
    const known = vocab?.capabilities || []
    const caps = [...new Set((f.capabilities || []).map((c) => str(c).toLowerCase()).filter(Boolean))]
    const unknown = caps.filter((c) => !known.includes(c))
    if (!caps.length) errors.capabilities = 'Choose at least one capability.'
    else if (unknown.length) errors.capabilities = `Unknown capability: ${unknown.join(', ')}.`
    else record.capabilities = caps
    const m = intIn(f.members, 1, 100000)
    if (m == null) errors.members = 'Members: a whole number of at least 1.'
    else record.members = m
  } else if (kind === 'vehicle') {
    const types = (vocab?.vehicle_types || []).map((t) => t.type)
    const t = str(f.type).toLowerCase()
    if (!types.includes(t)) errors.type = `Choose a known vehicle type: ${types.join(', ')}.`
    else record.type = t
    const p = intIn(f.passenger_capacity, 1, 100000)
    if (p == null) errors.passenger_capacity = 'Passenger seats: a whole number of at least 1.'
    else record.passenger_capacity = p
  } else if (kind === 'shelter') {
    const total = intIn(f.total_capacity, 1, 100000)
    if (total == null) errors.total_capacity = 'Capacity: a whole number of at least 1.'
    else record.total_capacity = total
    const occ = str(f.occupancy) === '' ? 0 : intIn(f.occupancy, 0, 100000)
    if (occ == null) errors.occupancy = 'Occupancy: a whole number, 0 or more.'
    else if (total != null && occ > total) errors.occupancy = 'Occupancy cannot be more than the capacity.'
    else record.occupancy = occ
    const st = str(f.status || 'open').toLowerCase()
    if (!(vocab?.shelter_statuses || ['open', 'closed']).includes(st)) errors.status = 'Status: open or closed.'
    else record.status = st
    const addr = textField(errors, 'address', f.address, 200, { label: 'Address' })
    if (addr) record.address = addr
  } else {
    errors.kind = 'Choose team, vehicle or shelter.'
  }
  return { errors, record }
}

/** A candidate shelter from form strings -> { errors, body } (body as the API expects it). */
export function validateCandidate(form, bbox) {
  const errors = {}
  const f = form || {}
  const body = {}
  body.name = textField(errors, 'name', f.name, 120, { required: true, phone: true, label: 'Name of the place' })
  const ll = pilotLonLat(f.lon, f.lat, bbox)
  if (ll.error) errors.location = ll.error
  else body.lonlat = ll.lonlat
  body.address = textField(errors, 'address', f.address, 200, { phone: true, label: 'Address or landmark' })
  body.description = textField(errors, 'description', f.description, 500, { phone: true, label: 'Description' })
  if (str(f.capacity_estimate) !== '') {
    const c = intIn(f.capacity_estimate, 1, 100000)
    if (c == null) errors.capacity_estimate = 'Rough capacity: a whole number of at least 1.'
    else body.capacity_estimate = c
  }
  return { errors, body }
}

// ---------------------------------------------------------------- import
/** RFC 4180-style CSV: quoted fields, "" escapes, commas and newlines inside quotes, CRLF; blank lines skipped. */
export function parseCsv(text) {
  const records = []
  let row = []; let field = ''; let quoted = false
  const s = String(text || '').replace(/^﻿/, '')
  for (let i = 0; i < s.length; i += 1) {
    const ch = s[i]
    if (quoted) {
      if (ch === '"' && s[i + 1] === '"') { field += '"'; i += 1 } else if (ch === '"') quoted = false
      else field += ch
    } else if (ch === '"') quoted = true
    else if (ch === ',') { row.push(field); field = '' } else if (ch === '\n' || ch === '\r') {
      if (ch === '\r' && s[i + 1] === '\n') i += 1
      row.push(field); records.push(row); row = []; field = ''
    } else field += ch
  }
  if (field !== '' || row.length) { row.push(field); records.push(row) }
  const nonEmpty = records.filter((r) => r.some((x) => x.trim() !== ''))
  if (!nonEmpty.length) return { header: [], rows: [] }
  const header = nonEmpty[0].map((h) => h.trim().toLowerCase())
  const rows = nonEmpty.slice(1).map((r) => Object.fromEntries(header.map((h, k) => [h, (r[k] ?? '').trim()])))
  return { header, rows }
}

export function detectFormat(filename, text) {
  const f = String(filename || '').toLowerCase()
  if (f.endsWith('.csv')) return 'csv'
  if (f.endsWith('.json')) return 'json'
  return /^\s*[[{]/.test(String(text || '')) ? 'json' : 'csv'
}

// ---------------------------------------------------------------- commitments, links, candidates
const LIVE_LEDGER = new Set(['ACCEPTED', 'DISPATCHED', 'IN_PROGRESS'])
const DONE_STAGES = new Set(['COMPLETED', 'ABORTED'])

/** What each team / vehicle / shelter is committed to now: active trips first, then live ledger assignments
 *  that have no active trip. Proposals, finished trips and cancelled assignments are not commitments. */
export function commitments(trips, ledger) {
  const out = {}
  const add = (id, item) => { if (id) (out[id] ||= []).push(item) }
  const viaTrip = new Set()
  for (const t of trips || []) {
    if (DONE_STAGES.has(t.stage)) continue
    viaTrip.add(t.assignment_id)
    const item = { kind: 'trip', id: t.trip_id, status: t.stage, assignment_id: t.assignment_id }
    add(t.team_id, item); add(t.vehicle_id, item); add(t.shelter_id, item)
  }
  for (const a of ledger || []) {
    if (!LIVE_LEDGER.has(a.status) || viaTrip.has(a.assignment_id)) continue
    const item = { kind: 'assignment', id: a.assignment_id, status: a.status, assignment_id: a.assignment_id }
    add(a.team_id, item); add(a.vehicle_id, item); add(a.shelter_id, item)
  }
  return out
}

/** The link a responder or volunteer opens: the token rides in the fragment (never sent to a server in a URL). */
export function responderUrl(origin, base, token) {
  const b = String(base || '/').replace(/\/?$/, '/')
  return `${String(origin).replace(/\/$/, '')}${b}#/resources?rt=${encodeURIComponent(token)}`
}

export function tokenFromHash(hash) {
  const m = /^#\/resources\/?\?(?:[^#]*&)?rt=([^&]+)/.exec(String(hash || ''))
  if (!m) return null
  try { return decodeURIComponent(m[1]) } catch { return null }
}

export function candidateSteps(c) {
  const state = c?.state
  if (state === 'REJECTED') {
    const reached = new Set((c.history || []).map((h) => h.state))
    return [...CANDIDATE_FLOW.filter((s) => reached.has(s)).map((s) => ({ state: s, label: CANDIDATE_LABEL[s], status: 'done' })),
      { state: 'REJECTED', label: CANDIDATE_LABEL.REJECTED, status: 'rejected' }]
  }
  const idx = CANDIDATE_FLOW.indexOf(state)
  return CANDIDATE_FLOW.map((s, i) => ({
    state: s, label: CANDIDATE_LABEL[s], status: i <= idx ? 'done' : i === idx + 1 ? 'current' : 'todo',
  }))
}

export function nextCandidateAction(c) {
  return { SUBMITTED: 'permission', PERMISSION_CONFIRMED: 'suitability', SUITABILITY_CHECKED: 'confirm' }[c?.state] || null
}

/** The modelled depth at a candidate site in words. UNKNOWN stays UNKNOWN with its reason. */
export function depthText(sc) {
  if (!sc) return 'UNKNOWN: not checked yet'
  if (sc.status !== 'MODELLED') return `UNKNOWN: ${sc.reason || 'no modelled depth'}`
  const d = Math.round(Number(sc.max_depth_cm) || 0)
  const dist = Math.round(Number(sc.distance_m) || 0)
  const road = sc.road_name || 'the nearest road'
  if (d <= 0) return `MODELLED 0 cm (no water on ${road} in the published forecast, ${dist} m away)`
  return `MODELLED ${d} cm at T+${Math.round(Number(sc.peak_t_min))} on ${road} (${dist} m away)`
}

// ---------------------------------------------------------------- date-time inputs
const pad = (n) => String(n).padStart(2, '0')
/** A Date -> the value of an <input type="datetime-local"> in this browser's time zone. */
export function toLocalInput(d = new Date()) {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}
/** The value of a datetime-local input -> UTC ISO text, or null. */
export function fromLocalInput(value) {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : d.toISOString()
}
