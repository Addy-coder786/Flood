// Run with:  npm run test:unit   (node --test, no extra dependency)
//
// WCAG 2.x contrast guard for the design tokens (D-37 FINAL, Agent E §4, Agent D test matrix §6.3 / P5).
// Self-contained: parses `:root { --name: #hex | rgb[a](...) }` from src/index.css, composites alpha colours
// over the named background, and applies the sRGB relative-luminance / contrast-ratio formula. No dependency.
//
// Thresholds: 4.5:1 for text (all FloodNet UI text is < 18.5 px bold / 24 px), 3:1 for non-text UI boundaries.
// Written test-first: the "required passes" below describe the palette AFTER the D-37 same-hue darkening and
// fail today for the measured reasons recorded next to each assertion. The hue pins stop the fix from swapping
// the brand for another colour: only lightness may change.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

import { ACCESS_STATUS, ACCESS_STATUS_ORDER } from './roadAccess.js'
import * as response from './response.js'
import * as alerts from './alerts.js'

const HERE = dirname(fileURLToPath(import.meta.url))
const SRC = join(HERE, '..')
const CSS = readFileSync(join(SRC, 'index.css'), 'utf8')

// ───────────────────────────────────────────── colour maths (WCAG 2.x, sRGB)

/** '#rgb' | '#rrggbb' | '#rrggbbaa' | 'rgb(r,g,b)' | 'rgba(r,g,b,a)' -> [r, g, b, a] or null. */
export function parseColor(s) {
  if (typeof s !== 'string') return null
  const v = s.trim()
  let m = v.match(/^#([0-9a-f]{3,8})$/i)
  if (m) {
    let h = m[1]
    if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join('')
    if (h.length !== 6 && h.length !== 8) return null
    const n = parseInt(h.slice(0, 6), 16)
    const a = h.length === 8 ? parseInt(h.slice(6), 16) / 255 : 1
    return [n >> 16, (n >> 8) & 255, n & 255, a]
  }
  m = v.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/i)
  if (m) return [+m[1], +m[2], +m[3], m[4] == null ? 1 : +m[4]]
  return null
}
/** Alpha-composite fg over an opaque bg (source-over). */
const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3])).concat([1])
const channel = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4 }
const luminance = ([r, g, b]) => 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
export function contrast(fg, bg) {
  const f = over(fg, bg)
  const [l1, l2] = [luminance(f), luminance(bg)]
  return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05)
}
/** HSL hue in degrees [0, 360). */
export function hue([r, g, b]) {
  r /= 255; g /= 255; b /= 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  if (d === 0) return 0
  let h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4
  h *= 60
  return h < 0 ? h + 360 : h
}
const hueDelta = (a, b) => { const d = Math.abs(hue(a) - hue(b)) % 360; return d > 180 ? 360 - d : d }
const r2 = (x) => Math.round(x * 100) / 100

// ───────────────────────────────────────────── token sheet

const rootBlock = CSS.match(/:root\s*\{([\s\S]*?)\n\}/)?.[1] ?? ''
const TOKENS = {}
for (const m of rootBlock.matchAll(/--([\w-]+)\s*:\s*([^;]+);/g)) TOKENS[m[1]] = m[2].trim()

/** Colour of a token, or null when it is missing / not a colour. */
const tok = (name) => parseColor(TOKENS[name])
/** Colour of a token that must exist (fails with a clear message otherwise). */
function need(name) {
  assert.ok(name in TOKENS, `token --${name} is missing from :root in src/index.css`)
  const c = tok(name)
  assert.ok(c, `token --${name} = "${TOKENS[name]}" is not a #hex / rgb[a]() colour`)
  return c
}
/** The `color:` of a class rule in index.css, e.g. '.tag-REAL'. */
function classColor(selector) {
  const re = new RegExp(`${selector.replace('.', '\\.')}\\s*\\{([^}]*)\\}`)
  const body = CSS.match(re)?.[1] ?? ''
  const raw = body.match(/(?:^|;|\s)color\s*:\s*([^;]+)/)?.[1]?.trim()
  if (!raw) return null
  const viaToken = raw.match(/^var\(--([\w-]+)\)$/)
  return viaToken ? tok(viaToken[1]) : parseColor(raw)
}
const WHITE = parseColor('#FFFFFF')
const SURFACES = ['bg-app', 'bg-elevated', 'bg-surface']

