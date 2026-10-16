// QA audit L2: after a reload the plan is stale because the forecast was not stored, not because it changed.
import test from 'node:test'
import assert from 'node:assert/strict'
import { emptyIncident, replanProposal } from './incident.js'

test('a restored incident asks to re-run the forecast and replan, without claiming the forecast changed', () => {
  const s = emptyIncident()
  const state = { ...s, currentT: 30, response: { ...s.response, staleReason: 'restored from this browser', forecastRevision: 0,
    ledger: [{ assignment_id: 'A1', status: 'ACCEPTED' }] } }
  const p = replanProposal(state)
  assert.ok(p)
  assert.doesNotMatch(p.reason, /Forecast changed/)
  assert.match(p.reason, /restored/i)
  assert.match(p.reason, /run the forecast/i)
  assert.equal(p.accepted, 1)
})

// QA audit L3: an Accept / Dispatch clicked while a plan request is in flight would be overwritten when the plan
// lands. The context must refuse ledger edits while `response.loading`, and the buttons must be disabled.
import { readFileSync } from 'node:fs'
test('assignment changes are refused while a plan is being computed', () => {
  const ctx = readFileSync(new URL('../state/FloodNetContext.jsx', import.meta.url), 'utf8')
  const start = ctx.indexOf('const setAssignment = useCallback(')
  const body = ctx.slice(start, ctx.indexOf('\n  }, [', start))
  assert.match(body, /r\.loading/, 'setAssignment must check response.loading')
  const panel = readFileSync(new URL('../components/ResponsePanel/ResponsePanel.jsx', import.meta.url), 'utf8')
  assert.match(panel, /disabled=\{busy\}/, 'transition buttons are disabled while a plan is computing')
})
