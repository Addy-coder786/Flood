// Run with:  npm run test:unit
//
// Landing-page scenario: the scripted sequence played on the landing map (roads -> terrain/weather -> risk areas ->
// at-risk road -> candidate rescue route -> closure and reroute -> assignment + alert preview -> settled). The road
// geometry can be REAL (OpenStreetMap, from /api/roads); everything scripted (rain band, risk areas, closure, route
// endpoints, assignment, alert) is DEMO and must say so. Pure logic lives in lib/landingScenario.js so the timeline,
// projection, route/reroute selection and reduced-motion behaviour are testable without a DOM; the component markup is
// pinned by source checks (the established pattern in this folder).
// Written test-first: before lib/landingScenario.js exists every test here fails.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const load = () => import('./landingScenario.js')
const read = (rel) => readFileSync(new URL(rel, import.meta.url), 'utf8')

// ---------------------------------------------------------------- timeline

test('STAGES: eight contiguous stages in story order, ending at TOTAL_MS', async () => {
  const { STAGES, TOTAL_MS } = await load()
  assert.deepEqual(STAGES.map((s) => s.id),
    ['roads', 'layers', 'risk', 'atRisk', 'route', 'reroute', 'response', 'settled'])
  assert.equal(STAGES[0].start, 0)
  for (let i = 1; i < STAGES.length; i++) assert.equal(STAGES[i].start, STAGES[i - 1].end, `gap before ${STAGES[i].id}`)
  for (const s of STAGES) {
    assert.ok(s.end > s.start, `${s.id} has a duration`)
    assert.ok(s.label && s.title && s.caption, `${s.id} has label, title and caption`)
  }
  assert.equal(TOTAL_MS, STAGES[STAGES.length - 1].end)
  assert.ok(TOTAL_MS >= 10000 && TOTAL_MS <= 24000, 'sequence is long enough to read, short enough to finish')
})

test('stageIndexAt: boundaries belong to the stage that starts there; clamps outside the timeline', async () => {
  const { STAGES, TOTAL_MS, stageIndexAt } = await load()
  assert.equal(stageIndexAt(-50), 0)
  assert.equal(stageIndexAt(0), 0)
  assert.equal(stageIndexAt(STAGES[1].start), 1)
  assert.equal(stageIndexAt(STAGES[1].start - 1), 0)
  assert.equal(stageIndexAt(TOTAL_MS), STAGES.length - 1)
  assert.equal(stageIndexAt(TOTAL_MS + 9999), STAGES.length - 1)
})

test('layerProgress: every channel is 0 at the start, 1 when settled, and never goes backwards', async () => {
  const { TOTAL_MS, layerProgress } = await load()
  const start = layerProgress(0)
  const end = layerProgress(TOTAL_MS)
  const channels = Object.keys(end)
  for (const k of ['roads', 'terrain', 'rain', 'risk', 'atRisk', 'route', 'closure', 'oldRoute', 'reroute', 'assignment', 'alert']) {
    assert.ok(channels.includes(k), `channel ${k}`)
  }
  for (const k of channels) {
    assert.equal(start[k], 0, `${k} starts hidden`)
    assert.equal(end[k], 1, `${k} settled`)
  }
  let prev = start
  for (let t = 0; t <= TOTAL_MS; t += 97) {
    const cur = layerProgress(t)
    for (const k of channels) {
      assert.ok(cur[k] >= prev[k] - 1e-12, `${k} monotonic at t=${t}`)
      assert.ok(cur[k] >= 0 && cur[k] <= 1, `${k} within [0,1] at t=${t}`)
    }
    prev = cur
  }
})

