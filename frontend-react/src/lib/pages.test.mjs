// Run with:  npm run test:unit   (node --test, no extra dependency)
//
// D-37 multi-page UX (Agent D test matrix §6, P1-P4): the page registry and hash helpers live in a PURE module
// `src/lib/pages.js` so routing decisions are testable without a DOM, and the shell wiring is pinned by source
// regexes (the established sources.test.mjs / stages.test.mjs pattern). Written test-first: before the module
// exists every registry/helper test fails with ERR_MODULE_NOT_FOUND, and the markup pins fail with AssertionError.
//
// The module is loaded lazily inside each test (not at the top of the file) so a missing module reports per
// test instead of taking the whole file down, and so the source pins can run and fail on their own merits.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..')
const ROOT = join(SRC, '..')
const load = () => import('./pages.js')

// The pages in sidebar order. D-37 decided six; D-47 (2026-10-11, live operations) adds 'trips' (rescue trips) and
// 'alerts' (public alerts: subscriber notifications, advisory drafts, official feed), and renames the labels
// (Situation, Forecast, Roads, Response, Trips, Reports, Public alerts, Evidence) while keeping every D-37 hash key.
// Hard-coded here (not read from the module) so the registry cannot drift without this file changing too.
// D-50 (2026-10-11) adds 'resources' (registry of teams, vehicles, shelters and candidate shelters) after Trips.
const EXPECTED_KEYS = ['overview', 'forecast', 'access', 'response', 'trips', 'resources', 'reports', 'alerts', 'replay']
const EXPECTED_LABELS = ['Situation', 'Forecast', 'Roads', 'Response', 'Trips', 'Resources', 'Reports', 'Public alerts', 'Evidence']
// Old right-tab keys that briefings / tours / stages.test.mjs still use, and the page each now opens. 'alerts' is a
// page of its own since D-47 (it hosts the advisory panel the old 'alerts' tab showed), so it is no longer an alias.
const EXPECTED_ALIASES = { why: 'forecast', routing: 'access', provenance: 'overview' }
// Right-tab keys that must STAY as `key: '<k>'` literals in App.jsx because stages.test.mjs pins RIGHT_TABS.
const LEGACY_LITERALS = ['overview', 'alerts', 'why', 'routing', 'response', 'provenance']
const STATUSES = ['empty', 'loading', 'stale', 'error', 'ready']

const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f)
  return statSync(p).isDirectory() ? walk(p) : /\.jsx$/.test(f) ? [p] : []
})
const APP = readFileSync(join(SRC, 'App.jsx'), 'utf8')

// ───────────────────────────────────────────── P1: registry

test('PAGES: exactly the D-37 + D-47 pages, in order, each with key/label/hash/title/purpose, unique keys and hashes', async () => {
  const { PAGES, DEFAULT_PAGE } = await load()
  assert.ok(Array.isArray(PAGES), 'PAGES is an ordered array')
  assert.deepEqual(PAGES.map((p) => p.key), EXPECTED_KEYS)
  assert.deepEqual(PAGES.map((p) => p.label), EXPECTED_LABELS)
  for (const p of PAGES) {
    for (const field of ['key', 'label', 'hash', 'title', 'purpose']) {
      assert.equal(typeof p[field], 'string', `${p.key}.${field} is a string`)
      assert.ok(p[field].trim().length > 0, `${p.key}.${field} is not empty`)
    }
    assert.equal(p.hash, `#/${p.key}`, `${p.key}: hash form is #/<key>`)
    // Agent E §8.10: purpose lines are one imperative sentence, no trailing jargon dump.
    assert.ok(p.purpose.length <= 120, `${p.key}.purpose: one short sentence`)
  }
  assert.equal(new Set(PAGES.map((p) => p.key)).size, PAGES.length, 'keys are unique')
  assert.equal(new Set(PAGES.map((p) => p.hash)).size, PAGES.length, 'hashes are unique')
  assert.equal(new Set(PAGES.map((p) => p.label)).size, PAGES.length, 'labels are unique')
  assert.equal(DEFAULT_PAGE, 'overview')
  assert.ok(PAGES.some((p) => p.key === DEFAULT_PAGE), 'the default page is in the registry')
})

