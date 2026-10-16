import { useCallback, useEffect, useState } from 'react'
import * as api from '../../api/client.js'
import { editBody, updateSubscriber } from '../../api/messaging.js'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { Section, StateBand } from '../Shell/PageHeader.jsx'
import styles from '../Pages/Pages.module.css'
import own from './SubscriberAlerts.module.css'
import DevTestSend from './DevTestSend.jsx'
import InfoTip from '../ui/InfoTip.jsx'

// Subscriber advisories (DECISIONS D-44, D-45): who has subscribed and consented, an advisory drafted from the
// shared forecast, a person's review and approval, sending (SIMULATED unless live messaging is configured and
// allowed), delivery status per recipient, and the public page each message links to. Phone numbers never reach
// this page: the server returns the last four digits only.

const NEXT = {
  DRAFT: [{ action: 'submit-review', label: 'Submit for review' }],
  REVIEW_REQUIRED: [{ action: 'approve', label: 'Approve', needsName: true }],
  APPROVED: [{ action: 'send', label: 'Send to eligible subscribers', primary: true }],
}

const BASE_URL = String(import.meta.env.BASE_URL || '/').replace(/\/$/, '')
const LANGUAGE_NAMES = { en: 'English', hi: 'Hindi', mr: 'Marathi' }

