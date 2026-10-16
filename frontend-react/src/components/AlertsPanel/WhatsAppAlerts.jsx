import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import * as api from '../../api/client.js'
import { computeAllAlerts, rankHotspots } from '../../lib/alerts.js'
import { forecastLines, responseLines, testLines } from '../../lib/whatsapp.js'
import styles from './AlertsPanel.module.css'

// Phone TEST alerts (docs/ALERTS_WHATSAPP.md): one click sends to every configured channel, WhatsApp (Twilio) and/or
// Telegram. Explicit operator action only. The backend adds the "not an official warning" banner and sends only to
// recipients in its .env.

const DELIVERY_COLOR = { delivered: '#16a34a', read: '#16a34a', 'delivered to Telegram': '#16a34a', sent: '#2563eb', queued: '#64748b', accepted: '#64748b', failed: '#dc2626', undelivered: '#dc2626' }
const CHANNEL_LABEL = { whatsapp: 'WhatsApp', telegram: 'Telegram' }

function ChannelStatus({ name, st }) {
  if (!st) return null
  return (
    <div className={styles.metaRow}>
      <b>{CHANNEL_LABEL[name]}:</b>{' '}
      {st.configured ? `ready → ${st.recipients.join(', ')}` : `not set up (${st.missing.join('; ')})`}
    </div>
  )
}

export default function WhatsAppAlerts() {
  const { run, series, route, currentT, roads, currentScenario, response, responseResources } = useFloodNet()
  const [status, setStatus] = useState(null)
  const [sending, setSending] = useState(null)
  const [last, setLast] = useState(null)
  const [error, setError] = useState(null)

  const refreshStatus = useCallback(() => {
    api.phoneStatus().then(setStatus).catch(() => setStatus(null))
  }, [])
  useEffect(() => { refreshStatus() }, [refreshStatus])

  const segNameById = useMemo(() => {
    const m = new Map()
    for (const f of roads?.features || []) m.set(String(f.properties.seg_id), f.properties.name || String(f.properties.seg_id))
    return m
  }, [roads])
  const names = useMemo(() => (responseResources
    ? { settlement: Object.fromEntries(responseResources.settlements.map((s) => [s.id, s.name])) } : null), [responseResources])

  const linesFor = useCallback((kind) => {
    const scenarioName = currentScenario?.name || run?.scenario_name
    if (kind === 'forecast') {
      return forecastLines({
        scenarioName, dataMode: run?.data_mode, currentT,
        hotspots: rankHotspots(series, segNameById, 5),
        alerts: computeAllAlerts({ series, route, currentT }),
      })
    }
    if (kind === 'response') return responseLines({ result: response.result, names })
    return testLines({ scenarioName })
  }, [currentScenario, run, currentT, series, segNameById, route, response.result, names])

  // WhatsApp only: Twilio accepts first and WhatsApp decides afterwards, so check once a few seconds later.
  const checkDelivery = useCallback(async (results) => {
    const checked = await Promise.all(results.map(async (r) => {
      if (r.channel !== 'whatsapp' || !r.ok || !r.sid) return r
      try {
        const st = await api.whatsappMessage(r.sid)
        return { ...r, status: st.status || r.status, error: st.error || null }
      } catch {
        return r
      }
    }))
    setLast((l) => (l ? { ...l, results: checked } : l))
  }, [])

  const send = useCallback(async (kind) => {
    setSending(kind)
    setError(null)
    try {
      const out = await api.sendPhone({ kind, lines: linesFor(kind) })
      const results = Object.entries(out.channels || {}).flatMap(([channel, c]) =>
        c.skipped ? [{ channel, to: CHANNEL_LABEL[channel], ok: false, error: { message: c.skipped } }] : (c.results || []).map((r) => ({ ...r, channel })))
      setLast({ kind, sent: out.sent, results })
      if (results.some((r) => r.channel === 'whatsapp' && r.ok)) setTimeout(() => checkDelivery(results), 5000)
    } catch (e) {
      setError(e.message)
    } finally {
      setSending(null)
    }
  }, [linesFor, checkDelivery])

  const ready = Boolean(status?.configured)
  const preview = useMemo(() => [status?.banner || '⚠ FloodNet TEST ALERT', ...linesFor('forecast')].join('\n'), [status, linesFor])

  return (
    <div className={styles.section} data-tour="panel-phone-alerts">
      <div className="panel-heading">Phone alerts (test)</div>
      {!status && <div className={styles.metaRow}>Phone alert status unavailable (backend not reachable?).</div>}
      <ChannelStatus name="telegram" st={status?.telegram} />
      <ChannelStatus name="whatsapp" st={status?.whatsapp} />
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6 }}>
        <button type="button" className="btn" disabled={!ready || !!sending} onClick={() => send('test')}>
          {sending === 'test' ? 'Sending…' : 'Send test'}
        </button>
        <button type="button" className="btn btn-primary" disabled={!ready || !!sending || !series} onClick={() => send('forecast')}>
          {sending === 'forecast' ? 'Sending…' : 'Send forecast alert'}
        </button>
        <button type="button" className="btn" disabled={!ready || !!sending || !response.result} onClick={() => send('response')}>
          {sending === 'response' ? 'Sending…' : 'Send response update'}
        </button>
        <button type="button" className="btn" onClick={refreshStatus}>Refresh</button>
      </div>
      {error && <div className={styles.metaRow} style={{ color: 'var(--sev-critical)' }}>{error}</div>}
      {last && (
        <div className={styles.metaRow}>
          {last.kind} alert: {last.sent} message(s) accepted.
          <ul style={{ margin: '4px 0 0', paddingLeft: 16 }}>
            {last.results.map((r, i) => (
              <li key={i}>
                {CHANNEL_LABEL[r.channel]} {r.to}: <b style={{ color: DELIVERY_COLOR[r.status] || (r.ok ? '#64748b' : '#dc2626') }}>{r.ok ? (r.status || 'queued') : 'not sent'}</b>
                {r.error ? ` (${r.error.code ?? ''} ${r.error.hint || r.error.message})` : ''}
              </li>
            ))}
          </ul>
        </div>
      )}
      {series && (
        <details style={{ marginTop: 6, fontSize: 11.5 }}>
          <summary>Preview forecast message</summary>
          <pre style={{ whiteSpace: 'pre-wrap', fontSize: 10.5, margin: '4px 0 0' }}>{preview}</pre>
        </details>
      )}
      <div className={styles.metaRow}>TEST only · manual sends · not an official warning</div>
    </div>
  )
}