test('layerProgress: the story order holds (route before closure, closure before reroute, reroute before cards)', async () => {
  const { STAGES, layerProgress } = await load()
  const at = (id) => STAGES.find((s) => s.id === id)
  const mid = (id) => (at(id).start + at(id).end) / 2
  assert.equal(layerProgress(mid('route')).closure, 0)
  assert.ok(layerProgress(at('route').end).route === 1)
  assert.equal(layerProgress(at('route').end).reroute, 0)
  assert.ok(layerProgress(at('reroute').end).reroute === 1)
  assert.equal(layerProgress(at('reroute').end).assignment, 0)
  assert.equal(layerProgress(mid('roads')).risk, 0)
})

// ---------------------------------------------------------------- playback + reduced motion

test('initialPlayback: motion plays from 0; reduced motion shows the settled state, not playing', async () => {
  const { TOTAL_MS, initialPlayback } = await load()
  assert.deepEqual(initialPlayback(false), { t: 0, playing: true })
  assert.deepEqual(initialPlayback(true), { t: TOTAL_MS, playing: false })
})

test('playbackReducer: tick advances and stops at the end; play at the end replays; seek pauses on a stage', async () => {
  const { TOTAL_MS, STAGES, playbackReducer } = await load()
  let s = { t: 0, playing: true }
  s = playbackReducer(s, { type: 'tick', dt: 1000 })
  assert.equal(s.t, 1000)
  s = playbackReducer(s, { type: 'tick', dt: TOTAL_MS * 3 })
  assert.deepEqual(s, { t: TOTAL_MS, playing: false })
  s = playbackReducer(s, { type: 'tick', dt: 500 })
  assert.equal(s.t, TOTAL_MS, 'paused state does not advance')
  s = playbackReducer(s, { type: 'play' })
  assert.deepEqual(s, { t: 0, playing: true }, 'play at the end starts again')
  s = playbackReducer({ t: 4000, playing: true }, { type: 'pause' })
  assert.deepEqual(s, { t: 4000, playing: false })
  s = playbackReducer(s, { type: 'toggle' })
  assert.equal(s.playing, true)
  s = playbackReducer({ t: 4000, playing: true }, { type: 'replay' })
  assert.deepEqual(s, { t: 0, playing: true })
  s = playbackReducer({ t: 0, playing: true }, { type: 'seek', stage: 4 })
  assert.equal(s.playing, false)
  assert.ok(s.t > STAGES[4].start && s.t < STAGES[4].end, 'seek stays inside the chosen stage (the end belongs to the next one)')
  s = playbackReducer({ t: 0, playing: true }, { type: 'seek', stage: 99 })
  assert.equal(s.t, TOTAL_MS)
})

test('seekTime: every stage is shown fully drawn and still counts as that stage', async () => {
  const { STAGES, TOTAL_MS, seekTime, stageIndexAt, layerProgress } = await load()
  STAGES.forEach((s, i) => assert.equal(stageIndexAt(seekTime(i)), i, `stage ${s.id}`))
  assert.equal(seekTime(STAGES.length - 1), TOTAL_MS)
  const at = (id) => layerProgress(seekTime(STAGES.findIndex((s) => s.id === id)))
  assert.ok(at('route').route > 0.999 && at('route').closure === 0)
  assert.ok(at('reroute').reroute > 0.999 && at('reroute').assignment === 0)
  assert.ok(at('response').alert > 0.999)
})

test('playbackReducer: with reduced motion nothing animates — play/replay show the settled state', async () => {
  const { TOTAL_MS, STAGES, playbackReducer } = await load()
  const rm = { reducedMotion: true }
  assert.deepEqual(playbackReducer({ t: TOTAL_MS, playing: false }, { type: 'play', ...rm }), { t: TOTAL_MS, playing: false })
  assert.deepEqual(playbackReducer({ t: 300, playing: false }, { type: 'replay', ...rm }), { t: TOTAL_MS, playing: false })
  const s = playbackReducer({ t: TOTAL_MS, playing: false }, { type: 'seek', stage: 2, ...rm })
  assert.equal(s.playing, false)
  assert.ok(s.t > STAGES[2].start && s.t < STAGES[2].end, 'steps still jump with reduced motion')
})

