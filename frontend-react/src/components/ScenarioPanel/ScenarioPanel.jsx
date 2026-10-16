import { useFloodNet, LIVE_ID, ECMWF_ID, RADAR_SRI_ID } from '../../state/FloodNetContext.jsx'
import { fmt, shortId } from '../../lib/format.js'
import { activeSource, scenarioLabel, scenarioSubtitle, utcClock } from '../../lib/sources.js'
import { useDataStatus, imdLiveUsable, imdLiveBadge } from '../../lib/useDataStatus.js'
import styles from './ScenarioPanel.module.css'

// A hypothetical what-if input the operator chooses (never a measured/real-time blockage reading) --
// labelled "Blockage scenario" in the UI, not "drainage state", so it's never mistaken for model-derived
// output. Actual model-derived drainage state (surcharging node count, utilization) is reported separately
// in MetricsPanel/WhyFloodedPanel from the real simulation output.
const BLOCKAGE_OPTIONS = [
  { value: 'none',    label: 'None — all drains clear (0%)',              spec: { mode: 'none' } },
  { value: 'half',    label: 'Uniform 50% blockage (what-if)',            spec: { mode: 'fraction', fraction: 0.5 } },
  { value: 'seventy', label: 'Uniform 70% blockage (what-if)', spec: { mode: 'fraction', fraction: 0.7 } },
  { value: 'random',  label: 'Random 30% of drains at 60% (what-if)',     spec: { mode: 'random', share: 0.3, fraction: 0.6 } },
]

/** What drove the completed run, in three or four short rows. Full provenance lives in the Sources tab. */
function RunSourceBlock({ source, rainfallSource }) {
  const d = rainfallSource?.detail || {}
  const rows = [['Rainfall', source.name]]
  if (d.timing?.staleness) rows.push(['Freshness', `${d.timing.staleness.toLowerCase()}${Number.isFinite(d.timing.age_min) ? ` · ${Math.round(d.timing.age_min)} min old` : ''}`])
  if (source.cached) rows.push(['Cached', `${source.cached.age_min} min ago · was ${source.cached.original_label}`])
  else if (source.fellBack) rows.push(['Failover', `using ${source.statusLabel}`])
  if (source.kind === 'radar') {
    rows.push(['Mode', `Radar-derived estimate${utcClock(d.image_observed_at_utc) ? ` · ${utcClock(d.image_observed_at_utc)}` : ''}`])
    for (const seg of (d.forecast_segments || []).slice(1)) rows.push([`${seg.from_min}–${seg.to_min} min`, seg.label])
    if (!d.forecast_segments?.length) rows.push(['Forecast', '3-hour persistence estimate'])
  } else if (source.kind === 'live') {
    if (d.station) rows.push(['Station', d.station])
    if (d.observed_rainfall_mm != null) rows.push(['Observed', `${fmt(d.observed_rainfall_mm, 1)} mm in 24 h`])
    rows.push(['Forecast', '3-hour persistence estimate (assumed constant rate; not a nowcast)'])
  } else if (source.kind === 'ecmwf') {
    if (d.distance_km_to_pilot != null) rows.push(['Model cell', `IFS HRES 9 km, ${fmt(d.distance_km_to_pilot, 1)} km from the pilot`])
    if (utcClock(d.retrieved_at)) rows.push(['Retrieved', utcClock(d.retrieved_at)])
    if (d.precipitation_mm?.length) rows.push(['Next 3 h', `${d.precipitation_mm.map((v) => fmt(v, 1)).join(' · ')} mm/h`])
  } else {
    return null
  }
  return (
    <div className={styles.provBlock}>
      {rows.map(([label, value]) => (
        <div className={styles.provRow} key={label}>
          <span className={styles.provLabel}>{label}</span>
          <span className={styles.provValue}>{value}</span>
        </div>
      ))}
    </div>
  )
}

