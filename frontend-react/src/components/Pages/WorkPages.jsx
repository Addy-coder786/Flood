import ScenarioPanel from '../ScenarioPanel/ScenarioPanel.jsx'
import LayerControl from '../LayerControl/LayerControl.jsx'
import FloodedStreets from '../FloodedStreets/FloodedStreets.jsx'
import WhyFloodedPanel from '../WhyFloodedPanel/WhyFloodedPanel.jsx'
import RoutePlanner from '../RoutePlanner/RoutePlanner.jsx'
import ArrivalAccess from '../ArrivalAccess/ArrivalAccess.jsx'
import RescueTrip from '../RescueTrip/RescueTrip.jsx'
import ResponsePanel from '../ResponsePanel/ResponsePanel.jsx'
import EvaluationSection from '../ResponsePanel/EvaluationSection.jsx'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import PageHeader, { Section } from '../Shell/PageHeader.jsx'
import InfoTip from '../ui/InfoTip.jsx'
import shell from '../Shell/Shell.module.css'
import styles from './Pages.module.css'

// The four map-centred work pages (D-37). They compose the existing, tested panels; each page states its one
// task and keeps rarely used controls behind an "optional" disclosure.

export function ForecastPage({ status, statusText }) {
  const { roadEvents, response } = useFloodNet()
  const incident = roadEvents.length + response.ledger.length + response.events.length > 0
  return (
    <>
      <PageHeader pageKey="forecast" status={status} statusText={statusText} />
      {incident && (
        <p className={shell.metaLine}>
          Incident open: a new forecast keeps its reports and assignments; the plan will need replanning.
        </p>
      )}
      <ScenarioPanel />
      <FloodedStreets />
      <div>
        <WhyFloodedPanel />
      </div>
      <Section title="Map layers and display" advanced>
        <LayerControl />
      </Section>
    </>
  )
}

export function AccessPage({ status, statusText }) {
  return (
    <>
      <PageHeader pageKey="access" status={status} statusText={statusText} />
      <RescueTrip />
      <ArrivalAccess />
      <Section title="Compare route objectives" advanced>
        <RoutePlanner />
      </Section>
    </>
  )
}

export function ResponsePage({ status, statusText, onNavigate }) {
  const { proposal, runResponsePlan, run, response } = useFloodNet()
  return (
    <>
      <PageHeader pageKey="response" status={proposal ? 'ready' : status} statusText={statusText} />
      {proposal && (
        <div className={styles.banner} role="status">
          <div className={styles.bannerTitle}>Replan recommended</div>
          {proposal.reason}
          <div className={styles.row}>
            <button type="button" className="btn btn-primary" disabled={!run || response.loading} onClick={runResponsePlan}>
              Replan with the new forecast at T+{proposal.suggested_t} min
            </button>
          </div>
        </div>
      )}
      <div>
        <ResponsePanel onNavigate={onNavigate} />
      </div>
    </>
  )
}

export function ReplayPage({ status, statusText }) {
  return (
    <>
      <PageHeader pageKey="replay" status={status} statusText={statusText} />
      <p className={shell.metaLine}>
        <span className="tag-badge tag-SYNTHETIC">SIMULATED</span>
        Compares routing methods; not real-world accuracy.
        <InfoTip label="How the evaluation works">
          A scripted incident (closures, a vehicle breakdown, a full shelter) is replayed twice on identical events and
          resources: once checking each road at the minute it is reached, once at dispatch (the baseline). Both plans are
          judged against a <b>different</b> simulated outcome, not observed flooding.
        </InfoTip>
      </p>
      <EvaluationSection />
    </>
  )
}