test('shouldAnimate: only while playing, on screen, tab visible and motion allowed', async () => {
  const { shouldAnimate } = await load()
  const base = { playing: true, inView: true, pageVisible: true, reducedMotion: false }
  assert.equal(shouldAnimate(base), true)
  assert.equal(shouldAnimate({ ...base, playing: false }), false)
  assert.equal(shouldAnimate({ ...base, inView: false }), false)
  assert.equal(shouldAnimate({ ...base, pageVisible: false }), false)
  assert.equal(shouldAnimate({ ...base, reducedMotion: true }), false)
})

// ---------------------------------------------------------------- projection

test('makeProjector: local metres, north up, origin at the centre', async () => {
  const { makeProjector } = await load()
  const p = makeProjector([72.845, 19.02])
  assert.deepEqual(p([72.845, 19.02]).map((v) => Math.round(v)), [0, 0])
  const [xE] = p([72.846, 19.02])
  assert.ok(xE > 100 && xE < 110, `0.001 deg lon at 19 N is ~105 m, got ${xE}`)
  const [, yN] = p([72.845, 19.021])
  assert.ok(yN < -105 && yN > -115, `0.001 deg lat is ~111 m and north is up (negative y), got ${yN}`)
})

test('pathD: an SVG path string with one M per line and rounded numbers', async () => {
  const { makeProjector, pathD } = await load()
  const p = makeProjector([72.845, 19.02])
  const d = pathD([[[72.845, 19.02], [72.846, 19.02]], [[72.845, 19.021], [72.845, 19.022], [72.846, 19.022]]], p)
  assert.equal((d.match(/M/g) || []).length, 2)
  assert.equal((d.match(/L/g) || []).length, 3)
  assert.ok(!/\d\.\d{2,}/.test(d), 'at most one decimal place')
})

// ---------------------------------------------------------------- road graph + routing

// A small ladder: two parallel streets joined by rungs. Lengths come from geometry.
const ladder = () => {
  const f = (id, a, b, name = 'Demo') => ({ type: 'Feature', geometry: { type: 'LineString', coordinates: [a, b] },
    properties: { seg_id: id, name, highway: 'residential' } })
  const S = (i) => [72.840 + i * 0.001, 19.010], N = (i) => [72.840 + i * 0.001, 19.011]
  return { type: 'FeatureCollection', features: [
    f('s0', S(0), S(1), 'South'), f('s1', S(1), S(2), 'South'), f('s2', S(2), S(3), 'South'),
    f('n0', N(0), N(1), 'North'), f('n1', N(1), N(2), 'North'), f('n2', N(2), N(3), 'North'),
    f('r0', S(0), N(0)), f('r1', S(1), N(1)), f('r2', S(2), N(2)), f('r3', S(3), N(3)),
  ] }
}

test('buildGraph + shortestPath: shared endpoints connect; the shortest path wins; blocked segments are avoided', async () => {
  const { buildGraph, shortestPath, nearestNode } = await load()
  const g = buildGraph(ladder().features)
  const a = nearestNode(g, [72.840, 19.010]), b = nearestNode(g, [72.843, 19.010])
  const r = shortestPath(g, a, b)
  assert.deepEqual(r.segIds, ['s0', 's1', 's2'])
  assert.ok(Math.abs(r.lengthM - 3 * 105.2) < 5, `length from geometry, got ${r.lengthM}`)
  const r2 = shortestPath(g, a, b, { blocked: new Set(['s1']) })
  assert.ok(!r2.segIds.includes('s1'))
  assert.ok(r2.lengthM > r.lengthM)
  assert.equal(shortestPath(g, a, b, { blocked: new Set(['s0', 'r0']) }), null, 'no route when cut off')
})

test('DEMO_ROADS: a small bundled network, explicitly labelled DEMO', async () => {
  const { DEMO_ROADS } = await load()
  assert.equal(DEMO_ROADS.type, 'FeatureCollection')
  assert.equal(DEMO_ROADS.provenance.tag, 'DEMO')
  assert.ok(DEMO_ROADS.features.length >= 20 && DEMO_ROADS.features.length <= 200)
  assert.ok(JSON.stringify(DEMO_ROADS).length < 40000, 'small enough to bundle')
})