function assertText(fgName, fg, bgName, bg, min = 4.5) {
  const ratio = contrast(fg, bg)
  assert.ok(ratio >= min, `${fgName} on ${bgName}: ${r2(ratio)}:1 < ${min}:1`)
  return ratio
}

// Original token values at the time this test was written (src/index.css, 2026-10-11). The D-37 fix may only
// DARKEN these within the same hue; a replacement that changes hue by more than HUE_TOLERANCE fails.
const ORIGINAL = {
  'text-faint': '#9C9184',
  'sev-minor': '#C99A1E',
  'sev-moderate': '#D9731F',
  real: '#1F8A4C',
}
const HUE_TOLERANCE = 12 // degrees

// Pairs that are explicitly OUT OF SCOPE for the text rule, with the ratio measured today. These are the raw
// severity tokens used as MAP STROKES (Leaflet polylines over the basemap) and legend swatches: WCAG treats them
// as graphics (3:1, with the legend text carrying the label), and D-37 FINAL keeps map strokes unchanged so the
// flood ramp on the map stays the familiar saturated one. They must NOT be used as running text: the darkened
// `--sev-*-text` variants exist for that. The allow-list is kept honest both ways: an entry that regresses
// further fails, and an entry that starts passing fails too (remove it from the list).
const KNOWN_FAILURES = {
  '--sev-clear on --bg-app': { fg: 'sev-clear', bg: 'bg-app', measured: 4.34, why: 'map stroke / legend swatch for the "clear" band (graphic, not text)' },
  '--sev-minor on --bg-app': { fg: 'sev-minor', bg: 'bg-app', measured: 2.32, why: 'map stroke for the 5-15 cm band; text uses --sev-minor-text' },
  '--sev-moderate on --bg-app': { fg: 'sev-moderate', bg: 'bg-app', measured: 2.93, why: 'map stroke for the 15-30 cm band; text uses --sev-moderate-text' },
  '--sev-severe on --bg-app': { fg: 'sev-severe', bg: 'bg-app', measured: 2.93, why: 'map stroke for the 30-60 cm band (same value as moderate in CSS today)' },
}

// ───────────────────────────────────────────── sanity: the maths against published reference values

test('contrast maths: black/white 21:1, white on white 1:1, known token pairs match the audited values', () => {
  assert.equal(r2(contrast(parseColor('#000'), WHITE)), 21)
  assert.equal(r2(contrast(WHITE, WHITE)), 1)
  // values audited independently in Agent E §4.1 / Agent D §6.3 from the same tokens
  assert.equal(r2(contrast(parseColor('#2B2118'), parseColor('#F7F2E9'))), 14.12)
  assert.equal(r2(contrast(parseColor('#6E6255'), parseColor('#F7F2E9'))), 5.32)
  assert.equal(r2(contrast(parseColor('#9C9184'), parseColor('#F7F2E9'))), 2.77)
  assert.equal(r2(contrast(parseColor('rgba(43, 33, 24, 0.50)'), parseColor('#F7F2E9'))), 3.07)
  assert.equal(r2(hue(parseColor('#FF0000'))), 0)
  assert.equal(r2(hue(parseColor('#00FF00'))), 120)
  assert.equal(r2(hue(parseColor('#0000FF'))), 240)
  assert.ok(Object.keys(TOKENS).length > 20, 'parsed the :root token sheet')
  for (const n of ['bg-app', 'bg-elevated', 'bg-surface', 'accent', 'text', 'text-dim', 'text-faint', 'text-inverse']) need(n)
})

// ───────────────────────────────────────────── required passes (text, 4.5:1)

test('--text and --text-dim reach 4.5:1 on --bg-app, --bg-elevated and --bg-surface', () => {
  for (const fg of ['text', 'text-dim']) for (const bg of SURFACES) assertText(`--${fg}`, need(fg), `--${bg}`, need(bg))
})

test('--text-faint reaches 4.5:1 on --bg-app and --bg-elevated (today 2.77 / 3.09: it is used for readable labels)', () => {
  for (const bg of ['bg-app', 'bg-elevated']) assertText('--text-faint', need('text-faint'), `--${bg}`, need(bg))
})

test('--text-inverse on --accent and --accent on --bg-app stay at or above 4.5:1', () => {
  assertText('--text-inverse', need('text-inverse'), '--accent', need('accent'))
  assertText('--accent', need('accent'), '--bg-app', need('bg-app'))
})

