// Incident state survives a forecast refresh (DECISION_MATRIX D-33; Agent D matrix section 2, F1-F6; Agent C T18).
// Written BEFORE the implementation (Phase 3, tests first). Targets the pure module src/lib/incident.js:
//   emptyIncident(), applyRunToIncident(prev, newRun), replanProposal(state), serializeIncident(state),
//   deserializeIncident(json)
// plus source-text wiring checks on FloodNetContext.jsx / the UI (the established pattern of sources.test.mjs).
// The module is imported inside each test so every test reports its own failure reason.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const load = () => import('./incident.js')
const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f)
  return statSync(p).isDirectory() ? walk(p) : /\.jsx?$/.test(f) ? [p] : []
})
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
const CTX = readFileSync(join(SRC, 'state', 'FloodNetContext.jsx'), 'utf8')
const UI = walk(SRC).map((p) => stripComments(readFileSync(p, 'utf8'))).join('\n')

// A run as FloodNetContext holds it after decodeRun(): only the fields the incident helpers may look at.
const run = (id, horizon = 180) => ({ run_id: id, scenario_id: 'heavy', frames_t_min: [0, 5, horizon], data_mode: 'REAL',
  bundle: { t_min: [0, 5, horizon], streets: { seg_ids: ['S1'], depth_m: new Float32Array([0, 0, 0.3]) } } })

const ledger = [
  { assignment_id: 'A1', status: 'DISPATCHED', settlement_id: 'S1', team_id: 'T1', vehicle_id: 'V1', shelter_id: 'SH1', people: 25,
    plan_version: 2, legs: [{ kind: 'to_settlement', depart_t_min: 0, arrive_t_min: 6, done_t_min: 16, route: { type: 'LineString', coordinates: [[72.84, 19.01]] },
      segments: [{ seg_id: 'x', name: 'Tilak Road', enter_t_min: 0, exit_t_min: 6, to_node: 3, status: 'USABLE', depth_cm: 0 }] }],
    history: [{ status: 'PROPOSED', t_min: 0, by: 'planner' }, { status: 'ACCEPTED', t_min: 1, by: 'operator' }, { status: 'DISPATCHED', t_min: 2, by: 'operator' }] },
  { assignment_id: 'A2', status: 'ACCEPTED', settlement_id: 'S2', team_id: 'T2', vehicle_id: 'V2', shelter_id: 'SH2', people: 20,
    plan_version: 2, legs: [{ kind: 'to_settlement', depart_t_min: 10, arrive_t_min: 18, done_t_min: 28, route: null, segments: [] }],
    history: [{ status: 'PROPOSED', t_min: 0, by: 'planner' }, { status: 'ACCEPTED', t_min: 1, by: 'operator' }] },
  { assignment_id: 'A3', status: 'PROPOSED', settlement_id: 'S3', team_id: 'T1', vehicle_id: 'V1', shelter_id: 'SH1', people: 15,
    plan_version: 2, legs: [], history: [{ status: 'PROPOSED', t_min: 0, by: 'planner' }] },
]

async function activeIncident() {
  const { emptyIncident } = await load()
  const s = emptyIncident()
  return {
    ...s,
    runId: 'run-A', currentT: 20,
    roadEvents: [{ event_id: 'C1', seg_id: 'x', name: 'Tilak Road', kind: 'closure', received_t_min: 5, applies_from_t_min: 5, source: 'operator report (SIMULATED)', source_tier: 'OPERATOR' }],
    response: {
      ...s.response, result: { plan_version: 2, sim_t_min: 20, assignments: ledger, summary: { unmet: 0 } }, ledger, version: 2,
      events: [{ event_id: 'E1', kind: 'vehicle_unavailable', vehicle_id: 'V2', received_t_min: 15, applies_from_t_min: 15, reason: 'breakdown', source: 'operator report (SIMULATED)' }],
      planLogs: [{ t: 0, lines: ['plan v1 built'] }, { t: 20, lines: ['plan v2 built'] }],
      planHistory: [{ plan_version: 1, sim_t_min: 0 }, { plan_version: 2, sim_t_min: 20 }],
    },
  }
}

test('F1: a forecast refresh keeps the incident and clears only run-bound results', async () => {
  const { applyRunToIncident } = await load()
  const prev = await activeIncident()
  const next = applyRunToIncident(prev, run('run-B'))
  assert.deepEqual(next.roadEvents, prev.roadEvents)
  assert.deepEqual(next.response.ledger, prev.response.ledger)
  assert.deepEqual(next.response.events, prev.response.events)
  assert.deepEqual(next.response.planLogs, prev.response.planLogs)
  assert.deepEqual(next.response.planHistory, prev.response.planHistory)
  assert.equal(next.response.version, 2)
  assert.equal(next.response.result, null)
  assert.equal(next.response.loading, false)
  assert.equal(next.response.error, null)
  assert.equal(next.response.staleReason, 'forecast changed')
  assert.equal(next.runId, 'run-B')
  // the original is untouched (state updates are immutable)
  assert.equal(prev.response.result.plan_version, 2)
  assert.equal(prev.runId, 'run-A')
})

test('F1/F2: a refresh records the revision and produces a replan proposal with mission counts', async () => {
  const { applyRunToIncident, replanProposal } = await load()
  const prev = await activeIncident()
  const next = applyRunToIncident(prev, run('run-B'))
  assert.equal(next.response.forecastRevision, 1)
  assert.deepEqual(next.response.lastRevision, { from_run_id: 'run-A', to_run_id: 'run-B', at_sim_t: 20 })
  const twice = applyRunToIncident(next, run('run-C'))
  assert.equal(twice.response.forecastRevision, 2)
  assert.deepEqual(twice.response.lastRevision, { from_run_id: 'run-B', to_run_id: 'run-C', at_sim_t: 20 })
  const p = replanProposal(next)
  assert.equal(typeof p.reason, 'string')
  assert.match(p.reason, /forecast/i)
  assert.equal(p.underway, 1)                      // A1 DISPATCHED
  assert.equal(p.accepted, 1)                      // A2
  assert.equal(p.proposed, 1)                      // A3
  assert.equal(p.suggested_t, 20)
  // no proposal while nothing is stale
  assert.equal(replanProposal(prev), null)
})

