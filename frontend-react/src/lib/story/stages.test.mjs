// Run with:  npm run test:unit   (node --test, no extra dependency)
//
// Guards the technology story, the Demo Story sequence and the route wording against drift: every stage must
// point at something the dashboard really renders, in all three languages, without any claim FloodNet cannot
// back (see docs/TECHNOLOGY_EXPLAINER.md).
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  PIPELINE_STAGES, STORY_HEADER, DEMO_STORY, RIGHT_TABS, MAP_LAYERS, stageById, layersToEnable, localized,
} from './stages.js'
import { narrate } from '../briefing/narration.js'
import { TAB_STEPS } from '../tour/tabSteps.js'
import { exposureOf } from '../routeExposure.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f)
  return statSync(p).isDirectory() ? walk(p) : /\.(jsx?|mjs)$/.test(f) ? [p] : []
})
const SOURCE = walk(SRC).filter((p) => !p.endsWith('.test.mjs')).map((p) => readFileSync(p, 'utf8')).join('\n')

const FORBIDDEN = [
  /official imd (radar )?qpe/i, /imd radar nowcast/i, /official (imd )?nowcast(?! or forecast)/i, /\bAI\b/, /machine learning/i,
  /validated accuracy/i, /live traffic/i, /observed blockage/i, /guaranteed/i, /\bbest route\b/i, /\bsafest\b/i,
]

test('the chain is exactly the seven agreed stages, in order', () => {
  assert.deepEqual(PIPELINE_STAGES.map((s) => s.id), ['rain', 'spatial', 'runoff', 'surface', 'drainage', 'depth', 'action'])
})

test('every stage is complete in en / hi / mr and is one short sentence', () => {
  for (const s of PIPELINE_STAGES) {
    for (const lang of ['en', 'hi', 'mr']) {
      assert.ok(s.title[lang]?.length > 1, `${s.id}.title.${lang}`)
      assert.ok(s.line[lang]?.length > 10, `${s.id}.line.${lang}`)
    }
    assert.ok(s.line.en.length <= 80, `${s.id}: keep it to one short line`)
    assert.equal((s.line.en.match(/[.!?]/g) || []).length, 1, `${s.id}: one sentence`)
    assert.ok(['REAL', 'ESTIMATED', 'SYNTHETIC', 'NWP'].includes(s.badge.tag), `${s.id}.badge.tag`)
    assert.ok(s.badge.text && s.glyph && s.chip)
  }
})

test('no stage, story step or narration makes a claim FloodNet cannot back', () => {
  const text = JSON.stringify([PIPELINE_STAGES, DEMO_STORY, STORY_HEADER])
    + ['en', 'hi', 'mr'].map((l) => narrate.rainSource({ rainfallSourceType: 'radar_image_derived' }, l)).join(' ')
  for (const re of FORBIDDEN) assert.doesNotMatch(text, re)
  assert.match(PIPELINE_STAGES[0].line.en, /radar-derived rainfall estimates/)
  assert.match(stageById('action').line.en, /flood-aware routing/)
})

test('navigation targets are real: tabs, layers and data-tour anchors exist in the app source', () => {
  for (const s of PIPELINE_STAGES) {
    if (s.focus.tab) assert.ok(RIGHT_TABS.includes(s.focus.tab), `${s.id}: unknown tab ${s.focus.tab}`)
    for (const k of s.focus.layersOn || []) assert.ok(MAP_LAYERS.includes(k), `${s.id}: unknown layer ${k}`)
    assert.ok(s.focus.target.length > 0)
    for (const sel of s.focus.target) {
      const anchor = sel.match(/data-tour="([^"]+)"/)?.[1]
      if (anchor) assert.ok(SOURCE.includes(`data-tour="${anchor}"`), `${s.id}: no element carries data-tour="${anchor}"`)
      else assert.ok(SOURCE.includes(`\`rtab-\${`) || SOURCE.includes(sel.slice(1)), `${s.id}: ${sel} not found`)
    }
  }
  for (const k of RIGHT_TABS) assert.ok(SOURCE.includes(`key: '${k}'`), `right tab ${k} is not defined in App.jsx`)
  for (const k of MAP_LAYERS) assert.ok(new RegExp(`key: '${k}'`).test(SOURCE), `layer ${k} is not in LayerControl`)
})

test('the dashboard story header covers every stage exactly once', () => {
  const covered = STORY_HEADER.flatMap((g) => g.stages)
  assert.deepEqual([...covered].sort(), PIPELINE_STAGES.map((s) => s.id).sort())
  assert.deepEqual(STORY_HEADER.map((g) => g.label), ['RAINFALL', 'FLOOD', 'DRAINAGE', 'ACTION'])
})

test('Demo Story follows rain → terrain → runoff → drainage → flood → why → alerts → route', () => {
  assert.deepEqual(DEMO_STORY.map((s) => s.id), [
    'story-rain', 'story-terrain', 'story-runoff', 'story-drainage', 'story-flood', 'story-why', 'story-alerts', 'story-route',
  ])
  for (const step of DEMO_STORY) {
    assert.ok(step.stage ? stageById(step.stage) : ['moments', 'where', 'alerts', 'routing'].includes(step.facts), step.id)
    for (const lang of ['en', 'hi', 'mr']) assert.ok(localized(step.title, lang))
  }
  assert.ok(SOURCE.includes("start('story')") && SOURCE.includes('useTour('), 'Demo Story must run on the shared tour engine')
})

test('layersToEnable only asks for layers that are currently off', () => {
  const drainage = stageById('drainage')
  assert.deepEqual(layersToEnable(drainage, { drainage: false }), ['drainage'])
  assert.deepEqual(layersToEnable(drainage, { drainage: true }), [])
  assert.deepEqual(layersToEnable(stageById('rain'), {}), [])
})

test('rainfall-source sentence comes from the run provenance, per language', () => {
  assert.match(narrate.rainSource({ rainfallSourceType: 'radar_image_derived' }, 'en'), /IMD Mumbai-Veravali radar-derived rainfall estimate/)
  assert.match(narrate.rainSource({ rainfallSourceType: 'radar_image_derived' }, 'mr'), /रडारवरून/)
  assert.match(narrate.rainSource({ rainfallSourceType: 'live_observation' }, 'hi'), /IMD/)
  assert.match(narrate.rainSource({ rainfallSourceType: 'scenario', scenarioName: 'Heavy' }, 'en'), /Heavy scenario/)
  assert.equal(narrate.rainSource({}, 'en'), null)      // nothing known -> nothing said
})

// D-37 (2026-10-11): the tour now also visits the Response plan page, so a first-time user sees the whole
// workflow. Intended behaviour change: this pin was updated with it, not weakened.
test('the six tab steps exist for both tours, including the response plan', () => {
  assert.deepEqual(TAB_STEPS.map((t) => t.tab), ['overview', 'alerts', 'why', 'routing', 'response', 'provenance'])
})

test('route exposure is relative wording from real depths, never "safe"', () => {
  assert.equal(exposureOf(42, 30).label, 'High')
  assert.equal(exposureOf(16, 30).label, 'Moderate')
  assert.equal(exposureOf(8, 30).label, 'Lower')
  assert.equal(exposureOf(0, 30).label, 'Dry')
  assert.equal(exposureOf(null, 30), null)
  assert.equal(exposureOf(10, 0), null)
})