test('--border-control exists and composites to at least 3:1 on --bg-app (WCAG 1.4.11 control boundary)', () => {
  const c = need('border-control')
  assertText('--border-control', c, '--bg-app', need('bg-app'), 3.0)
})

test('--sev-minor-text and --sev-moderate-text exist and reach 4.5:1 on --bg-app', () => {
  for (const n of ['sev-minor-text', 'sev-moderate-text']) assertText(`--${n}`, need(n), '--bg-app', need('bg-app'))
})

test('REAL provenance text (--real-text, else .tag-REAL colour) reaches 4.5:1 on white (today #1F8A4C = 4.38)', () => {
  const c = tok('real-text') ?? classColor('.tag-REAL')
  assert.ok(c, 'neither a --real-text token nor a .tag-REAL { color } rule was found in index.css')
  assertText('--real-text / .tag-REAL', c, 'white', WHITE)
})

// ───────────────────────────────────────────── hue preservation (darken only, never re-brand)

test('darkened tokens keep the hue of the original value (|dHue| <= 12 degrees)', () => {
  const pairs = [
    ['--text-faint', need('text-faint'), ORIGINAL['text-faint']],
    ['--sev-minor-text', need('sev-minor-text'), ORIGINAL['sev-minor']],
    ['--sev-moderate-text', need('sev-moderate-text'), ORIGINAL['sev-moderate']],
    ['--real-text / .tag-REAL', tok('real-text') ?? classColor('.tag-REAL'), ORIGINAL.real],
  ]
  for (const [name, now, was] of pairs) {
    assert.ok(now, `${name} missing`)
    const d = hueDelta(now, parseColor(was))
    assert.ok(d <= HUE_TOLERANCE, `${name}: hue moved ${r2(d)} degrees from ${was} (hue ${r2(hue(parseColor(was)))}) — darken, do not re-colour`)
    // and it really is a darkening (never lighter than the original)
    assert.ok(luminance(now) <= luminance(parseColor(was)) + 1e-9, `${name} must not be lighter than ${was}`)
  }
  // the untouched map-stroke tokens keep their original values exactly (D-37: map strokes unchanged)
  for (const n of ['sev-minor', 'sev-moderate']) assert.equal(TOKENS[n].toUpperCase(), ORIGINAL[n], `--${n} is a map stroke and must stay ${ORIGINAL[n]}`)
})

// ───────────────────────────────────────────── status chips (tinted style: darkened text on --bg-elevated)

test('ACCESS_STATUS: every status gains a `text` colour at 4.5:1 on --bg-elevated; keys, label, hint and color unchanged', () => {
  assert.deepEqual(Object.keys(ACCESS_STATUS), ['USABLE', 'UNCERTAIN', 'UNKNOWN', 'AT_RISK', 'CLOSED'])
  assert.deepEqual(ACCESS_STATUS_ORDER, ['USABLE', 'UNCERTAIN', 'UNKNOWN', 'AT_RISK', 'CLOSED'])
  const bg = need('bg-elevated')
  for (const [k, v] of Object.entries(ACCESS_STATUS)) {
    assert.equal(typeof v.label, 'string', `${k}.label`); assert.ok(v.label, `${k}.label`)
    assert.equal(typeof v.hint, 'string', `${k}.hint`); assert.ok(v.hint, `${k}.hint`)
    assert.ok(parseColor(v.color), `${k}.color stays a colour (map strokes use it)`)
    assert.ok(parseColor(v.text), `${k}.text is missing or not a colour (tinted chips need a darkened text colour)`)
    assertText(`ACCESS_STATUS.${k}.text`, parseColor(v.text), '--bg-elevated', bg)
  }
})

