import { useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { REPORT_STATUS, SOURCE_TIERS, TIER_LABEL, annotateRoadEvents } from '../../lib/roadAccess.js'
import PageHeader, { Section } from '../Shell/PageHeader.jsx'
import styles from './Pages.module.css'

// Alerts & reports (D-32, D-37): log road reports with their source tier, and see what each report does at the
// decision time and why: in force, lifted, not applied (lower tier), unconfirmed, stale, not yet received. The
// derived statuses come from lib/roadAccess.js, the browser mirror of backend/floodnet/access/events.py.

const FILTERS = [
  { key: 'all', label: 'All reports' },
  { key: 'attention', label: 'Needs attention' },
  { key: 'force', label: 'Closures in force' },
  { key: 'future', label: 'Not yet received' },
]
const needsAttention = (e) => ['REJECTED_LOWER_TIER', 'UNCONFIRMED'].includes(e.status)
  || (e.stale && e.status === 'ACTIVE') || e.contested_by?.length > 0
const STATUS_TONE = {
  ACTIVE: 'toneClosed', LIFTED: 'toneMuted', APPLIED: 'toneOk', NO_EFFECT: 'toneMuted',
  REJECTED_LOWER_TIER: 'toneWarn', UNCONFIRMED: 'toneWarn', FUTURE: 'toneMuted',
}
const fmtT = (t) => (t == null ? '—' : `T+${Number(t).toFixed(0)}`)

function ReportStatus({ e }) {
  const s = REPORT_STATUS[e.status] || { label: e.status, glyph: '·' }
  return (
    <span className={styles.statusCell}>
      <span className={`${styles.chip} ${styles[STATUS_TONE[e.status]] || ''}`}>
        <span aria-hidden="true">{s.glyph}</span> {s.label}
      </span>
      {e.stale && e.status !== 'FUTURE' && <span className={`${styles.chip} ${styles.toneWarn}`}><span aria-hidden="true">◷</span> Stale {Math.round(e.age_min)} min</span>}
      {e.contested_by?.length > 0 && <span className={`${styles.chip} ${styles.toneWarn}`}><span aria-hidden="true">⚑</span> Contested</span>}
      {e.scheduled && <span className={`${styles.chip} ${styles.toneMuted}`}>Scheduled</span>}
    </span>
  )
}

function ResourceReports() {
  const { response, responseResources, currentT } = useFloodNet()
  const st = response.result?.state
  const ids = (k) => new Set((st?.[k] || []).map((e) => e.event_id ?? e))
  const used = ids('events_used')
  const unconfirmed = ids('events_unconfirmed')
  const scheduled = ids('events_scheduled')
  const name = (e) => {
    const id = e.shelter_id || e.vehicle_id || e.settlement_id
    const all = [...(responseResources?.shelters || []), ...(responseResources?.vehicles || []), ...(responseResources?.settlements || [])]
    return all.find((x) => x.id === id)?.name || id
  }
  const label = (e) => {
    if (e.received_t_min > currentT) return 'Not yet received'
    if (unconfirmed.has(e.event_id)) return 'Unconfirmed (public): not applied'
    if (scheduled.has(e.event_id)) return 'Scheduled'
    if (used.has(e.event_id)) return 'Applied in the current plan'
    return response.result ? 'Received; replan to apply' : 'Received; build a plan to apply'
  }
  if (!response.events.length) {
    return <p className={styles.empty}>None yet. Log them on the Response page.</p>
  }
  return (
    <div className={styles.tableWrap}>
      <table className={styles.table}>
        <thead><tr><th scope="col">Received</th><th scope="col">Report</th><th scope="col">About</th><th scope="col">Status</th></tr></thead>
        <tbody>
          {response.events.map((e) => (
            <tr key={e.event_id}>
              <td className={styles.num}>{fmtT(e.received_t_min)}</td>
              <td>{e.event_id} · {e.kind.replace('_', ' ')}{e.occupancy != null ? ` (occupancy ${e.occupancy})` : ''}</td>
              <td>{name(e)}</td>
              <td>{label(e)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default function ReportsPage({ status }) {
  const {
    roadEvents, currentT, closeRoadMode, setCloseRoadMode, addRoadReport, reopenRoad, selectedSegId, roads, run,
  } = useFloodNet()
  const [filter, setFilter] = useState('all')
  const [kind, setKind] = useState('closure')
  const [tier, setTier] = useState('OPERATOR')
  const [onlyFor, setOnlyFor] = useState('')          // '' = every vehicle; otherwise a restriction (D-42)
  const vehicleTypes = kind === 'closure' && onlyFor ? [onlyFor] : null
  const [openRow, setOpenRow] = useState(null)

  const annotated = useMemo(() => annotateRoadEvents(roadEvents, currentT), [roadEvents, currentT])
  const counts = {
    all: annotated.length,
    attention: annotated.filter(needsAttention).length,
    force: annotated.filter((e) => e.kind === 'closure' && e.status === 'ACTIVE').length,
    future: annotated.filter((e) => e.status === 'FUTURE').length,
  }
  const rows = annotated.filter((e) => (filter === 'all' ? true
    : filter === 'attention' ? needsAttention(e)
      : filter === 'force' ? e.kind === 'closure' && e.status === 'ACTIVE'
        : e.status === 'FUTURE'))
  const selectedName = useMemo(() => {
    if (!selectedSegId) return null
    const f = (roads?.features || []).find((x) => String(x.properties.seg_id) === String(selectedSegId))
    return f ? (f.properties.name || `segment ${selectedSegId}`) : `segment ${selectedSegId}`
  }, [roads, selectedSegId])
  const picking = Boolean(closeRoadMode)

  return (
    <>
      <PageHeader pageKey="reports" status={status === 'empty' ? 'ready' : status}
        statusText={status === 'stale' ? 'Inputs changed since the last run.' : null}>
        <button type="button" className="btn btn-primary" aria-pressed={picking}
          onClick={() => setCloseRoadMode(picking ? false : { kind, tier, vehicleTypes })}>
          {picking ? 'Click a road on the map… (Esc: cancel)' : `Log a ${kind === 'closure' ? 'closure' : 'reopening'} on the map`}
        </button>
      </PageHeader>

      <Section title="Log a road report" hint={<>
        Reports are received at the decision time ({fmtT(currentT)} min). Choose what was reported and by whom, then click
        the road on the map. Project rules (simulation policy, not legal authority): a confirmed closure stays in force
        until a reopening of the same or higher tier says otherwise. Public reports are never binding: a public closure
        makes the road <b>Uncertain</b>. Nothing expires; old reports are flagged <b>Stale</b>.
      </>}>
        <div className={styles.formGrid}>
          <fieldset className={styles.radioGroup}>
            <legend className="field-label">Report</legend>
            <label><input type="radio" name="report-kind" value="closure" checked={kind === 'closure'} onChange={() => setKind('closure')} /> Road closed</label>
            <label><input type="radio" name="report-kind" value="reopen" checked={kind === 'reopen'} onChange={() => setKind('reopen')} /> Road reopened</label>
          </fieldset>
          <div>
            <label className="field-label" htmlFor="report-tier">Reported by</label>
            <select id="report-tier" value={tier} onChange={(e) => setTier(e.target.value)}>
              {SOURCE_TIERS.map((t) => <option key={t} value={t}>{TIER_LABEL[t]}</option>)}
            </select>
          </div>
          {kind === 'closure' && (
            <div>
              <label className="field-label" htmlFor="report-vehicles">Closed to</label>
              <select id="report-vehicles" value={onlyFor} onChange={(e) => setOnlyFor(e.target.value)}>
                <option value="">Every vehicle</option>
                <option value="truck">Trucks only (restriction)</option>
                <option value="ambulance">Ambulances only (restriction)</option>
                <option value="car">Cars only (restriction)</option>
                <option value="motorcycle">Motorcycles only (restriction)</option>
              </select>
            </div>
          )}
        </div>
        <div className={styles.row}>
          <button type="button" className="btn" disabled={!selectedSegId}
            onClick={() => addRoadReport({ segId: selectedSegId, name: selectedName, kind, tier, vehicleTypes })}>
            {selectedSegId ? `Log on selected road: ${selectedName}` : 'Select a road on the map to log without picking'}
          </button>
        </div>
      </Section>

      <Section title={`Road reports at ${fmtT(currentT)} min`} id="rtab-reports-table">
        <div className={styles.filters} role="group" aria-label="Filter road reports">
          {FILTERS.map((f) => (
            <button key={f.key} type="button" aria-pressed={filter === f.key}
              className={`${styles.filterChip} ${filter === f.key ? styles.filterChipOn : ''}`} onClick={() => setFilter(f.key)}>
              {f.label} <span className={styles.count}>{counts[f.key]}</span>
            </button>
          ))}
        </div>
        {rows.length === 0 ? (
          <p className={styles.empty}>
            {annotated.length === 0 ? 'No road reports yet.' : 'No report matches this filter.'}
          </p>
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead><tr><th scope="col">Received</th><th scope="col">Road</th><th scope="col">Report</th><th scope="col">Source</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {rows.map((e) => (
                  <ReportRow key={e.event_id} e={e} open={openRow === e.event_id} onToggle={() => setOpenRow(openRow === e.event_id ? null : e.event_id)}
                    onReopen={() => reopenRoad(e.seg_id, e.name, tier)} onConfirm={() => addRoadReport({ segId: e.seg_id, name: e.name, kind: 'closure', tier: 'OPERATOR' })} tier={tier} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {!run && roadEvents.length > 0 && <p className={styles.note}>Run a forecast to apply these reports.</p>}
      </Section>

      <Section title="Shelter, vehicle and settlement reports">
        <ResourceReports />
      </Section>

    </>
  )
}

function ReportRow({ e, open, onToggle, onReopen, onConfirm, tier }) {
  return (
    <>
      <tr className={open ? styles.rowOpen : ''}>
        <td className={styles.num}>{fmtT(e.received_t_min)}{e.scheduled ? <div className={styles.sub}>applies {fmtT(e.applies_from_t_min)}</div> : null}</td>
        <td>
          <button type="button" className={styles.rowLink} aria-expanded={open} onClick={onToggle}>{e.name || `segment ${e.seg_id}`}</button>
        </td>
        <td>{e.event_id} · {e.kind === 'closure' ? 'Closed' : 'Reopened'}</td>
        <td>{TIER_LABEL[e.source_tier] || e.source_tier}</td>
        <td><ReportStatus e={e} /></td>
      </tr>
      {open && (
        <tr className={styles.detailRow}>
          <td colSpan={5}>
            <div className={styles.explain}>{e.explanation}</div>
            <div className={styles.row}>
              {e.kind === 'closure' && e.status === 'ACTIVE' && (
                <button type="button" className={`btn ${styles.small}`} onClick={onReopen}>Record reopening as {TIER_LABEL[tier]}</button>
              )}
              {e.status === 'UNCONFIRMED' && (
                <button type="button" className={`btn ${styles.small}`} onClick={onConfirm}>Confirm closure as Operator</button>
              )}
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
