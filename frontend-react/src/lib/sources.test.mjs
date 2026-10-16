// Guards the ONE canonical rainfall-source mapping and the "no stale / internal text on screen" rules.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { SOURCE_KINDS, SCENARIO_LABELS, activeSource, kindOf, scenarioLabel, scenarioSubtitle, utcClock } from './sources.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f)
  return statSync(p).isDirectory() ? walk(p) : /\.jsx?$/.test(f) ? [p] : []
})
// Rendered text only: strip comments so developer notes may still explain history.
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')
const UI = walk(SRC).map((p) => stripComments(readFileSync(p, 'utf8'))).join('\n')

test('every source kind has an operator name, a badge and a status — never UNKNOWN', () => {
  for (const [k, v] of Object.entries(SOURCE_KINDS)) {
    for (const field of ['name', 'badge', 'status', 'tone', 'subtitle', 'method', 'mode']) assert.ok(v[field], `${k}.${field}`)
    assert.doesNotMatch(`${v.badge} ${v.status}`, /unknown/i)
  }
  assert.equal(SOURCE_KINDS.live.status, 'VERIFIED')
  assert.equal(SOURCE_KINDS.ecmwf.status, 'VERIFIED')
  assert.equal(SOURCE_KINDS.replay.status, 'HISTORICAL REAL')
  assert.equal(SOURCE_KINDS.scenario.status, 'SCENARIO')
})

test('radar reads as IMD DWR / RADAR-DERIVED while its scientific class stays ESTIMATED', () => {
  const r = SOURCE_KINDS.radar
  assert.equal(r.name, 'IMD Mumbai-Veravali DWR')
  assert.equal(r.badge, 'RADAR-DERIVED')
  assert.equal(r.status, 'RADAR-DERIVED ESTIMATE')
  assert.equal(r.mode, 'ESTIMATED')
  assert.match(r.method, /image-derived rainfall estimate/)
  assert.doesNotMatch(JSON.stringify(r), /official|QPE|nowcast|gridded/i)
})

test('internal enums and ids map to operator labels and never appear in them', () => {
  assert.equal(kindOf({ sourceType: 'radar_image_derived' }), 'radar')
  assert.equal(kindOf({ sourceType: 'live_observation' }), 'live')
  assert.equal(kindOf({ sourceType: 'ecmwf_forecast' }), 'ecmwf')
  assert.equal(kindOf({ scenarioId: 'july2005' }), 'replay')
  assert.equal(kindOf({ scenarioId: 'heavy' }), 'scenario')
  // what is DISPLAYED (values), not the lookup keys
  const shown = JSON.stringify([Object.values(SOURCE_KINDS), Object.values(SCENARIO_LABELS)])
  assert.doesNotMatch(shown, /imd_sri|imd_radar|live_observation|radar_image_derived|ecmwf_forecast/)
  assert.deepEqual(Object.values(SCENARIO_LABELS).map((s) => s.label), [
    'Moderate steady rain', 'Heavy steady rain', 'Cloudburst', '26 July 2005 replay',
    'IMD live observation', 'IMD live observation', 'IMD Mumbai-Veravali DWR', 'Best available source', 'Demo scenario',   // D-55
  ])
  assert.equal(scenarioLabel('custom', 'Pulse storm (90 mm/h x 1 h)'), 'Pulse storm')
  assert.equal(scenarioSubtitle('heavy', 'ignored'), '50 mm/h for 2 hours')
})

test('the header source follows the ACTIVE source: the run while current, else the selection', () => {
  const radarRun = { scenario_id: 'imd_sri', provenance: { rainfall_source: { source_type: 'radar_image_derived' } } }
  assert.deepEqual(pick(activeSource({ run: radarRun, isStale: false, scenarioId: 'imd_sri' })), ['IMD Mumbai-Veravali DWR', true])
  // user switched to the ecmwf option (shown as IMD LIVE, D-55) but has not run it: the header must not keep
  // showing radar, nor claim the new source ran
  assert.deepEqual(pick(activeSource({ run: radarRun, isStale: true, scenarioId: 'ecmwf' })), ['IMD live observation', false])
  // and the reverse: never ECMWF while radar is selected
  const ecmwfRun = { scenario_id: 'ecmwf', provenance: { rainfall_source: { source_type: 'ecmwf_forecast' } } }
  assert.deepEqual(pick(activeSource({ run: ecmwfRun, isStale: true, scenarioId: 'imd_sri' })), ['IMD Mumbai-Veravali DWR', false])
  const replay = { scenario_id: 'july2005', scenario_name: '26 July 2005 replay (Santacruz gauge, hourly)', provenance: {} }
  assert.deepEqual(pick(activeSource({ run: replay, isStale: false, scenarioId: 'july2005' })), ['26 July 2005 replay', true])
  const heavy = { scenario_id: 'heavy', scenario_name: 'Heavy steady rain (50 mm/h x 2 h)', provenance: {} }
  assert.equal(activeSource({ run: heavy, isStale: false, scenarioId: 'heavy' }).badge, 'SCENARIO')
  assert.deepEqual(pick(activeSource({ run: null, isStale: false, scenarioId: 'live' })), ['IMD live observation', false])
})
const pick = (s) => [s.name, s.ran]