// Edit a subscriber (2026-10-11): language, area, permitted channels (a newly added channel needs a consent record;
// a removed channel is a recorded withdrawal) and the verified flag. The number cannot be edited.
function EditSubscriber({ sub, localities, onDone, busy, setBusy, setError }) {
  const [form, setForm] = useState(() => ({
    language: sub.language || 'en', localityId: sub.area?.kind === 'locality' ? sub.area.locality_id : '',
    sms: !!sub.channels?.sms, whatsapp: !!sub.channels?.whatsapp, verified: !!sub.verified, method: '',
  }))
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value }))
  const save = async (e) => {
    e.preventDefault()
    setBusy(true); setError(null)
    try {
      const body = editBody(sub, form)
      if (!Object.keys(body).length) { onDone(); return }
      await updateSubscriber(sub.id, body)
      await onDone(true)
    } catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  const adding = (form.sms && !sub.channels?.sms) || (form.whatsapp && !sub.channels?.whatsapp)
  return (
    <form onSubmit={save} className={own.formGrid} aria-label={`Edit subscriber ${sub.id}`}>
      <label className="field-label" htmlFor={`ed-lang-${sub.id}`}>Preferred language</label>
      <select id={`ed-lang-${sub.id}`} value={form.language} onChange={set('language')} className={own.narrowSelect}>
        {Object.entries(LANGUAGE_NAMES).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
      </select>
      {form.language !== 'en' && <p className={styles.note}>Messages are English only until Hindi and Marathi texts have a human-reviewed translation.</p>}
      <label className="field-label" htmlFor={`ed-loc-${sub.id}`}>Area</label>
      <select id={`ed-loc-${sub.id}`} value={form.localityId} onChange={set('localityId')}>
        <option value="">{sub.area?.kind === 'point' ? `Keep: ${sub.area.radius_m} m around a point` : 'Choose a pilot locality…'}</option>
        {(localities || []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
      </select>
      <fieldset className={own.channels}>
        <legend className="field-label">Permitted channels (consent recorded)</legend>
        <label><input type="checkbox" checked={form.sms} onChange={set('sms')} /> SMS</label>
        <label><input type="checkbox" checked={form.whatsapp} onChange={set('whatsapp')} /> WhatsApp</label>
      </fieldset>
      {adding && (
        <>
          <label className="field-label" htmlFor={`ed-method-${sub.id}`}>How consent for the new channel was obtained</label>
          <input id={`ed-method-${sub.id}`} value={form.method} onChange={set('method')} placeholder="e.g. phone call, paper form" />
        </>
      )}
      <p className={styles.note}>Unticking a channel records a withdrawal of consent for it. To stop all messages use Opt out.</p>
      <label><input type="checkbox" checked={form.verified} onChange={set('verified')} /> Number verified</label>
      <div className={own.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy}>Save changes</button>
        <button type="button" className="btn" disabled={busy} onClick={() => onDone()}>Cancel</button>
      </div>
    </form>
  )
}

function Subscribers({ data, reload, busy, setBusy, setError }) {
  const [number, setNumber] = useState('')
  const [locality, setLocality] = useState('')
  const [sms, setSms] = useState(true)
  const [wa, setWa] = useState(false)
  const [editing, setEditing] = useState(null)
  const run = async (fn) => {
    setBusy(true); setError(null)
    try { await fn(); await reload() } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const subs = data?.subscribers || []
  const add = (e) => {
    e.preventDefault()
    const channels = {}
    if (sms) channels.sms = { method: 'recorded by operator' }
    if (wa) channels.whatsapp = { method: 'recorded by operator' }
    run(() => api.addSubscriber({ number: number.trim(), area: { locality_id: locality }, channels, language: 'en', verified: true }))
      .then(() => setNumber(''))
  }
  return (
    <Section title={`Subscribers (${subs.length})`} hint="People who asked for FloodNet advisories for an area, with the consent recorded for each channel. Numbers are stored encrypted and shown as the last four digits only.">
      {!subs.length ? <p className={styles.empty}>No subscribers yet.</p> : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <thead><tr><th scope="col">Number</th><th scope="col">Area</th><th scope="col">Channels</th><th scope="col">Status</th><th scope="col"><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {subs.map((s) => [
                <tr key={s.id}>
                  <td>{s.number_masked || '—'}{s.simulated && <span className={own.sim}> SIMULATED</span>}
                    {s.dev_test && <span className={own.dev}> DEV TEST{s.label ? ` · ${s.label}` : ''}</span>}
                    {s.language && s.language !== 'en' && <span className={styles.small}> · {LANGUAGE_NAMES[s.language] || s.language}</span>}</td>
                  <td>{s.area?.locality_name || (s.area?.lonlat ? `${s.area.radius_m} m around a point` : '—')}</td>
                  <td>{Object.keys(s.channels || {}).join(', ') || 'none'}</td>
                  <td>{s.status}{s.verified ? '' : ' · not verified'}</td>
                  <td className={own.rowActions}>
                    {s.status === 'ACTIVE' && <button type="button" className="btn" disabled={busy} aria-expanded={editing === s.id} onClick={() => setEditing(editing === s.id ? null : s.id)}>Edit</button>}
                    {s.status === 'ACTIVE' && <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.optOutSubscriber(s.id))}>Opt out</button>}
                    {s.status !== 'ERASED' && <button type="button" className="btn" disabled={busy} onClick={() => { if (window.confirm('Erase this subscriber’s number permanently?')) run(() => api.eraseSubscriber(s.id)) }}>Erase</button>}
                  </td>
                </tr>,
                editing === s.id && (
                  <tr key={`${s.id}-edit`}>
                    <td colSpan={5}>
                      <EditSubscriber sub={s} localities={data?.localities} busy={busy} setBusy={setBusy} setError={setError}
                        onDone={async (changed) => { setEditing(null); if (changed) await reload() }} />
                    </td>
                  </tr>
                ),
              ])}
            </tbody>
          </table>
        </div>
      )}
      <details className={own.form}>
        <summary>Add a subscriber</summary>
        <form onSubmit={add} className={own.formGrid}>
          <label className="field-label" htmlFor="sub-number">Mobile number (10 digits, or with +91)</label>
          <input id="sub-number" type="tel" autoComplete="off" inputMode="tel" value={number} onChange={(e) => setNumber(e.target.value)} placeholder="98… or +91 98…" />
          <label className="field-label" htmlFor="sub-locality">Area</label>
          <select id="sub-locality" value={locality} onChange={(e) => setLocality(e.target.value)}>
            <option value="">Choose a pilot locality…</option>
            {(data?.localities || []).map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
          <fieldset className={own.channels}>
            <legend className="field-label">Consent recorded for</legend>
            <label><input type="checkbox" checked={sms} onChange={(e) => setSms(e.target.checked)} /> SMS</label>
            <label><input type="checkbox" checked={wa} onChange={(e) => setWa(e.target.checked)} /> WhatsApp</label>
          </fieldset>
          <p className={styles.note}>Only add a person who asked to subscribe and agreed to notice {data?.notice_version || ''}. {data?.retention_note || ''}</p>
          <button type="submit" className="btn btn-primary" disabled={busy || !number.trim() || !locality || (!sms && !wa)}>Add subscriber</button>
        </form>
      </details>
      <button type="button" className="btn" disabled={busy} onClick={() => run(() => api.seedSimulatedSubscribers())}>Add SIMULATED test subscribers</button>
    </Section>
  )
}

function AlertDetail({ alert, onAction, busy }) {
  const [approver, setApprover] = useState('')
  const v = (alert.versions || []).find((x) => x.version === alert.current_version) || (alert.versions || []).slice(-1)[0]
  const targets = alert.targets?.[alert.current_version] || alert.targets?.[String(alert.current_version)] || []
  const eligible = targets.filter((t) => t.eligible)
  return (
    <div className={own.detail}>
      <div className={own.detailHead}>
        <strong>{alert.title}</strong>
        <span className={own.state}>{alert.state.replace('_', ' ')}</span>
      </div>
      <p className={styles.small}>Version {alert.current_version}{v?.change_summary ? ` · ${v.change_summary}` : ''}</p>
      {alert.link && (
        <p className={styles.small}>Public page: <a href={alert.link} target="_blank" rel="noopener noreferrer">{alert.link}</a>{v?.published_at ? '' : ' (published when sent)'}</p>
      )}
      {v?.texts && (
        <details className={own.texts} open={alert.state !== 'SENT'}>
          <summary>Message text</summary>
          {Object.entries(v.texts).map(([ch, text]) => (
            <div key={ch}><div className={styles.small}>{ch.toUpperCase()}{typeof text === 'string' ? ` · ${text.length} characters` : ''}</div><pre className={own.pre}>{typeof text === 'string' ? text : JSON.stringify(text)}</pre></div>
          ))}
        </details>
      )}
      {targets.length > 0 && (
        <details className={own.texts}>
          <summary>Targeting: {eligible.length} of {targets.length} subscriber(s) eligible</summary>
          <ul className={own.list}>{targets.map((t) => <li key={t.subscriber_id}>{t.subscriber_id}: {t.eligible ? '✓' : '✕'} {t.reason}</li>)}</ul>
        </details>
      )}
      {alert.delivery_summary && Object.keys(alert.delivery_summary).length > 0 && (
        <p className={styles.small}>Deliveries: {Object.entries(alert.delivery_summary).map(([k, n]) => `${k} ${n}`).join(' · ')}
          <InfoTip label="What delivery states mean">“ACCEPTED/SENT” means the provider took the message, not that the phone received it.</InfoTip></p>
      )}
      <div className={own.actions}>
        {(NEXT[alert.state] || []).map((n) => (
          <span key={n.action} className={own.actionGroup}>
            {n.needsName && <input aria-label="Approver name" placeholder="Approver name" value={approver} onChange={(e) => setApprover(e.target.value)} />}
            <button type="button" className={`btn ${n.primary ? 'btn-primary' : ''}`} disabled={busy || (n.needsName && !approver.trim())}
              onClick={() => onAction(alert.alert_id, n.action, n.needsName ? { approver_name: approver.trim(), version: alert.current_version } : {})}>{n.label}</button>
          </span>
        ))}
        {['SENT', 'PARTIAL', 'APPROVED', 'DRAFT', 'REVIEW_REQUIRED'].includes(alert.state) && (
          <button type="button" className="btn" disabled={busy} onClick={() => onAction(alert.alert_id, 'refresh')} title="Re-check the forecast and closures; a new version is made only for a material change">Check for changes</button>
        )}
        {['SENT', 'PARTIAL'].includes(alert.state) && (
          <>
            <button type="button" className="btn" disabled={busy} onClick={() => onAction(alert.alert_id, 'simulate-callbacks')} title="SIMULATED provider only">Simulate delivery reports</button>
            <button type="button" className="btn" disabled={busy} onClick={() => onAction(alert.alert_id, 'resolve')}>Mark no longer active</button>
          </>
        )}
        {!['CANCELLED', 'RESOLVED'].includes(alert.state) && (
          <button type="button" className="btn" disabled={busy} onClick={() => onAction(alert.alert_id, 'cancel', { reason: 'cancelled by dispatcher' })}>Cancel</button>
        )}
      </div>
    </div>
  )
}

export default function SubscriberAlerts() {
  const { ops, run } = useFloodNet()
  const [subs, setSubs] = useState(null)
  const [alerts, setAlerts] = useState([])
  const [selected, setSelected] = useState(null)
  const [status, setStatus] = useState(null)
  const [official, setOfficial] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [windowMin, setWindowMin] = useState(60)

  const reload = useCallback(async () => {
    const [s, a, st, off] = await Promise.all([api.listSubscribers(), api.listAlerts(), api.messagingStatus(), api.officialAlerts().catch(() => null)])
    setSubs(s); setAlerts(a.alerts || []); setStatus(st); setOfficial(off)
    setSelected((cur) => cur)
  }, [])
  useEffect(() => {
    if (!ops.available) return undefined
    const id = setTimeout(() => { reload().catch((e) => setError(e.message)) }, 0)
    return () => clearTimeout(id)
  }, [ops.available, reload])

  const openAlert = useCallback(async (id) => {
    try { setSelected(await api.getAlert(id)) } catch (e) { setError(e.message) }
  }, [])
  const act = async (id, action, body) => {
    setBusy(true); setError(null)
    try {
      const out = await api.alertAction(id, action, body)
      if (action === 'refresh' && out && out.changed === false) setError(`No material change: ${out.reason}`)
      await reload(); await openAlert(id)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }
  const draft = async () => {
    setBusy(true); setError(null)
    try {
      const a = await api.draftAlert({ windowMin, publicBase: window.location.origin + BASE_URL })
      await reload(); await openAlert(a.alert_id)
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  if (!ops.available) {
    return (
      <Section title="Subscriber advisories">
        <p className={styles.empty}>{ops.reason || 'Live operations are not connected.'} Enter the token on the Trips page.</p>
      </Section>
    )
  }
  return (
    <>
      {error && <StateBand kind="error">{error}</StateBand>}
      {status && (
        <p className={own.mode} role="note">
          Sending mode:{' '}
          <span className={status.live_messaging_flag ? own.live : own.sim}>{status.live_messaging_flag ? 'LIVE · allow-listed test numbers only' : 'SIMULATED'}</span>
          <InfoTip label="Sending mode details">
            {status.sms_note || 'Real SMS is BLOCKED until a sender is configured.'} {status.whatsapp_note || ''}
          </InfoTip>
        </p>
      )}
      <Section title="Prepare an advisory" hint="Drafted from the forecast and closures shared with the live store. A person must review and approve it before anything is sent.">
        <div className={own.actions}>
          <label className="field-label" htmlFor="adv-window">Look ahead</label>
          <select id="adv-window" value={windowMin} onChange={(e) => setWindowMin(Number(e.target.value))} className={own.narrowSelect}>
            {[30, 60, 120, 180].map((m) => <option key={m} value={m}>{m} min</option>)}
          </select>
          <button type="button" className="btn btn-primary" disabled={busy || !run} onClick={draft}>Prepare advisory</button>
        </div>
        {!run && <p className={styles.note}>Needs a forecast.</p>}
        {alerts.length > 0 && (() => {
          // the three most recent advisories stay in view; earlier ones fold away
          const item = (a) => (
            <li key={a.alert_id}>
              <button type="button" className={`${own.alertBtn} ${selected?.alert_id === a.alert_id ? own.alertBtnOn : ''}`} onClick={() => openAlert(a.alert_id)}>
                <span>{a.alert_id} · {a.title}</span><span className={own.state}>{a.state.replace('_', ' ')}</span>
              </button>
            </li>
          )
          const older = alerts.slice(0, -3)
          return (
            <>
              <ul className={own.alertList}>{alerts.slice(-3).map(item)}</ul>
              {older.length > 0 && (
                <details className={own.texts}>
                  <summary>Earlier advisories ({older.length})</summary>
                  <ul className={own.alertList}>{older.map(item)}</ul>
                </details>
              )}
            </>
          )
        })()}
        {selected && <AlertDetail alert={selected} onAction={act} busy={busy} />}
      </Section>
      <Subscribers data={subs} reload={reload} busy={busy} setBusy={setBusy} setError={setError} />
      <DevTestSend data={subs} status={status} reload={reload} busy={busy} setBusy={setBusy} setError={setError} />
      <Section title="Official warnings (SACHET, NDMA)" hint="Shown as received from the official feed; FloodNet never edits or re-issues them.">
        {official?.items?.length ? (
          <ul className={own.list}>{official.items.slice(0, 8).map((it) => <li key={it.guid || it.link}><a href={it.link} target="_blank" rel="noopener noreferrer">{it.title}</a> · {it.author} · {it.pubDate}</li>)}</ul>
        ) : (
          <p className={styles.empty}>No official items cached. See <a href="https://sachet.ndma.gov.in/" target="_blank" rel="noopener noreferrer">sachet.ndma.gov.in</a>.
            <InfoTip label="About the official feed">The feed is read only when the server is configured with its address (SACHET_RSS_URL).</InfoTip></p>
        )}
      </Section>
    </>
  )
}
