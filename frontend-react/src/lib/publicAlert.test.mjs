// Run with:  npm run test:unit
//
// Public alert page (DECISIONS D-45, docs/LIVE_OPERATIONS.md §7, research §8): the pure helpers behind
// src/public/PublicAlertApp.jsx and the path routing in src/main.jsx. Written test-first.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  PUBLIC_ID_RE, routeFor, formatIST, formatISTTime, relativeAge, updatedLine, statusDisplay, stateBanners,
  evidenceLabel, ROAD_CLASSES, makeProjector, pathD, polygonD, midPoint, mapAriaLabel, legendRows,
  apiPathFor, versionFromSearch, shortLocality, docTitle, updatesNewestFirst, latestHref,
} from './publicAlert.js'
import * as PA from './publicAlert.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..')
const read = (p) => readFileSync(join(SRC, p), 'utf8')

// ───────────────────────────────────────────── routing (main.jsx)
test('routeFor: alerts/<id> and driver under the app base, everything else is the app', () => {
  const id = 'AbC_d-0123456789xyzQRS'                    // secrets.token_urlsafe(16) shape
  assert.deepEqual(routeFor(`/static/alerts/${id}`, '/static/'), { kind: 'public', id })
  assert.deepEqual(routeFor(`/static/alerts/${id}/`, '/static/'), { kind: 'public', id })
  assert.deepEqual(routeFor(`/alerts/${id}`, '/'), { kind: 'public', id })
  assert.deepEqual(routeFor('/static/driver', '/static/'), { kind: 'driver' })
  assert.deepEqual(routeFor('/static/driver/', '/static/'), { kind: 'driver' })
  assert.deepEqual(routeFor('/driver', '/'), { kind: 'driver' })
  assert.deepEqual(routeFor('/static/', '/static/'), { kind: 'app' })
  assert.deepEqual(routeFor('/static', '/static/'), { kind: 'app' })
  assert.deepEqual(routeFor('/static/dashboard', '/static/'), { kind: 'app' })
  assert.deepEqual(routeFor('/static/driverx', '/static/'), { kind: 'app' })
  assert.deepEqual(routeFor('/static/driver/extra', '/static/'), { kind: 'app' })
  assert.deepEqual(routeFor(`/alerts/${id}`, '/static/'), { kind: 'app' }, 'outside the base is not ours')
  assert.deepEqual(routeFor('/', '/'), { kind: 'app' })
  assert.deepEqual(routeFor('', undefined), { kind: 'app' })
})

test('routeFor: a malformed alert id is a not-found public page, never a fetch', () => {
  for (const bad of ['', 'a b', 'a%20b', 'x'.repeat(65), 'abc/def', 'abc.def', '<script>', 'ab+cd', 'ab=cd']) {
    assert.deepEqual(routeFor(`/static/alerts/${bad}`, '/static/'), { kind: 'public', id: null }, JSON.stringify(bad))
  }
  assert.deepEqual(routeFor('/static/alerts', '/static/'), { kind: 'public', id: null })
  assert.ok(PUBLIC_ID_RE.test('x'.repeat(64)))
  assert.ok(!PUBLIC_ID_RE.test('x'.repeat(65)))
})