test('F3: newIncident / emptyIncident resets everything', async () => {
  const { emptyIncident } = await load()
  const e = emptyIncident()
  assert.deepEqual(e.roadEvents, [])
  assert.deepEqual(e.response.ledger, [])
  assert.deepEqual(e.response.events, [])
  assert.deepEqual(e.response.planLogs, [])
  assert.deepEqual(e.response.planHistory, [])
  assert.equal(e.response.version, 0)
  assert.equal(e.response.result, null)
  assert.equal(e.response.staleReason, null)
  assert.equal(e.response.forecastRevision, 0)
  assert.equal(e.response.loading, false)
  assert.equal(e.response.error, null)
  assert.equal(e.runId, null)
  // calling it twice gives independent objects
  assert.notEqual(emptyIncident().roadEvents, e.roadEvents)
})

test('F4: a run with no active incident is a plain reset without a proposal', async () => {
  const { emptyIncident, applyRunToIncident, replanProposal } = await load()
  const next = applyRunToIncident(emptyIncident(), run('run-A'))
  assert.equal(next.runId, 'run-A')
  assert.equal(next.response.staleReason, null)
  assert.equal(next.response.forecastRevision, 0)
  assert.equal(replanProposal(next), null)
  assert.equal(next.currentT, 0)                   // first frame
})

test('F5: the sim clock survives a refresh and is clamped to the new horizon', async () => {
  const { applyRunToIncident } = await load()
  const prev = await activeIncident()
  assert.equal(applyRunToIncident(prev, run('run-B', 180)).currentT, 20)
  assert.equal(applyRunToIncident({ ...prev, currentT: 120 }, run('run-B', 60)).currentT, 60)
})

test('F6: serialize/deserialize round trip; corrupt or old schema returns null; the run bundle is never persisted', async () => {
  const { serializeIncident, deserializeIncident, applyRunToIncident } = await load()
  const prev = applyRunToIncident(await activeIncident(), run('run-B'))
  const withRun = { ...prev, run: run('run-B') }   // even if a caller passes the run, it must not be written
  const text = serializeIncident(withRun)
  assert.equal(typeof text, 'string')
  const parsed = JSON.parse(text)
  assert.equal(parsed.schema_version, 'floodnet.incident.v1')
  assert.doesNotMatch(text, /"bundle"/)
  assert.doesNotMatch(text, /depth_m/)
  const back = deserializeIncident(text)
  assert.deepEqual(back.roadEvents, prev.roadEvents)
  assert.deepEqual(back.response.ledger, prev.response.ledger)
  assert.deepEqual(back.response.events, prev.response.events)
  assert.deepEqual(back.response.planLogs, prev.response.planLogs)
  assert.deepEqual(back.response.planHistory, prev.response.planHistory)
  assert.equal(back.response.version, prev.response.version)
  assert.equal(back.response.forecastRevision, prev.response.forecastRevision)
  assert.equal(back.response.staleReason, prev.response.staleReason)
  assert.equal(back.runId, 'run-B')
  assert.equal(back.response.loading, false)       // never restored mid-request
  for (const bad of [null, undefined, '', '{', '[]', '{"schema_version":"floodnet.incident.v0"}', JSON.stringify({ schema_version: 'x' }), '42']) {
    assert.equal(deserializeIncident(bad), null, `deserializeIncident(${JSON.stringify(bad)})`)
  }
})

test('wiring: applyRun and runCompare no longer wipe the incident; newIncident exists', () => {
  const body = (name) => {
    const start = CTX.indexOf(`const ${name} = useCallback(`)
    assert.ok(start >= 0, `${name} not found in FloodNetContext.jsx`)
    const end = CTX.indexOf('\n  }, [', start)
    return CTX.slice(start, end)
  }
  for (const name of ['applyRun', 'runCompare']) {
    const b = body(name)
    assert.doesNotMatch(b, /setRoadEvents\(\[\]\)/, `${name} still clears roadEvents`)
    assert.doesNotMatch(b, /setResponse\(emptyResponse\(\)\)/, `${name} still clears the response state`)
  }
  assert.match(CTX, /newIncident/)
  assert.match(CTX, /from '\.\.\/lib\/incident\.js'/)
  assert.match(CTX, /localStorage/)
})

test('wiring: the UI offers "New incident" and a replan proposal; no bare Reset / Clear log remain', () => {
  assert.match(UI, /New incident/)
  assert.match(UI, /Forecast (changed|refreshed)/i)
  assert.doesNotMatch(UI, />Reset</)
  assert.doesNotMatch(UI, />Clear log</)
})

test('an incident has an id that survives serialisation; a stored incident without one gets one', async () => {
  const m = await import('./incident.js')
  const s = m.emptyIncident()
  assert.match(s.incidentId, /^INC-[A-Za-z0-9]{8,}$/)
  assert.notEqual(m.emptyIncident().incidentId, s.incidentId)
  const back = m.deserializeIncident(m.serializeIncident({ ...s, roadEvents: [{ event_id: 'C1' }] }))
  assert.equal(back.incidentId, s.incidentId)
  const legacy = JSON.parse(m.serializeIncident(s)); delete legacy.incidentId
  assert.match(m.deserializeIncident(JSON.stringify(legacy)).incidentId, /^INC-/)
})