test('buildScenario on the DEMO network: route avoids risk, closure lies on the route, reroute avoids both', async () => {
  const { buildScenario, DEMO_ROADS } = await load()
  const sc = buildScenario(DEMO_ROADS)
  assert.equal(sc.roadsTag, 'DEMO', 'fallback geometry is DEMO')
  assert.ok(sc.route && sc.route.segIds.length > 1)
  assert.ok(sc.atRisk.segIds.length >= 1)
  for (const id of sc.atRisk.segIds) assert.ok(!sc.route.segIds.includes(id), 'candidate route avoids at-risk roads')
  assert.ok(sc.route.segIds.includes(sc.closure.segId), 'the closure is on the candidate route')
  assert.ok(sc.reroute && !sc.reroute.segIds.includes(sc.closure.segId), 'reroute avoids the closure')
  for (const id of sc.atRisk.segIds) assert.ok(!sc.reroute.segIds.includes(id), 'reroute still avoids at-risk roads')
  assert.deepEqual(sc.reroute.from, sc.route.from)
  assert.deepEqual(sc.reroute.to, sc.route.to)
  assert.ok(sc.reroute.lengthM >= sc.route.lengthM)
  assert.ok(sc.closure.reason && /DEMO/.test(sc.closure.reason))
  assert.ok(sc.riskAreas.length >= 1 && sc.riskAreas.every((r) => r.radiusM > 0 && r.center.length === 2))
})

test('buildScenario: scripted parts are always DEMO; road geometry keeps its own provenance tag', async () => {
  const { buildScenario, DEMO_ROADS } = await load()
  const real = { ...DEMO_ROADS, provenance: { tag: 'REAL', source: 'test fixture' } }
  const sc = buildScenario(real)
  assert.equal(sc.roadsTag, 'REAL')
  for (const part of [sc.assignment, sc.alert, sc.closure]) assert.equal(part.tag, 'DEMO')
  assert.match(sc.alert.text, /DEMO|demonstration/i)
  assert.ok(sc.alert.sent === false, 'the alert preview is never sent')
  assert.equal(buildScenario(null).roadsTag, 'DEMO', 'no roads -> bundled DEMO geometry')
  assert.equal(buildScenario({ type: 'FeatureCollection', features: [] }).roadsTag, 'DEMO')
})

test('buildScenario is deterministic', async () => {
  const { buildScenario, DEMO_ROADS } = await load()
  assert.deepEqual(buildScenario(DEMO_ROADS), buildScenario(DEMO_ROADS))
})

// ---------------------------------------------------------------- component source checks