test('main.jsx: lazy dynamic imports for every screen, StrictMode and index.css kept', () => {
  const main = read('main.jsx')
  assert.match(main, /import\(['"]\.\/public\/PublicAlertApp\.jsx['"]\)/)
  assert.match(main, /import\(['"]\.\/driver\/DriverApp\.jsx['"]\)/)
  assert.match(main, /import\(['"]\.\/App\.jsx['"]\)/)
  assert.doesNotMatch(main, /^import\s+App\s+from/m, 'a static App import pulls the dashboard into the public page')
  assert.match(main, /StrictMode/)
  assert.match(main, /import ['"]\.\/index\.css['"]/)
  assert.match(main, /routeFor\(/)
  assert.match(main, /import\.meta\.env\.BASE_URL/)
})

test('the public page never statically imports the dashboard, its context, Leaflet or three.js', () => {
  const src = read('public/PublicAlertApp.jsx') + read('public/AlertMap.jsx')
  for (const re of [/from ['"]\.\.\/App/, /FloodNetContext/, /from ['"]leaflet/, /from ['"]three/, /react-leaflet/]) {
    assert.doesNotMatch(src, re)
  }
  assert.doesNotMatch(src, /subscribe/i, 'no subscription button: not implemented')
  assert.doesNotMatch(src, /\bsafe\b/i, 'the public page never calls an area safe')
  assert.match(src, /role="img"/)
  assert.match(src, /OpenStreetMap contributors/)
  assert.match(src, /not an official government warning/)
})

// ───────────────────────────────────────────── time (Asia/Kolkata, independent of the device timezone)
test('formatIST: 12-hour clock in IST with weekday and date, from a UTC ISO time', () => {
  assert.equal(formatIST('2026-10-11T14:10:00Z'), '7:40 pm IST, Sun 11 Oct 2026')
  assert.equal(formatIST('2026-10-10T18:30:00+00:00'), '12:00 am IST, Sun 11 Oct 2026')   // crosses midnight
  assert.equal(formatIST('2026-10-11T06:35:00Z'), '12:05 pm IST, Sun 11 Oct 2026')
  assert.equal(formatIST('2026-12-31T20:00:00Z'), '1:30 am IST, Fri 1 Jan 2027')
  assert.equal(formatISTTime('2026-10-11T03:39:00Z'), '9:09 am IST')
  assert.equal(formatIST(null), null)
  assert.equal(formatIST('not a time'), null)
  assert.equal(formatISTTime(undefined), null)
})

test('relativeAge: plain words, never negative', () => {
  const now = Date.parse('2026-10-11T14:22:00Z')
  assert.equal(relativeAge('2026-10-11T14:10:00Z', now), '12 min ago')
  assert.equal(relativeAge('2026-10-11T14:21:40Z', now), 'just now')
  assert.equal(relativeAge('2026-10-11T14:21:00Z', now), '1 min ago')
  assert.equal(relativeAge('2026-10-11T12:22:00Z', now), '2 h ago')
  assert.equal(relativeAge('2026-10-11T12:07:00Z', now), '2 h 15 min ago')
  assert.equal(relativeAge('2026-10-10T14:00:00Z', now), '1 day ago')
  assert.equal(relativeAge('2026-10-08T14:00:00Z', now), '3 days ago')
  assert.equal(relativeAge('2026-10-11T14:22:30Z', now), 'just now', 'small clock skew')
  assert.equal(relativeAge('2026-10-11T15:22:00Z', now), 'in 1 h')
  assert.equal(relativeAge('bad', now), null)
})

test('updatedLine: "Updated <IST time> (<age>)"', () => {
  const now = Date.parse('2026-10-11T14:22:00Z')
  assert.equal(updatedLine('2026-10-11T14:10:00Z', now), 'Updated 7:40 pm IST, Sun 11 Oct 2026 (12 min ago)')
  assert.equal(updatedLine(null, now), 'Update time not available')
})

// ───────────────────────────────────────────── status, banners, evidence
test('statusDisplay: FloodNet words from the API plus a glyph, never colour alone', () => {
  const a = statusDisplay({ status: 'advisory', status_text: 'Flooding likely – avoid the area' })
  assert.deepEqual([a.glyph, a.text, a.tone], ['!', 'Flooding likely – avoid the area', 'advisory'])
  const w = statusDisplay({ status: 'watch', status_text: 'Flooding possible – plan ahead' })
  assert.equal(w.glyph, '?')
  const e = statusDisplay({ status: 'ended', status_text: 'No longer active' })
  assert.equal(e.glyph, '–')
  const u = statusDisplay({ status: 'weird', status_text: 'Something' })
  assert.equal(u.text, 'Something')
  assert.equal(u.tone, 'unknown')
  for (const s of [a, w, e, u]) assert.ok(s.glyph && s.text)
  // never an authority's level name or a colour word
  assert.doesNotMatch(JSON.stringify([a, w, e]), /red|orange|yellow|amber|warning|alert/i)
})

test('stateBanners: stale, resolved/ended, expired and older-version texts; never "safe"', () => {
  const base = { updated_at: '2026-10-11T14:10:00Z', expires_at: '2026-10-11T20:10:00Z', is_latest: true,
    version: 2, latest_version: 2, status: 'advisory' }
  assert.deepEqual(stateBanners({ ...base, state: 'active' }), [])
  const [stale] = stateBanners({ ...base, state: 'stale' })
  assert.equal(stale.kind, 'stale')
  assert.equal(stale.text, 'This page has not been updated since 7:40 pm IST, Sun 11 Oct 2026. Conditions may have '
    + 'changed. Check BMC or IMD before you travel.')
  const [res] = stateBanners({ ...base, state: 'resolved', status: 'ended' })
  assert.equal(res.kind, 'resolved')
  assert.equal(res.text, 'No longer active as of 7:40 pm IST, Sun 11 Oct 2026. Roads may still be waterlogged. '
    + 'Follow BMC instructions.')
  // an "ended" version that is not resolved yet still reads as no longer active
  assert.equal(stateBanners({ ...base, state: 'active', status: 'ended' })[0].kind, 'resolved')
  const [exp] = stateBanners({ ...base, state: 'expired' })
  assert.equal(exp.kind, 'expired')
  assert.match(exp.text, /expired at 1:40 am IST, Mon 12 Oct 2026/)
  assert.match(exp.text, /Roads may still be waterlogged/)
  const older = stateBanners({ ...base, state: 'active', is_latest: false, version: 1 })
  assert.equal(older[0].kind, 'older')
  assert.equal(older[0].text, 'You are viewing an older version (version 1 of 2).')
  for (const b of [stale, res, exp, ...older]) assert.doesNotMatch(b.text, /\bsafe\b/i)
})

test('evidenceLabel: the three evidence kinds keep distinct, honest labels', () => {
  assert.equal(evidenceLabel({ kind: 'model_estimate' }), 'Model estimate')
  assert.equal(evidenceLabel({ kind: 'reported' }), 'Reported (unconfirmed)')
  assert.equal(evidenceLabel({ kind: 'confirmed_closure' }), 'Confirmed road closure')
  assert.equal(evidenceLabel({ kind: 'reported', label: 'Reported (unconfirmed)' }), 'Reported (unconfirmed)')
  assert.equal(evidenceLabel({ kind: 'other', label: 'X' }), 'X')
})

// ───────────────────────────────────────────── map (inline SVG, no tiles)
test('ROAD_CLASSES: each affected-road class differs by line pattern and words, not colour alone', () => {
  const keys = ['MODEL_ESTIMATE', 'CONFIRMED_CLOSURE', 'REPORTED']
  assert.deepEqual(Object.keys(ROAD_CLASSES), keys)
  const patterns = keys.map((k) => `${ROAD_CLASSES[k].dash}|${ROAD_CLASSES[k].cross}`)
  assert.equal(new Set(patterns).size, 3, 'three distinct line patterns')
  assert.equal(new Set(keys.map((k) => ROAD_CLASSES[k].label)).size, 3)
  assert.equal(ROAD_CLASSES.CONFIRMED_CLOSURE.cross, true)
  assert.equal(ROAD_CLASSES.CONFIRMED_CLOSURE.dash, null)
})

test('makeProjector: equirectangular into the bbox; north up, corners land on the padding', () => {
  const bbox = [72.84, 19.00, 72.85, 19.01]
  const p = makeProjector(bbox, 300, 10)
  assert.deepEqual(p.project([72.84, 19.01]), [10, 10])                    // north-west corner
  const [x, y] = p.project([72.85, 19.00])                                  // south-east corner
  assert.equal(x, 290)
  assert.equal(y, p.height - 10)
  // a degree of longitude is cos(lat) of a degree of latitude: the box is taller than wide in pixels
  const k = Math.cos((19.005 * Math.PI) / 180)
  assert.ok(Math.abs(p.height - (20 + 280 / k)) < 0.2, `height ${p.height}`)
  const [cx, cy] = p.project([72.845, 19.005])
  assert.ok(Math.abs(cx - 150) < 1e-6)
  assert.ok(Math.abs(cy - p.height / 2) < 1e-6)
  assert.equal(makeProjector(null, 300, 10), null)
  assert.equal(makeProjector([1, 1, 1, 1], 300, 10), null, 'degenerate bbox')
})

test('pathD / polygonD / midPoint build compact SVG geometry', () => {
  const p = { project: ([x, y]) => [x * 10, y * 10] }
  assert.equal(pathD([[0, 0], [1, 2], [3.333, 4]], p), 'M0 0L10 20L33.3 40')
  assert.equal(pathD([[0, 0]], p), '')
  assert.equal(pathD(null, p), '')
  assert.equal(polygonD([[[0, 0], [1, 0], [1, 1], [0, 0]]], p), 'M0 0L10 0L10 10L0 0Z')
  assert.equal(polygonD(null, p), '')
  assert.deepEqual(midPoint([[0, 0], [2, 0], [2, 2]], p), [20, 0])
  assert.deepEqual(midPoint([[0, 0], [4, 0]], p), [20, 0])
  assert.equal(midPoint([], p), null)
})

test('mapAriaLabel and legendRows describe the affected roads in words', () => {
  const map = { affected_roads: [
    { type: 'LineString', name: 'Dr Ambedkar Road', class: 'MODEL_ESTIMATE', coordinates: [] },
    { type: 'LineString', name: 'Dr Ambedkar Road', class: 'MODEL_ESTIMATE', coordinates: [] },
    { type: 'LineString', name: 'Tilak Road', class: 'CONFIRMED_CLOSURE', coordinates: [] },
    { type: 'LineString', name: '', class: 'REPORTED', coordinates: [] },
  ], area_outline: null, context_roads: [] }
  assert.equal(mapAriaLabel(map), 'Map of the affected area, north up. Affected roads: Dr Ambedkar Road (model '
    + 'estimate), Tilak Road (confirmed closure), unnamed road (reported, unconfirmed).')
  assert.equal(mapAriaLabel({ affected_roads: [] }), 'Map of the area. No affected roads in this version.')
  const rows = legendRows(map)
  assert.deepEqual(rows.map((r) => [r.key, r.count]), [['MODEL_ESTIMATE', 2], ['CONFIRMED_CLOSURE', 1], ['REPORTED', 1]])
  const none = legendRows({ affected_roads: [map.affected_roads[0]] })
  assert.equal(none.find((r) => r.key === 'REPORTED').count, 0)
})

// ───────────────────────────────────────────── API path, versions, titles, updates
test('apiPathFor / versionFromSearch: only digits pass as a version', () => {
  assert.equal(apiPathFor('abc_D-1'), '/api/public/alerts/abc_D-1')
  assert.equal(apiPathFor('abc', '3'), '/api/public/alerts/abc?v=3')
  assert.equal(apiPathFor('abc', 'x'), '/api/public/alerts/abc')
  assert.equal(versionFromSearch('?v=2'), '2')
  assert.equal(versionFromSearch('?v=latest'), null)
  assert.equal(versionFromSearch('?v=2;drop'), null)
  assert.equal(versionFromSearch(''), null)
  assert.equal(latestHref('/static/alerts/abc'), '/static/alerts/abc')
})

test('shortLocality / docTitle use the headline place, kept short', () => {
  const a = { headline: 'Flooding likely – avoid the area: Hindmata and Dadar TT', locality_text: 'Near Hindmata. Roads: X' }
  assert.equal(shortLocality(a), 'Hindmata and Dadar TT')
  assert.equal(docTitle(a), 'FloodNet advisory – Hindmata and Dadar TT')
  assert.equal(shortLocality({ headline: 'No colon here', locality_text: 'Near Dadar. Roads: Y' }), 'Near Dadar')
  const long = shortLocality({ headline: `x: ${'Road '.repeat(20)}` })
  assert.ok(long.length <= 40 && long.endsWith('…'), long)
  assert.equal(docTitle(null), 'FloodNet advisory')
})

test('updatesNewestFirst sorts by version without mutating the input', () => {
  const u = [{ version: 1 }, { version: 3 }, { version: 2 }]
  assert.deepEqual(updatesNewestFirst(u).map((x) => x.version), [3, 2, 1])
  assert.deepEqual(u.map((x) => x.version), [1, 3, 2])
  assert.deepEqual(updatesNewestFirst(null), [])
})

// ───────────────────────────────────────────── interactive map, plain risk, official feed (2026-10-11)
// Written before the helpers existed. Road names are REAL pilot roads; depths are SYNTHETIC TEST FIXTURE values.

const MAP = {
  bbox: [72.84, 19.01, 72.85, 19.02],
  affected_roads: [
    { type: 'LineString', name: 'Dadasaheb Phalke Road', class: 'MODEL_ESTIMATE', depth_cm: 40, coordinates: [[72.841, 19.011], [72.842, 19.012]] },
    { type: 'LineString', name: 'Dadasaheb Phalke Road', class: 'MODEL_ESTIMATE', depth_cm: 22, coordinates: [[72.842, 19.012], [72.843, 19.013]] },
    { type: 'LineString', name: 'Tilak Road', class: 'CONFIRMED_CLOSURE', depth_cm: null, coordinates: [[72.844, 19.014], [72.845, 19.015]] },
    { type: 'LineString', name: '', class: 'REPORTED', depth_cm: null, coordinates: [[72.846, 19.016], [72.847, 19.017]] },
  ],
}
const ALERT = {
  status: 'advisory', status_text: 'Flooding likely – avoid the area', state: 'active', map: MAP,
  time_window: { from: '2026-10-11T14:10:00Z', to: '2026-10-11T15:10:00Z', text: 'Next 60 min from 11 Oct 19:40 IST' },
  updates: [{ version: 1, time: '2026-10-11T14:10:00Z', what_changed: 'First issue', status_text: 'Flooding possible – plan ahead' },
    { version: 2, time: '2026-10-11T14:30:00Z', what_changed: 'Status raised to Flooding likely – avoid the area', status_text: 'Flooding likely – avoid the area' }],
  evidence: [{ kind: 'model_estimate' }, { kind: 'confirmed_closure' }],
}

test('plainRisk: what the data says, in plain words, with the time horizon; never the word safe', () => {
  const r = PA.plainRisk(ALERT)
  const all = r.lines.join(' ')
  assert.match(all, /up to 40 cm/)
  assert.match(all, /2 road sections/)
  assert.match(all, /1 road confirmed closed by an operator/)
  assert.match(all, /1 road reported closed by the public, not confirmed/)
  assert.match(all, /computer.model estimate/i)
  assert.equal(r.horizon, 'Until 8:40 pm IST (the next 60 min from 7:40 pm IST)')
  const ended = PA.plainRisk({ ...ALERT, status: 'ended', state: 'resolved' })
  assert.match(ended.lines.join(' '), /no longer expects flooding/i)
  assert.match(ended.lines.join(' '), /may still be waterlogged/i)
  assert.equal(ended.horizon, null)
  for (const a of [ALERT, { ...ALERT, status: 'watch' }, { ...ALERT, status: 'ended' }, {}]) {
    assert.doesNotMatch(PA.plainRisk(a).lines.join(' '), /\bsafe\b/i)
  }
  assert.equal(PA.plainRisk({ time_window: { text: 'Next 30 min' } }).horizon, 'Next 30 min')
})

test('evidenceTypes: model / reported / official are separate, and official is never claimed by FloodNet', () => {
  const t = PA.evidenceTypes(ALERT)
  assert.deepEqual(t.map((x) => x.kind), ['model_estimate', 'reported', 'confirmed_closure', 'official'])
  assert.deepEqual(t.map((x) => x.present), [true, false, true, false])
  const official = t.find((x) => x.kind === 'official')
  assert.match(official.text, /SACHET/)
  assert.match(official.text, /does not issue official warnings/i)
})

test('closedRoads lists confirmed closures once each; latestChange is the newest update', () => {
  assert.deepEqual(PA.closedRoads(MAP), ['Tilak Road'])
  assert.deepEqual(PA.closedRoads({}), [])
  assert.equal(PA.latestChange(ALERT).version, 2)
  assert.equal(PA.latestChange({ updates: [ALERT.updates[0]] }), null)        // the first issue is not a change
})

test('roadInfo: tap-to-see text per road, honest about what is an estimate', () => {
  const m = PA.roadInfo(MAP.affected_roads[0])
  assert.equal(m.title, 'Dadasaheb Phalke Road')
  assert.match(m.lines.join(' '), /Model estimate/)
  assert.match(m.lines.join(' '), /up to 40 cm/)
  assert.match(m.lines.join(' '), /not a measurement/)
  const c = PA.roadInfo(MAP.affected_roads[2])
  assert.match(c.lines.join(' '), /confirmed closed by an operator/i)
  assert.doesNotMatch(c.lines.join(' '), /cm/)
  const r = PA.roadInfo(MAP.affected_roads[3])
  assert.equal(r.title, 'unnamed road')
  assert.match(r.lines.join(' '), /not confirmed/)
})

test('affectedRoadList groups sections of the same road for the keyboard/tap list', () => {
  const list = PA.affectedRoadList(MAP)
  assert.deepEqual(list.map((x) => [x.name, x.cls, x.indices]), [
    ['Tilak Road', 'CONFIRMED_CLOSURE', [2]],
    ['Dadasaheb Phalke Road', 'MODEL_ESTIMATE', [0, 1]],
    ['unnamed road', 'REPORTED', [3]],
  ])
  assert.equal(list[1].depth_cm, 40)
})

test('zoomView / panView keep the view inside the map and within the zoom limits', () => {
  const full = { x: 0, y: 0, w: 360, h: 200 }
  const z = PA.zoomView(full, full, 2, [180, 100])
  assert.deepEqual(z, { x: 90, y: 50, w: 180, h: 100 })
  assert.deepEqual(PA.zoomView(z, full, 0.25, [180, 100]), full)                      // never beyond the whole map
  let v = full
  for (let i = 0; i < 10; i += 1) v = PA.zoomView(v, full, 2, [0, 0])
  assert.equal(v.w, 360 / PA.MAX_ZOOM)                                                  // capped
  assert.deepEqual(PA.panView(z, full, -500, -500), { x: 0, y: 0, w: 180, h: 100 })
  assert.deepEqual(PA.panView(z, full, 500, 500), { x: 180, y: 100, w: 180, h: 100 })
  assert.deepEqual(PA.panView(full, full, 50, 50), full)
})

test('officialItemsFor: only SACHET items naming Maharashtra or Mumbai, newest first, quoted as received', () => {
  // HAND-WRITTEN TEST FIXTURE shaped like GET /api/public/official-alerts; not real SACHET data
  const items = [
    { guid: 'a', title: 'FIXTURE heavy rain Mumbai', link: 'https://example.test/a', author: 'fixture', pub_date: 'Sun, 11 Oct 2026 08:00:00 GMT', label: 'OFFICIAL (SACHET/NDMA)' },
    { guid: 'b', title: 'FIXTURE thunderstorm Kerala', link: 'https://example.test/b', author: 'fixture', pub_date: 'Sun, 11 Oct 2026 09:00:00 GMT', label: 'OFFICIAL (SACHET/NDMA)' },
    { guid: 'c', title: 'FIXTURE rain', link: null, author: 'Maharashtra SDMA (fixture)', pub_date: 'Sun, 11 Oct 2026 10:00:00 GMT', label: 'OFFICIAL (SACHET/NDMA)' },
  ]
  const out = PA.officialItemsFor(items)
  assert.deepEqual(out.items.map((i) => i.guid), ['c', 'a'])
  assert.equal(out.cached, 3)
  assert.deepEqual(PA.officialItemsFor(null), { items: [], cached: 0 })
})

test('the street map uses the dashboard tile source and attribution, and loads only on request', () => {
  const mapView = read('components/MapView/MapView.jsx')
  assert.ok(mapView.includes(`'${PA.TILE_URL}'`), 'TILE_URL must match MapView')
  assert.ok(mapView.includes(`'${PA.TILE_ATTRIBUTION}'`), 'attribution must match MapView')
  const alertMap = read('public/AlertMap.jsx')
  assert.match(alertMap, /import\(['"]\.\/TileMap\.jsx['"]\)/)                         // dynamic: its own chunk
  assert.doesNotMatch(alertMap, /^import .*TileMap/m, 'TileMap (Leaflet) must never be in the first download')
  const tileMap = read('public/TileMap.jsx')
  assert.match(tileMap, /TILE_URL/)
  assert.match(tileMap, /TILE_ATTRIBUTION/)
  assert.doesNotMatch(tileMap, /\bsafe\b/i)
})

test('the public page explains how messages start and stop, with no sign-up form', () => {
  const src = read('public/PublicAlertApp.jsx')
  assert.match(src, /reply STOP/i)
  assert.match(src, /official-alerts/)
  assert.doesNotMatch(src, /<form|<input/)
})
