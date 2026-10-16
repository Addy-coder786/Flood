// The dispatcher browser and the shared live-operations store (DECISIONS D-38, D-41). Pure helpers: the context
// (state/useOps.js) calls the API, these decide how server facts change the browser's incident.
//
//  * Road events made in this browser (C1, R2 ...) are synced up; field reports from drivers (F1 ...) come down and
//    are merged once by event_id. Records are immutable, so a merge never edits an existing event.
//  * A trip is a dispatched assignment: its stage moves the ledger forward through LEDGER_FOR_STAGE (the backend's
//    trips.ledger_status_for_stage), never backwards; an accepted reroute replaces the assignment's legs once.

import { activeSource } from './sources.js'

/** What the shared forecast was computed from, in words a resident-facing page can show: the RAINFALL source first
 * (a design storm is SYNTHETIC even though the terrain and drainage data are REAL), then the pilot data mode. */
export function forecastInputLabel(run) {
  if (!run) return 'UNKNOWN'
  const src = activeSource({ run, isStale: false })
  const kindWord = { scenario: 'SYNTHETIC', demo: 'SYNTHETIC', replay: 'HISTORICAL' }[src.kind] || src.badge || String(src.kind).toUpperCase()
  return `${kindWord} rainfall: ${src.name} on ${run.data_mode || 'UNKNOWN'} pilot data`
}

export const LEDGER_FOR_STAGE = {
  ASSIGNED: 'DISPATCHED', EN_ROUTE: 'DISPATCHED', AT_PICKUP: 'DISPATCHED', AT_SETTLEMENT: 'DISPATCHED',
  LOADED: 'IN_PROGRESS', AT_SHELTER: 'IN_PROGRESS', COMPLETED: 'COMPLETED', ABORTED: 'CANCELLED',
}
const ORDER = ['PROPOSED', 'ACCEPTED', 'DISPATCHED', 'IN_PROGRESS', 'COMPLETED']
const FINAL = new Set(['COMPLETED', 'CANCELLED'])

export const STAGE_LABEL = {
  ASSIGNED: 'Assigned, not started', EN_ROUTE: 'En route', AT_PICKUP: 'At team pickup', AT_SETTLEMENT: 'At the settlement',
  LOADED: 'People on board', AT_SHELTER: 'At the shelter', COMPLETED: 'Completed', ABORTED: 'Aborted',
  // direct trips from the Roads page (D-49)
  PLANNED: 'Planned, waiting for the driver', ACCEPTED: 'Accepted by the driver', ARRIVED: 'Arrived at the destination',
}

export function dispatcherEvents(events) {
  return (events || []).filter((e) => !e.origin || e.origin === 'dispatcher')
}

export function mergeFieldEvents(local, server, incidentId = null) {
  const have = new Set((local || []).map((e) => e.event_id))
  const add = (server || []).filter((e) => e.origin && e.origin !== 'dispatcher' && !have.has(e.event_id)
    && (!incidentId || !e.incident_id || e.incident_id === incidentId))   // another incident's report stays there
  if (!add.length) return local
  return [...local, ...add.map((e) => ({ ...e, name: e.name || e.seg_id }))]
}

export function applyTripsToLedger(ledger, trips, t, incidentId = null) {
  const byAssignment = new Map()
  for (const tr of tripsOf(trips, incidentId)) {
    if (!tr.assignment_id) continue          // a direct trip (D-49) is not a planner assignment
    // the most recent trip for an assignment wins (a replaced driver link keeps the same trip)
    const prev = byAssignment.get(tr.assignment_id)
    if (!prev || String(tr.trip_id) > String(prev.trip_id)) byAssignment.set(tr.assignment_id, tr)
  }
  const changes = []
  let changed = false
  const out = (ledger || []).map((a) => {
    const tr = byAssignment.get(a.assignment_id)
    if (!tr || FINAL.has(a.status)) return a
    let next = a
    const want = LEDGER_FOR_STAGE[tr.stage]
    if (want === 'CANCELLED') {
      const why = (tr.stage_history || []).filter((h) => h.stage === 'ABORTED').slice(-1)[0]?.reason || ''
      next = { ...a, status: 'CANCELLED', history: [...(a.history || []), { status: 'CANCELLED', t_min: t, reason: `trip ${tr.trip_id}: ABORTED${why ? ` (${why})` : ''}`, by: 'trip' }] }
    } else if (want && ORDER.indexOf(want) > ORDER.indexOf(a.status)) {
      // every intermediate status is recorded: the backend ledger only accepts legal steps (ledger.py TRANSITIONS)
      const steps = ORDER.slice(ORDER.indexOf(a.status) + 1, ORDER.indexOf(want) + 1)
      next = { ...a, status: want, history: [...(a.history || []), ...steps.map((st) => ({ status: st, t_min: t, reason: `trip ${tr.trip_id}: ${tr.stage}`, by: 'trip' }))] }
    }
    if ((tr.route_version || 1) > (next.trip_route_version || 1) && Array.isArray(tr.legs) && tr.legs.length) {
      next = { ...next, legs: tr.legs, trip_route_version: tr.route_version }
    }
    if (next !== a) {
      changed = true
      changes.push({ assignment_id: a.assignment_id, from: a.status, to: next.status, trip_id: tr.trip_id })
    }
    return next
  })
  return { ledger: changed ? out : ledger, changes }
}

/** Only this incident's trips: assignment ids repeat across incidents. Without an id (old callers) all trips. */
export function tripsOf(trips, incidentId) {
  return (trips || []).filter((tr) => !incidentId || tr.incident_id === incidentId)
}

export function driverUrl(origin, base, link) {
  const b = String(base || '/').replace(/\/?$/, '/')
  return `${String(origin).replace(/\/$/, '')}${b}${link}`
}

export function tripSummary(trip) {
  const open = (trip.proposals || []).filter((p) => p.status === 'OPEN')
  const esc = trip.escalation?.open ? trip.escalation.reason : null
  let attention = null
  if (esc) attention = `Escalated: ${esc}`
  else if (open.length) attention = `A ${open[0].kind === 'REQUIRED' ? 'required' : 'suggested'} route change needs a decision`
  else if (trip.attention) attention = trip.attention
  const p = trip.position
  const gps = !p ? 'No GPS shared yet'
    : p.freshness === 'FRESH' ? `GPS ${Math.round(p.age_s)} s old${p.source === 'SIMULATED' ? ' (SIMULATED)' : ''}`
      : p.freshness === 'STALE' ? `GPS stale: ${Math.round(p.age_s)} s old`
        : `GPS lost: last fix ${Math.round(p.age_s / 60)} min ago`
  return { stage: STAGE_LABEL[trip.stage] || trip.stage, attention, gps, awaitingAck: Boolean(trip.awaiting_ack) }
}
