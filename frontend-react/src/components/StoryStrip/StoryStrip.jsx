// Dashboard story header + interactive "How FloodNet works".
//
// The strip makes the pipeline visible at a glance (RAINFALL → FLOOD → DRAINAGE → ACTION). Clicking a stage
// opens the REAL feature behind it — the tab, the map layer, the timeline — and spotlights it with the same
// TourOverlay/useTour engine the Guided Briefing uses (no second tour engine, no demo data). Anything it
// switches on (a map layer, playback, the right-panel tab) is restored when the explainer closes.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { useTour } from '../../lib/tour/useTour.js'
import { waitForAnyTarget } from '../../lib/tour/tabSteps.js'
import TourOverlay from '../GuidedTour/TourOverlay.jsx'
import { PIPELINE_STAGES, STORY_HEADER } from '../../lib/story/stages.js'
import styles from './StoryStrip.module.css'

export default function StoryStrip({ openStage = null, onOpened, showStrip = true }) {
  const {
    layers, ensureLayers, restoreLayers, activeRightTab, setActiveRightTab, setPlaying, run, mapMode, setMapMode,
  } = useFloodNet()
  const hasRun = Boolean(run)

  // Snapshot of what the explainer may change, so closing it hands the dashboard back exactly as it was.
  const [before, setBefore] = useState(null)

  const steps = useMemo(() => PIPELINE_STAGES.map((stage) => ({
    id: `how-${stage.id}`,
    title: stage.title.en,
    target: stage.focus.target,
    body: stage.line.en,
    badge: stage.badge,
    run: async ({ isCancelled }) => {
      const { focus } = stage
      if (focus.tab) setActiveRightTab(focus.tab)
      setMapMode(focus.mode3d ? '3d' : '2d')
      ensureLayers(focus.layersOn || [])
      const playing = Boolean(focus.play && hasRun)
      if (playing) setPlaying(true)
      await waitForAnyTarget(focus.target)
      if (isCancelled()) return undefined
      return () => { if (playing) setPlaying(false) }
    },
  })), [setActiveRightTab, ensureLayers, setPlaying, setMapMode, hasRun])

  const onExit = useCallback(() => {
    setPlaying(false)
    if (before) {
      restoreLayers(before.layers)
      setActiveRightTab(before.tab)
      setMapMode(before.mapMode)
    }
    setBefore(null)
  }, [setPlaying, restoreLayers, setActiveRightTab, setMapMode, before])

  const tour = useTour(steps, { onExit })

  const open = useCallback((stageId) => {
    const i = PIPELINE_STAGES.findIndex((s) => s.id === stageId)
    if (i < 0) return
    if (!tour.active) {
      // Another tour (Guided Briefing / Demo Story / Explore) owns the screen: never stack a second overlay.
      if (document.querySelector('[data-tour-callout]')) return
      setBefore({ tab: activeRightTab, layers, mapMode })
      tour.start()
    }
    tour.goTo(i)
  }, [tour, activeRightTab, layers, mapMode])

  // Requested from the landing page: open once the dashboard has mounted and laid out.
  useEffect(() => {
    if (!openStage) return undefined
    const id = setTimeout(() => { open(openStage); onOpened?.() }, 450)
    return () => clearTimeout(id)
  }, [openStage, open, onOpened])

  const activeId = tour.active ? PIPELINE_STAGES[tour.index]?.id : null

  return (
    <>
      <nav className={styles.strip} aria-label="How FloodNet works" data-tour="story-strip" hidden={!showStrip && !tour.active}>
        {STORY_HEADER.map((group, gi) => {
          const on = group.stages.includes(activeId)
          return (
            <span key={group.label} className={styles.group}>
              {gi > 0 && <span className={styles.arrow} aria-hidden="true">→</span>}
              <button
                type="button"
                className={`${styles.chip} ${on ? styles.chipOn : ''}`}
                onClick={() => open(group.stages[0])}
                aria-pressed={on}
                title={group.stages.map((id) => PIPELINE_STAGES.find((s) => s.id === id)?.title.en).join(' · ')}
              >
                {group.label}
              </button>
            </span>
          )
        })}
        <button type="button" className={styles.how} onClick={() => open('rain')}>How it works</button>
      </nav>

      {tour.active && tour.step && (
        <TourOverlay
          step={tour.step}
          index={tour.index}
          total={tour.total}
          paused={false}
          onNext={tour.next}
          onBack={tour.back}
          onExit={tour.stop}
          dim
          statusLine={(
            <span className={styles.stageRow}>
              <span className={`tag-badge tag-${tour.step.badge.tag}`}>{tour.step.badge.text}</span>
              <span className={styles.dots} role="tablist" aria-label="Pipeline stages">
                {PIPELINE_STAGES.map((s, i) => (
                  <button
                    key={s.id}
                    type="button"
                    role="tab"
                    aria-selected={i === tour.index}
                    className={`${styles.dot} ${i === tour.index ? styles.dotOn : ''}`}
                    onClick={() => tour.goTo(i)}
                    title={s.title.en}
                  >
                    <span aria-hidden="true">{s.glyph}</span>
                  </button>
                ))}
              </span>
            </span>
          )}
        />
      )}
    </>
  )
}
