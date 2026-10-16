import { useCallback, useMemo } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import styles from './ForecastTimeline.module.css'

const W = 1000
const H = 68

const TICKS = [
  { t: 0, label: 'NOW' },
  { t: 15, label: '+15' },
  { t: 30, label: '+30' },
  { t: 60, label: '+60' },
  { t: 90, label: '+90' },
  { t: 120, label: '+120' },
  { t: 180, label: '+180 MIN' },
]

export default function ForecastTimeline() {
  const { run, series, compareResult, currentT, setCurrentT, playing, setPlaying } = useFloodNet()

  const ts = run?.frames_t_min || []
  const tMin = ts.length ? ts[0] : 0
  const tMax = ts.length ? ts[ts.length - 1] : 180
  const step = ts.length > 1 ? ts[1] - ts[0] : 5

  const x = useCallback((t) => ((t - tMin) / Math.max(1, tMax - tMin)) * W, [tMin, tMax])
  const cursorX = x(currentT)

  // Sourced from the RUN's own actual per-frame rainfall (series.rain_mm_h, from the real engine frames --
  // see api/main.py's /series endpoint), never from the static scenario table (`currentScenario`). The
  // static table only covers the 4 predefined scenarios (moderate/heavy/cloudburst/july2005) -- using it here
  // left the "Rain input" hyetograph silently EMPTY for live/ECMWF runs, which have no entry in that table
  // despite the real per-frame rain data existing on every run via `series`.
  const rainData = useMemo(() => {
    if (!series?.t_min?.length || !series?.rain_mm_h?.length) return { bars: [], imax: 0, total: 0 }
    const t = series.t_min
    const vals = series.rain_mm_h
    const imax = Math.max(1, ...vals)
    const n = t.length
    const bw = Math.max(2, W / n - 1.5)
    const dtMin = n > 1 ? t[1] - t[0] : step || 5
    const bars = t.map((tt, i) => ({
      x: ((tt - tMin) / Math.max(1, tMax - tMin)) * W,
      w: bw,
      h: (vals[i] / imax) * (H * 0.42),
      val: vals[i],
      t: tt,
    }))
    const total = vals.reduce((sum, v) => sum + v * (dtMin / 60), 0)
    return { bars, imax, total }
  }, [series, tMin, tMax, step])

  const depthLines = useMemo(() => {
    const lines = []
    if (compareResult) {
      const t = compareResult.t_min || compareResult.frames_t_min
      lines.push({ t, y: compareResult.normal.flooded_segments, color: '#2563eb', label: 'normal drainage' })
      lines.push({ t, y: compareResult.blocked.flooded_segments, color: '#ea580c', label: 'blocked drainage' })
    } else if (series) {
      lines.push({ t: series.t_min, y: series.max_depth_cm, color: '#2563eb', label: 'modeled max depth' })
    }
    return lines
  }, [series, compareResult])

  const ymax = Math.max(5, ...depthLines.flatMap((l) => l.y || []))

  // Find Peak Depth and Peak Time from active series
  const peak = useMemo(() => {
    if (!series?.max_depth_cm?.length) return null
    let maxVal = -1
    let maxIdx = 0
    for (let i = 0; i < series.max_depth_cm.length; i++) {
      if (series.max_depth_cm[i] > maxVal) {
        maxVal = series.max_depth_cm[i]
        maxIdx = i
      }
    }
    const peakT = series.t_min[maxIdx]
    return {
      val: Math.round(maxVal),
      t: peakT,
      x: x(peakT),
      y: H - 4 - (maxVal / ymax) * (H - 14),
    }
  }, [series, ymax, x])

  // Dynamic 1-line interpretation strictly derived from model data
  const interpretation = useMemo(() => {
    if (!run) return null
    if (currentT === 0) return 'T+0 Initial dry baseline condition before runoff accumulation.'
    if (peak && Math.abs(currentT - peak.t) <= (step || 5)) {
      return `Peak modeled flood depth of ${peak.val} cm reached at T+${peak.t} min.`
    }
    if (peak && currentT < peak.t) {
      return `Flood depth is increasing towards peak inundation (${peak.val} cm at T+${peak.t} min).`
    }
    if (peak && currentT > peak.t && currentT < tMax) {
      return `Surface water is receding from peak (${peak.val} cm) via gravity drainage.`
    }
    if (currentT >= tMax) {
      return 'Forecast horizon complete (180 min). Residual ponding in low-elevation sumps.'
    }
    return `Simulated flood state at T+${currentT} min.`
  }, [run, currentT, peak, step, tMax])

  const toPath = (l) =>
    (l.y || [])
      .map((v, i) => `${i ? 'L' : 'M'}${x(l.t[i])},${H - 4 - (v / ymax) * (H - 14)}`)
      .join(' ')

  const toAreaPath = (l) => {
    const pts = (l.y || []).map((v, i) => `${i ? 'L' : 'M'}${x(l.t[i])},${H - 4 - (v / ymax) * (H - 14)}`)
    if (!pts.length) return ''
    return `${pts.join(' ')} L${x(l.t[l.t.length - 1])},${H} L${x(l.t[0])},${H} Z`
  }

  const handlePlay = () => {
    if (!run) return
    setPlaying(!playing)
  }

  // Modelled max street depth at the selected time (same series the chart draws), for the clock readout.
  const iNow = series?.t_min?.indexOf(currentT) ?? -1
  const depthNow = iNow >= 0 && Number.isFinite(series?.max_depth_cm?.[iNow]) ? Math.round(series.max_depth_cm[iNow]) : null

  return (
    <div className={`${styles.bar} glass-panel`} data-tour="timeline">
      <button
        className={`btn btn-primary ${styles.playBtn}`}
        onClick={handlePlay}
        disabled={!run}
        title={playing ? 'Pause forecast playback' : 'Play forecast evolution'}
        aria-label={playing ? 'Pause' : 'Play'}
      >
        {playing ? '❚❚' : '▶'}
      </button>

      <div className={styles.body}>
        <div className={styles.topRow}>
          <div className={styles.headerLeft}>
            <span className={styles.titleText}>Forecast Timeline</span>
            {interpretation && <span className={styles.interpretationText}>&middot; {interpretation}</span>}
          </div>

          <div className={styles.headerRight}>
            {run && (
              <div className={styles.legendWrap}>
                <span className={styles.legendItem}>
                  <span className={styles.legendBarSample} /> Rain input
                </span>
                <span className={styles.legendItem}>
                  <span className={styles.legendLineSample} /> {compareResult ? 'Normal vs Blocked' : 'Modeled flood depth'}
                </span>
                {peak && (
                  <span className={styles.peakBadge}>
                    Peak: {peak.val} cm @ T+{peak.t}m
                  </span>
                )}
              </div>
            )}
          </div>
        </div>

        <div className={styles.chartWrap}>
          {run ? (
            <>
              <svg className={styles.chart} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
                <defs>
                  <linearGradient id="depthGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#2563eb" stopOpacity="0.28" />
                    <stop offset="100%" stopColor="#2563eb" stopOpacity="0.03" />
                  </linearGradient>
                </defs>

                {/* Rain bars (hyetograph) - subtle neutral slate */}
                {rainData.bars.map((b, i) => (
                  <rect
                    key={i}
                    x={b.x}
                    y={H * 0.42 - b.h}
                    width={b.w}
                    height={b.h}
                    fill="#94a3b8"
                    stroke="#64748b"
                    strokeWidth="0.5"
                    opacity="0.65"
                    rx="1"
                  />
                ))}

                {/* Flood depth area fill & hydrograph line */}
                {depthLines.map((l) => (
                  <g key={l.label}>
                    {!compareResult && (
                      <path d={toAreaPath(l)} fill="url(#depthGrad)" />
                    )}
                    <path d={toPath(l)} fill="none" stroke={l.color} strokeWidth="2.4" strokeLinecap="round" />
                  </g>
                ))}

                {/* Peak Depth Marker */}
                {peak && !compareResult && (
                  <g className={styles.peakMarkerGroup}>
                    <line
                      x1={peak.x}
                      x2={peak.x}
                      y1={peak.y}
                      y2={H}
                      stroke="#C4273D"
                      strokeWidth="1.2"
                      strokeDasharray="3 3"
                      opacity="0.8"
                    />
                    <circle cx={peak.x} cy={peak.y} r="4" fill="#C4273D" stroke="#ffffff" strokeWidth="1.5" />
                  </g>
                )}

                {/* Active Time cursor */}
                <line x1={cursorX} x2={cursorX} y1="0" y2={H} stroke="#1d1d1f" strokeWidth="2" opacity="0.9" />
                <circle cx={cursorX} cy="4" r="4" fill="#2563eb" stroke="#ffffff" strokeWidth="1.5" />
              </svg>
              <div className={styles.futureShade} style={{ width: `${Math.max(0, 100 - (cursorX / W) * 100)}%` }} />
            </>
          ) : (
            <div className={styles.empty}>Run a forecast to see the 0&ndash;180 min timeline</div>
          )}
        </div>

        {/* Time slider */}
        <input
          className={styles.slider}
          type="range"
          min={tMin}
          max={tMax}
          step={step || 5}
          value={currentT}
          disabled={!run}
          onChange={(e) => setCurrentT(Number(e.target.value))}
          aria-label="Forecast timestep slider"
        />

        {/* Timeline tick labels */}
        <div className={styles.ticksRow}>
          {TICKS.map(({ t, label }) => (
            <span
              key={t}
              className={`${styles.tick} ${Math.abs(t - currentT) <= 7.5 ? styles.tickActive : ''}`}
            >
              {label}
            </span>
          ))}
        </div>
      </div>

      <div className={styles.clock}>
        <div className={styles.clockValue}>{run ? `T+${currentT} min` : '—'}</div>
        <div className={styles.clockUnit}>
          {!run
            ? 'No active run'
            : currentT === 0
              ? 'Start of forecast'
              : depthNow != null
                ? `${depthNow} cm max depth`
                : `${currentT} min elapsed`}
        </div>
      </div>
    </div>
  )
}
