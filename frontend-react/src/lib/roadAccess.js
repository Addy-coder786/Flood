// Arrival-time road access (MISC-04 Phase 1): shared display rules and road-event helpers.
// Status meanings are defined by the backend (backend/floodnet/access/road_status.py, docs/ROAD_ACCESS.md);
// this file only names and colours them, it never decides a road's flood status itself.

// `color` is the map stroke / swatch; `text` is the same hue darkened for chip text (4.5:1, contrast.test.mjs);
// `glyph` makes the status readable without colour (WCAG 1.4.1).
export const ACCESS_STATUS = {
  USABLE: { label: 'Usable', color: '#16a34a', text: '#117E39', glyph: '✓', hint: 'no known reason it is impassable; not a guarantee of safe passage' },
  UNCERTAIN: { label: 'Uncertain', color: '#f59e0b', text: '#9B6306', glyph: '?', hint: 'near the vehicle limit, unreliable terrain data, or an unconfirmed public report' },
  UNKNOWN: { label: 'Unknown', color: '#94a3b8', text: '#5D708B', glyph: '–', hint: 'not modelled, or beyond the forecast horizon' },
  AT_RISK: { label: 'At risk', color: '#dc2626', text: '#D02222', glyph: '▲', hint: 'forecast depth reaches the vehicle limit on arrival' },
  CLOSED: { label: 'Closed', color: '#111827', text: '#111827', glyph: '⛔', hint: 'confirmed closure in force' },
}
export const ACCESS_STATUS_ORDER = ['USABLE', 'UNCERTAIN', 'UNKNOWN', 'AT_RISK', 'CLOSED']
export const CURRENT_STATUS_ROUTE_COLOR = '#7c3aed'

const M_PER_DEG_LAT = 111320

/** Nearest road segment to a clicked [lon, lat], or null if none is within maxM metres. Equirectangular
 * projection around the click point: accurate to well under a metre across the ~2 km pilot area. */
export function nearestSegment(roads, lonlat, maxM = 40) {
  const [lon0, lat0] = lonlat
  const kx = M_PER_DEG_LAT * Math.cos((lat0 * Math.PI) / 180)
  let best = null
  for (const f of roads?.features || []) {
    const pts = (f.geometry?.coordinates || []).map(([lon, lat]) => [(lon - lon0) * kx, (lat - lat0) * M_PER_DEG_LAT])
    for (let i = 1; i < pts.length; i += 1) {
      const d = distToSegment(pts[i - 1], pts[i])
      if (d <= maxM && (!best || d < best.distM)) best = { segId: String(f.properties.seg_id), name: f.properties.name || '', distM: d }
    }
  }
  return best
}

function distToSegment([ax, ay], [bx, by]) {
  const dx = bx - ax
  const dy = by - ay
  const len2 = dx * dx + dy * dy
  const t = len2 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / len2)) : 0
  return Math.hypot(ax + t * dx, ay + t * dy)
}

// ---------------------------------------------------------------- report reconciliation (DECISIONS D-32)
// Mirror of backend/floodnet/access/events.py; the same cases are pinned in roadReports.test.mjs and
// backend/tests/test_report_reconciliation.py so the map and the router never disagree. Project policy for a
// demonstration system, not a claim about any agency's authority:
//  - tiers rank AUTHORITY > OPERATOR = FIELD_TEAM > PUBLIC (an untiered report counts as OPERATOR);
//  - a reopen lifts a closure only if it was recorded no earlier than the closure, describes a later moment
//    (a full tie goes to the closure) and comes from an equal or higher tier;
//  - a PUBLIC closure is never binding: the road becomes UNCERTAIN until an operator confirms it;
//  - nothing expires; staleness is a flag that asks for confirmation, it never changes routing.
export const SOURCE_TIERS = ['AUTHORITY', 'OPERATOR', 'FIELD_TEAM', 'PUBLIC']
export const TIER_RANK = { AUTHORITY: 4, OPERATOR: 3, FIELD_TEAM: 3, PUBLIC: 1 }
export const TIER_LABEL = { AUTHORITY: 'Authority', OPERATOR: 'Operator', FIELD_TEAM: 'Field team', PUBLIC: 'Public (unverified)' }
export const STALE_AFTER_MIN = { AUTHORITY: 180, OPERATOR: 90, FIELD_TEAM: 90, PUBLIC: 45 }   // ASSUMED, uncalibrated
export const REPORT_STATUS = {
  ACTIVE: { label: 'In force', glyph: '●' },
  LIFTED: { label: 'Lifted', glyph: '↻' },
  APPLIED: { label: 'Applied', glyph: '✓' },
  NO_EFFECT: { label: 'No effect', glyph: '–' },
  REJECTED_LOWER_TIER: { label: 'Not applied: lower tier', glyph: '✕' },
  UNCONFIRMED: { label: 'Unconfirmed', glyph: '?' },
  FUTURE: { label: 'Not yet received', glyph: '◷' },
}