test('LandingPage: DEMO chip, reduced motion, offscreen/hidden pause, existing entry points kept', () => {
  const page = read('../components/LandingPage/LandingPage.jsx')
  const story = read('../components/LandingPage/LandingStory.jsx')
  assert.match(page, /data-tour="hero"/, 'Explore tour anchor kept')
  assert.match(page, /onOpenStage\?\.\(/, 'How-it-works cards still open dashboard stages')
  assert.match(page, /onExplore/, 'guided tour button kept')
  assert.match(page, /onEnter/, 'control-centre CTA kept')
  assert.match(page, /not an official warning/i, 'honesty line')
  assert.match(story, /DEMO scenario/, 'DEMO chip on the map')
  assert.match(story, /prefers-reduced-motion/)
  assert.match(story, /visibilitychange/)
  assert.match(story, /IntersectionObserver/)
  assert.match(story, /requestAnimationFrame/)
  assert.doesNotMatch(page + story, /from 'three'|terrain3d/i, 'no three.js on the landing path')
})

test('sceneViewBox: frames the route, reroute and risk areas with padding and a minimum size', async () => {
  const { buildScenario, DEMO_ROADS, makeProjector, sceneViewBox, VIEW_CENTER } = await load()
  const sc = buildScenario(DEMO_ROADS)
  const p = makeProjector(VIEW_CENTER)
  const [x, y, w, h] = sceneViewBox(sc, p, { padM: 200, minM: 1200 })
  assert.ok(w >= 1200 && h >= 1200)
  for (const c of [...sc.route.coords, ...sc.reroute.coords, ...sc.riskAreas.map((r) => r.center)]) {
    const [px, py] = p(c)
    assert.ok(px >= x + 199 && px <= x + w - 199 && py >= y + 199 && py <= y + h - 199, 'inside, with padding')
  }
})

test('fitView: viewBox with the frame aspect, content kept clear of a reserved right-hand strip', async () => {
  const { fitView } = await load()
  const content = [0, 0, 1000, 2000]
  // wide frame, 300 px reserved on the right for cards
  const { vb, u } = fitView(content, 800, 500, 300)
  assert.ok(Math.abs(vb[2] / vb[3] - 800 / 500) < 1e-6, 'same aspect as the frame: no letterbox')
  assert.ok(Math.abs(u - vb[2] / 800) < 1e-9, 'u = SVG units per CSS pixel')
  const toPx = (x) => (x - vb[0]) / u
  assert.ok(toPx(0) >= -1e-6 && toPx(1000) <= 800 - 300 + 1e-6, 'content sits left of the reserved strip')
  assert.ok((0 - vb[1]) / u >= -1e-6 && (2000 - vb[1]) / u <= 500 + 1e-6, 'content fits vertically')
  // nothing reserved: content centred
  const c = fitView(content, 500, 500, 0)
  assert.ok(Math.abs(((0 - c.vb[0]) + (1000 - c.vb[0])) / 2 / c.u - 250) < 1e-6)
  // degenerate frame sizes fall back to the content box
  assert.deepEqual(fitView(content, 0, 0, 0).vb, content)
})

test('fitView: top and bottom strips (map labels) are kept clear too', async () => {
  const { fitView } = await load()
  const content = [0, 0, 1000, 1000]
  const { vb, u } = fitView(content, 400, 500, 0, { top: 40, bottom: 60 })
  const py = (y) => (y - vb[1]) / u
  assert.ok(py(0) >= 40 - 1e-6, 'below the top strip')
  assert.ok(py(1000) <= 500 - 60 + 1e-6, 'above the bottom strip')
  assert.ok(Math.abs(vb[2] / vb[3] - 400 / 500) < 1e-6)
})

test('LandingPage order (2026-10-11): centred FLOODNET hero first, the demo map directly below it', () => {
  const page = read('../components/LandingPage/LandingPage.jsx')
  const hero = page.indexOf('className={styles.heroSection}')
  const demo = page.indexOf('id="demo"')
  const story = page.indexOf('<LandingStory')
  const next = page.indexOf('id="how-it-works"')
  assert.ok(hero > 0 && demo > hero && story > demo && next > story, 'hero, then the demo-map section, then the rest')
  assert.equal((page.match(/<LandingStory/g) || []).length, 1, 'one demo map')
  assert.equal((page.match(/<nav\b/g) || []).length, 1, 'one navigation bar')
  assert.match(page, /<HeroVisual \/>/, 'the animated dotted background is back')
  assert.match(page, />\s*FLOODNET\s*</, 'centred FLOODNET headline')
  for (const label of ['Physical Model', 'How it works', 'Pilot', 'Capabilities', 'Enter Control Centre']) assert.ok(page.includes(label), label)
  assert.match(page, /scrollToSection\('demo'\)/, 'Explore Forecast scrolls to the demo map')
  assert.match(page, /PREDICTION WINDOW[\s\S]*STREET NETWORK[\s\S]*DEPTH UNITS[\s\S]*DECISION SUPPORT/, 'information strip')
  assert.equal((page.match(/Know where the water will be/g) || []).length, 1, 'tagline not repeated')
})