test('response.js: STATUS_TEXT_COLOR / PRIORITY_TEXT_COLOR exist for every status and priority at 4.5:1 on --bg-elevated; STATUS_COLOR / PRIORITY_COLOR unchanged', () => {
  const bg = need('bg-elevated')
  assert.deepEqual(Object.keys(response.STATUS_COLOR), ['PROPOSED', 'ACCEPTED', 'DISPATCHED', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'])
  assert.deepEqual(Object.keys(response.PRIORITY_COLOR), ['critical', 'high', 'medium', 'low'])
  for (const [k, v] of Object.entries({ ...response.STATUS_COLOR, ...response.PRIORITY_COLOR })) {
    assert.ok(parseColor(v), `${k}: STATUS_COLOR / PRIORITY_COLOR values stay plain colour strings (map markers use them)`)
  }
  for (const [mapName, colors] of [['STATUS_TEXT_COLOR', response.STATUS_COLOR], ['PRIORITY_TEXT_COLOR', response.PRIORITY_COLOR]]) {
    const textMap = response[mapName]
    assert.ok(textMap && typeof textMap === 'object', `response.js must export ${mapName} (darkened text colour per key)`)
    assert.deepEqual(Object.keys(textMap), Object.keys(colors), `${mapName} has the same keys as its colour map`)
    for (const [k, v] of Object.entries(textMap)) {
      assert.ok(parseColor(v), `${mapName}.${k} is not a colour`)
      assertText(`${mapName}.${k}`, parseColor(v), '--bg-elevated', bg)
    }
  }
})

test('alerts.js: TIER_TEXT_COLOR exists for every alert tier at 4.5:1 on --bg-app; TIER_COLOR keys unchanged (D-37 tier text variants)', () => {
  const bg = need('bg-app')
  assert.deepEqual(Object.keys(alerts.TIER_COLOR), ['WATCH', 'ADVISORY', 'SEVERE', 'CRITICAL', 'DRAINAGE', 'ROUTE'])
  const textMap = alerts.TIER_TEXT_COLOR
  assert.ok(textMap && typeof textMap === 'object', 'alerts.js must export TIER_TEXT_COLOR (darkened text colour per tier)')
  assert.deepEqual(Object.keys(textMap), Object.keys(alerts.TIER_COLOR))
  for (const [k, v] of Object.entries(textMap)) {
    assert.ok(parseColor(v), `TIER_TEXT_COLOR.${k} is not a colour`)
    assertText(`TIER_TEXT_COLOR.${k}`, parseColor(v), '--bg-app', bg)
  }
})

// ───────────────────────────────────────────── allow-list kept honest

test('KNOWN_FAILURES (map-stroke graphics) neither regress further nor linger once fixed', () => {
  for (const [name, { fg, bg, measured }] of Object.entries(KNOWN_FAILURES)) {
    const ratio = contrast(need(fg), need(bg))
    assert.ok(ratio >= measured - 0.05, `${name}: ${r2(ratio)}:1 regressed below the recorded ${measured}:1`)
    assert.ok(ratio < 4.5, `${name}: now ${r2(ratio)}:1 — it passes as text, remove it from KNOWN_FAILURES`)
  }
})

// ───────────────────────────────────────────── wording pin (Agent E §8.1)

const walk = (dir) => readdirSync(dir).flatMap((f) => {
  const p = join(dir, f)
  return statSync(p).isDirectory() ? walk(p) : /\.jsx?$/.test(f) && !/\.test\.mjs$/.test(f) ? [p] : []
})
const stripComments = (s) => s.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1')

test('no rendered text promises a "safe route", a "guaranteed safe" anything, or a "fastest route"', () => {
  for (const p of walk(SRC)) {
    const s = stripComments(readFileSync(p, 'utf8'))
    for (const re of [/safe route/i, /guaranteed safe/i, /fastest route/i]) {
      assert.doesNotMatch(s, re, `${p} renders ${re}`)
    }
  }
})

// ───────────────────────────────────────────── chips as actually rendered (QA audit L1)
// Chips draw their text on a light tint of their own colour (`${color}14`, about 8 %) over a white panel, not on
// plain white. The text colour must reach 4.5:1 on THAT background.
test('status chips reach 4.5:1 on their own 8% tint over white, as rendered', () => {
  const white = parseColor('#FFFFFF')
  const tintOf = (hex) => over(parseColor(`${hex.slice(0, 7)}14`), white)
  const rows = [
    ...Object.entries(ACCESS_STATUS).map(([k, v]) => [`ACCESS_STATUS.${k}`, v.text, v.color]),
    ...Object.entries(response.STATUS_TEXT_COLOR).map(([k, v]) => [`STATUS_TEXT_COLOR.${k}`, v, response.STATUS_COLOR[k]]),
    ...Object.entries(response.PRIORITY_TEXT_COLOR).map(([k, v]) => [`PRIORITY_TEXT_COLOR.${k}`, v, response.PRIORITY_COLOR[k]]),
  ]
  for (const [name, text, color] of rows) {
    const ratio = contrast(parseColor(text), tintOf(color))
    assert.ok(ratio >= 4.5, `${name}: ${r2(ratio)}:1 on its tint < 4.5:1`)
  }
})
