// Run with:  npm run test:unit
//
// Collapsible panels (DECISIONS D-47): the navigation sidebar and the work panel slide in and out. Their state is a
// pure reducer so toggling, responsive layouts and persistence are testable without a DOM; the shell markup is pinned
// by source checks (aria-expanded, aria-controls, reduced motion), the established pattern in this folder.
// Written test-first: before lib/panels.js exists every test here fails.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = () => import('./panels.js')
const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8')

test('layoutFor: wide above 1280 px, laptop 961-1280, narrow at 960 and below', async () => {
  const { layoutFor } = await load()
  assert.equal(layoutFor(1600), 'wide')
  assert.equal(layoutFor(1281), 'wide')
  assert.equal(layoutFor(1280), 'laptop')
  assert.equal(layoutFor(961), 'laptop')
  assert.equal(layoutFor(960), 'narrow')
  assert.equal(layoutFor(375), 'narrow')
})

test('initialPanels: sensible defaults per layout; a stored desktop choice wins except on narrow screens', async () => {
  const { initialPanels } = await load()
  assert.deepEqual(initialPanels(1600, null), { nav: 'expanded', work: 'open' })
  assert.deepEqual(initialPanels(1100, null), { nav: 'collapsed', work: 'open' })
  assert.deepEqual(initialPanels(400, null), { nav: 'collapsed', work: 'open' })
  assert.deepEqual(initialPanels(1600, { nav: 'collapsed', work: 'closed' }), { nav: 'collapsed', work: 'closed' })
  assert.deepEqual(initialPanels(400, { nav: 'expanded', work: 'closed' }), { nav: 'collapsed', work: 'closed' },
    'the navigation drawer never starts open over a phone screen')
})

test('toggle flips one panel and leaves the other untouched; two toggles restore the state', async () => {
  const { panelsReducer } = await load()
  const s0 = { nav: 'expanded', work: 'open' }
  const s1 = panelsReducer(s0, { type: 'toggle', panel: 'work' })
  assert.deepEqual(s1, { nav: 'expanded', work: 'closed' })
  assert.deepEqual(panelsReducer(s1, { type: 'toggle', panel: 'work' }), s0)
  assert.deepEqual(panelsReducer(s0, { type: 'toggle', panel: 'nav' }), { nav: 'collapsed', work: 'open' })
})

test('repeated or unknown actions never leave an inconsistent state', async () => {
  const { panelsReducer } = await load()
  const s0 = { nav: 'expanded', work: 'open' }
  assert.equal(panelsReducer(s0, { type: 'set', panel: 'work', value: 'open' }), s0, 'setting the same value is a no-op')
  assert.equal(panelsReducer(s0, { type: 'nonsense' }), s0)
  assert.equal(panelsReducer(s0, { type: 'toggle', panel: 'map' }), s0)
  assert.equal(panelsReducer(s0, { type: 'set', panel: 'work', value: 'sideways' }), s0)
  let s = s0
  for (let i = 0; i < 7; i++) s = panelsReducer(s, { type: 'toggle', panel: 'work' })
  assert.equal(s.work, 'closed', 'an odd number of clicks ends closed, whatever the speed')
})

test('navigating closes the navigation drawer on narrow screens only, and opens the work panel', async () => {
  const { panelsReducer } = await load()
  const s = { nav: 'expanded', work: 'closed' }
  assert.deepEqual(panelsReducer(s, { type: 'navigate', layout: 'narrow' }), { nav: 'collapsed', work: 'open' })
  assert.deepEqual(panelsReducer(s, { type: 'navigate', layout: 'wide' }), { nav: 'expanded', work: 'open' })
})

test('entering the narrow layout closes the navigation drawer', async () => {
  const { panelsReducer } = await load()
  assert.deepEqual(panelsReducer({ nav: 'expanded', work: 'open' }, { type: 'layout', layout: 'narrow' }),
    { nav: 'collapsed', work: 'open' })
  const wide = { nav: 'expanded', work: 'open' }
  assert.equal(panelsReducer(wide, { type: 'layout', layout: 'wide' }), wide)
})

test('persistence round-trips and rejects anything malformed', async () => {
  const { serializePanels, deserializePanels } = await load()
  const s = { nav: 'collapsed', work: 'closed' }
  assert.deepEqual(deserializePanels(serializePanels(s)), s)
  for (const bad of [null, '', '{', '[]', '{"nav":"x","work":"open"}', '{"nav":"expanded"}']) {
    assert.equal(deserializePanels(bad), null, String(bad))
  }
})

test('labels and timing: controls say what they will do; transitions stay within 200-300 ms', async () => {
  const { panelControl, TRANSITION_MS } = await load()
  assert.ok(TRANSITION_MS >= 200 && TRANSITION_MS <= 300)
  assert.deepEqual(panelControl({ nav: 'expanded', work: 'open' }, 'work'),
    { expanded: true, label: 'Hide the page panel', title: 'Hide the page panel (more map)' })
  assert.deepEqual(panelControl({ nav: 'collapsed', work: 'closed' }, 'work'),
    { expanded: false, label: 'Show the page panel', title: 'Show the page panel' })
  assert.equal(panelControl({ nav: 'collapsed', work: 'open' }, 'nav').label, 'Expand navigation')
})

test('shell markup: toggles are buttons with aria-expanded and aria-controls; reduced motion is respected', () => {
  const app = read('../App.jsx')
  const css = read('../App.module.css')
  assert.match(app, /aria-controls="page-panel"/)
  assert.match(app, /aria-expanded=\{/)
  assert.match(app, /id="page-panel"/)
  assert.match(app, /inert=/, 'a closed panel is inert so keyboard focus cannot enter it')
  assert.match(css, /prefers-reduced-motion:\s*reduce/)
  assert.match(css, /transition:[^;]*grid-template-columns/)
  const map = read('../components/MapView/MapView.jsx')
  assert.match(map, /ResizeObserver/, 'the map re-measures itself when a panel opens or closes')
  assert.match(map, /invalidateSize/)
})