test('LEGACY_ALIASES: old right-tab keys map onto pages, never collide with page keys, never point outside the registry', async () => {
  const { LEGACY_ALIASES, PAGES } = await load()
  assert.deepEqual(LEGACY_ALIASES, EXPECTED_ALIASES)
  const keys = new Set(PAGES.map((p) => p.key))
  for (const [alias, target] of Object.entries(LEGACY_ALIASES)) {
    assert.ok(!keys.has(alias), `alias ${alias} must not shadow a page key`)
    assert.ok(keys.has(target), `alias ${alias} -> ${target} must be a page`)
  }
})

// ───────────────────────────────────────────── P1: hash helpers

test('pageFromHash: empty/bare/trailing-slash/query forms, unknown -> default, aliases -> mapped page, never throws', async () => {
  const { pageFromHash, DEFAULT_PAGE } = await load()
  for (const h of ['', '#', '#/', '/', '#//']) assert.equal(pageFromHash(h), DEFAULT_PAGE, JSON.stringify(h))
  assert.equal(pageFromHash('#/access'), 'access')
  assert.equal(pageFromHash('#/access/'), 'access')
  assert.equal(pageFromHash('#/access?x=1'), 'access')
  assert.equal(pageFromHash('#/access/?x=1&y=2'), 'access')
  assert.equal(pageFromHash('#/replay'), 'replay')
  assert.equal(pageFromHash('#/nope'), DEFAULT_PAGE)
  assert.equal(pageFromHash('#/access/extra/segments'), DEFAULT_PAGE, 'nested paths are not pages')
  for (const [alias, target] of Object.entries(EXPECTED_ALIASES)) assert.equal(pageFromHash(`#/${alias}`), target, alias)
  // defensive: anything that is not a string is "no hash"
  for (const bad of [undefined, null, 42, {}, [], true]) {
    assert.doesNotThrow(() => pageFromHash(bad), `pageFromHash(${String(bad)}) must not throw`)
    assert.equal(pageFromHash(bad), DEFAULT_PAGE)
  }
})

test('hashForPage: #/<key> for every page, round-trips through pageFromHash, resolves aliases and unknown keys', async () => {
  const { hashForPage, pageFromHash, PAGES, DEFAULT_PAGE } = await load()
  for (const p of PAGES) {
    assert.equal(hashForPage(p.key), `#/${p.key}`)
    assert.equal(hashForPage(p.key), p.hash)
    assert.equal(pageFromHash(hashForPage(p.key)), p.key, `${p.key} round trip`)
  }
  for (const [alias, target] of Object.entries(EXPECTED_ALIASES)) assert.equal(hashForPage(alias), `#/${target}`, alias)
  assert.equal(hashForPage('nope'), `#/${DEFAULT_PAGE}`)
  assert.equal(hashForPage(undefined), `#/${DEFAULT_PAGE}`)
})

test('resolvePageKey: page -> page, alias -> page, unknown/empty -> default', async () => {
  const { resolvePageKey, DEFAULT_PAGE } = await load()
  for (const k of EXPECTED_KEYS) assert.equal(resolvePageKey(k), k)
  for (const [alias, target] of Object.entries(EXPECTED_ALIASES)) assert.equal(resolvePageKey(alias), target)
  for (const bad of ['nope', '', undefined, null, 7]) assert.equal(resolvePageKey(bad), DEFAULT_PAGE, String(bad))
})