// Same normalisation as the backend's parse_tier (case and blanks ignored); the backend rejects unknown tiers, which
// the UI cannot produce, so they are shown as OPERATOR here.
const normTier = (x) => String(x ?? '').trim().toUpperCase()
const tierOf = (e) => (TIER_RANK[normTier(e.source_tier)] ? normTier(e.source_tier) : 'OPERATOR')
const rank = (e) => TIER_RANK[tierOf(e)]
const appliesOf = (e) => e.applies_from_t_min ?? e.received_t_min
const cmpKey = (a, b) => appliesOf(a) - appliesOf(b) || a.received_t_min - b.received_t_min
  || String(a.event_id).localeCompare(String(b.event_id))
// r describes a later moment than c; on an equal moment the later-recorded one; a full tie is NOT later.
const later = (r, c) => appliesOf(r) > appliesOf(c) || (appliesOf(r) === appliesOf(c) && r.received_t_min > c.received_t_min)
const fmt = (t) => `T+${Number(t).toFixed(0)}`

function annotateSegment(list, decisionT) {
  // Every closure can be lifted by a qualifying reopen, including an unconfirmed PUBLIC one (an operator who finds the
  // road open clears the public report); only confirmed closures are ever routed as CLOSED.
  const closures = list.filter((e) => e.kind === 'closure')
  const info = new Map(list.map((e) => [e.event_id, { status: null, lifts: [], liftedBy: null, contestedBy: [], why: null }]))
  for (const r of list.filter((e) => e.kind === 'reopen')) {
    const ri = info.get(r.event_id)
    const lowerTier = []
    for (const c of closures) {
      if (!(r.received_t_min >= c.received_t_min && later(r, c))) continue
      if (rank(r) < rank(c)) { lowerTier.push(c); continue }
      ri.lifts.push(c.event_id)
      const ci = info.get(c.event_id)
      const current = ci.liftedBy && list.find((x) => x.event_id === ci.liftedBy)
      if (!current || cmpKey(r, current) < 0) ci.liftedBy = r.event_id      // the earliest lifting reopen counts
    }
    // A reopen refused by a higher-tier closure contests it, even when it lifts another closure.
    for (const c of lowerTier) info.get(c.event_id).contestedBy.push(r.event_id)
    if (ri.lifts.length) { ri.status = 'APPLIED'; continue }
    if (lowerTier.length) {
      const c = lowerTier[lowerTier.length - 1]
      ri.status = 'REJECTED_LOWER_TIER'
      ri.why = `rejected: ${tierOf(r)} cannot lift a ${tierOf(c)} closure (${c.event_id}); record a ${tierOf(c)} or higher reopen to confirm`
      continue
    }
    ri.status = 'NO_EFFECT'
    const first = closures.find((c) => r.received_t_min < c.received_t_min || !later(r, c))
    ri.why = first
      ? (r.received_t_min < first.received_t_min
        ? `no effect: recorded before closure ${first.event_id} (received ${fmt(first.received_t_min)})`
        : `no effect: describes the same moment as closure ${first.event_id}; a tie keeps the closure`)
      : 'no effect: no confirmed closure to lift'
  }
  return list.map((e) => {
    const i = info.get(e.event_id)
    const tier = tierOf(e)
    const age = decisionT - e.received_t_min
    const stale = age > STALE_AFTER_MIN[tier]
    const scheduled = appliesOf(e) > e.received_t_min
    let status = i.status
    if (e.kind === 'closure') {
      const lifted = i.liftedBy && appliesOf(list.find((x) => x.event_id === i.liftedBy)) <= decisionT
      status = lifted ? 'LIFTED' : tier === 'PUBLIC' ? 'UNCONFIRMED' : 'ACTIVE'
    }
    let head = `${e.event_id} ${e.kind} ${e.name || e.seg_id} by ${tier}: ${status}; received ${fmt(e.received_t_min)}, applies ${fmt(appliesOf(e))}`
    if (scheduled) head += ', scheduled'
    if (stale) head += `, stale ${Math.round(age)} min`
    const parts = [head]
    if (e.vehicle_types?.length) parts.push(`restriction: binds only ${e.vehicle_types.join(', ')}`)   // D-42
    if (i.lifts.length) parts.push(`lifts ${i.lifts.join(', ')}`)
    if (i.liftedBy) parts.push(`lifted by ${i.liftedBy}`)
    if (i.contestedBy.length) parts.push(`contested by ${i.contestedBy.join(', ')}`)
    if (status === 'UNCONFIRMED') parts.push('unconfirmed PUBLIC report: the road is UNCERTAIN until an operator confirms')
    if (i.why) parts.push(i.why)
    return {
      ...e, source_tier: tier, status, stale, age_min: age, scheduled,
      lifts: i.lifts, lifted_by: i.liftedBy, contested_by: i.contestedBy, explanation: parts.join('; '),
    }
  })
}

