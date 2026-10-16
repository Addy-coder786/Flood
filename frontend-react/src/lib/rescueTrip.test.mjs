// Rescue trips on the Roads page (DECISIONS D-49): route style, lifecycle, ETA labels, freshness, vehicle choice.
// Written before lib/rescueTrip.js existed.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import {
  ROUTE_STYLE, DIRECT_STAGES, lifecycle, etaRows, freshnessChips, vehicleOptions, nextSegment, positionText,
  isDirect, tripTitle,
} from './rescueTrip.js'
import { ACCESS_STATUS, CURRENT_STATUS_ROUTE_COLOR } from './roadAccess.js'
import { SEVERITY_COLOR } from './severity.js'

const rgb = (h) => { const n = parseInt(h.slice(1, 7), 16); return [n >> 16, (n >> 8) & 255, n & 255] }
const lum = (c) => { const [r, g, b] = rgb(c).map((v) => { v /= 255; return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
const contrast = (a, b) => { const [x, y] = [lum(a), lum(b)]; return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05) }
const hue = (c) => { const [r, g, b] = rgb(c).map((v) => v / 255); const mx = Math.max(r, g, b), d = mx - Math.min(r, g, b); if (!d) return 0
  let h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; h *= 60; return h < 0 ? h + 360 : h }
const hueGap = (a, b) => { const d = Math.abs(hue(a) - hue(b)) % 360; return Math.min(d, 360 - d) }

test('the selected route stands apart from every flood, status and baseline colour', () => {
  const core = ROUTE_STYLE.core.color
  const warn = [...Object.values(SEVERITY_COLOR).filter((c) => c !== '#6B7280'), ...['USABLE', 'UNCERTAIN', 'AT_RISK'].map((k) => ACCESS_STATUS[k].color), CURRENT_STATUS_ROUTE_COLOR]
  for (const c of warn) assert.ok(hueGap(core, c) >= 30, `route core ${core} is too close in hue to ${c}`)
  // a dark outline and a white halo make it readable on the light basemap and over any overlay
  assert.ok(contrast(ROUTE_STYLE.outline.color, '#F2EFE9') >= 7, 'outline must be dark on the light basemap')
  assert.ok(contrast(ROUTE_STYLE.core.color, '#F2EFE9') >= 3, 'core must reach 3:1 (WCAG 1.4.11) on the basemap')
  assert.ok(ROUTE_STYLE.halo.weight > ROUTE_STYLE.outline.weight && ROUTE_STYLE.outline.weight > ROUTE_STYLE.core.weight)
  // alternatives are thinner, dashed and muted
  assert.ok(ROUTE_STYLE.alternative.weight < ROUTE_STYLE.core.weight && ROUTE_STYLE.alternative.dashArray)
  assert.ok(ROUTE_STYLE.alternative.opacity < 1)
})

test('direct-trip lifecycle marks done, current and next stages; exceptions are explicit', () => {
  assert.deepEqual(DIRECT_STAGES, ['PLANNED', 'ACCEPTED', 'EN_ROUTE', 'ARRIVED', 'COMPLETED'])
  const l = lifecycle({ kind: 'direct', stage: 'EN_ROUTE' })
  assert.deepEqual(l.steps.map((s) => s.state), ['done', 'done', 'current', 'todo', 'todo'])
  assert.equal(l.exception, null)
  const ab = lifecycle({ kind: 'direct', stage: 'ABORTED', stage_history: [{ stage: 'PLANNED' }, { stage: 'ABORTED', reason: 'road gone' }] })
  assert.match(ab.exception, /Aborted.*road gone/)
  const esc = lifecycle({ kind: 'direct', stage: 'EN_ROUTE', escalation: { open: true, reason: 'no feasible route' } })
  assert.match(esc.exception, /Escalated.*no feasible route/)
  assert.equal(lifecycle({ kind: 'direct', stage: 'COMPLETED' }).steps.at(-1).state, 'done')
})

test('ETA rows keep modelled, assumed and unknown parts apart and never print a number for unknown traffic', () => {
  const eta = {
    eta_min: 7.4, free_flow_min: 6.1, flood_delay_min: 1.3,
    components: [
      { key: 'base', label: 'ASSUMED ROAD SPEEDS', minutes: 6.1, detail: 'speeds by road type' },
      { key: 'flood', label: 'MODELLED FLOOD DELAY', minutes: 1.3, detail: '2 roads slowed' },
      { key: 'traffic', label: 'UNKNOWN', minutes: null, detail: 'no live traffic feed' },
    ],
  }
  const rows = etaRows(eta, { label: 'SIMULATED' })
  assert.deepEqual(rows.map((r) => r.label), ['ASSUMED ROAD SPEEDS', 'MODELLED FLOOD DELAY', 'UNKNOWN'])
  assert.equal(rows[0].value, '6.1 min')
  assert.equal(rows[1].value, '+1.3 min')
  assert.match(rows[1].note, /SIMULATED forecast/)
  assert.equal(rows[2].value, 'not included')
  assert.doesNotMatch(rows.map((r) => r.value).join(' '), /LIVE/)
  assert.deepEqual(etaRows(null), [])
})

test('freshness chips: simulated, stale and unknown never look like live data', () => {
  const chips = freshnessChips({
    forecast: { label: 'SIMULATED', detail: 'forecast from SYNTHETIC rainfall' },
    closures: { label: 'OPERATIONS LOG', events_known: 2, detail: 'x' },
    traffic: { label: 'UNKNOWN', detail: 'no feed' },
    vehicle: { label: 'STALE', age_min: 75, detail: 'seed' },
  })
  const by = Object.fromEntries(chips.map((c) => [c.key, c]))
  assert.equal(by.forecast.tone, 'sim')
  assert.equal(by.traffic.tone, 'warn')
  assert.equal(by.vehicle.tone, 'warn')
  assert.equal(by.closures.tone, 'ok')
  assert.ok(chips.every((c) => !/^LIVE/.test(c.label)))
})

test('vehicle options use registry status and active trips; types without a record are labelled untracked', () => {
  const resources = [
    { kind: 'vehicle', id: 'V1', name: 'Fire truck', type: 'truck', status: 'AVAILABLE', source: 'SIMULATED seed', stale: false },
    { kind: 'vehicle', id: 'V2', name: 'Police truck', type: 'truck', status: 'OUT_OF_SERVICE', out_of_service_reason: 'MECHANICAL', source: 'SIMULATED seed' },
    { kind: 'vehicle', id: 'V3', name: 'Ambulance', type: 'ambulance', status: 'AVAILABLE', source: 'dispatcher entry', stale: true, age_min: 90 },
    { kind: 'team', id: 'T1', name: 'NDRF' },
  ]
  const trips = [{ trip_id: 'TR4', vehicle_id: 'V3', stage: 'EN_ROUTE' }]
  const { registry, types } = vehicleOptions(resources, trips, { car: 30, truck: 60, ambulance: 40 })
  assert.deepEqual(registry.map((o) => o.value), ['V1', 'V2', 'V3'])
  assert.equal(registry[0].disabled, false)
  assert.match(registry[1].reason, /OUT_OF_SERVICE.*MECHANICAL/)
  assert.equal(registry[1].disabled, true)
  assert.match(registry[2].reason, /on trip TR4/)
  assert.match(registry[0].label, /60 cm/)
  assert.ok(types.every((t) => t.value.startsWith('type:') && /not tracked/.test(t.label)))
})

test('next segment comes from the remaining route of the last assessment', () => {
  const trip = { remaining: [{ segments: [{ seg_id: 's12', name: 'South Rd', status: 'USABLE' }, { seg_id: 'e25' }] }] }
  assert.deepEqual(nextSegment(trip), { seg_id: 's12', name: 'South Rd', status: 'USABLE' })
  assert.equal(nextSegment({ remaining: [] }), null)
  assert.equal(nextSegment({}), null)
})

test('position text gives age, freshness, source and the recorded time', () => {
  const p = { freshness: 'FRESH', age_s: 12, source: 'DEVICE_GPS', recorded_at: '2026-10-11T05:00:00Z', accuracy_m: 8 }
  assert.match(positionText(p), /12 s old/)
  assert.match(positionText(p), /10:30/)                       // IST
  assert.match(positionText({ ...p, source: 'SIMULATED' }), /SIMULATED/)
  assert.match(positionText({ ...p, freshness: 'LOST', age_s: 600 }), /lost.*10 min/i)
  assert.equal(positionText(null), 'No GPS shared yet')
})

test('trip titles and kinds', () => {
  assert.equal(isDirect({ kind: 'direct' }), true)
  assert.equal(isDirect({}), false)
  assert.equal(tripTitle({ trip_id: 'TR2', kind: 'direct', names: { origin: 'Base', destination: 'KEM' } }), 'TR2 · Base → KEM')
  assert.equal(tripTitle({ trip_id: 'TR1', settlement_id: 'S1', shelter_id: 'SH1', names: {} }), 'TR1 · S1 → SH1')
})

test('a vehicle committed in the response plan is busy too (QA 2026-10-11 H2)', () => {
  const resources = [{ kind: 'vehicle', id: 'V1', name: 'Fire truck', type: 'truck', status: 'AVAILABLE', source: 'dispatcher entry' }]
  const ledger = [{ assignment_id: 'A3', vehicle_id: 'V1', status: 'DISPATCHED' }, { assignment_id: 'A9', vehicle_id: 'V1', status: 'COMPLETED' }]
  const { registry } = vehicleOptions(resources, [], { truck: 60 }, ledger)
  assert.equal(registry[0].disabled, true)
  assert.match(registry[0].reason, /assigned in plan A3/)
  assert.equal(vehicleOptions(resources, [], { truck: 60 }, [{ assignment_id: 'A1', vehicle_id: 'V1', status: 'PROPOSED' }]).registry[0].disabled, false)
})

test('rescue layers are hidden until a rescue view is chosen (2026-10-11, owner request)', async () => {
  const { rescueLayerVisibility, MISSION_LEGEND } = await import('./rescueTrip.js')
  for (const page of ['overview', 'forecast', 'reports', 'alerts', 'replay', 'resources']) {
    assert.deepEqual(rescueLayerVisibility(page, { missions: false, route: true }), { missions: false, trips: false }, page)
  }
  assert.deepEqual(rescueLayerVisibility('response', { missions: false, route: true }), { missions: true, trips: true })
  assert.deepEqual(rescueLayerVisibility('trips', { missions: false, route: true }), { missions: true, trips: true })
  // Roads: the rescue trip being planned or followed, not the whole plan
  assert.deepEqual(rescueLayerVisibility('access', { missions: false, route: true }), { missions: false, trips: true })
  // the "Rescue missions" map layer shows both on any page; the route layer switch still hides trips
  assert.deepEqual(rescueLayerVisibility('overview', { missions: true, route: true }), { missions: true, trips: true })
  assert.deepEqual(rescueLayerVisibility('trips', { missions: false, route: false }), { missions: true, trips: false })
  assert.deepEqual(MISSION_LEGEND.map((r) => r.status), ['PROPOSED', 'ACCEPTED', 'DISPATCHED', 'IN_PROGRESS'])
})

test('live traffic (2026-10-11): chips, colours and the ETA comparison never pass simulated or missing data off as live', async () => {
  const { freshnessChips, TRAFFIC_LEVELS, trafficLines, trafficSummary } = await import('./rescueTrip.js')
  const live = freshnessChips({ traffic: { label: 'LIVE', detail: 'TomTom', fetched_at: '2026-10-11T04:30:00Z' } })[0]
  assert.equal(live.tone, 'live')
  assert.match(live.label, /^LIVE/)
  const off = freshnessChips({ traffic: { label: 'UNAVAILABLE', detail: 'no key' } })[0]
  assert.equal(off.tone, 'warn')
  assert.deepEqual(TRAFFIC_LEVELS.map((l) => l.level), ['normal', 'moderate', 'heavy'])
  assert.ok(TRAFFIC_LEVELS.every((l) => /^#/.test(l.color) && l.label))
  const traffic = { mode: 'LIVE', fetched_at: '2026-10-11T04:30:00Z', sampled: 2, total: 3,
    segments: { a: { level: 'heavy' }, b: { level: 'normal' }, c: { level: 'unknown' } } }
  assert.deepEqual(trafficLines(traffic), [{ seg_id: 'a', level: 'heavy', color: TRAFFIC_LEVELS[2].color }, { seg_id: 'b', level: 'normal', color: TRAFFIC_LEVELS[0].color }])
  assert.deepEqual(trafficLines({ mode: 'UNAVAILABLE', segments: {} }), [])
  const eta = { eta_min: 9.2, eta_without_traffic_min: 6.1, traffic_delay_min: 3.1 }
  const s = trafficSummary(traffic, eta)
  assert.equal(s.mode, 'LIVE')
  assert.match(s.withTraffic, /9\.2 min/)
  assert.match(s.withoutTraffic, /6\.1 min/)
  assert.match(s.updated, /10:00/)                              // IST
  assert.match(s.coverage, /2 of 3/)
  const none = trafficSummary({ mode: 'UNAVAILABLE', note: 'Live traffic unavailable — TOMTOM_API_KEY not configured; using estimated travel time' }, { eta_min: 6.1, traffic_delay_min: null })
  assert.equal(none.mode, 'UNAVAILABLE')
  assert.match(none.text, /Live traffic unavailable — using estimated travel time/)
  assert.equal(none.withTraffic, null)
})
