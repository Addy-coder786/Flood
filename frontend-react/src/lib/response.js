// Emergency response planner (MISC-04 Phase 2): the client-held ledger and event log helpers.
// Rules (states, transitions, what the planner keeps) are the backend's (backend/floodnet/response/ledger.py);
// this file only applies operator status changes and builds events. History is append-only.

export const TRANSITIONS = {
  PROPOSED: ['ACCEPTED', 'CANCELLED'],
  ACCEPTED: ['DISPATCHED', 'CANCELLED'],
  DISPATCHED: ['IN_PROGRESS', 'CANCELLED'],
  IN_PROGRESS: ['COMPLETED', 'CANCELLED'],
  COMPLETED: [],
  CANCELLED: [],
}
export const STATUS_LABEL = {
  PROPOSED: 'Proposed', ACCEPTED: 'Accepted', DISPATCHED: 'Dispatched', IN_PROGRESS: 'In progress',
  COMPLETED: 'Completed', CANCELLED: 'Cancelled',
}
export const STATUS_COLOR = {
  PROPOSED: '#64748b', ACCEPTED: '#2563eb', DISPATCHED: '#7c3aed', IN_PROGRESS: '#d97706',
  COMPLETED: '#16a34a', CANCELLED: '#9ca3af',
}
export const PRIORITY_COLOR = { critical: '#dc2626', high: '#ea580c', medium: '#d97706', low: '#64748b' }
// Same hues darkened for TEXT in tinted chips (4.5:1 on the chip's own 8 % tint over white, contrast.test.mjs). The colours above stay for map
// markers, left borders and tints; chips render glyph + label in these colours on a light tint of the same hue.
export const STATUS_TEXT_COLOR = {
  PROPOSED: '#5F6E84', ACCEPTED: '#2563EB', DISPATCHED: '#7C3AED', IN_PROGRESS: '#A55B05',
  COMPLETED: '#117E39', CANCELLED: '#666F7F',
}
export const PRIORITY_TEXT_COLOR = { critical: '#D02222', high: '#BC470A', medium: '#A55B05', low: '#5F6E84' }
export const STATUS_GLYPH = { PROPOSED: '○', ACCEPTED: '◔', DISPATCHED: '→', IN_PROGRESS: '◐', COMPLETED: '✓', CANCELLED: '✕' }

/** A new ledger with one assignment moved to `status` at sim time t (operator action). Throws on an illegal move. */
export function setAssignmentStatus(ledger, assignmentId, status, t, reason = 'operator') {
  return ledger.map((a) => {
    if (a.assignment_id !== assignmentId) return a
    if (!TRANSITIONS[a.status]?.includes(status)) throw new Error(`${a.assignment_id}: cannot go from ${a.status} to ${status}`)
    return { ...a, status, history: [...(a.history || []), { status, t_min: t, reason, by: 'operator' }] }
  })
}

export function makeResponseEvent(events, { kind, t, reason = '', ...fields }) {
  return {
    event_id: `E${(events?.length || 0) + 1}`, kind, received_t_min: t, applies_from_t_min: t,
    reason, source: 'operator report (SIMULATED)', ...fields,
  }
}

/** Lines for the dispatch log: one per assignment history entry, in time order, plus the planner's log lines. */
export function dispatchLog(ledger, planLogs) {
  const rows = []
  for (const a of ledger || []) {
    for (const h of a.history || []) {
      rows.push({ t: h.t_min, text: `${a.assignment_id} → ${h.status}${h.reason ? ` (${h.reason})` : ''}`, by: h.by || 'operator' })
    }
  }
  for (const { t, lines } of planLogs || []) for (const text of lines) rows.push({ t, text, by: 'planner' })
  return rows.sort((x, y) => x.t - y.t)
}
