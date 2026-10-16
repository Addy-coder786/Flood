import { useCallback, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import {
  ALERT_LABEL, ADVISORY_LABEL, CATEGORY_COLOR,
  computeAllAlerts, describeAlert, rankHotspots, recommendedActions,
} from '../../lib/alerts.js'
import { fmt } from '../../lib/format.js'
import InfoTip from '../ui/InfoTip.jsx'
import styles from './AlertsPanel.module.css'

// The CAP draft (backend/floodnet/api/main.py::_alert_payload) arrives inside the simulation response itself, so
// it never depends on which server instance computed the run. Shape asserted by
// backend/tests/test_alerts_cap.py::test_alert_endpoint_contract.
function capDraftOf(run) {
  if (run?.alert) return run.alert
  throw new Error(run?.alert_error || 'No CAP draft is available for this run.')
}

function capField(draft, name) {
  return draft?.cap?.info?.[0]?.[name]
}

export default function AlertsPanel() {
  const { run, series, route, currentT, roads, selectSegment, currentScenario } = useFloodNet()

  // --- CAP 1.2 draft export ------------------------------------------------------------------
  // This is a DRAFT for an authorised officer, never an issued warning. FloodNet is not a designated
  // alerting authority (no NDMA SACHET credential, no submission path, no SMS/cell-broadcast channel
  // and no subscriber data), so the only honest product is a standards-conformant file that an
  // authority can review, edit and issue under its own authority. The backend emits CAP status="Draft",
  // which the OASIS CAP 1.2 spec itself defines as "A preliminary template or draft, not actionable in
  // its current form".
  const [capDraft, setCapDraft] = useState(null)
  const [capState, setCapState] = useState('idle') // idle | loading | ready | error
  const [capError, setCapError] = useState(null)
  const [showXml, setShowXml] = useState(false)
  const [copied, setCopied] = useState(false)

  const runId = run?.run_id ?? null

  const generateCap = useCallback(async () => {
    if (!runId) return
    setCapState('loading')
    setCapError(null)
    setCopied(false)
    try {
      const data = capDraftOf(run)
      setCapDraft(data)
      setCapState('ready')
    } catch (e) {
      setCapDraft(null)
      setCapError(e.message || String(e))
      setCapState('error')
    }
  }, [runId, run])

  const copyXml = useCallback(async () => {
    if (!capDraft?.cap_xml) return
    try {
      await navigator.clipboard.writeText(capDraft.cap_xml)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }, [capDraft])

  const downloadXml = useCallback(() => {
    if (!capDraft?.cap_xml) return
    const blob = new Blob([capDraft.cap_xml], { type: 'application/cap+xml' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `floodnet-cap-DRAFT-NOT-ISSUED-${capDraft.run_id}.xml`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }, [capDraft])

  // A draft belongs to the run it was generated from; if the user runs a new forecast, drop it rather
  // than leaving stale numbers on screen next to a different run.
  const staleDraft = capDraft != null && capDraft.run_id !== runId

  const segNameById = useMemo(() => {
    const m = new Map()
    for (const f of roads?.features || []) {
      if (f.properties?.seg_id != null) m.set(String(f.properties.seg_id), f.properties.name || f.properties.seg_id)
    }
    return m
  }, [roads])

  const priorities = useMemo(() => rankHotspots(series, segNameById, 5), [series, segNameById])
  const subjectLabel = priorities[0]?.name

  const rawAlerts = useMemo(() => {
    if (!run || !series) return []
    return computeAllAlerts({ series, route, currentT, subjectLabel })
  }, [run, series, route, currentT, subjectLabel])

  // Deduplicate depth alerts: If CRITICAL depth alert exists, keep the escalated CRITICAL alert
  // rather than showing two separate stacked cards for the same event.
  const alerts = useMemo(() => {
    const hasCriticalDepth = rawAlerts.some((a) => a.id === 'depth-critical')
    return rawAlerts.filter((a) => {
      if (a.id === 'depth-severe' && hasCriticalDepth) return false
      return true
    })
  }, [rawAlerts])

  const isHistorical = currentScenario?.source === 'historical' || run?.scenario_id === 'july2005'

  const getStateTag = (state) => {
    if (state === 'ACTIVE') return 'ACTIVE'
    if (state === 'RESOLVED') return 'RESOLVED'
    return isHistorical ? 'REPLAY EVENT' : 'FORECAST EVENT'
  }

  const handleAlertClick = (alert) => {
    if (alert.type === 'DEPTH' && priorities[0]) selectSegment(priorities[0].segId)
  }

  return (
    <section className={styles.section} data-tour="panel-alerts">
      <div className="panel-heading">
        Incident Advisories {alerts.length > 0 && <span className={styles.count}>({alerts.length})</span>}
      </div>

      {!run ? (
        <div className={styles.empty}>Run a forecast to generate incident advisories.</div>
      ) : alerts.length === 0 ? (
        <div className={styles.empty}>No critical thresholds crossed — conditions remain within manageable levels.</div>
      ) : (
        <ul className={styles.list}>
          {alerts.map((a, alertIdx) => {
            const stateLabel = getStateTag(a.state)
            const isActive = a.state === 'ACTIVE'
            return (
              <li
                key={a.id}
                // The guided briefing spotlights the top-priority alert specifically. computeAllAlerts()
                // already returns these most-severe-first, so index 0 IS the highest-priority real alert.
                data-tour={alertIdx === 0 ? 'alert-top' : undefined}
                className={styles.alert}
                style={{ borderLeftColor: a.color || 'var(--border)' }}
                onClick={() => handleAlertClick(a)}
              >
                <div className={styles.alertHead}>
                  <span className={styles.icon}>{a.icon}</span>
                  <span
                    className={styles.tierTag}
                    style={{ background: `${a.color}22`, color: a.color, border: `1px solid ${a.color}55` }}
                  >
                    {a.title}
                  </span>
                  <span className={`${styles.stateTag} ${isActive ? styles.stateTag_ACTIVE : ''}`}>
                    {isActive && <span className={styles.activeDot} />}
                    {stateLabel}
                  </span>
                </div>
                <div className={styles.body}>{describeAlert(a)}</div>
                <div className={styles.alertContextRow}>
                  <div className={styles.alertContextCol}>
                    <span className={styles.alertContextLabel}>CURRENT (T+{currentT}m)</span>
                    <span className={styles.alertContextVal}>
                      {a.currentValue != null && a.currentValue > 0 ? `${Math.round(a.currentValue)} ${a.unit || ''}` : 'Nominal / Dry'}
                    </span>
                  </div>
                  <div className={styles.alertContextCol}>
                    <span className={styles.alertContextLabel}>FORECAST PEAK</span>
                    <span className={styles.alertContextVal}>
                      {Math.round(a.peakValue)} {a.unit || ''} @ T+{Math.round(a.peakTMin)}m
                    </span>
                  </div>
                </div>
                {(a.tier === 'CRITICAL' || a.tier === 'SEVERE') && recommendedActions(a).length > 0 && (
                  <div className={styles.actions}>
                    <div className={styles.actionsHeader}>Recommended operator actions:</div>
                    {recommendedActions(a).map((line) => (
                      <div key={line} className={styles.actionLine}>• {line}</div>
                    ))}
                  </div>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {run && priorities.length > 0 && (
        <>
          <div className={`panel-heading ${styles.priorityHeading}`}>Top flood priorities</div>
          <ul className={styles.priorityList}>
            {priorities.map((p, i) => (
              <li key={p.segId} className={styles.priorityRow} onClick={() => selectSegment(p.segId)}>
                <span className={styles.priorityRank}>#{i + 1}</span>
                <span className={styles.priorityName}>{p.name}</span>
                <span className={styles.priorityCat} style={{ background: `${CATEGORY_COLOR[p.category]}22`, color: CATEGORY_COLOR[p.category] }}>
                  {p.category}
                </span>
                <span className={styles.priorityMeta}>
                  {fmt(p.peakDepthCm, 0)} cm &middot; +{fmt(p.peakTMin, 0)} min
                </span>
              </li>
            ))}
          </ul>
        </>
      )}

      {run && (
        <>
          <div className={`panel-heading ${styles.capHeading}`}>CAP alert draft</div>

          <div className={styles.capStatus}>
            <span className={styles.capStatusBadge}>DRAFT • NOT ISSUED</span>
            <span className={styles.capStatusNote}>Requires authorized government issuance.</span>
            <details className={styles.capWhy}>
              <summary>Why?</summary>
              FloodNet is not a designated alerting authority: it has no NDMA SACHET credential, no SMS or
              cell-broadcast channel and no subscriber data. The button produces a machine-generated OASIS
              CAP 1.2 file with <code>status=Draft</code> for an authorised officer to review, edit and issue
              through their own agency.
            </details>
          </div>

          <button
            type="button"
            className={styles.capButton}
            onClick={generateCap}
            disabled={capState === 'loading' || !runId}
          >
            {capState === 'loading'
              ? 'Generating CAP 1.2 draft…'
              : capDraft && !staleDraft
                ? 'Regenerate CAP alert draft'
                : 'Generate CAP alert draft'}
          </button>

          {capState === 'error' && (
            <div className={styles.capError}>Could not generate a draft: {capError}</div>
          )}

          {capDraft && staleDraft && (
            <div className={styles.capStale}>
              The draft on screen belongs to an earlier run ({capDraft.run_id}). Generate a new one for the
              current forecast.
            </div>
          )}

          {capDraft && !staleDraft && (
            <div className={styles.capDraft}>
              <div className={styles.capWatermark}>
                DRAFT · NOT ISSUED · {capDraft.message_class}
              </div>

              <div className={styles.capHeadline}>{capField(capDraft, 'headline')}</div>

              <div className={styles.capGrid}>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>CAP status</span>
                  <span className={styles.capValue}>{capDraft.cap?.status}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Scope</span>
                  <span className={styles.capValue}>{capDraft.cap?.scope}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Severity</span>
                  <span className={styles.capValue}>{capField(capDraft, 'severity')}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Urgency</span>
                  <span className={styles.capValue}>{capField(capDraft, 'urgency')}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Certainty</span>
                  <span className={styles.capValue}>{capField(capDraft, 'certainty')}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Onset</span>
                  <span className={styles.capValue}>{capField(capDraft, 'onset') || 'none forecast'}</span>
                </div>
              </div>

              <div className={styles.capSubLabel}>Derived from this run</div>
              <div className={styles.capGrid}>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Peak depth</span>
                  <span className={styles.capValue}>
                    {fmt(capDraft.model_basis?.peak_depth_cm, 1)} cm @ T+
                    {fmt(capDraft.model_basis?.peak_t_min, 0)}m
                  </span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Affected segments</span>
                  <span className={styles.capValue}>{capDraft.model_basis?.affected_segment_count}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Surcharging nodes</span>
                  <span className={styles.capValue}>{capDraft.model_basis?.surcharging_nodes_at_peak}</span>
                </div>
                <div className={styles.capCell}>
                  <span className={styles.capLabel}>Rainfall provenance</span>
                  <span className={styles.capValue}>
                    {capDraft.model_basis?.rainfall_provenance?.tag || 'UNKNOWN'}
                  </span>
                </div>
              </div>

              <div className={styles.capSubLabel}>Affected area (CAP areaDesc)</div>
              <div className={styles.capText}>{capDraft.cap?.info?.[0]?.area?.[0]?.areaDesc}</div>

              <div className={styles.capSubLabel}>Description</div>
              <div className={styles.capText}>{capField(capDraft, 'description')}</div>

              <div className={styles.capSubLabel}>Instruction to the reviewing officer</div>
              <div className={styles.capText}>{capField(capDraft, 'instruction')}</div>

              <div className={styles.capActions}>
                <button type="button" className={styles.capMiniButton} onClick={() => setShowXml((v) => !v)}>
                  {showXml ? 'Hide CAP XML' : 'Show CAP XML'}
                </button>
                <button type="button" className={styles.capMiniButton} onClick={copyXml}>
                  {copied ? 'Copied ✓' : 'Copy XML'}
                </button>
                <button type="button" className={styles.capMiniButton} onClick={downloadXml}>
                  Download .xml
                </button>
              </div>

              {showXml && <pre className={styles.capXml}>{capDraft.cap_xml}</pre>}

              <div className={styles.capFooter}>
                CAP {capDraft.cap_version} · model severity, not an official classification · not sent
                <InfoTip label="About this CAP draft">
                  {capDraft.cap_namespace}. Severity is derived from FloodNet’s own working depth bands (“WORKING
                  THRESHOLDS, NOT CITED GUIDANCE”) — a model classification, <b>not</b> an official flood-severity
                  classification from NDMA, IMD, CWC or MCGM. No India-specific CAP profile was found published, so
                  conformance is claimed to CAP 1.2 only.
                </InfoTip>
              </div>
            </div>
          )}
        </>
      )}

      {run && <div className={styles.disclaimer}>{ALERT_LABEL} — {ADVISORY_LABEL}</div>}
    </section>
  )
}