test('utcClock never invents a time', () => {
  assert.equal(utcClock('2026-09-18T06:23:33+00:00'), '06:23 UTC')
  assert.equal(utcClock(null), null)
  assert.equal(utcClock('not a date'), null)
})

test('no stale or prototype text is rendered anywhere in the UI', () => {
  for (const re of [
    /temporary/i, /access is pending/i, /IMD access pending/i, /MUNICIPAL NOWCAST/, /NOT AN ISSUED WARNING/,
    /Traffic data: not connected/i, /Out of date\./, /causal chain/i, /Attributed Factor/,
  ]) assert.doesNotMatch(UI, re)
  assert.match(UI, /URBAN FLOOD INTELLIGENCE/)
  assert.match(UI, /DRAFT • NOT ISSUED/)
  assert.match(UI, /Requires authorized government issuance\./)
  assert.match(UI, /FLOOD-RISK ROUTING/)
  assert.match(UI, /Traffic conditions are not included\./)
  assert.match(UI, /Route snapshot: T\+/)
  assert.match(UI, /No flooded streets at this time/)
  assert.match(UI, /View provenance details/)
})

test('every network call goes through the configurable API origin', () => {
  const calls = UI.match(/fetch\(([^)]*)\)/g) || []
  assert.ok(calls.length >= 3)
  for (const c of calls) assert.match(c, /apiUrl\(/, `${c} bypasses VITE_API_BASE_URL`)
  assert.doesNotMatch(UI, /localhost|127\.0\.0\.1/)
})

test('an auto run shows the source it really used, and CACHED / DEMO are never upgraded to live', () => {
  const auto = (label, source_type, extra = {}) => ({
    scenario_id: 'auto',
    provenance: { rainfall_source: { source_type, detail: { source_status: { label, fell_back: label !== 'LIVE', ...extra } } } },
  })
  const pickAll = (run) => { const s = activeSource({ run, isStale: false, scenarioId: 'auto' }); return [s.name, s.badge, s.fellBack] }
  assert.deepEqual(pickAll(auto('LIVE', 'live_observation')), ['IMD live observation', 'IMD LIVE', false])
  assert.deepEqual(pickAll(auto('RADAR-DERIVED', 'radar_image_derived')), ['IMD Mumbai-Veravali DWR', 'IMD DWR RADAR-DERIVED', true])
  assert.deepEqual(pickAll(auto('FORECAST', 'ecmwf_forecast')), ['ECMWF NWP forecast', 'ECMWF NWP', true])
  const cached = activeSource({ run: auto('CACHED', 'live_observation', { cached: { age_min: 42, original_label: 'LIVE' } }), isStale: false, scenarioId: 'auto' })
  assert.equal(cached.badge, 'CACHED'); assert.doesNotMatch(cached.badge, /LIVE/); assert.equal(cached.cached.age_min, 42)
  assert.deepEqual(pickAll(auto('DEMO', 'scenario')), ['Demo scenario', 'DEMO', true])
  assert.deepEqual([activeSource({ run: null, isStale: false, scenarioId: 'auto' }).name], ['Best available source'])
  assert.equal(activeSource({ run: auto('BOGUS', 'ecmwf_forecast'), isStale: false, scenarioId: 'auto' }).badge, 'FORECAST')
  // before any run the selection is not a claim about data: no IMD LIVE badge for an unrun auto selection
  assert.doesNotMatch(activeSource({ run: null, isStale: false, scenarioId: 'auto' }).badge, /LIVE/)
})

test('final-pass UI: hotspots card, street onset, source health and rotation-free token handling', () => {
  assert.match(UI, /Flood Hotspots/)
  assert.match(UI, /run\.hotspots/)                          // delivered with the run, not fetched by run_id
  assert.match(UI, /ONSET/)
  assert.match(UI, /Nearby drainage network is over capacity/)
  assert.doesNotMatch(UI, /this (manhole|drain) caused/i)
  assert.doesNotMatch(UI, /\bsafest route\b/i)
  assert.match(UI, /getDataStatus\(\)/)                       // compact source-health row
  // the browser never handles IMD / TTS / admin credentials
  assert.doesNotMatch(UI, /IMD_API_TOKEN|GOOGLE_TTS_API_KEY|FLOODNET_ADMIN_TOKEN|X-Admin-Token|imd-token/)
  assert.doesNotMatch(UI, /VITE_[A-Z_]*(KEY|TOKEN|SECRET)/)
})

