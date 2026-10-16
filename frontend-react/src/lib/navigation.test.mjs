// Navigation mode on the Roads page (2026-10-11): pure logic for the next-turn card, remaining distance and time,
// hazard warnings, route-change handling, risk-data freshness and GPS status words. Written before lib/navigation.js.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  tokenFromLink, navSummary, warningFor, riskFreshness, gpsStatus, navStageAction, NAV_GPS_INTERVAL_S,
} from './navigation.js'

const VIEW = {
  trip_id: 'TR3', kind: 'direct', stage: 'EN_ROUTE', route_version: 1, assessed_t_min: 2,
  destination: 'KEM Hospital',
  remaining: [{
    kind: 'to_destination', to: 'KEM Hospital', arrive_t_min: 9.5,
    segments: [{ seg_id: 'a', name: 'Tilak Rd', status: 'USABLE', enter_t_min: 2 }, { seg_id: 'b', name: 'Dr Ambedkar Rd', status: 'UNCERTAIN', enter_t_min: 5, depth_cm: 18 }],
    maneuvers: [
      { type: 'depart', instruction: 'Head north on Tilak Rd', distance_m: 420 },
      { type: 'turn', modifier: 'left', instruction: 'Turn left onto Dr Ambedkar Rd', distance_m: 760 },
      { type: 'arrive', instruction: 'Arrive at KEM Hospital', distance_m: 0 },
    ],
  }],
  hazards_ahead: [{ seg_id: 'b', name: 'Dr Ambedkar Rd', status: 'UNCERTAIN', depth_cm: 18, enter_t_min: 5, reason: 'within 20 cm of the limit' }],
  position: { freshness: 'FRESH', age_s: 4, source: 'DEVICE_GPS' },
  route_change: null, escalation: null,
  eta: { forecast_input: { label: 'SIMULATED', detail: 'forecast from SYNTHETIC rainfall' } },
  next_stages: ['ARRIVED', 'COMPLETED'],
}

test('tokenFromLink reads the driver token out of a driver link', () => {
  assert.equal(tokenFromLink('driver#t=abcDEF123_-'), 'abcDEF123_-')
  assert.equal(tokenFromLink('https://x/static/driver#t=zzz'), 'zzz')
  assert.equal(tokenFromLink(''), null)
  assert.equal(tokenFromLink(null), null)
})

test('navSummary: next turn, distance to it, then-instruction, remaining distance and minutes', () => {
  const s = navSummary(VIEW)
  assert.equal(s.next.instruction, 'Head north on Tilak Rd')
  assert.equal(s.next.distance, '420 m')
  assert.equal(s.next.then, 'Turn left onto Dr Ambedkar Rd')
  assert.equal(s.remainingDistance, '1.2 km')            // 420 + 760 m
  assert.equal(s.remainingMinutes, 8)                     // arrive 9.5 - enter 2 = 7.5, rounded up
  assert.equal(s.destination, 'KEM Hospital')
  assert.equal(s.arrived, false)
  const done = navSummary({ ...VIEW, stage: 'ARRIVED', remaining: [] })
  assert.equal(done.arrived, true)
  assert.equal(done.next, null)
  assert.deepEqual(navSummary(null), { next: null, remainingDistance: '–', remainingMinutes: null, destination: null, arrived: false })
})

test('warningFor: escalation first, then a proposed route change, then hazards on the route, else clear', () => {
  assert.equal(warningFor(VIEW).level, 'hazard')
  assert.match(warningFor(VIEW).text, /1 road ahead/)
  assert.match(warningFor(VIEW).text, /Dr Ambedkar Rd/)
  const change = { ...VIEW, route_change: { proposal_id: 'P1', kind: 'REQUIRED', reason: 'Tilak Rd: CLOSED (confirmed closure F1)' } }
  assert.equal(warningFor(change).level, 'reroute')
  assert.match(warningFor(change).text, /new route/i)
  const open = { ...VIEW, open_proposal: { proposal_id: 'P2', kind: 'REQUIRED', reason: 'flooded ahead' } }
  assert.equal(warningFor(open).level, 'reroute')
  assert.match(warningFor(open).text, /safer route found/i)
  const esc = { ...VIEW, escalation: 'no feasible route to KEM Hospital: confirmed road closures block every path' }
  assert.equal(warningFor(esc).level, 'escalated')
  assert.match(warningFor(esc).text, /no safer route/i)
  assert.equal(warningFor({ ...VIEW, hazards_ahead: [] }).level, 'clear')
  assert.match(warningFor({ ...VIEW, hazards_ahead: [] }).text, /not a guarantee/i)
})

test('riskFreshness: says when risk information may be outdated and never calls simulated data live', () => {
  const sim = riskFreshness(VIEW, 2)
  assert.match(sim.text, /SIMULATED/)
  assert.equal(sim.outdated, false)
  const stale = riskFreshness({ ...VIEW, eta: { forecast_input: { label: 'STALE', detail: 'cached' } } }, 2)
  assert.equal(stale.outdated, true)
  assert.match(stale.text, /may be outdated/i)
  const unknown = riskFreshness({ ...VIEW, eta: null }, 2)
  assert.equal(unknown.outdated, true)
  const old = riskFreshness(VIEW, 2 + 31)                 // last check more than 30 model minutes ago
  assert.equal(old.outdated, true)
  assert.match(old.text, /last checked/i)
  assert.doesNotMatch(sim.text + stale.text + unknown.text, /\bLIVE\b/)
})

test('gpsStatus: permission, no fix, fix age, offline; the fix is never simulated here', () => {
  assert.match(gpsStatus({ support: { ok: false, reason: 'needs https' } }).text, /https/)
  assert.equal(gpsStatus({ support: { ok: true }, error: 'Location permission was refused.' }).tone, 'error')
  assert.match(gpsStatus({ support: { ok: true }, fix: null }).text, /waiting/i)
  const fresh = gpsStatus({ support: { ok: true }, fix: { accuracy: 8, at: 1000 }, nowMs: 5000 })
  assert.equal(fresh.tone, 'ok')
  assert.match(fresh.text, /±8 m/)
  const old = gpsStatus({ support: { ok: true }, fix: { accuracy: 8, at: 1000 }, nowMs: 1000 + 70_000 })
  assert.equal(old.tone, 'warn')
  assert.match(old.text, /1 min/)
  assert.match(gpsStatus({ support: { ok: true }, fix: { accuracy: 8, at: 1000 }, nowMs: 5000, offline: true }).text, /offline/i)
  assert.equal(NAV_GPS_INTERVAL_S, 15)
})

test('navStageAction: one button for the stage the driver would press next', () => {
  assert.deepEqual(navStageAction({ stage: 'PLANNED', next_stages: ['ACCEPTED', 'EN_ROUTE', 'ARRIVED', 'COMPLETED'] }), { stage: 'EN_ROUTE', label: 'Start driving', steps: ['ACCEPTED', 'EN_ROUTE'] })
  assert.deepEqual(navStageAction({ stage: 'EN_ROUTE', next_stages: ['ARRIVED', 'COMPLETED'] }), { stage: 'ARRIVED', label: 'I have arrived', steps: ['ARRIVED'] })
  assert.deepEqual(navStageAction({ stage: 'ARRIVED', next_stages: ['COMPLETED'] }), { stage: 'COMPLETED', label: 'Finish trip', steps: ['COMPLETED'] })
  assert.equal(navStageAction({ stage: 'COMPLETED', next_stages: [] }), null)
})
