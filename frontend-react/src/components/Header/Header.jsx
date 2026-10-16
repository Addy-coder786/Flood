import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { activeSource, utcClock } from '../../lib/sources.js'
import { shortId } from '../../lib/format.js'
import LocationSelector from '../LocationSelector/LocationSelector.jsx'
import styles from './Header.module.css'

// Top context bar (D-37), the same on every page: pilot area · rainfall source and its freshness · decision time ·
// run · status. Freshness is shown only from a real timestamp in the run's provenance; otherwise "no timestamp".

function statusLabel(opts) {
  const { bootLoading, bootError, simulating, simError, isStale, run } = opts
  if (bootLoading) return 'Connecting'
  if (bootError)   return 'Offline'
  if (simulating)  return 'Running'
  if (simError)    return 'Error'
  if (isStale)     return 'Inputs changed'
  if (!run)        return 'Ready'
  return 'Complete'
}

const STALENESS_TEXT = { FRESH: 'fresh', AGING: 'ageing', STALE: 'stale', UNKNOWN: 'age unknown' }

/** "observed 19:00 UTC · 42 min old · fresh" from the provider's timing block; null when nothing is known. */
function freshness(rainfallSource) {
  const d = rainfallSource?.detail || {}
  const t = d.timing
  if (t) {
    const at = utcClock(t.observed_at_utc) ? `observed ${utcClock(t.observed_at_utc)}`
      : utcClock(t.received_at_utc) ? `retrieved ${utcClock(t.received_at_utc)}` : null
    const age = Number.isFinite(t.age_min) ? `${Math.round(t.age_min)} min old` : null
    return [at, age, STALENESS_TEXT[t.staleness]].filter(Boolean).join(' · ') || null
  }
  const at = utcClock(d.image_observed_at_utc) || utcClock(d.retrieved_at)
  return at ? `at ${at}` : null
}

export default function Header({ leading = null }) {
  const {
    scenarioId, currentScenario, run, isStale, currentT,
    simulating, simError, bootLoading, bootError, roadEvents, response,
  } = useFloodNet()

  const src = activeSource({ run, isStale, scenarioId, currentScenario })
  // Design storms and the 2005 replay have no observation time: say what they are instead of "no timestamp".
  const NON_LIVE = { scenario: 'synthetic design storm', demo: 'demo scenario (synthetic)', replay: 'historical replay (26 Jul 2005)' }
  const fresh = src.ran ? (NON_LIVE[src.kind] || freshness(run?.provenance?.rainfall_source)) : null
  const statusText = statusLabel({ bootLoading, bootError, simulating, simError, isStale, run })
  const tone = bootError || simError ? 'bad' : simulating ? 'busy' : isStale ? 'stale' : 'ok'
  const incident = roadEvents.length + response.ledger.length + response.events.length > 0

  return (
    <header className={styles.topbar}>
      {leading}
      <div className={`${styles.group} ${styles.optional}`}>
        <LocationSelector />
      </div>

      <div className={`${styles.group} ${styles.optional}`} title={src.ran ? src.name : `${src.name} (selected, not run yet)`}>
        <span className={styles.label}>Rainfall source</span>
        <span className={styles.value}>
          <span className={`tag-badge tag-${src.tone}`}>{src.badge}</span>
          <span className={styles.valueText}>{src.ran ? (fresh || 'no timestamp') : 'not run yet'}</span>
        </span>
      </div>

      <div className={styles.group}>
        <span className={styles.label} id="decision-time-label">Decision time</span>
        <span className={styles.decision} aria-labelledby="decision-time-label">
          {run && !isStale ? `T+${currentT} min` : '—'}
        </span>
      </div>

      <div className={`${styles.group} ${styles.optional}`}>
        <span className={styles.label}>Run</span>
        <span className={styles.valueText}>
          {run ? <code className={styles.code}>{shortId(run.run_id)}</code> : 'none'}
          {incident && <span className={styles.incident}> · incident open</span>}
        </span>
      </div>

      {/* Polite live region: run status changes without user action and infrequently. The dot is decorative; the
          word carries the state. */}
      <div className={`${styles.pill} ${styles[`pill_${tone}`]}`} role="status" aria-atomic="true">
        <span className={styles.dot} aria-hidden="true" />
        <span>{statusText}</span>
      </div>
    </header>
  )
}
