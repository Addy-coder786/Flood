import { useState, useEffect } from 'react'
import { useFloodNet, SIMULATE_STAGES } from '../../state/FloodNetContext.jsx'
import styles from './LoadingOverlay.module.css'

/** Phase 6: a synchronous ~60-120s /api/simulate call has no incremental-progress signal, so this shows
 * WHICH real engine stage (backend/floodnet/simulation/engine.py's actual coupling loop) is running,
 * cycling on a timer with an honest elapsed runtime counter -- never a fabricated completion percentage. */
export default function LoadingOverlay() {
  const { simulating, simStageIdx } = useFloodNet()
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!simulating) return
    const t0 = Date.now()
    const timer = setInterval(() => {
      setElapsed(Math.floor((Date.now() - t0) / 1000))
    }, 1000)
    return () => {
      clearInterval(timer)
      setElapsed(0)
    }
  }, [simulating])

  if (!simulating) return null

  return (
    <div className={styles.overlay}>
      <div className={`${styles.card} glass-panel`}>
        <div className={styles.spinner} />
        <div className={styles.title}>GENERATING FLOOD FORECAST</div>
        <div className={styles.sub}>
          Running the coupled rainfall &rarr; runoff &rarr; drainage &rarr; street-depth model over the 0&ndash;180 min window
        </div>
        <div className={styles.elapsedBadge}>
          Elapsed runtime: <strong>{elapsed}s</strong>
        </div>
        <div className={styles.stageList}>
          {SIMULATE_STAGES.map((label, i) => {
            const cls = i === simStageIdx ? styles.stageActive : i < simStageIdx ? styles.stageDone : ''
            return (
              <div key={label} className={`${styles.stage} ${cls}`}>
                <span className={styles.dot} />
                {label}
              </div>
            )
          })}
        </div>
        <div className={styles.note}>
          Usually about a minute. Stages show the computation order, not a percentage.
        </div>
      </div>
    </div>
  )
}
