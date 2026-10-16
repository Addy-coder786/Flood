// Dispatcher messaging helpers (2026-10-11): development test recipients, "Send test SMS", subscriber editing.
// Written before src/api/messaging.js existed. fetch is replaced: no request leaves the process, nothing is sent.
import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

import {
  canRetry, canSimulateReport, consentedChannels, editBody, listTestSends, modeBadge, newIdempotencyKey,
  previewTestSend, refreshTestSendStatus, retryTestSend, seedDevTestSubscribers, segmentText, sendTestMessage, simulateTestSendReport,
  updateSubscriber,
} from './messaging.js'

const HERE = dirname(fileURLToPath(import.meta.url))

function withFetch(fn) {
  const calls = []
  const saved = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init, body: init?.body ? JSON.parse(init.body) : undefined })
    return { ok: true, status: 200, json: async () => ({ ok: true }) }
  }
  return Promise.resolve(fn(calls)).finally(() => { globalThis.fetch = saved })
}

test('newIdempotencyKey: one fresh server-acceptable key per confirmation', () => {
  const a = newIdempotencyKey()
  const b = newIdempotencyKey()
  assert.match(a, /^[A-Za-z0-9_-]{8,80}$/)
  assert.notEqual(a, b)
})

test('API calls use the dispatcher endpoints with the right methods and bodies', () => withFetch(async (calls) => {
  await seedDevTestSubscribers('dadar-west')
  await updateSubscriber('SUB 7', { language: 'hi' })
  await previewTestSend({ subscriberId: 'SUB1', channel: 'sms' })
  await sendTestMessage({ subscriberId: 'SUB1', channel: 'sms', idempotencyKey: 'k-12345678', textSha256: 'ab', expectedMode: 'SIMULATED' })
  await listTestSends()
  await retryTestSend('TEST:k-12345678')
  await simulateTestSendReport('TEST:k-12345678')
  await refreshTestSendStatus('TEST:k-12345678')
  const seen = calls.map((c) => [c.init?.method || 'GET', c.url])
  assert.deepEqual(seen, [
    ['POST', '/api/ops/subscribers/seed-dev-test'],
    ['PATCH', '/api/ops/subscribers/SUB%207'],
    ['POST', '/api/ops/test-sends/preview'],
    ['POST', '/api/ops/test-sends'],
    ['GET', '/api/ops/test-sends'],
    ['POST', '/api/ops/test-sends/TEST%3Ak-12345678/retry'],
    ['POST', '/api/ops/test-sends/TEST%3Ak-12345678/simulate-callbacks'],
    ['POST', '/api/ops/test-sends/TEST%3Ak-12345678/refresh-status'],
  ])
  assert.deepEqual(calls[0].body, { locality_id: 'dadar-west' })
  assert.deepEqual(calls[2].body, { subscriber_id: 'SUB1', channel: 'sms' })
  // the send carries the mode the preview showed: a real message only after a LIVE preview (QA 2026-10-11 M8)
  assert.deepEqual(calls[3].body, { subscriber_id: 'SUB1', channel: 'sms', idempotency_key: 'k-12345678', text_sha256: 'ab', expected_mode: 'SIMULATED' })
}))

test('modeBadge: SIMULATED unless the server said LIVE, and always says which', () => {
  assert.equal(modeBadge({ mode: 'SIMULATED' }).text, 'SIMULATED')
  assert.equal(modeBadge({ mode: 'LIVE' }).text, 'LIVE')
  assert.equal(modeBadge({}).text, 'SIMULATED')
  assert.equal(modeBadge(null).text, 'SIMULATED')
  assert.match(modeBadge({ mode: 'LIVE' }).note, /real message/i)
  assert.match(modeBadge({ mode: 'SIMULATED' }).note, /nothing is sent/i)
})

test('segmentText: characters, encoding and segments in words', () => {
  assert.equal(segmentText({ characters: 118, encoding: 'GSM-7', segments: 1 }), '118 characters · GSM-7 · 1 SMS segment')
  assert.equal(segmentText({ characters: 200, encoding: 'UCS-2', segments: 3 }), '200 characters · UCS-2 · 3 SMS segments')
  assert.equal(segmentText(null), '')
})

test('consentedChannels lists only channels with a consent record', () => {
  assert.deepEqual(consentedChannels({ channels: { sms: { consent: {} }, whatsapp: { consent: {} } } }), ['sms', 'whatsapp'])
  assert.deepEqual(consentedChannels({ channels: {} }), [])
  assert.deepEqual(consentedChannels(null), [])
})

test('canRetry / canSimulateReport follow the server, never guess', () => {
  assert.equal(canRetry({ retryable: true, status: 'FAILED' }), true)
  assert.equal(canRetry({ retryable: false, status: 'FAILED' }), false)
  assert.equal(canRetry({ status: 'UNKNOWN' }), false)
  assert.equal(canSimulateReport({ provider: 'simulated', status: 'ACCEPTED', provider_message_id: 'SIM-1' }), true)
  assert.equal(canSimulateReport({ provider: 'twilio', status: 'ACCEPTED', provider_message_id: 'SM1' }), false)
  assert.equal(canSimulateReport({ provider: 'simulated', status: 'DELIVERED', provider_message_id: 'SIM-1' }), false)
})

test('editBody sends only what changed; kept channels keep their consent, new ones carry a method', () => {
  const sub = { language: 'en', verified: true, area: { kind: 'locality', locality_id: 'naigaon' },
    channels: { sms: { consent: { method: 'web_form' } } } }
  assert.deepEqual(editBody(sub, { language: 'en', localityId: 'naigaon', sms: true, whatsapp: false, verified: true, method: '' }), {})
  assert.deepEqual(editBody(sub, { language: 'mr', localityId: 'dadar-west', sms: true, whatsapp: true, verified: true, method: 'phone call' }),
    { language: 'mr', locality_id: 'dadar-west', channels: { sms: {}, whatsapp: { method: 'phone call' } } })
  assert.deepEqual(editBody(sub, { language: 'en', localityId: 'naigaon', sms: false, whatsapp: true, verified: false, method: 'paper form' }),
    { channels: { whatsapp: { method: 'paper form' } }, verified: false })
  assert.throws(() => editBody(sub, { language: 'en', localityId: 'naigaon', sms: true, whatsapp: true, verified: true, method: ' ' }), /consent/)
  assert.throws(() => editBody(sub, { language: 'en', localityId: 'naigaon', sms: false, whatsapp: false, verified: true, method: '' }), /opt out/i)
})

test('messaging.js keeps credentials and numbers out of the browser', () => {
  const src = readFileSync(join(HERE, 'messaging.js'), 'utf8')
  assert.doesNotMatch(src, /Authorization/)
  assert.doesNotMatch(src, /import\.meta\.env\.VITE_(?!API_BASE_URL)/)
  assert.doesNotMatch(src, /TWILIO|MSG91|FLOODNET_DEV_TEST_RECIPIENTS=|\+91\d/)
  assert.match(src, /from '\.\/client\.js'/)
})
