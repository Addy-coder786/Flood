// Dispatcher messaging API (2026-10-11): designated development test recipients, the "Send test SMS" workflow and
// subscriber editing. The older messaging calls stay in client.js. Every call goes through request() with the
// dispatcher token header; no credential, provider key or phone number is held here. Test recipients come only
// from the server's configuration; the server returns the last four digits of a number at most.
import { request } from './client.js'

const enc = encodeURIComponent

export const seedDevTestSubscribers = (localityId) =>
  request('/api/ops/subscribers/seed-dev-test', { method: 'POST', body: { locality_id: localityId } })
export const updateSubscriber = (id, body) => request(`/api/ops/subscribers/${enc(id)}`, { method: 'PATCH', body })
export const previewTestSend = ({ subscriberId, channel = 'sms' }) =>
  request('/api/ops/test-sends/preview', { method: 'POST', body: { subscriber_id: subscriberId, channel } })
// expectedMode = the mode the preview showed: the server refuses a send whose mode changed since (a real message only
// after a LIVE preview)
export const sendTestMessage = ({ subscriberId, channel = 'sms', idempotencyKey, textSha256, expectedMode }) =>
  request('/api/ops/test-sends', {
    method: 'POST',
    body: { subscriber_id: subscriberId, channel, idempotency_key: idempotencyKey, text_sha256: textSha256, expected_mode: expectedMode },
  })
export const listTestSends = () => request('/api/ops/test-sends')
export const retryTestSend = (deliveryId) => request(`/api/ops/test-sends/${enc(deliveryId)}/retry`, { method: 'POST' })
/** LIVE sends only: ask the provider (Twilio) for the delivery status -- a local server cannot be called back. */
export const refreshTestSendStatus = (deliveryId) =>
  request(`/api/ops/test-sends/${enc(deliveryId)}/refresh-status`, { method: 'POST' })
export const simulateTestSendReport = (deliveryId) =>
  request(`/api/ops/test-sends/${enc(deliveryId)}/simulate-callbacks`, { method: 'POST' })

/** One key per confirmed send: a double click re-sends the same key and the server returns the first record. */
export function newIdempotencyKey() {
  const c = globalThis.crypto
  if (c?.randomUUID) return `ui-${c.randomUUID()}`
  const bytes = new Uint8Array(16)
  if (c?.getRandomValues) c.getRandomValues(bytes)
  else for (let i = 0; i < bytes.length; i += 1) bytes[i] = Math.floor(Math.random() * 256)
  return `ui-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

/** SIMULATED unless the server said LIVE; the note says what that means. */
export function modeBadge(x) {
  if (x?.mode === 'LIVE') {
    return { text: 'LIVE', note: 'A real message goes to the phone through the provider.' }
  }
  return { text: 'SIMULATED', note: 'Recorded by the simulated provider; nothing is sent to a phone.' }
}

export function segmentText(info) {
  if (!info || info.characters == null) return ''
  const n = info.segments
  return `${info.characters} characters · ${info.encoding} · ${n} SMS segment${n === 1 ? '' : 's'}`
}

export function consentedChannels(sub) {
  return Object.keys(sub?.channels || {}).filter((ch) => sub.channels[ch])
}

export const canRetry = (s) => !!s?.retryable
export const canSimulateReport = (s) => s?.provider === 'simulated' && s?.status === 'ACCEPTED' && !!s?.provider_message_id

/**
 * The PATCH body for an edited subscriber: only changed fields. A channel that stays keeps its consent record ({});
 * a newly permitted channel needs how consent was obtained (`method`); leaving every channel out is an opt-out,
 * which has its own button.
 */
export function editBody(sub, form) {
  const out = {}
  if (form.language && form.language !== sub.language) out.language = form.language
  const currentLocality = sub.area?.kind === 'locality' ? sub.area.locality_id : null
  if (form.localityId && form.localityId !== currentLocality) out.locality_id = form.localityId
  const had = new Set(consentedChannels(sub))
  const want = ['sms', 'whatsapp'].filter((ch) => form[ch])
  if (!want.length) throw new Error('Keep at least one channel. To stop all messages use Opt out.')
  const changed = want.length !== had.size || want.some((ch) => !had.has(ch))
  if (changed) {
    const channels = {}
    for (const ch of want) {
      if (had.has(ch)) channels[ch] = {}
      else {
        const method = String(form.method || '').trim()
        if (!method) throw new Error(`Record how consent for ${ch} was obtained before adding it.`)
        channels[ch] = { method }
      }
    }
    out.channels = channels
  }
  if (typeof form.verified === 'boolean' && form.verified !== !!sub.verified) out.verified = form.verified
  return out
}
