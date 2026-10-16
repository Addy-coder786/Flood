import { useCallback, useEffect, useState } from 'react'
import {
  canRetry, canSimulateReport, consentedChannels, listTestSends, modeBadge, newIdempotencyKey, previewTestSend,
  refreshTestSendStatus, retryTestSend, seedDevTestSubscribers, segmentText, sendTestMessage, simulateTestSendReport,
} from '../../api/messaging.js'
import { formatIST } from '../../lib/publicAlert.js'
import { Section } from '../Shell/PageHeader.jsx'
import InfoTip from '../ui/InfoTip.jsx'
import styles from '../Pages/Pages.module.css'
import own from './SubscriberAlerts.module.css'

// "Send test message" (2026-10-11): only to the development test recipients the project owner listed in the server's
// configuration (FLOODNET_DEV_TEST_RECIPIENTS). The dispatcher picks a recipient (masked), sees the exact text, its
// length and SMS segments and whether the send is SIMULATED or LIVE, confirms, then sends. One idempotency key per
// confirmation: a double click returns the first record. Retry only where the server says it is safe.

function ModeTag({ x }) {
  const b = modeBadge(x)
  return <span className={b.text === 'LIVE' ? own.live : own.sim}>{b.text}</span>
}

export default function DevTestSend({ data, status, reload, busy, setBusy, setError }) {
  const dev = status?.dev_test
  const recipients = (data?.subscribers || []).filter((s) => s.dev_test && s.status === 'ACTIVE')
  const [locality, setLocality] = useState('')
  const [recipient, setRecipient] = useState('')
  const [channel, setChannel] = useState('sms')
  const [preview, setPreview] = useState(null)        // {..., key}: the key is made when the text is shown
  const [confirmed, setConfirmed] = useState(false)
  const [result, setResult] = useState(null)
  const [sends, setSends] = useState([])

  const loadSends = useCallback(async () => {
    const out = await listTestSends()
    setSends(out.sends || [])
  }, [])
  useEffect(() => {
    const id = setTimeout(() => { loadSends().catch(() => {}) }, 0)
    return () => clearTimeout(id)
  }, [loadSends])

  const chosen = recipients.find((r) => r.id === recipient) || null
  const channels = consentedChannels(chosen)
  const run = async (fn) => {
    setBusy(true); setError(null)
    try { await fn() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const pick = (id) => {
    setRecipient(id); setPreview(null); setConfirmed(false); setResult(null)
    const r = recipients.find((x) => x.id === id)
    setChannel(consentedChannels(r)[0] || 'sms')
  }
  const doPreview = () => run(async () => {
    const p = await previewTestSend({ subscriberId: recipient, channel })
    setPreview({ ...p, key: newIdempotencyKey() }); setConfirmed(false); setResult(null)
  })
  const doSend = () => run(async () => {
    const out = await sendTestMessage({ subscriberId: preview.subscriber_id, channel: preview.channel,
      idempotencyKey: preview.key, textSha256: preview.text_sha256, expectedMode: preview.mode })
    setResult(out); setPreview(null); setConfirmed(false)
    await loadSends()
  })
  const act = (fn) => run(async () => { setResult(await fn()); await loadSends() })

  return (
    <Section title="Send a test message" hint="Only to development test recipients that the project owner listed in the server configuration. Numbers are never shown here, only the last four digits.">
      <p className={styles.small}>
        Test recipients on the server: <strong>{dev?.configured ? `yes (${dev.valid} valid${dev.invalid ? `, ${dev.invalid} invalid` : ''})` : 'no'}</strong>
        {dev?.production ? ` · ${dev.note}` : ''}
      </p>
      <div className={own.actions}>
        <label className="field-label" htmlFor="dev-locality">Area for new test recipients</label>
        <select id="dev-locality" value={locality} onChange={(e) => setLocality(e.target.value)} className={own.narrowSelect}>
          <option value="">Choose a pilot locality…</option>
          {(data?.localities || []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
        </select>
        <button type="button" className="btn" disabled={busy || !locality || !dev?.seeding_allowed}
          onClick={() => run(async () => { await seedDevTestSubscribers(locality); await reload() })}>
          Add test recipients from server configuration
        </button>
      </div>

      {!recipients.length ? <p className={styles.empty}>No active development test recipient yet.</p> : (
        <div className={own.formGrid}>
          <label className="field-label" htmlFor="dev-recipient">Recipient</label>
          <select id="dev-recipient" value={recipient} onChange={(e) => pick(e.target.value)}>
            <option value="">Choose a test recipient…</option>
            {recipients.map((r) => <option key={r.id} value={r.id}>{r.number_masked}{r.label ? ` · ${r.label}` : ''} ({r.id})</option>)}
          </select>
          {chosen && (
            <>
              <label className="field-label" htmlFor="dev-channel">Channel</label>
              <select id="dev-channel" value={channel} className={own.narrowSelect}
                onChange={(e) => { setChannel(e.target.value); setPreview(null); setConfirmed(false) }}>
                {channels.map((c) => <option key={c} value={c}>{c === 'sms' ? 'SMS' : 'WhatsApp'}</option>)}
              </select>
              <div className={own.actions}>
                <button type="button" className="btn" disabled={busy || !channels.includes(channel)} onClick={doPreview}>Preview the message</button>
              </div>
            </>
          )}
        </div>
      )}

      {preview && (
        <div className={own.detail}>
          <p className={styles.small}>To {preview.number_masked}{preview.recipient_label ? ` · ${preview.recipient_label}` : ''} by {preview.channel === 'sms' ? 'SMS' : 'WhatsApp'} · <ModeTag x={preview} /></p>
          <pre className={own.pre}>{preview.text}</pre>
          <p className={styles.small}>{segmentText(preview)}</p>
          <p className={styles.small}>{modeBadge(preview).note} {preview.mode_reason}</p>
          {preview.channel_note && <p className={styles.note}>{preview.channel_note}</p>}
          <label className={own.confirm}>
            <input type="checkbox" checked={confirmed} onChange={(e) => setConfirmed(e.target.checked)} />
            {' '}I checked the recipient and the exact text above{preview.mode === 'LIVE' ? ', and I want a REAL message sent' : ''}.
          </label>
          <div className={own.actions}>
            <button type="button" className={`btn ${preview.mode === 'LIVE' ? 'btn-primary' : ''}`} disabled={busy || !confirmed} onClick={doSend}>
              Send test {preview.channel === 'sms' ? 'SMS' : 'WhatsApp'} ({modeBadge(preview).text})
            </button>
            <button type="button" className="btn" disabled={busy} onClick={() => { setPreview(null); setConfirmed(false) }}>Cancel</button>
          </div>
        </div>
      )}

      {result && (
        <p className={own.mode} role="status">
          {result.duplicate ? 'Already sent with this confirmation (not sent again): ' : 'Test send: '}
          <ModeTag x={result} /> <strong>{result.status}</strong>. {result.status_meaning}
        </p>
      )}

      <details className={own.texts}>
        <summary>Recent test sends ({sends.length})</summary>
        {!sends.length ? <p className={styles.empty}>None yet.</p> : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th scope="col">When</th><th scope="col">To</th><th scope="col">Mode</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {sends.map((t) => (
                  <tr key={t.delivery_id}>
                    <td>{formatIST(t.requested_at) || '—'}</td>
                    <td>{t.number_masked || '—'}{t.recipient_label ? ` · ${t.recipient_label}` : ''} · {t.channel}</td>
                    <td><ModeTag x={t} /></td>
                    <td><strong>{t.status}</strong>{(t.attempts || []).length > 1 ? ` (attempt ${t.attempts.length})` : ''}<br /><span className={styles.small}>{t.status_meaning}</span></td>
                    <td className={own.rowActions}>
                      {canRetry(t) && <button type="button" className="btn" disabled={busy} onClick={() => act(() => retryTestSend(t.delivery_id))}>Retry</button>}
                      {t.mode === 'LIVE' && t.provider_message_id && <button type="button" className="btn" disabled={busy} onClick={() => act(() => refreshTestSendStatus(t.delivery_id))} title="Ask the provider for the delivery status">Check status</button>}
                      {canSimulateReport(t) && <button type="button" className="btn" disabled={busy} onClick={() => act(() => simulateTestSendReport(t.delivery_id))} title="SIMULATED provider only">Simulate delivery report</button>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {sends.length > 0 && (
          <p className={styles.small}>Retry rules
            <InfoTip label="When retry is offered">Retry is offered only when the provider never took the message (nothing was sent). UNKNOWN (timeout) and REJECTED are never retried.</InfoTip>
          </p>
        )}
      </details>
    </Section>
  )
}