test('pageTitle: the registry title for pages and aliases; the default title for unknown keys', async () => {
  const { pageTitle, PAGES, DEFAULT_PAGE } = await load()
  const byKey = Object.fromEntries(PAGES.map((p) => [p.key, p]))
  for (const p of PAGES) assert.equal(pageTitle(p.key), p.title)
  for (const [alias, target] of Object.entries(EXPECTED_ALIASES)) assert.equal(pageTitle(alias), byKey[target].title)
  assert.equal(pageTitle('nope'), byKey[DEFAULT_PAGE].title)
  assert.equal(pageTitle(undefined), byKey[DEFAULT_PAGE].title)
})

test('nextPage: clamps at both ends, steps in registry order, and never leaves the registry', async () => {
  const { nextPage, PAGES } = await load()
  const keys = PAGES.map((p) => p.key)
  assert.equal(nextPage('overview', -1), 'overview', 'clamped at the first page')
  assert.equal(nextPage('replay', 1), 'replay', 'clamped at the last page')
  assert.equal(nextPage('overview', 1), 'forecast')
  assert.equal(nextPage('access', -1), 'forecast')
  assert.equal(nextPage('access', 0), 'access')
  // walking forward from the first page visits every page in order and then sticks
  let k = keys[0]
  const visited = [k]
  for (let i = 0; i < keys.length + 2; i += 1) { k = nextPage(k, 1); visited.push(k) }
  assert.deepEqual(visited.slice(0, keys.length), keys)
  assert.ok(visited.every((v) => keys.includes(v)), 'never leaves the registry')
  // aliases and unknown keys resolve first, then step
  assert.equal(nextPage('why', 1), nextPage('forecast', 1))
  assert.ok(keys.includes(nextPage('nope', 1)))
  assert.ok(keys.includes(nextPage(undefined, -1)))
})

// ───────────────────────────────────────────── P4: page status selectors (pure, no DOM)

test('pageStatus: {run:null} is empty on every page except overview, reports, alerts and resources, which are ready', async () => {
  const { pageStatus } = await load()
  for (const k of EXPECTED_KEYS) {
    // alerts: subscribers and the official feed need no forecast; drafting an advisory says so on the page
    // resources: the registry needs no forecast (only a candidate shelter's site check uses one, and says so)
    const expected = ['overview', 'reports', 'alerts', 'resources'].includes(k) ? 'ready' : 'empty'
    assert.equal(pageStatus(k, { run: null }), expected, k)
  }
})

test('pageStatus: a running simulation is loading and a failed one is error, on every page', async () => {
  const { pageStatus } = await load()
  for (const k of EXPECTED_KEYS) {
    assert.equal(pageStatus(k, { run: null, simulating: true }), 'loading', `${k} loading`)
    assert.equal(pageStatus(k, { run: {}, simulating: true }), 'loading', `${k} loading with a previous run`)
    assert.equal(pageStatus(k, { run: null, simError: 'x' }), 'error', `${k} error`)
    assert.equal(pageStatus(k, { run: {}, simError: 'boom' }), 'error', `${k} error with a previous run`)
  }
})

test('pageStatus: a stale run is stale on every page; a current run is ready', async () => {
  const { pageStatus } = await load()
  for (const k of EXPECTED_KEYS) {
    assert.equal(pageStatus(k, { run: {}, isStale: true }), 'stale', `${k} stale`)
  }
  for (const k of ['overview', 'forecast', 'access', 'trips', 'reports', 'alerts', 'replay']) {
    assert.equal(pageStatus(k, { run: { run_id: 'r1' }, isStale: false }), 'ready', `${k} ready`)
  }
})

test('pageStatus(response): a plan built at another decision time or against another run is stale', async () => {
  const { pageStatus } = await load()
  const run = { run_id: 'r1' }
  // plan built at T+10, decision time is now T+30 -> stale
  assert.equal(pageStatus('response', { run, response: { result: { sim_t_min: 10, forecast: { run_id: 'r1' } } }, currentT: 30 }), 'stale')
  // plan built against a previous forecast run -> stale even at the same decision time
  assert.equal(pageStatus('response', { run: { run_id: 'r2' }, response: { result: { sim_t_min: 30, forecast: { run_id: 'r1' } } }, currentT: 30 }), 'stale')
  // same decision time, same run -> ready
  assert.equal(pageStatus('response', { run, response: { result: { sim_t_min: 30, forecast: { run_id: 'r1' } } }, currentT: 30 }), 'ready')
  // no run at all -> empty (the planner needs a forecast first)
  assert.equal(pageStatus('response', { run: null, response: { result: null } }), 'empty')
})

