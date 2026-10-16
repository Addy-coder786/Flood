import { useMemo } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { PAGES } from '../../lib/pages.js'
import { annotateRoadEvents } from '../../lib/roadAccess.js'
import MetricsPanel from '../MetricsPanel/MetricsPanel.jsx'
import HotspotsCard from '../HotspotsCard/HotspotsCard.jsx'
import ProvenancePanel from '../ProvenancePanel/ProvenancePanel.jsx'
import PageHeader, { Section } from '../Shell/PageHeader.jsx'
import styles from './Pages.module.css'

// Overview (D-37): what is happening, where the operator is in the workflow, and the one next action.
// Every number here is read from the run, the incident state or /api/data-status; nothing is filled in.

const clock = (iso) => {
  const d = iso ? new Date(iso) : null
  return d && !Number.isNaN(d.getTime()) ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : null
}

export function IncidentSummary() {
  const { roadEvents, response, currentT, newIncident } = useFloodNet()
  const ann = useMemo(() => annotateRoadEvents(roadEvents, currentT), [roadEvents, currentT])
  const inForce = ann.filter((e) => e.kind === 'closure' && e.status === 'ACTIVE').length
  const attention = ann.filter((e) => ['REJECTED_LOWER_TIER', 'UNCONFIRMED'].includes(e.status) || (e.stale && e.status === 'ACTIVE') || e.contested_by.length).length
  const r = response.result
  const live = response.ledger.filter((a) => a.status !== 'CANCELLED')
  const confirmReset = () => {
    // A destructive, irreversible action: ask once, in plain words.
    if (window.confirm('Start a new incident? This clears every road report, resource report, assignment and plan version kept for the current incident (the forecast on screen stays). Export the ledger first if you need a record.')) newIncident()
  }
  return (
    <>
      <dl className={styles.kv}>
        <dt>Road reports</dt><dd>{roadEvents.length} logged · {inForce} closure(s) in force{attention ? ` · ${attention} need attention` : ''}</dd>
        <dt>Resource reports</dt><dd>{response.events.length} logged</dd>
        <dt>Response plan</dt><dd>{response.version ? `version ${response.version} · ${live.length} active assignment(s)` : 'not built yet'}{response.staleReason ? ' · needs replanning' : ''}</dd>
        {r && <><dt>Unmet need</dt><dd>{r.summary.unmet} people (simulated)</dd></>}
      </dl>
      {(roadEvents.length > 0 || response.ledger.length > 0 || response.events.length > 0) && (
        <div className={styles.row}>
          <button type="button" className={styles.dangerLink} onClick={confirmReset}>New incident (clear reports and plan)</button>
        </div>
      )}
    </>
  )
}

export default function OverviewPage({ status, statusText, onNavigate, stepState }) {
  const { run, incidentRestoredAt, proposal } = useFloodNet()
  return (
    <>
      <PageHeader pageKey="overview" status={status} statusText={statusText}>
        <button type="button" className="btn btn-primary" onClick={() => onNavigate(run ? 'access' : 'forecast')}>
          {run ? 'Continue: check road access →' : 'Start a forecast →'}
        </button>
      </PageHeader>

      {incidentRestoredAt && (
        <div className={styles.banner} role="status">
          <div className={styles.bannerTitle}>Incident restored from this browser</div>
          Saved {clock(incidentRestoredAt) || 'at an unknown time'}. Run the forecast again, then replan.
        </div>
      )}
      {proposal && (
        <div className={styles.banner} role="status">
          <div className={styles.bannerTitle}>Replan recommended</div>
          {proposal.reason}
          <div className={styles.row}><button type="button" className="btn" onClick={() => onNavigate('response')}>Review the plan</button></div>
        </div>
      )}

      <Section title="Current incident">
        <IncidentSummary />
      </Section>

      {run && (
        <div>
          <MetricsPanel />
          <HotspotsCard />
        </div>
      )}

      <Section title="Workflow" hint="Work through the pages in order. The decision time on the timeline applies to every page.">
        <ol className={styles.journey}>
          {PAGES.filter((p) => p.key !== 'overview').map((p, i) => (
            <li key={p.key} className={`${styles.journeyItem} ${stepState[p.key + 'Done'] ? styles.journeyDone : ''}`}>
              <span className={styles.journeyNum} aria-hidden="true">{i + 2}</span>
              <span>
                <span className={styles.journeyTitle}>{p.label}</span>
                <span className={styles.journeyState}> · {stepState[p.key] || 'not started'}</span>
              </span>
              <button type="button" className={`btn ${styles.small}`} onClick={() => onNavigate(p.key)} aria-label={`Open ${p.label}`}>Open</button>
            </li>
          ))}
        </ol>
      </Section>

      <div>
        <ProvenancePanel />
      </div>
    </>
  )
}
