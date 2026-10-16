// Incident state (DECISIONS D-33): the road reports, resource reports, assignment ledger and plan history of one
// emergency belong to the INCIDENT, not to a forecast run. A new forecast (scenario re-run, live/ECMWF refresh,
// compare run) only invalidates results that were computed from the old flood frames; the operator then replans.
// Only an explicit "New incident" clears everything. Pure functions: FloodNetContext holds the state, these decide
// how it changes, and tests pin the rules (incident.test.mjs).

export const INCIDENT_SCHEMA = 'floodnet.incident.v1'
export const INCIDENT_STORAGE_KEY = 'floodnet.incident.v1'

export function emptyEvaluation() {
  return { result: null, error: null, loading: false, outcomeScenarioId: null }
}

/** The response-planner slice: ledger + resource-report log + plan logs/history, plus run-bound results. */
export function emptyResponse() {
  return {
    result: null, error: null, loading: false,
    ledger: [], events: [], planLogs: [], version: 0, planHistory: [],
    staleReason: null, forecastRevision: 0, lastRevision: null,
    evaluation: emptyEvaluation(),
  }
}

/** A new incident id: live-operations trips are tied to it, because assignment ids (A1, A2 ...) repeat in every
 * incident (D-41). Random, not secret. */
export function newIncidentId() {
  const abc = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
  const bytes = new Uint8Array(10)
  globalThis.crypto.getRandomValues(bytes)
  return 'INC-' + Array.from(bytes, (b) => abc[b % abc.length]).join('')
}

export function emptyIncident() {
  return { incidentId: newIncidentId(), runId: null, currentT: 0, roadEvents: [], response: emptyResponse() }
}

/** True when the operator has recorded anything that a new forecast must not erase. */
export function hasIncident(state) {
  const r = state?.response || {}
  return Boolean((state?.roadEvents?.length || 0) + (r.ledger?.length || 0) + (r.events?.length || 0) + (r.planLogs?.length || 0))
}

function frameRange(run) {
  const ts = run?.frames_t_min || run?.bundle?.t_min || []
  return ts.length ? [ts[0], ts[ts.length - 1]] : [0, 0]
}

/** State after a new forecast run lands. With no active incident this is a plain reset to the run's first frame
 * (today's behaviour). With an active incident everything the operator recorded is kept; only the plan RESULT
 * (routes/statuses drawn from the old frames) is dropped and marked stale, and the decision time is kept,
 * clamped to the new horizon. */
export function applyRunToIncident(prev, newRun) {
  const [first, last] = frameRange(newRun)
  const runId = newRun?.run_id ?? null
  if (!hasIncident(prev)) return { ...emptyIncident(), runId, currentT: first }
  const r = prev.response
  const t = Math.min(Math.max(prev.currentT ?? first, first), last)
  return {
    ...prev,
    runId,
    currentT: t,
    roadEvents: prev.roadEvents,
    response: {
      ...r,
      result: null, error: null, loading: false,
      staleReason: 'forecast changed',
      forecastRevision: (r.forecastRevision || 0) + 1,
      lastRevision: { from_run_id: prev.runId ?? null, to_run_id: runId, at_sim_t: prev.currentT ?? first },
      evaluation: emptyEvaluation(),     // an evaluation scores one forecast; it does not carry over
    },
  }
}

const count = (ledger, statuses) => (ledger || []).filter((a) => statuses.includes(a.status)).length

/** What the operator should be told after a forecast change; null when the plan is not stale. */
export function replanProposal(state) {
  const r = state?.response
  if (!r?.staleReason) return null
  const underway = count(r.ledger, ['DISPATCHED', 'IN_PROGRESS'])
  const accepted = count(r.ledger, ['ACCEPTED'])
  const proposed = count(r.ledger, ['PROPOSED'])
  const t = state.currentT ?? 0
  const parts = []
  if (underway) parts.push(`${underway} underway`)
  if (accepted) parts.push(`${accepted} accepted`)
  if (proposed) parts.push(`${proposed} proposed`)
  const what = parts.length ? `re-check ${parts.join(', ')} mission(s)` : 'rebuild the plan'
  const reason = r.staleReason === 'restored from this browser'
    ? `Incident restored from this browser: the forecast is not stored, so run the forecast again, then replan at T+${t} min to ${what}. `
    : `Forecast changed (revision ${r.forecastRevision}): replan at T+${t} min to ${what} against the new forecast. `
  return {
    reason: reason + 'Completed work and recorded reports are kept.',
    underway, accepted, proposed, suggested_t: t,
  }
}

/** JSON for localStorage. Never contains the run bundle or plan results (they are recomputed); the ledger is kept
 * whole because underway missions need their legs to be re-routed from the estimated position. */
export function serializeIncident(state) {
  const r = state?.response || emptyResponse()
  return JSON.stringify({
    schema_version: INCIDENT_SCHEMA,
    saved_at: new Date().toISOString(),
    incidentId: state?.incidentId ?? null,
    runId: state?.runId ?? null,
    currentT: state?.currentT ?? 0,
    roadEvents: state?.roadEvents || [],
    response: {
      ledger: r.ledger || [], events: r.events || [], planLogs: r.planLogs || [], planHistory: r.planHistory || [],
      version: r.version || 0, staleReason: r.staleReason ?? null, forecastRevision: r.forecastRevision || 0,
      lastRevision: r.lastRevision ?? null,
    },
  })
}

const arr = (x) => (Array.isArray(x) ? x : [])

/** Parse a stored incident; anything unreadable or from another schema gives null (never throws). */
export function deserializeIncident(text) {
  if (typeof text !== 'string' || !text) return null
  let d
  try { d = JSON.parse(text) } catch { return null }
  if (!d || typeof d !== 'object' || Array.isArray(d) || d.schema_version !== INCIDENT_SCHEMA) return null
  const r = d.response && typeof d.response === 'object' ? d.response : {}
  return {
    incidentId: typeof d.incidentId === 'string' && /^INC-[A-Za-z0-9]{8,}$/.test(d.incidentId) ? d.incidentId : newIncidentId(),
    runId: d.runId ?? null,
    currentT: Number.isFinite(d.currentT) ? d.currentT : 0,
    roadEvents: arr(d.roadEvents),
    savedAt: typeof d.saved_at === 'string' ? d.saved_at : null,
    response: {
      ...emptyResponse(),
      ledger: arr(r.ledger), events: arr(r.events), planLogs: arr(r.planLogs), planHistory: arr(r.planHistory),
      version: Number.isFinite(r.version) ? r.version : 0,
      staleReason: r.staleReason ?? null,
      forecastRevision: Number.isFinite(r.forecastRevision) ? r.forecastRevision : 0,
      lastRevision: r.lastRevision ?? null,
    },
  }
}