test('pageStatus(reports): ready without a run, and ready with operator road events alone', async () => {
  const { pageStatus } = await load()
  const ev = { event_id: 'C1', seg_id: '12', kind: 'closure', received_t_min: 0, applies_from_t_min: 0, source: 'operator report (SIMULATED)' }
  assert.equal(pageStatus('reports', { run: null, roadEvents: [ev] }), 'ready')
  assert.equal(pageStatus('reports', { run: null, roadEvents: [] }), 'ready')
  assert.equal(pageStatus('reports', { run: { run_id: 'r1' }, roadEvents: [ev] }), 'ready')
})

test('pageStatus: always one of the five statuses, for every page and alias, with sparse or empty state', async () => {
  const { pageStatus } = await load()
  for (const k of [...EXPECTED_KEYS, ...Object.keys(EXPECTED_ALIASES), 'nope']) {
    for (const state of [{}, { run: null }, { run: {} }, { run: {}, response: {} }, { run: {}, response: { result: null } }]) {
      let out
      assert.doesNotThrow(() => { out = pageStatus(k, state) }, `${k} ${JSON.stringify(state)}`)
      assert.ok(STATUSES.includes(out), `${k}: ${out} is not a page status`)
    }
  }
  for (const [alias, target] of Object.entries(EXPECTED_ALIASES)) {
    assert.equal(pageStatus(alias, { run: null }), pageStatus(target, { run: null }), `${alias} behaves as ${target}`)
  }
})

// ───────────────────────────────────────────── P2/P3: source pins (no DOM; regex on the shell source)

test('App.jsx imports ./lib/pages.js and no router library is installed', () => {
  // boolean assertion on purpose: assert.match would dump the whole App.jsx source as `actual` on failure
  assert.ok(/import\s+[^;]*\bfrom\s+['"]\.\/lib\/pages\.js['"]/.test(APP), 'App.jsx must import ./lib/pages.js')
  const pkg = JSON.parse(readFileSync(join(ROOT, 'package.json'), 'utf8'))
  const deps = Object.keys({ ...pkg.dependencies, ...pkg.devDependencies, ...pkg.peerDependencies, ...pkg.optionalDependencies })
  assert.deepEqual(deps.filter((d) => /react-router/.test(d)), [], 'no react-router* dependency (hash routing is hand-rolled)')
})

test('navigation markup is accessible: one component renders <nav aria-label=...> whose items carry aria-current', () => {
  const files = walk(join(SRC, 'components'))
  const navFiles = files.filter((p) => {
    const s = readFileSync(p, 'utf8')
    return /<nav\b[^>]*\baria-label=/.test(s) && /\baria-current\b/.test(s)
  })
  assert.ok(navFiles.length >= 1, 'expected a .jsx under src/components with <nav aria-label=...> AND aria-current (page navigation)')
})

test('every page key is a `key: \'<key>\'` literal in App.jsx', () => {
  for (const k of EXPECTED_KEYS) assert.ok(APP.includes(`key: '${k}'`), `page ${k} is not declared as a key: '${k}' literal in App.jsx`)
})

test('legacy right-tab literals and the rtab-${ id template survive (stages.test.mjs and the tours depend on them)', () => {
  for (const k of LEGACY_LITERALS) assert.ok(APP.includes(`key: '${k}'`), `right tab ${k} must stay a key: '${k}' literal in App.jsx`)
  assert.ok(APP.includes('`rtab-${'), 'the `rtab-${...}` id template must stay in App.jsx')
})
