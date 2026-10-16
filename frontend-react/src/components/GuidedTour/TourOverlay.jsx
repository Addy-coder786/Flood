// Shared presentation layer for both guided features (Explore FloodNet tour, Guided Briefing).
//
// Deliberate behaviours:
//  • The dim mask and the spotlight ring are `pointer-events: none`, so the map stays pannable and the page
//    stays scrollable during a tour. Only the callout card itself is interactive.
//  • If a step's target cannot be resolved (wrong view, panel collapsed, element not mounted), the callout
//    falls back to a centred card and the tour continues -- it never breaks and never invents a target.
//  • The target rect is re-measured on scroll/resize, and once more on a short timer after the step opens,
//    because several targets are inside panels that animate or lazily render.
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { prefersReducedMotion } from '../../lib/tour/useTour.js'
import styles from './TourOverlay.module.css'

const CALLOUT_W = 340
const GAP = 14
const PAD = 8   // breathing room between the target's own edge and the focus ring / dim cutout

/**
 * Resolve a step's target. `selector` may be a single CSS selector OR an ordered preference list: the first
 * entry that resolves to a laid-out element wins. That is what lets a tab step aim at the most specific real
 * element (the top alert card, the route result) and degrade to the tab's own content container when that
 * element genuinely does not exist — no alert, no route planned yet — instead of pointing at nothing.
 */
function measure(selector) {
  if (!selector) return null
  const list = Array.isArray(selector) ? selector : [selector]
  for (const sel of list) {
    let el = null
    try {
      el = document.querySelector(sel)
    } catch {
      continue // malformed selector: skip it rather than dropping the whole step
    }
    if (!el) continue
    const r = el.getBoundingClientRect()
    if (!r.width && !r.height) continue // present but not laid out (e.g. a hidden tab panel)
    return { top: r.top, left: r.left, width: r.width, height: r.height }
  }
  return null
}

/** Place the callout beside the target, flipping/clamping so it always stays fully on screen. */
function placeCallout(rect) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  if (!rect) {
    return { centred: true, top: Math.max(24, vh / 2 - 120), left: Math.max(16, vw / 2 - CALLOUT_W / 2) }
  }
  const spaceRight = vw - (rect.left + rect.width)
  const spaceBelow = vh - (rect.top + rect.height)

  let left
  if (spaceRight >= CALLOUT_W + GAP) left = rect.left + rect.width + GAP        // to the right
  else if (rect.left >= CALLOUT_W + GAP) left = rect.left - CALLOUT_W - GAP     // to the left
  else left = Math.min(Math.max(16, rect.left), vw - CALLOUT_W - 16)            // overlap, clamped

  let top = rect.top
  if (spaceBelow < 160 && rect.top > 200) top = rect.top + rect.height - 220
  top = Math.min(Math.max(16, top), Math.max(16, vh - 236))

  return { centred: false, top, left }
}

export default function TourOverlay({
  step, index, total, paused,
  onNext, onBack, onExit, onPause, onResume,
  dim = false,
  labels = {},
  accent,
  extraControls = null,
  statusLine = null,
}) {
  const [rect, setRect] = useState(null)
  const calloutRef = useRef(null)
  const reduced = prefersReducedMotion()

  const sync = useCallback(() => {
    setRect(measure(step?.target))
  }, [step?.target])

  useLayoutEffect(() => {
    sync()
    // Targets inside animating/lazily-mounted panels can settle a beat late; re-measure a few times.
    const timers = [60, 180, 420, 900].map((ms) => setTimeout(sync, ms))
    return () => timers.forEach(clearTimeout)
  }, [sync, step?.id])

  useEffect(() => {
    const onChange = () => sync()
    window.addEventListener('scroll', onChange, true)
    window.addEventListener('resize', onChange)
    return () => {
      window.removeEventListener('scroll', onChange, true)
      window.removeEventListener('resize', onChange)
    }
  }, [sync])

  // Move keyboard focus to the callout so Tab/Escape work immediately and screen readers announce the step.
  useEffect(() => {
    calloutRef.current?.focus?.({ preventScroll: true })
  }, [step?.id])

  if (!step) return null

  const pos = placeCallout(rect)
  const isLast = index >= total - 1
  const L = {
    back: 'Back', next: 'Next', finish: 'Finish', skip: 'Skip tour',
    pause: 'Pause', resume: 'Resume', exit: 'Exit',
    ...labels,
  }

  return (
    <div className={styles.layer} data-reduced={reduced ? 'true' : 'false'}>
      {/* Dimming is drawn as FOUR rectangles around the target rather than one sheet with a border on top.
          That leaves the target itself completely uncovered — full contrast, full colour, nothing washed
          over it — so it reads as lifted out of a softened page instead of merely outlined. With no target
          resolved we fall back to a single gentle sheet. */}
      {dim && (rect
        ? (
          <div aria-hidden="true">
            <div className={styles.dim} style={{ top: 0, left: 0, right: 0, height: Math.max(0, rect.top - PAD) }} />
            <div className={styles.dim} style={{ top: rect.top + rect.height + PAD, left: 0, right: 0, bottom: 0 }} />
            <div className={styles.dim} style={{ top: Math.max(0, rect.top - PAD), left: 0, width: Math.max(0, rect.left - PAD), height: rect.height + PAD * 2 }} />
            <div className={styles.dim} style={{ top: Math.max(0, rect.top - PAD), left: rect.left + rect.width + PAD, right: 0, height: rect.height + PAD * 2 }} />
          </div>
        )
        : <div className={styles.dim} style={{ inset: 0 }} aria-hidden="true" />
      )}

      {rect && (
        <div
          className={styles.spotlight}
          aria-hidden="true"
          style={{
            top: rect.top - PAD,
            left: rect.left - PAD,
            width: rect.width + PAD * 2,
            height: rect.height + PAD * 2,
            ...(accent ? { '--tour-accent': accent } : null),
          }}
        />
      )}

      <div
        ref={calloutRef}
        className={`${styles.callout} ${pos.centred ? styles.calloutCentred : ''}`}
        style={{ top: pos.top, left: pos.left, width: CALLOUT_W, ...(accent ? { '--tour-accent': accent } : null) }}
        role="dialog"
        data-tour-callout=""
        aria-modal="false"
        aria-label={`${step.title} — step ${index + 1} of ${total}`}
        tabIndex={-1}
      >
        <div className={styles.head}>
          <span className={styles.progress}>
            {String(index + 1).padStart(2, '0')} / {String(total).padStart(2, '0')}
          </span>
          <button type="button" className={styles.closeBtn} onClick={onExit} aria-label={L.skip}>
            ×
          </button>
        </div>

        <h3 className={styles.title}>{step.title}</h3>
        <p className={styles.body}>{step.body}</p>

        {statusLine && <div className={styles.status}>{statusLine}</div>}
        {extraControls}

        <div className={styles.controls}>
          <button
            type="button"
            className={styles.ghostBtn}
            onClick={onBack}
            disabled={index === 0}
          >
            {L.back}
          </button>

          {onPause && (
            <button type="button" className={styles.ghostBtn} onClick={paused ? onResume : onPause}>
              {paused ? L.resume : L.pause}
            </button>
          )}

          <button type="button" className={styles.primaryBtn} onClick={onNext}>
            {isLast ? L.finish : L.next}
          </button>
        </div>

        <button type="button" className={styles.skipLink} onClick={onExit}>
          {onPause ? L.exit : L.skip}
        </button>
      </div>
    </div>
  )
}