test('IMD live is only offered as available when the server says the token can answer', async () => {
  const { imdLiveUsable } = await import('./useDataStatus.js')
  for (const st of ['VALID', 'EXPIRING_SOON']) assert.equal(imdLiveUsable({ imd_auth_status: st }), true)
  for (const st of ['EXPIRED', 'UNAVAILABLE']) assert.equal(imdLiveUsable({ imd_auth_status: st }), false)
  assert.equal(imdLiveUsable(null), true)          // unknown (e.g. status not loaded yet) never blocks the selector
  assert.match(UI, /IMD sign-in unavailable right now/)
})

test('the IMD badge never says IMD LIVE after a failed IMD request', async () => {
  const { imdLiveBadge } = await import('./useDataStatus.js')
  const b = (d) => imdLiveBadge(d).text
  assert.equal(b({ imd_auth_status: 'VALID', imd_refresh_status: 'idle', imd_live: { ok: true } }), 'IMD LIVE')
  assert.equal(b({ imd_auth_status: 'VALID', imd_refresh_status: 'renewed', imd_live: { ok: true } }), 'IMD LIVE')
  assert.equal(b({ imd_auth_status: 'EXPIRED', imd_refresh_status: 'idle' }), 'IMD AUTH EXPIRED')
  assert.equal(b({ imd_auth_status: 'EXPIRED', imd_refresh_status: 'refreshing' }), 'IMD RENEWING')
  assert.equal(b({ imd_auth_status: 'EXPIRED', imd_refresh_status: 'failed' }), 'IMD UNAVAILABLE')
  assert.equal(b({ imd_auth_status: 'UNAVAILABLE', imd_refresh_status: 'idle' }), 'IMD UNAVAILABLE')
  assert.equal(b({ imd_auth_status: 'VALID', imd_refresh_status: 'idle', imd_live: { ok: false } }), 'IMD UNAVAILABLE')
  assert.equal(b(null), 'IMD')
})

test('with automatic renewal: RENEWING -> LIVE, UNAVAILABLE only when renewal fails', async () => {
  const { imdLiveBadge, imdLiveUsable } = await import('./useDataStatus.js')
  const auto = { imd_auto_renewal: true }
  assert.equal(imdLiveBadge({ ...auto, imd_auth_status: 'EXPIRED', imd_refresh_status: 'idle' }).text, 'IMD RENEWING')
  assert.equal(imdLiveBadge({ ...auto, imd_auth_status: 'EXPIRED', imd_refresh_status: 'refreshing' }).text, 'IMD RENEWING')
  assert.equal(imdLiveBadge({ ...auto, imd_auth_status: 'VALID', imd_refresh_status: 'renewed', imd_live: { ok: true } }).text, 'IMD LIVE')
  assert.equal(imdLiveBadge({ ...auto, imd_auth_status: 'EXPIRED', imd_refresh_status: 'failed' }).text, 'IMD UNAVAILABLE')
  assert.equal(imdLiveUsable({ ...auto, imd_auth_status: 'EXPIRED', imd_refresh_status: 'idle' }), true)
  assert.equal(imdLiveUsable({ ...auto, imd_auth_status: 'EXPIRED', imd_refresh_status: 'failed' }), false)
  assert.equal(imdLiveUsable({ imd_auth_status: 'EXPIRED', imd_refresh_status: 'idle' }), false)
})

test('RENEWING auth state reads IMD RENEWING and does not block IMD live', async () => {
  const { imdLiveBadge, imdLiveUsable } = await import('./useDataStatus.js')
  const d = { imd_auth_status: 'RENEWING', imd_refresh_status: 'refreshing', imd_auto_renewal: true }
  assert.equal(imdLiveBadge(d).text, 'IMD RENEWING')
  assert.equal(imdLiveUsable(d), true)
})

test('the browser bundle source never handles IMD credentials or tokens', () => {
  for (const word of ['IMD_EMAIL', 'IMD_PASSWORD', 'IMD_API_KEY', 'IMD_API_TOKEN', 'access_token', 'Authorization',
    'token.php']) {
    assert.ok(!UI.includes(word), `frontend source mentions ${word}`)
  }
})
