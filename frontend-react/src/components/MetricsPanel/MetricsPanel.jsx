import { useMemo } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { fmt, fmtInt } from '../../lib/format.js'
import { DEFAULT_BANDS_CM, severityColor, severityOf } from '../../lib/severity.js'
import styles from './MetricsPanel.module.css'

// "Flooded" for the KPI threshold -- read out of shared bands table
const FLOODED_THRESHOLD_CM = DEFAULT_BANDS_CM.find(([, label]) => label === 'minor')?.[0] ?? DEFAULT_BANDS_CM[0][0]

export default function MetricsPanel() {
  const { run, frame, series, currentT, isStale } = useFloodNet()

  const hasData = Boolean(run && frame && !isStale)

  const streetFeatures = frame?.streets?.features || []
  const streetDepths = streetFeatures.map((f) => f.properties?.depth_cm).filter((d) => d != null)
  const maxDepthCm = streetDepths.length ? Math.max(...streetDepths) : frame?.depth_grid?.max_depth_cm ?? null
  const depthSeverity = maxDepthCm != null ? severityOf(maxDepthCm) : null

  const surchargingNodes = (frame?.nodes || []).filter((n) => n.surcharging).length
  const floodedSegments = streetFeatures.filter((f) => (f.properties?.depth_cm ?? 0) >= FLOODED_THRESHOLD_CM).length

  // Trend and peak metrics over entire forecast series
  const analysis = useMemo(() => {
    if (!series?.t_min?.length) return null

    const ts = series.t_min
    let idxNow = 0
    let minDiff = Infinity
    for (let i = 0; i < ts.length; i++) {
      const diff = Math.abs(ts[i] - currentT)
      if (diff < minDiff) {
        minDiff = diff
        idxNow = i
      }
    }

    let peakDepth = 0
    let peakDepthT = 0
    if (series.max_depth_cm?.length) {
      for (let i = 0; i < series.max_depth_cm.length; i++) {
        if (series.max_depth_cm[i] > peakDepth) {
          peakDepth = series.max_depth_cm[i]
          peakDepthT = ts[i]
        }
      }
    }

    if (idxNow === 0) {
      return {
        peakDepth: Math.round(peakDepth),
        peakDepthT,
        depthTrend: 'Baseline (T+0)',
        rainTrend: 'Baseline (T+0)',
        nodeTrend: '0',
        segTrend: '0',
      }
    }

    const idxPrev = idxNow - 1
    const dtMin = ts[idxNow] - ts[idxPrev]

    const dDepth = (series.max_depth_cm?.[idxNow] ?? 0) - (series.max_depth_cm?.[idxPrev] ?? 0)
    const dRain = (series.rain_mm_h?.[idxNow] ?? 0) - (series.rain_mm_h?.[idxPrev] ?? 0)
    const dNodes = (series.surcharging_count?.[idxNow] ?? 0) - (series.surcharging_count?.[idxPrev] ?? 0)
    const dSegs = (series.flooded_segments?.[idxNow] ?? 0) - (series.flooded_segments?.[idxPrev] ?? 0)

    const fmtDiff = (diff, unit = '') => {
      if (Math.abs(diff) < 0.1) return `Stable (vs -${dtMin}m)`
      const sign = diff > 0 ? '+ ' : '- '
      return `${sign}${Math.round(Math.abs(diff))}${unit ? ' ' + unit : ''}`
    }

    return {
      peakDepth: Math.round(peakDepth),
      peakDepthT,
      depthTrend: fmtDiff(dDepth, 'cm'),
      rainTrend: fmtDiff(dRain, 'mm/h'),
      nodeTrend: fmtDiff(dNodes),
      segTrend: fmtDiff(dSegs),
    }
  }, [series, currentT])

  return (
    <section className={styles.section} aria-labelledby="metrics-panel-heading" data-tour="panel-overview">
      <div className="panel-heading">
        <span id="metrics-panel-heading">Hydrodynamic metrics</span>
        {hasData && <span className={styles.tMin}>Frame: T+{currentT} min</span>}
      </div>

      {isStale ? (
        <div className={styles.empty}>
          <strong>Scenario inputs changed.</strong><br />
          Click &ldquo;Run forecast&rdquo; to generate results for the selected scenario.
        </div>
      ) : !hasData ? (
        <div className={styles.empty}>
          Run a forecast to view street inundation and drainage metrics.
        </div>
      ) : (
        <div className={styles.container}>
          {/* Section 1: CURRENT TIMESTEP */}
          <div className={styles.dominantKpi}>
            <div className={styles.dominantHeader}>
              <span className={styles.dominantLabel}>CURRENT TIMESTEP (T+{currentT} min)</span>
              <span className={styles.frameLabel}>Frame snapshot</span>
            </div>

            {/* Polite live region: these values change when the operator scrubs the timeline, which is
                not a change the screen-reader user would otherwise be told about. Scoped to the headline
                depth + severity + rate only -- the secondary row and peak box are deliberately NOT live
                so scrubbing does not flood the announcement queue. */}
            <div className={styles.dominantValueRow} role="status" aria-atomic="true">
              <div>
                <div
                  className={styles.dominantValue}
                  style={depthSeverity && maxDepthCm > 0 ? { color: severityColor(depthSeverity) } : undefined}
                >
                  {maxDepthCm != null ? fmt(maxDepthCm, 0) : '0'}
                  <span className={styles.dominantUnit}>cm</span>
                </div>
                <div className={styles.valueCaption}>Current modeled street depth</div>
              </div>
              <div className={styles.dominantMeta}>
                <div
                  className={styles.severityTag}
                  style={depthSeverity && maxDepthCm > 0 ? { color: severityColor(depthSeverity) } : undefined}
                >
                  {maxDepthCm === 0 || maxDepthCm == null ? 'NOMINAL / DRY' : `${depthSeverity.toUpperCase()} INUNDATION`}
                </div>
                {analysis?.depthTrend && (
                  <div className={styles.dominantTrend}>
                    Rate: {analysis.depthTrend}
                  </div>
                )}
              </div>
            </div>

            {/* Current secondary indicators */}
            <div className={styles.secondaryRow}>
              <div className={styles.secondaryItem}>
                <span className={styles.secondaryLabel}>Rainfall</span>
                <div className={styles.secondaryValue}>
                  {fmt(frame?.rain_mm_h, 0)} <span className={styles.secondaryUnit}>mm/h</span>
                </div>
                {analysis?.rainTrend && (
                  <span className={styles.secondarySub}>{analysis.rainTrend}</span>
                )}
              </div>

              <div className={styles.secondaryItem}>
                <span className={styles.secondaryLabel}>Flooded streets</span>
                <div className={styles.secondaryValue}>
                  {fmtInt(floodedSegments)} <span className={styles.secondaryUnit}>segs</span>
                </div>
                {analysis?.segTrend && (
                  <span className={styles.secondarySub}>{analysis.segTrend}</span>
                )}
              </div>

              <div className={styles.secondaryItem}>
                <span className={styles.secondaryLabel}>Surcharging</span>
                <div className={styles.secondaryValue}>
                  {fmtInt(surchargingNodes)} <span className={styles.secondaryUnit}>nodes</span>
                </div>
                {analysis?.nodeTrend && (
                  <span className={styles.secondarySub}>{analysis.nodeTrend}</span>
                )}
              </div>
            </div>
          </div>

          {/* Section 2: FORECAST PEAK */}
          {analysis && (
            <div className={styles.peakKpiBox}>
              <div className={styles.peakKpiHeader}>
                <span className={styles.peakKpiTitle}>FORECAST PEAK (0–180 min)</span>
                <span className={styles.peakKpiBadge}>Event envelope</span>
              </div>
              <div className={styles.peakKpiBody}>
                <div className={styles.peakKpiValue}>
                  <strong>{analysis.peakDepth} cm</strong>
                  <span className={styles.peakKpiTime}>at T+{analysis.peakDepthT} min</span>
                </div>
                <div className={styles.peakKpiNote}>
                  {analysis.peakDepth > 0
                    ? `Peak modeled inundation over 3h window. Current frame is at ${maxDepthCm ? Math.round((maxDepthCm / analysis.peakDepth) * 100) : 0}% of event peak.`
                    : 'No significant surface inundation projected across 180 min horizon.'}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  )
}

