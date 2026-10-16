// "Explore FloodNet" — the homepage walkthrough that explains the PRODUCT (what this is, what you can see,
// what you can do), as opposed to GuidedBriefing which explains the CURRENT flood situation.
//
// It deliberately points at the real interface rather than mock screenshots: step 1 runs on the landing
// page, then the tour navigates into the actual control centre for the map / timeline / alerts / drainage /
// routing steps, and offers a route back to the homepage at the end. No dashboard content is duplicated for
// the tour's benefit.
//
// It is visual-only by design (the brief keeps speech with the separate Guided Briefing), but the step
// content is plain data here, so a future voice layer can read `body` without touching this component.
import { useCallback, useEffect, useMemo } from 'react'
import { useTour } from '../../lib/tour/useTour.js'
import { TAB_STEPS, waitForAnyTarget } from '../../lib/tour/tabSteps.js'
import TourOverlay from '../GuidedTour/TourOverlay.jsx'

const NAV_SETTLE_MS = 620 // App's landing→dashboard transition runs on a 360ms timer; wait it out, then some

/** Wait until a selector resolves to a laid-out element, or give up. Keeps steps robust against panels
 *  that mount a beat after the view switches, without resorting to fixed sleeps everywhere. */
function waitForTarget(selector, timeoutMs = 1500) {
  return new Promise((resolve) => {
    const started = Date.now()
    const tick = () => {
      let el = null
      try { el = document.querySelector(selector) } catch { el = null }
      if (el) {
        const r = el.getBoundingClientRect()
        if (r.width || r.height) return resolve(true)
      }
      if (Date.now() - started > timeoutMs) return resolve(false)
      setTimeout(tick, 80)
    }
    tick()
  })
}

export default function ExploreTour({ active, view, onGoDashboard, onGoLanding, onClose, setTab }) {
  const goDashboard = useCallback(async () => {
    if (view !== 'dashboard') {
      onGoDashboard?.()
      await new Promise((r) => setTimeout(r, NAV_SETTLE_MS))
    }
  }, [view, onGoDashboard])

  const steps = useMemo(() => [
    {
      id: 'what',
      title: 'What is FloodNet?',
      target: '[data-tour="hero"]',
      body: 'FloodNet is an urban flood forecasting and decision-support system. It converts rainfall, '
        + 'terrain and the city drainage network into street-level flood insight for the Mumbai '
        + 'Hindmata–Dadar pilot area.',
      run: async () => { onGoLanding?.() },
    },
    {
      id: 'map',
      title: 'Live Flood Map',
      target: '[data-tour="map"]',
      body: 'The map shows where flooding is predicted — affected street segments coloured by predicted '
        + 'water depth in centimetres. Switch to 3D to see the terrain model the forecast runs on.',
      run: async () => {
        await goDashboard()
        await waitForTarget('[data-tour="map"]')
      },
    },
    {
      id: 'forecast',
      title: 'Forecast Timeline',
      target: '[data-tour="timeline"]',
      body: 'Move through the 0–3 hour forecast window to see how conditions evolve. Each step is a real '
        + 'model frame at five-minute resolution, not an animation.',
      run: async () => {
        await goDashboard()
        await waitForTarget('[data-tour="timeline"]')
      },
    },
    {
      id: 'rainfall',
      title: 'Rainfall Source',
      target: ['[data-tour="rainfall-source"]', '[data-tour="left-panel"]'],
      body: 'Pick the rainfall that drives the forecast. FloodNet can use live IMD observations and '
        + 'radar-derived rainfall inputs when available.',
      run: async () => {
        await goDashboard()
        await waitForAnyTarget(['[data-tour="rainfall-source"]', '[data-tour="left-panel"]'])
      },
    },
    // ── Control Centre Navigation: one compact step per real tab ──────────────────────────────────
    // Built from the SAME metadata the Guided Briefing uses (lib/tour/tabSteps.js), so the two features can
    // never drift apart. Each one activates the real tab, waits for its content, and points at what is
    // inside it — never at the nav button. Kept to one short line each: this is orientation, not a reading
    // of the dashboard.
    ...TAB_STEPS.map((t) => ({
      id: `explore-${t.id}`,
      title: t.title.en,
      target: t.target,
      body: t.short.en,
      run: async () => {
        await goDashboard()
        setTab?.(t.tab)
        await waitForAnyTarget(t.target)
      },
    })),
    {
      id: 'workflow',
      title: 'Your Decision Dashboard',
      target: '[data-tour="map"]',
      body: 'Forecast, flood depth, drainage stress, alerts and flood-aware routing sit in one operational view, '
        + 'each value carrying its own data provenance. You’re ready to explore.',
      run: async () => { await goDashboard() },
    },
  ], [goDashboard, onGoLanding, setTab])

  const tour = useTour(steps, { onExit: onClose })

  // Drive the tour from the `active` prop so the CTA stays the single source of truth. Done in an effect,
  // never during render, so this never starts/stops a tour as a render side effect.
  const { active: tourActive, start: startTour, stop: stopTour } = tour
  useEffect(() => {
    if (active && !tourActive) startTour()
    else if (!active && tourActive) stopTour()
  }, [active, tourActive, startTour, stopTour])

  if (!tour.active || !tour.step) return null

  return (
    <TourOverlay
      step={tour.step}
      index={tour.index}
      total={tour.total}
      dim
      onNext={tour.next}
      onBack={tour.back}
      onExit={tour.stop}
      labels={{ back: 'Back', next: 'Next', finish: 'Finish', skip: 'Skip tour' }}
      statusLine={
        tour.index === tour.total - 1
          ? 'You can return to the homepage from the FloodNet logo in the header.'
          : null
      }
    />
  )
}