/** Every road report with its derived status at the decision time, in a deterministic order. Reports received
 * after the decision time are FUTURE and take part in nothing (no future information). */
export function annotateRoadEvents(events, decisionT) {
  const out = []
  const bySeg = new Map()
  for (const e of [...(events || [])].sort(cmpKey)) {
    if (e.received_t_min > decisionT) {
      out.push({
        ...e, source_tier: tierOf(e), status: 'FUTURE', stale: false, age_min: null, scheduled: appliesOf(e) > e.received_t_min,
        lifts: [], lifted_by: null, contested_by: [],
        explanation: `${e.event_id} ${e.kind} ${e.name || e.seg_id}: not yet received at ${fmt(decisionT)} (received ${fmt(e.received_t_min)})`,
      })
      continue
    }
    if (!bySeg.has(e.seg_id)) bySeg.set(e.seg_id, [])
    bySeg.get(e.seg_id).push(e)
  }
  for (const list of bySeg.values()) out.push(...annotateSegment(list, decisionT))
  return out.sort(cmpKey)
}

/** seg_id -> confirmed closure in force at time `t`, using only reports received by `decisionT` (mirrors
 * ClosureBook.closure_at). `t` defaults to the decision time. */
export function activeClosures(events, decisionT, t = decisionT) {
  const out = new Map()
  const ann = annotateRoadEvents(events, decisionT)
  const byId = new Map(ann.map((e) => [e.event_id, e]))
  for (const c of ann) {
    if (c.kind !== 'closure' || c.status === 'FUTURE' || c.source_tier === 'PUBLIC' || appliesOf(c) > t) continue
    const lifter = c.lifted_by ? byId.get(c.lifted_by) : null
    if (lifter && appliesOf(lifter) <= t) continue
    const prev = out.get(c.seg_id)
    if (!prev || cmpKey(prev, c) < 0) out.set(c.seg_id, c)
  }
  return out
}

/** seg_id -> unconfirmed (PUBLIC) closure received by the decision time: shown and routed as UNCERTAIN only. */
export function unconfirmedClosures(events, decisionT) {
  const out = new Map()
  for (const e of annotateRoadEvents(events, decisionT)) {
    if (e.status === 'UNCONFIRMED' && appliesOf(e) <= decisionT) out.set(e.seg_id, e)
  }
  return out
}

const TIER_SOURCE = {
  AUTHORITY: 'authority report (SIMULATED)',
  OPERATOR: 'operator report (SIMULATED)',
  FIELD_TEAM: 'field team report (SIMULATED)',
  PUBLIC: 'public report (SIMULATED, unverified)',
}

export function makeRoadEvent(events, { segId, name, kind, t, tier = 'OPERATOR', appliesFrom = null, vehicleTypes = null, note = null }) {
  const sourceTier = TIER_RANK[normTier(tier)] ? normTier(tier) : 'OPERATOR'
  // A closure may bind only some vehicle types (D-42, e.g. a weak bridge closed to trucks); a reopen never does.
  const restriction = kind === 'closure' && vehicleTypes?.length ? { vehicle_types: [...vehicleTypes] } : {}
  return {
    ...restriction,
    event_id: `${kind === 'closure' ? 'C' : 'R'}${(events?.length || 0) + 1}`,
    seg_id: segId, name, kind,
    received_t_min: t, applies_from_t_min: appliesFrom ?? t,
    source: TIER_SOURCE[sourceTier],
    source_tier: sourceTier,
    ...(note ? { note: String(note).slice(0, 300) } : {}),
  }
}