export default function ScenarioPanel() {
  const {
    scenarios, scenarioId, setScenarioId, currentScenario,
    blockage, setBlockage,
    runSimulation, runCompare, simulating,
    run, simError, status, isStale,
  } = useFloodNet()

  const blockageKey = BLOCKAGE_OPTIONS.find((o) => JSON.stringify(o.spec) === JSON.stringify(blockage))?.value ?? 'none'
  const selected = activeSource({ run: null, isStale: true, scenarioId, currentScenario })
  const ranSource = activeSource({ run, isStale, scenarioId, currentScenario })
  const dataStatus = useDataStatus()
  const liveConfigured = status?.rainfall_providers?.find((p) => p.id === LIVE_ID)?.available !== false
    && imdLiveUsable(dataStatus)

  const summary = run?.summary
  const mb = run?.mass_balance

  return (
    <section className={styles.section} data-tour="rainfall-source">
      <div className="panel-heading">Scenario &amp; Rainfall</div>

      <div className={styles.field}>
        <label className="field-label" htmlFor="scenario-select">Scenario</label>
        <select
          id="scenario-select"
          value={scenarioId ?? ''}
          onChange={(e) => setScenarioId(e.target.value)}
          disabled={simulating}
        >
          {scenarios.map((s) => (
            <option key={s.id} value={s.id}>{scenarioLabel(s.id, s.name)}</option>
          ))}
          {/* D-55: the IMD LIVE option runs the backend `ecmwf` scenario; the real-IMD `live` id (HTTP 403 from
              this server's IP) is not offered. */}
          <option value={ECMWF_ID}>{scenarioLabel(ECMWF_ID)}</option>
          <option value={RADAR_SRI_ID}>{scenarioLabel(RADAR_SRI_ID)}</option>
          <option value="auto">{scenarioLabel('auto')}</option>
          <option value="demo">{scenarioLabel('demo')}</option>
        </select>
      </div>

      {scenarioId && (
        <div className={styles.sourceLine}>
          {(() => {
            // IMD live: the badge follows the server's latest IMD state, never a stale "IMD LIVE"
            const b = selected.kind === 'live' ? imdLiveBadge(dataStatus) : { text: selected.badge, tone: selected.tone }
            return <span className={`tag-badge tag-${b.tone}`}>{b.text}</span>
          })()}
          <span className={styles.sourceSub}>
            {selected.kind === 'live' && !liveConfigured
              ? 'IMD sign-in unavailable right now — Best available source will use the next source'
              : scenarioSubtitle(scenarioId, currentScenario?.description)}
            {selected.kind === 'scenario' && currentScenario?.total_mm != null ? ` · ${fmt(currentScenario.total_mm, 0)} mm` : ''}
            {selected.kind === 'replay' && currentScenario?.total_mm != null ? ` · ${fmt(currentScenario.total_mm, 0)} mm` : ''}
          </span>
        </div>
      )}

      <div className={styles.actions}>
        <button
          className="btn btn-primary btn-block"
          onClick={() => runSimulation()}
          disabled={simulating || !scenarioId}
          aria-busy={simulating}
        >
          {simulating ? (
            <><span className={styles.btnSpinner} aria-hidden="true" />Generating forecast…</>
          ) : 'Run forecast'}
        </button>
      </div>

      {/* Blockage is a secondary what-if exploration, not part of the primary
          rainfall -> forecast path — kept collapsed by default so only the
          scenario selector and "Run forecast" show in the default state. */}
      <details className={styles.whatIf}>
        <summary className={styles.whatIfSummary}>What-if: reduced drainage capacity (optional)</summary>
        <div className={styles.whatIfBody}>
          <div className={styles.field}>
            <label className="field-label" htmlFor="blockage-select">Blockage scenario (what-if)</label>
            <select
              id="blockage-select"
              value={blockageKey}
              onChange={(e) => setBlockage(BLOCKAGE_OPTIONS.find((o) => o.value === e.target.value)?.spec)}
              disabled={simulating}
            >
              {BLOCKAGE_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>{o.label}</option>
              ))}
            </select>
          </div>
          <button
            className={`btn btn-block ${styles.whatIfActions}`}
            onClick={() => runCompare()}
            disabled={simulating || !scenarioId}
          >
            Compare normal vs blocked
          </button>
        </div>
      </details>

      {simError && <div className={styles.errBox}>{simError}</div>}

      {ranSource.ran && <RunSourceBlock source={ranSource} rainfallSource={run.provenance?.rainfall_source} />}

      {/* Run summary grid — shown only when current active run matches selected inputs */}
      {run && !isStale && summary && (
        <div className={styles.runSummary}>
          {run.__isCompareBlocked && (
            <div className={styles.compareNote}>
              Viewing <strong>BLOCKED</strong> run — normal (blue) vs blocked (orange) on timeline
            </div>
          )}
          <div className={styles.summaryGrid}>
            <div className={styles.summaryCell}>
              <span className={styles.summaryCellLabel}>Forecast peak depth</span>
              <span className={styles.summaryCellValue}>{fmt(summary.max_depth_cm, 0)} cm</span>
            </div>
            <div className={styles.summaryCell}>
              <span className={styles.summaryCellLabel}>Surcharge volume</span>
              <span className={styles.summaryCellValue}>{fmt(summary.total_surcharge_m3, 0)} m³</span>
            </div>
            <div className={styles.summaryCell}>
              <span className={styles.summaryCellLabel}>Flooded segs</span>
              <span className={styles.summaryCellValue}>{summary.peak_flooded_segments ?? '–'}</span>
            </div>
            <div className={styles.summaryCell}>
              <span className={styles.summaryCellLabel}>Surcharging</span>
              <span className={styles.summaryCellValue}>{summary.peak_surcharging_nodes ?? '–'} nodes</span>
            </div>
            <div className={styles.summaryCell}>
              <span className={styles.summaryCellLabel}>Runtime</span>
              <span className={styles.summaryCellValue}>{fmt(run.runtime_s, 1)} s</span>
            </div>
          </div>
          <div className={styles.summaryFooter}>
            Mass-balance error: <strong>{fmt(mb?.error_pct, 4)}%</strong>
            {' · '}
            Run <code>{shortId(run.run_id)}</code>
          </div>
        </div>
      )}
    </section>
  )
}
