import { useCallback, useEffect, useId, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import * as api from '../../api/registry.js'
import {
  CANDIDATE_LABEL, CHECK_LABEL, PERMISSION_METHOD_LABEL, STATUS_LABEL, ageText, candidateSteps, commitments, depthText,
  detectFormat, freshnessChip, fromLocalInput, nextCandidateAction, occupancy, parseCsv, responderUrl, sourceChip,
  toLocalInput, tokenFromHash, validateCandidate, validateRecord,
} from '../../lib/registry.js'
import PageHeader, { Section, StateBand } from '../Shell/PageHeader.jsx'
import InfoTip from '../ui/InfoTip.jsx'
import shell from '../Shell/Shell.module.css'
import styles from './Pages.module.css'
import css from './Resources.module.css'

// Resources (2026-10-11): the registry of teams, vehicles and shelters the planner can use, how fresh each record is
// and where it came from; registration and import by the dispatcher; scoped links for responders, shelter staff and
// volunteers; and the queue of volunteer-submitted candidate shelters, which become shelters only when a dispatcher
// confirms them. Seed records are SIMULATED demonstration values and say so.

const RESPONDER_KEY = 'floodnet.responderToken'
const TONE = { ok: styles.toneOk, warn: styles.toneWarn, muted: styles.toneMuted, bad: styles.toneBad, sim: styles.toneSim }
const STATUS_TONE = { AVAILABLE: 'ok', ASSIGNED: 'warn', OUT_OF_SERVICE: 'bad', open: 'ok', closed: 'bad' }
const ROLE_LABEL = { field_team: 'Field team', shelter_staff: 'Shelter staff', volunteer: 'Volunteer' }
const ROLE_KINDS = { field_team: ['team', 'vehicle'], shelter_staff: ['shelter'], volunteer: [] }
const KIND_LABEL = { team: 'Teams', vehicle: 'Vehicles', shelter: 'Shelters' }

function Chip({ tone = 'muted', glyph, title, children }) {
  return (
    <span className={`${styles.chip} ${TONE[tone] || TONE.muted}`} title={title}>
      {glyph && <span aria-hidden="true">{glyph}</span>}{children}
    </span>
  )
}

/** Label + control + hint/error, with the ids the control needs for aria-describedby. */
function Field({ id, label, error, hint, full = false, children }) {
  return (
    <div className={`${css.field} ${full ? css.full : ''}`}>
      <label className="field-label" htmlFor={id}>{label}</label>
      {children}
      {hint && !error && <p id={`${id}-hint`} className={css.hint}>{hint}</p>}
      {error && <p id={`${id}-err`} className={css.error}>{error}</p>}
    </div>
  )
}
const aria = (id, error, hint) => ({
  id, 'aria-invalid': error ? true : undefined,
  'aria-describedby': error ? `${id}-err` : hint ? `${id}-hint` : undefined,
})

function useFormState(initial) {
  const [f, setF] = useState(initial)
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  return [f, set, setF]
}

function errText(e) { return e?.message || String(e) }

// ---------------------------------------------------------------- resource cards
function ResourceMeta({ r }) {
  if (r.kind === 'team') {
    return <p className={css.meta}>{(r.capabilities || []).join(' · ') || 'no capability recorded'} · {r.members ?? '?'} members · {r.base || 'base not recorded'}{r.organisation ? ` · ${r.organisation}` : ''}</p>
  }
  if (r.kind === 'vehicle') {
    return (
      <p className={css.meta}>
        {r.type} · {r.passenger_capacity ?? '?'} seats
        {r.depth_limit_cm != null && <> · water limit {r.depth_limit_cm} cm <abbr title={r.depth_limit_source}>(assumed)</abbr></>}
        {' · '}{r.base || 'base not recorded'}
      </p>
    )
  }
  return r.address ? <p className={css.meta}>{r.address}</p> : null
}

function Occupancy({ r }) {
  const o = occupancy(r)
  const fill = o.level === 'full' ? css.fillFull : o.level === 'warn' ? css.fillWarn : ''
  return (
    <>
      <div className={css.bar} aria-hidden="true"><div className={`${css.fill} ${fill}`} style={{ width: `${o.pct}%` }} /></div>
      <p className={css.occText}>{o.text}{o.level === 'full' ? ' · FULL' : ''}</p>
    </>
  )
}

function UpdateForm({ r, busy, onSubmit, idPrefix }) {
  const shelter = r.kind === 'shelter'
  const [f, set] = useFormState({
    status: r.status || (shelter ? 'open' : 'AVAILABLE'), reason: r.out_of_service_reason || '',
    occupancy: String(r.occupancy ?? ''), total: String(r.total_capacity ?? ''), observed: toLocalInput(),
  })
  const [error, setError] = useState('')
  const id = (k) => `${idPrefix}-${r.id}-${k}`
  const submit = (e) => {
    e.preventDefault()
    const fields = shelter
      ? { status: f.status, occupancy: f.occupancy, total_capacity: f.total }
      : { status: f.status, ...(f.status === 'OUT_OF_SERVICE' ? { out_of_service_reason: f.reason } : {}) }
    if (!shelter && f.status === 'OUT_OF_SERVICE' && !f.reason) { setError('Out of service needs a reason.'); return }
    if (shelter && Number(f.occupancy) > Number(f.total)) { setError('Occupancy cannot be more than the capacity.'); return }
    setError('')
    onSubmit(r.id, fields, fromLocalInput(f.observed))
  }
  return (
    <form className={css.form} onSubmit={submit} aria-label={`Update ${r.name}`}>
      <Field id={id('status')} label="Status">
        <select id={id('status')} value={f.status} onChange={set('status')}>
          {(shelter ? ['open', 'closed'] : ['AVAILABLE', 'ASSIGNED', 'OUT_OF_SERVICE']).map((s) => <option key={s} value={s}>{STATUS_LABEL[s]}</option>)}
        </select>
      </Field>
      {!shelter && f.status === 'OUT_OF_SERVICE' && (
        <Field id={id('reason')} label="Reason">
          <select id={id('reason')} value={f.reason} onChange={set('reason')}>
            <option value="">Choose…</option>
            <option value="MECHANICAL">Mechanical</option><option value="REST">Rest</option><option value="PERSONNEL">Personnel</option>
          </select>
        </Field>
      )}
      {shelter && (
        <>
          <Field id={id('occ')} label="People in the shelter">
            <input id={id('occ')} type="number" min="0" inputMode="numeric" value={f.occupancy} onChange={set('occupancy')} />
          </Field>
          <Field id={id('cap')} label="Capacity">
            <input id={id('cap')} type="number" min="1" inputMode="numeric" value={f.total} onChange={set('total')} />
          </Field>
        </>
      )}
      <Field id={id('obs')} label="True as of" hint="When this was seen, not when it is typed.">
        <input className="input" type="datetime-local" {...aria(id('obs'), null, true)} value={f.observed} onChange={set('observed')} />
      </Field>
      <div className={css.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy}>Send update</button>
        {error && <span className={css.error} role="alert">{error}</span>}
      </div>
    </form>
  )
}

function ResourceCard({ r, commits, busy, onUpdate, idPrefix = 'res' }) {
  const fresh = freshnessChip(r)
  const src = sourceChip(r)
  return (
    <li className={css.card}>
      <div className={css.cardHead}>
        <h3 className={css.title}><span className={css.rid}>{r.id}</span>{r.name}</h3>
        <Chip tone={STATUS_TONE[r.status] || 'muted'}>
          {STATUS_LABEL[r.status] || r.status}{r.status === 'OUT_OF_SERVICE' && r.out_of_service_reason ? ` · ${r.out_of_service_reason.toLowerCase()}` : ''}
        </Chip>
      </div>
      <ResourceMeta r={r} />
      {r.kind === 'shelter' && <Occupancy r={r} />}
      <div className={css.chips}>
        <Chip tone={src.tone} title={src.title}>{src.text}</Chip>
        <Chip tone={fresh.tone} glyph={fresh.glyph} title={fresh.title}>{fresh.text}</Chip>
        <span className={css.when}>
          {r.observed_at ? `updated ${ageText(r.age_min)}` : 'never confirmed'}
          {r.received_at && r.observed_at !== r.received_at ? ` · received ${new Date(r.received_at).toLocaleTimeString()}` : ''}
        </span>
      </div>
      {commits?.length > 0 && (
        <p className={css.commit}>Committed: {commits.map((c) => `${c.kind === 'trip' ? 'trip' : 'assignment'} ${c.id} (${c.status.toLowerCase().replaceAll('_', ' ')})`).join(' · ')}</p>
      )}
      {onUpdate && (
        <details className={css.disclosure}>
          <summary>Update status</summary>
          <UpdateForm r={r} busy={busy} onSubmit={onUpdate} idPrefix={idPrefix} />
        </details>
      )}
    </li>
  )
}

// ---------------------------------------------------------------- register one record
const EMPTY_RECORD = { name: '', lon: '', lat: '', base: '', organisation: '', capabilities: [], members: '', type: '', passenger_capacity: '',
  total_capacity: '', occupancy: '0', status: '', out_of_service_reason: '', address: '' }

function AddRecordForm({ vocab, busy, onDone }) {
  const uid = useId()
  const [kind, setKind] = useState('team')
  const [f, set, setF] = useFormState({ ...EMPTY_RECORD, observed: toLocalInput() })
  const [errors, setErrors] = useState({})
  const [msg, setMsg] = useState(null)
  const id = (k) => `${uid}-${k}`
  const submit = async (e) => {
    e.preventDefault()
    const v = validateRecord(kind, f, vocab)
    setErrors(v.errors)
    if (Object.keys(v.errors).length) { setMsg({ kind: 'error', text: 'Check the highlighted fields.' }); return }
    try {
      const out = await api.registerRecord({ kind, record: v.record, observedAt: fromLocalInput(f.observed), clientRef: `form-${Date.now()}` })
      setMsg({ kind: 'ok', text: `Registered ${out.resource.id} ${out.resource.name} (source: dispatcher entry).` })
      setF({ ...EMPTY_RECORD, observed: toLocalInput() })
      onDone()
    } catch (err) { setMsg({ kind: 'error', text: errText(err) }) }
  }
  const caps = vocab?.capabilities || []
  return (
    <form className={css.form} onSubmit={submit} noValidate aria-label="Register a record">
      <fieldset className={`${css.full} ${css.checks}`}>
        <legend className="field-label">Kind</legend>
        <div className={css.seg}>
          {['team', 'vehicle', 'shelter'].map((k) => (
            <label key={k}><input type="radio" name={`${uid}-kind`} value={k} checked={kind === k} onChange={() => { setKind(k); setErrors({}) }} /><span>{k[0].toUpperCase() + k.slice(1)}</span></label>
          ))}
        </div>
      </fieldset>
      <Field id={id('name')} label="Name" error={errors.name} full>
        <input type="text" {...aria(id('name'), errors.name)} value={f.name} onChange={set('name')} maxLength={120} autoComplete="off" />
      </Field>
      {kind === 'team' && (
        <fieldset className={`${css.full} ${css.checks}`} aria-describedby={errors.capabilities ? id('caps-err') : undefined}>
          <legend className="field-label">Capabilities</legend>
          {caps.map((c) => (
            <label key={c}>
              <input type="checkbox" checked={f.capabilities.includes(c)}
                onChange={(e) => setF((x) => ({ ...x, capabilities: e.target.checked ? [...x.capabilities, c] : x.capabilities.filter((y) => y !== c) }))} />
              {c}
            </label>
          ))}
          {errors.capabilities && <p id={id('caps-err')} className={`${css.error} ${css.full}`}>{errors.capabilities}</p>}
          <p className={`${css.hint} ${css.full}`}>Planner capability words only.</p>
        </fieldset>
      )}
      {kind === 'team' && (
        <Field id={id('members')} label="Members" error={errors.members}>
          <input type="number" min="1" inputMode="numeric" {...aria(id('members'), errors.members)} value={f.members} onChange={set('members')} />
        </Field>
      )}
      {kind === 'vehicle' && (
        <>
          <Field id={id('type')} label="Vehicle type" error={errors.type} hint="Each type has an assumed water limit used by the router.">
            <select {...aria(id('type'), errors.type, true)} value={f.type} onChange={set('type')}>
              <option value="">Choose…</option>
              {(vocab?.vehicle_types || []).map((t) => <option key={t.type} value={t.type}>{t.type} (limit {t.depth_limit_cm} cm)</option>)}
            </select>
          </Field>
          <Field id={id('seats')} label="Passenger seats" error={errors.passenger_capacity}>
            <input type="number" min="1" inputMode="numeric" {...aria(id('seats'), errors.passenger_capacity)} value={f.passenger_capacity} onChange={set('passenger_capacity')} />
          </Field>
        </>
      )}
      {(kind === 'team' || kind === 'vehicle') && (
        <>
          <Field id={id('base')} label="Base" error={errors.base}>
            <input type="text" {...aria(id('base'), errors.base)} value={f.base} onChange={set('base')} maxLength={120} />
          </Field>
          <Field id={id('org')} label="Organisation (optional)" error={errors.organisation}>
            <input type="text" {...aria(id('org'), errors.organisation)} value={f.organisation} onChange={set('organisation')} maxLength={80} />
          </Field>
        </>
      )}
      {kind === 'shelter' && (
        <>
          <Field id={id('cap')} label="Capacity (people)" error={errors.total_capacity}>
            <input type="number" min="1" inputMode="numeric" {...aria(id('cap'), errors.total_capacity)} value={f.total_capacity} onChange={set('total_capacity')} />
          </Field>
          <Field id={id('occ')} label="People there now" error={errors.occupancy}>
            <input type="number" min="0" inputMode="numeric" {...aria(id('occ'), errors.occupancy)} value={f.occupancy} onChange={set('occupancy')} />
          </Field>
          <Field id={id('addr')} label="Address or landmark (optional)" error={errors.address} full>
            <input type="text" {...aria(id('addr'), errors.address)} value={f.address} onChange={set('address')} maxLength={200} />
          </Field>
        </>
      )}
      <Field id={id('lon')} label="Longitude" error={errors.location}>
        <input type="text" inputMode="decimal" {...aria(id('lon'), errors.location)} value={f.lon} onChange={set('lon')} placeholder="72.846" />
      </Field>
      <Field id={id('lat')} label="Latitude" hint={vocab ? `Pilot area: lon ${vocab.pilot_bbox[0]}–${vocab.pilot_bbox[2]}, lat ${vocab.pilot_bbox[1]}–${vocab.pilot_bbox[3]}` : undefined}>
        <input type="text" inputMode="decimal" {...aria(id('lat'), null, Boolean(vocab))} value={f.lat} onChange={set('lat')} placeholder="19.014" />
      </Field>
      <Field id={id('obs')} label="True as of" full>
        <input className="input" type="datetime-local" id={id('obs')} value={f.observed} onChange={set('observed')} />
      </Field>
      <div className={css.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy || !vocab}>Register {kind}</button>
        {msg && <span className={msg.kind === 'error' ? css.error : css.hint} role={msg.kind === 'error' ? 'alert' : 'status'}>{msg.text}</span>}
      </div>
    </form>
  )
}

// ---------------------------------------------------------------- import
function ImportForm({ vocab, onDone }) {
  const uid = useId()
  const [label, setLabel] = useState('')
  const [text, setText] = useState('')
  const [fileName, setFileName] = useState('')
  const [preview, setPreview] = useState(null)
  const [previewed, setPreviewed] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const format = detectFormat(fileName, text)
  const quick = useMemo(() => (format === 'csv' && text.trim() ? parseCsv(text) : null), [format, text])
  const onFile = (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setFileName(file.name)
    setLabel((l) => l || file.name)
    file.text().then((t) => { setText(t); setPreview(null) }).catch((err) => setMsg({ kind: 'error', text: errText(err) }))
  }
  const run = async (commit) => {
    setBusy(true); setMsg(null)
    try {
      const out = await api.importRecords({ label: label.trim() || fileName || 'pasted list', format, text, commit })
      setPreview(out); setPreviewed(text)
      if (commit) {
        setMsg({ kind: 'ok', text: `Imported ${out.ids.length} record(s): ${out.ids.join(', ')}.` })
        setText(''); setPreview(null); setPreviewed(''); setFileName('')
        onDone()
      }
    } catch (err) { setMsg({ kind: 'error', text: errText(err) }) } finally { setBusy(false) }
  }
  const canCommit = preview && !preview.committed && preview.invalid === 0 && previewed === text
  return (
    <div>
      <div className={css.form}>
        <Field id={`${uid}-label`} label="Label (shown as the source)">
          <input id={`${uid}-label`} type="text" value={label} onChange={(e) => setLabel(e.target.value)} maxLength={80} placeholder="ward resource list, 11 Oct" />
        </Field>
        <Field id={`${uid}-file`} label="File (.csv or .json)">
          <input id={`${uid}-file`} type="file" accept=".csv,.json,text/csv,application/json" onChange={onFile} className="input" />
        </Field>
        <Field id={`${uid}-text`} label={`Or paste ${format.toUpperCase()}`} full
          hint={`Columns: ${(vocab?.import_columns || []).join(', ')}. Capabilities separated by ";". Rows without observed_at are kept as UNKNOWN freshness.`}>
          <textarea id={`${uid}-text`} aria-describedby={`${uid}-text-hint`} className={`input ${css.textarea}`} value={text}
            onChange={(e) => { setText(e.target.value); setPreview(null) }} spellCheck={false}
            placeholder={'kind,name,lon,lat,total_capacity,observed_at\nshelter,Community hall,72.846,19.014,60,2026-10-11T09:30:00Z'} />
        </Field>
        {quick && <p className={`${css.hint} ${css.full}`}>{quick.rows.length} data row(s) read; columns: {quick.header.join(', ') || 'none'}{quick.header.length && !quick.header.includes('kind') ? ' — a "kind" column is required' : ''}.</p>}
        <div className={css.actions}>
          <button type="button" className="btn" disabled={busy || !text.trim()} onClick={() => run(false)}>Preview (nothing is saved)</button>
          <button type="button" className="btn btn-primary" disabled={busy || !canCommit} onClick={() => run(true)}>
            Import {preview?.valid ?? 0} record(s)
          </button>
          {msg && <span className={msg.kind === 'error' ? css.error : css.hint} role={msg.kind === 'error' ? 'alert' : 'status'}>{msg.text}</span>}
        </div>
      </div>
      {preview && (
        <div className={styles.tableWrap} style={{ marginTop: 8 }}>
          <table className={styles.table}>
            <caption className="sr-only">Import preview: {preview.valid} valid, {preview.invalid} with errors</caption>
            <thead><tr><th scope="col">Row</th><th scope="col">Record</th><th scope="col">Check</th></tr></thead>
            <tbody>
              {preview.rows.map((r) => (
                <tr key={r.row}>
                  <td className={styles.num}>{r.row}</td>
                  <td>{r.kind || '?'} · {r.name || 'no name'}{r.record ? <div className={styles.sub}>freshness {r.freshness}</div> : null}</td>
                  <td>{r.errors.length ? <ul className={css.error}>{r.errors.map((e) => <li key={e}>{e}</li>)}</ul> : <Chip tone="ok" glyph="✓">OK</Chip>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {preview && preview.invalid > 0 && <p className={styles.note}>Fix the rows with errors and preview again: an import is all or nothing.</p>}
    </div>
  )
}

// ---------------------------------------------------------------- responders and links
function RespondersSection({ resources, responders, busy, onDone }) {
  const uid = useId()
  const [f, set, setF] = useFormState({ name: '', role: 'shelter_staff', scope: [] })
  const [issued, setIssued] = useState(null)
  const [msg, setMsg] = useState(null)
  const [confirmRevoke, setConfirmRevoke] = useState(null)
  const eligible = resources.filter((r) => ROLE_KINDS[f.role].includes(r.kind))
  const submit = async (e) => {
    e.preventDefault()
    if (!f.name.trim()) { setMsg({ kind: 'error', text: 'Name is required.' }); return }
    if (f.role !== 'volunteer' && !f.scope.length) { setMsg({ kind: 'error', text: 'Choose at least one resource this person may update.' }); return }
    try {
      const out = await api.registerResponder({ name: f.name.trim(), role: f.role, scope: f.role === 'volunteer' ? [] : f.scope })
      setIssued({ ...out.responder, url: responderUrl(window.location.origin, import.meta.env.BASE_URL, out.token) })
      setF({ name: '', role: f.role, scope: [] }); setMsg(null)
      onDone()
    } catch (err) { setMsg({ kind: 'error', text: errText(err) }) }
  }
  const revoke = async (id) => {
    try { await api.revokeResponder(id); setConfirmRevoke(null); onDone() } catch (err) { setMsg({ kind: 'error', text: errText(err) }) }
  }
  return (
    <>
      <form className={css.form} onSubmit={submit} aria-label="Issue a responder link">
        <Field id={`${uid}-name`} label="Name or call sign">
          <input id={`${uid}-name`} type="text" value={f.name} onChange={set('name')} maxLength={80} placeholder="SH1 desk, evening shift" autoComplete="off" />
        </Field>
        <Field id={`${uid}-role`} label="Role">
          <select id={`${uid}-role`} value={f.role} onChange={(e) => setF((x) => ({ ...x, role: e.target.value, scope: [] }))}>
            {Object.entries(ROLE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </Field>
        {f.role === 'volunteer' ? (
          <p className={`${css.hint} ${css.full}`}>Volunteer links can only suggest shelters.</p>
        ) : (
          <fieldset className={`${css.full} ${css.checks}`}>
            <legend className="field-label">May update</legend>
            {eligible.map((r) => (
              <label key={r.id}>
                <input type="checkbox" checked={f.scope.includes(r.id)}
                  onChange={(e) => setF((x) => ({ ...x, scope: e.target.checked ? [...x.scope, r.id] : x.scope.filter((y) => y !== r.id) }))} />
                {r.id} {r.name}
              </label>
            ))}
          </fieldset>
        )}
        <div className={css.actions}>
          <button type="submit" className="btn btn-primary" disabled={busy}>Issue link</button>
          {msg && <span className={css.error} role="alert">{msg.text}</span>}
        </div>
      </form>
      {issued && (
        <div className={css.linkBox} role="status">
          <strong>Link for {issued.name} ({ROLE_LABEL[issued.role]}, {issued.responder_id}). Shown once.</strong>
          <span className={css.linkText}>{issued.url}</span>
          <div className={css.actions}>
            <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(issued.url)}>Copy link</button>
            <a className="btn" href={issued.url} target="_blank" rel="noopener noreferrer">Open as {ROLE_LABEL[issued.role].toLowerCase()}</a>
            <button type="button" className="btn" onClick={() => setIssued(null)}>Done</button>
          </div>
          <p className={css.hint}>Send it privately — it works like a key. Revoke it when the shift ends.</p>
        </div>
      )}
      {responders.length > 0 && (
        <ul className={css.list} style={{ marginTop: 10 }}>
          {responders.map((p) => (
            <li key={p.responder_id} className={css.card}>
              <div className={css.cardHead}>
                <span className={css.title}><span className={css.rid}>{p.responder_id}</span>{p.name}</span>
                <Chip tone={p.active ? 'ok' : 'muted'}>{p.active ? 'ACTIVE' : 'REVOKED'}</Chip>
              </div>
              <p className={css.meta}>{ROLE_LABEL[p.role] || p.role}{p.scope?.length ? ` · may update ${p.scope.join(', ')}` : ''}</p>
              {p.active && (
                <div className={css.chips}>
                  {confirmRevoke === p.responder_id ? (
                    <>
                      <button type="button" className="btn btn-danger" onClick={() => revoke(p.responder_id)}>Confirm: revoke link</button>
                      <button type="button" className="btn" onClick={() => setConfirmRevoke(null)}>Keep</button>
                    </>
                  ) : <button type="button" className="btn" onClick={() => setConfirmRevoke(p.responder_id)}>Revoke link</button>}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </>
  )
}

// ---------------------------------------------------------------- candidate shelters
function CandidateForm({ bbox, busy, onSubmit, onBehalf = false, submitLabel = 'Submit place' }) {
  const uid = useId()
  const [f, set, setF] = useFormState({ on_behalf_of: '', name: '', lon: '', lat: '', address: '', description: '', capacity_estimate: '' })
  const [errors, setErrors] = useState({})
  const [msg, setMsg] = useState(null)
  const id = (k) => `${uid}-${k}`
  const submit = async (e) => {
    e.preventDefault()
    const v = validateCandidate(f, bbox)
    const errs = { ...v.errors }
    if (onBehalf && !f.on_behalf_of.trim()) errs.on_behalf_of = 'Name the volunteer this place comes from.'
    setErrors(errs)
    if (Object.keys(errs).length) return
    try {
      const out = await onSubmit(onBehalf ? { ...v.body, on_behalf_of: f.on_behalf_of.trim() } : v.body)
      setMsg({ kind: 'ok', text: `Received as ${out.candidate.candidate_id}. It is a candidate only until a dispatcher checks and confirms it.` })
      setF({ on_behalf_of: '', name: '', lon: '', lat: '', address: '', description: '', capacity_estimate: '' })
    } catch (err) { setMsg({ kind: 'error', text: errText(err) }) }
  }
  return (
    <form className={css.form} onSubmit={submit} noValidate aria-label="Submit a candidate shelter">
      {onBehalf && (
        <Field id={id('who')} label="On behalf of (volunteer)" error={errors.on_behalf_of} full hint="A name or role. No phone numbers.">
          <input type="text" {...aria(id('who'), errors.on_behalf_of, true)} value={f.on_behalf_of} onChange={set('on_behalf_of')} maxLength={80} />
        </Field>
      )}
      <Field id={id('name')} label="Name of the place" error={errors.name} full>
        <input type="text" {...aria(id('name'), errors.name)} value={f.name} onChange={set('name')} maxLength={120} />
      </Field>
      <Field id={id('lon')} label="Longitude" error={errors.location}>
        <input type="text" inputMode="decimal" {...aria(id('lon'), errors.location)} value={f.lon} onChange={set('lon')} placeholder="72.846" />
      </Field>
      <Field id={id('lat')} label="Latitude">
        <input type="text" inputMode="decimal" id={id('lat')} value={f.lat} onChange={set('lat')} placeholder="19.014" />
      </Field>
      <Field id={id('addr')} label="Address or landmark" error={errors.address} full>
        <input type="text" {...aria(id('addr'), errors.address)} value={f.address} onChange={set('address')} maxLength={200} />
      </Field>
      <Field id={id('desc')} label="What is it, and who agreed?" error={errors.description} full hint="For example: school hall, ground floor, the principal agreed. No phone numbers.">
        <textarea className={`input ${css.textarea}`} style={{ minHeight: 64, fontFamily: 'inherit' }} {...aria(id('desc'), errors.description, true)}
          value={f.description} onChange={set('description')} maxLength={500} />
      </Field>
      <Field id={id('cap')} label="Rough capacity (optional)" error={errors.capacity_estimate}>
        <input type="number" min="1" inputMode="numeric" {...aria(id('cap'), errors.capacity_estimate)} value={f.capacity_estimate} onChange={set('capacity_estimate')} />
      </Field>
      <div className={css.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy}>{submitLabel}</button>
        {msg && <span className={msg.kind === 'error' ? css.error : css.hint} role={msg.kind === 'error' ? 'alert' : 'status'}>{msg.text}</span>}
      </div>
    </form>
  )
}

function Stepper({ c }) {
  const steps = candidateSteps(c)
  const cls = { done: css.stepDone, current: css.stepCurrent, rejected: css.stepRejected, todo: '' }
  return (
    <ol className={css.stepper} aria-label={`Progress of ${c.candidate_id}`}>
      {steps.map((s) => (
        <li key={s.state} className={`${css.step} ${cls[s.status]}`} aria-current={s.status === 'current' ? 'step' : undefined}>
          {s.status === 'done' ? '✓ ' : s.status === 'rejected' ? '✕ ' : ''}{s.label}
        </li>
      ))}
    </ol>
  )
}

function PermissionForm({ c, busy, run }) {
  const uid = useId()
  const [f, set] = useFormState({ granted_by_role: '', granted_by_name: '', method: 'IN_PERSON', granted_at: toLocalInput(), note: '' })
  return (
    <form className={css.form} aria-label={`Record permission for ${c.name}`}
      onSubmit={(e) => { e.preventDefault(); run(() => api.candidatePermission(c.candidate_id, { ...f, granted_at: fromLocalInput(f.granted_at) }), 'Permission recorded.') }}>
      <Field id={`${uid}-role`} label="Granted by (role)" hint="Owner, manager, trustee, principal…">
        <input id={`${uid}-role`} aria-describedby={`${uid}-role-hint`} type="text" value={f.granted_by_role} onChange={set('granted_by_role')} maxLength={80} required />
      </Field>
      <Field id={`${uid}-name`} label="Name (optional)">
        <input id={`${uid}-name`} type="text" value={f.granted_by_name} onChange={set('granted_by_name')} maxLength={80} />
      </Field>
      <Field id={`${uid}-how`} label="How">
        <select id={`${uid}-how`} value={f.method} onChange={set('method')}>
          {Object.entries(PERMISSION_METHOD_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
        </select>
      </Field>
      <Field id={`${uid}-when`} label="When">
        <input id={`${uid}-when`} className="input" type="datetime-local" value={f.granted_at} onChange={set('granted_at')} required />
      </Field>
      <Field id={`${uid}-note`} label="Note (optional)" full>
        <input id={`${uid}-note`} type="text" value={f.note} onChange={set('note')} maxLength={300} />
      </Field>
      <div className={css.actions}><button type="submit" className="btn btn-primary" disabled={busy || !f.granted_by_role.trim()}>Record permission</button></div>
    </form>
  )
}

function SuitabilityForm({ c, busy, run }) {
  const uid = useId()
  const [site, setSite] = useState(null)
  const [siteErr, setSiteErr] = useState('')
  const [f, set] = useFormState({ access: '', water_sanitation: '', accessibility: '', capacity_estimate: String(c.capacity_estimate ?? ''), notes: '' })
  const [checking, setChecking] = useState(false)
  const check = async () => {
    setSiteErr(''); setChecking(true)
    try { setSite(await api.candidateSiteCheck(c.candidate_id)) } catch (err) { setSiteErr(errText(err)) } finally { setChecking(false) }
  }
  const ready = ['access', 'water_sanitation', 'accessibility'].every((k) => f[k]) && Number(f.capacity_estimate) >= 1
  return (
    <form className={css.form} aria-label={`Suitability checklist for ${c.name}`}
      onSubmit={(e) => { e.preventDefault(); run(() => api.candidateSuitability(c.candidate_id, f), 'Checklist recorded with the modelled depth.') }}>
      <div className={css.full}>
        <button type="button" className="btn" onClick={check} disabled={checking}>{checking ? 'Checking…' : 'Check modelled flood depth here'}</button>
        <p className={css.depth} aria-live="polite" aria-busy={checking}>
          <strong>{checking ? 'Checking the published forecast…' : depthText(site)}</strong>
          {site?.forecast_label && <><br />Forecast v{site.forecast_version}: {site.forecast_label}</>}
          {site?.note && <><br />{site.note}</>}
        </p>
        {siteErr && <p className={css.error} role="alert">{siteErr}</p>}
      </div>
      {Object.entries(CHECK_LABEL).map(([k, label]) => (
        <fieldset key={k} className={`${css.full} ${css.checks}`}>
          <legend className="field-label">{label}</legend>
          {['YES', 'NO', 'UNKNOWN'].map((v) => (
            <label key={v}><input type="radio" name={`${uid}-${k}`} value={v} checked={f[k] === v} onChange={set(k)} />{v === 'UNKNOWN' ? 'Not known' : v === 'YES' ? 'Yes' : 'No'}</label>
          ))}
        </fieldset>
      ))}
      <Field id={`${uid}-cap`} label="Capacity estimate (people)">
        <input id={`${uid}-cap`} type="number" min="1" inputMode="numeric" value={f.capacity_estimate} onChange={set('capacity_estimate')} />
      </Field>
      <Field id={`${uid}-notes`} label="Notes (optional)">
        <input id={`${uid}-notes`} type="text" value={f.notes} onChange={set('notes')} maxLength={300} />
      </Field>
      <div className={css.actions}><button type="submit" className="btn btn-primary" disabled={busy || !ready}>Record checklist</button></div>
    </form>
  )
}

function ConfirmForm({ c, busy, run }) {
  const uid = useId()
  const s = c.suitability || {}
  const [f, set] = useFormState({ total_capacity: String(s.capacity_estimate ?? ''), occupancy: '0' })
  const warnings = Object.keys(CHECK_LABEL).filter((k) => s[k] !== 'YES').map((k) => `${CHECK_LABEL[k]}: ${s[k] === 'NO' ? 'no' : 'not known'}`)
  return (
    <form className={css.form} aria-label={`Confirm ${c.name} as a shelter`}
      onSubmit={(e) => { e.preventDefault(); run(() => api.candidateConfirm(c.candidate_id, { total_capacity: Number(f.total_capacity), occupancy: Number(f.occupancy) }), 'Confirmed: now a shelter the planner can use.') }}>
      <p className={`${css.depth} ${css.full}`}>
        <strong>{depthText(s.modelled_flood_depth)}</strong>
        {warnings.length > 0 && <><br />Check before confirming: {warnings.join('; ')}.</>}
      </p>
      <Field id={`${uid}-cap`} label="Capacity (people)">
        <input id={`${uid}-cap`} type="number" min="1" inputMode="numeric" value={f.total_capacity} onChange={set('total_capacity')} />
      </Field>
      <Field id={`${uid}-occ`} label="People there now">
        <input id={`${uid}-occ`} type="number" min="0" inputMode="numeric" value={f.occupancy} onChange={set('occupancy')} />
      </Field>
      <div className={css.actions}>
        <button type="submit" className="btn btn-primary" disabled={busy || !(Number(f.total_capacity) >= 1)}>Confirm as shelter</button>
      </div>
    </form>
  )
}

function CandidateCard({ c, busy, run }) {
  const [why, setWhy] = useState('')
  const next = nextCandidateAction(c)
  const open = !['CONFIRMED', 'REJECTED'].includes(c.state)
  return (
    <li className={css.card}>
      <div className={css.cardHead}>
        <h3 className={css.title}><span className={css.rid}>{c.candidate_id}</span>{c.name}</h3>
        <Chip tone={c.state === 'CONFIRMED' ? 'ok' : c.state === 'REJECTED' ? 'bad' : 'warn'}>{c.state === 'CONFIRMED' || c.state === 'REJECTED' ? CANDIDATE_LABEL[c.state] : 'CANDIDATE'}</Chip>
      </div>
      <p className={css.meta}>
        {c.submitted_by} · received {new Date(c.received_at).toLocaleString()}
        {c.address ? ` · ${c.address}` : ''}{c.capacity_estimate ? ` · about ${c.capacity_estimate} people` : ''}
      </p>
      {c.description && <p className={css.meta}>“{c.description}”</p>}
      <Stepper c={c} />
      {c.permission && <p className={css.meta}>Permission: {c.permission.granted_by_role}{c.permission.granted_by_name ? ` (${c.permission.granted_by_name})` : ''}, {PERMISSION_METHOD_LABEL[c.permission.method]?.toLowerCase()}, {new Date(c.permission.granted_at).toLocaleString()}</p>}
      {c.state === 'CONFIRMED' && <p className={css.commit}>Now shelter {c.shelter_id} (source: volunteer-submitted, verified by dispatcher).</p>}
      {c.state === 'REJECTED' && <p className={css.meta}>Rejected: {c.rejection?.reason}</p>}
      {next && (
        <details className={css.disclosure} open>
          <summary>{{ permission: 'Record the owner’s permission', suitability: 'Suitability checklist', confirm: 'Confirm as a shelter' }[next]}</summary>
          {next === 'permission' && <PermissionForm c={c} busy={busy} run={run} />}
          {next === 'suitability' && <SuitabilityForm c={c} busy={busy} run={run} />}
          {next === 'confirm' && <ConfirmForm c={c} busy={busy} run={run} />}
        </details>
      )}
      {open && (
        <div className={css.chips}>
          <label className="sr-only" htmlFor={`reject-${c.candidate_id}`}>Reason for rejecting {c.name}</label>
          <input id={`reject-${c.candidate_id}`} className="input" style={{ flex: '1 1 160px', width: 'auto' }} placeholder="Reason (to reject)" value={why} onChange={(e) => setWhy(e.target.value)} maxLength={300} />
          <button type="button" className="btn btn-danger" disabled={busy || !why.trim()} onClick={() => run(() => api.candidateReject(c.candidate_id, why.trim()), 'Candidate rejected.')}>Reject</button>
        </div>
      )}
    </li>
  )
}

// ---------------------------------------------------------------- responder / volunteer view (own token)
export function ResponderView({ token, onExit }) {
  const [me, setMe] = useState(null)
  const [error, setError] = useState('')
  const [msg, setMsg] = useState(null)
  const [busy, setBusy] = useState(false)
  const load = useCallback(async () => {
    try { setMe(await api.responderMe(token)); setError('') } catch (err) { setError(errText(err)) }
  }, [token])
  useEffect(() => {
    const id = setTimeout(load, 0)
    const every = setInterval(load, 30000)
    return () => { clearTimeout(id); clearInterval(every) }
  }, [load])
  const update = async (id, fields, observedAt) => {
    setBusy(true)
    try {
      const out = await api.responderUpdate(token, id, fields, observedAt)
      setMsg({ kind: 'ok', text: out.update.applied ? `Sent: ${id} updated.` : `Stored but not applied: ${out.update.note}` })
      load()
    } catch (err) { setMsg({ kind: 'error', text: errText(err) }) } finally { setBusy(false) }
  }
  const role = me?.responder?.role
  return (
    <>
      <div className={css.toolbar}>
        <p className={styles.note} style={{ margin: 0 }}>
          {me ? <>Signed in by link as <strong>{me.responder.name}</strong> ({ROLE_LABEL[role]}).</> : 'Opening your link…'}
        </p>
        <button type="button" className="btn" onClick={onExit}>Leave this view</button>
      </div>
      {error && <StateBand kind="error">{error}. Ask the dispatcher for a new link.</StateBand>}
      {msg && (msg.kind === 'error' ? <StateBand kind="error">{msg.text}</StateBand>
        : <p className={styles.note} role="status" style={{ marginTop: 0 }}>✓ {msg.text}</p>)}
      {me && role !== 'volunteer' && (
        <Section title="Your resources" hint="Send what is true now, with the time it was true. The dispatcher sees who sent it and when.">
          <ul className={css.list}>{me.resources.map((r) => <ResourceCard key={r.id} r={r} busy={busy} onUpdate={update} idPrefix="me" />)}</ul>
        </Section>
      )}
      {me && role === 'volunteer' && (
        <>
          <Section title="Suggest a shelter" hint="A place that could take people during flooding. The dispatcher checks permission, access, water and toilets, and the flood model before it is used.">
            <CandidateForm bbox={me.pilot_bbox} busy={busy} onSubmit={(body) => api.submitCandidate(body, token).then((out) => { load(); return out })} />
          </Section>
          <Section title={`Your suggestions (${me.candidates?.length || 0})`}>
            {!me.candidates?.length ? <p className={styles.empty}>Nothing submitted yet.</p> : (
              <ul className={css.list}>
                {me.candidates.map((c) => (
                  <li key={c.candidate_id} className={css.card}>
                    <div className={css.cardHead}><span className={css.title}><span className={css.rid}>{c.candidate_id}</span>{c.name}</span><Chip tone={c.state === 'CONFIRMED' ? 'ok' : c.state === 'REJECTED' ? 'bad' : 'warn'}>{CANDIDATE_LABEL[c.state]}</Chip></div>
                    {c.rejection_reason && <p className={css.meta}>Reason: {c.rejection_reason}</p>}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </>
      )}
    </>
  )
}

function initialResponderToken() {
  const t = tokenFromHash(window.location.hash)
  if (t) {
    try { window.sessionStorage.setItem(RESPONDER_KEY, t) } catch { /* storage blocked: the token lives in memory only */ }
    try { window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}#/resources?responder=1`) } catch { /* keep going */ }
    return t
  }
  if (/[?&]responder=1/.test(window.location.hash)) {
    try { return window.sessionStorage.getItem(RESPONDER_KEY) || '' } catch { return '' }
  }
  return ''
}

// ---------------------------------------------------------------- page
function Connect({ ops, setToken }) {
  const [token, setTok] = useState('')
  if (ops.available) return null
  if (ops.available === null) return <StateBand kind="loading">Checking the shared incident store…</StateBand>
  return (
    <form className={css.form} onSubmit={(e) => { e.preventDefault(); setToken(token); setTok('') }}>
      <div className={css.full}><StateBand kind="error">{ops.reason}</StateBand></div>
      <Field id="registry-dispatcher-token" label="Dispatcher token" full>
        <input id="registry-dispatcher-token" className="input" type="password" autoComplete="off" value={token} onChange={(e) => setTok(e.target.value)} />
      </Field>
      <div className={css.actions}><button type="submit" className="btn" disabled={!token.trim()}>Connect</button></div>
    </form>
  )
}

export default function ResourcesPage({ status, statusText }) {
  const { ops, setToken, response } = useFloodNet()
  const [responderToken, setResponderToken] = useState(initialResponderToken)
  const [data, setData] = useState({ vocab: null, resources: [], responders: [], candidates: [], loadedAt: null })
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [tab, setTab] = useState('team')

  const load = useCallback(async () => {
    try {
      const [vocab, res, resp, cand] = await Promise.all([api.registryVocabulary(), api.registryResources(), api.listResponders(), api.listCandidates()])
      setData({ vocab, resources: res.resources, responders: resp.responders, candidates: cand.candidates, loadedAt: Date.now() })
      setError('')
    } catch (err) { setError(errText(err)) }
  }, [])
  useEffect(() => {
    if (responderToken || !ops.available) return undefined
    const id = setTimeout(load, 0)
    const every = setInterval(load, 30000)
    return () => { clearTimeout(id); clearInterval(every) }
  }, [load, ops.available, responderToken])

  const run = useCallback(async (fn, done) => {
    setBusy(true); setNotice('')
    try { await fn(); await load(); setNotice(done) } catch (err) { setError(errText(err)) } finally { setBusy(false) }
  }, [load])
  const update = (id, fields, observedAt) => run(() => api.updateResource(id, fields, observedAt), `${id} updated.`)

  const commits = useMemo(() => commitments(ops.allTrips || ops.trips, response?.ledger), [ops.allTrips, ops.trips, response?.ledger])
  const byKind = useMemo(() => {
    const out = { team: [], vehicle: [], shelter: [] }
    for (const r of data.resources) out[r.kind]?.push(r)
    return out
  }, [data.resources])
  const openCandidates = data.candidates.filter((c) => !['CONFIRMED', 'REJECTED'].includes(c.state))
  const closedCandidates = data.candidates.filter((c) => ['CONFIRMED', 'REJECTED'].includes(c.state))
  const available = (rows) => rows.filter((r) => r.status === 'AVAILABLE').length
  const freePlaces = byKind.shelter.filter((s) => s.status === 'open').reduce((n, s) => n + Math.max(0, (s.total_capacity || 0) - (s.occupancy || 0)), 0)
  const simulated = data.resources.filter((r) => sourceChip(r).text === 'SIMULATED').length
  const stale = data.resources.filter((r) => r.freshness !== 'CURRENT').length

  if (responderToken) {
    return (
      <>
        <PageHeader pageKey="resources" status="ready" statusText="" />
        <ResponderView token={responderToken} onExit={() => {
          try { window.sessionStorage.removeItem(RESPONDER_KEY) } catch { /* nothing stored */ }
          setResponderToken('')
          window.location.hash = '#/resources'
        }} />
      </>
    )
  }

  return (
    <>
      <PageHeader pageKey="resources" status={status} statusText={statusText} />
      <Connect ops={ops} setToken={setToken} />
      {ops.available && error && <StateBand kind="error">{error}</StateBand>}
      {notice && <p className={styles.note} role="status" style={{ marginTop: 0 }}>✓ {notice}</p>}

      {ops.available && (
        <>
          <p className={shell.metaLine}>
            {simulated > 0 && <span className="tag-badge tag-SYNTHETIC">{simulated} SIMULATED</span>}
            {stale > 0 && <span className="tag-badge tag-UNKNOWN">{stale} STALE / UNKNOWN</span>}
            <span>No government database connected</span>
            <InfoTip label="Where these records come from">
              SIMULATED records are the demonstration seed: real places, invented numbers. Everything else was entered or
              imported by the dispatcher, sent by a registered responder, or confirmed from a volunteer suggestion.
              Confirm STALE or UNKNOWN records before relying on them.
            </InfoTip>
          </p>

          <div className={styles.tiles} style={{ marginBottom: 12 }}>
            <div className={styles.tile}><span className={styles.tileLabel}>Teams available</span><span className={styles.tileValue}>{available(byKind.team)}/{byKind.team.length}</span></div>
            <div className={styles.tile}><span className={styles.tileLabel}>Vehicles available</span><span className={styles.tileValue}>{available(byKind.vehicle)}/{byKind.vehicle.length}</span></div>
            <div className={styles.tile}><span className={styles.tileLabel}>Free shelter places</span><span className={styles.tileValue}>{freePlaces}</span><span className={styles.tileSub}>open shelters, as last reported</span></div>
            <div className={styles.tile}><span className={styles.tileLabel}>Candidates to check</span><span className={styles.tileValue}>{openCandidates.length}</span><span className={styles.tileSub}>not shelters yet</span></div>
          </div>

          <Section title="Registry" hint="What the planner can use when it plans from the registry. Status, source and how fresh each record is.">
            <div className={styles.filters} role="group" aria-label="Show">
              {['team', 'vehicle', 'shelter'].map((k) => (
                <button key={k} type="button" aria-pressed={tab === k} className={`${styles.filterChip} ${tab === k ? styles.filterChipOn : ''}`} onClick={() => setTab(k)}>
                  {KIND_LABEL[k]} <span className={styles.count}>{byKind[k].length}</span>
                </button>
              ))}
            </div>
            {!byKind[tab].length ? <p className={styles.empty}>No {KIND_LABEL[tab].toLowerCase()} yet.</p> : (
              <ul className={css.list}>{byKind[tab].map((r) => <ResourceCard key={r.id} r={r} commits={commits[r.id]} busy={busy} onUpdate={update} />)}</ul>
            )}
          </Section>

          <Section title={`Candidate shelters (${openCandidates.length} to check)`}
            hint="Places suggested by volunteers. Permission, then suitability, then confirmation. A candidate is never used by the planner before it is confirmed.">
            {!openCandidates.length ? <p className={styles.empty}>No candidates waiting.</p>
              : <ul className={css.list}>{openCandidates.map((c) => <CandidateCard key={c.candidate_id} c={c} busy={busy} run={run} />)}</ul>}
            {closedCandidates.length > 0 && (
              <details className={css.disclosure}>
                <summary>Confirmed and rejected ({closedCandidates.length})</summary>
                <ul className={css.list}>{closedCandidates.map((c) => <CandidateCard key={c.candidate_id} c={c} busy={busy} run={run} />)}</ul>
              </details>
            )}
            <details className={css.disclosure}>
              <summary>Enter a suggestion on a volunteer’s behalf</summary>
              <CandidateForm bbox={data.vocab?.pilot_bbox} busy={busy} onBehalf submitLabel="Add candidate"
                onSubmit={(body) => api.submitCandidate(body).then((out) => { load(); return out })} />
            </details>
          </Section>

          <Section title="Responder and volunteer links" hint="A link lets one person update only the resources you choose (or, for a volunteer, only suggest shelters). Revoke it when it is no longer needed.">
            <RespondersSection resources={data.resources} responders={data.responders} busy={busy} onDone={load} />
          </Section>

          <Section title="Register a team, vehicle or shelter" advanced>
            <AddRecordForm vocab={data.vocab} busy={busy} onDone={load} />
          </Section>

          <Section title="Import a list (CSV or JSON)" advanced>
            <ImportForm vocab={data.vocab} onDone={load} />
          </Section>

          <p className={styles.note}>
            {data.loadedAt ? `Refreshed ${new Date(data.loadedAt).toLocaleTimeString()} · every 30 s` : 'Loading…'} · STALE after
            {' '}{data.vocab?.stale_after_min ?? 60} min without confirmation (ASSUMED)
          </p>
        </>
      )}
    </>
  )
}
