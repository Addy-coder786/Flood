// Generic guided-tour controller, shared by the two tour features:
//   • ExploreTour     — homepage "Explore FloodNet" walkthrough (visual, explains the product)
//   • GuidedBriefing  — dashboard operational briefing (spoken, explains the CURRENT flood situation)
//
// It owns ONLY sequencing: which step is active, play/pause, and running each step's side effects. It holds
// no FloodNet data and knows nothing about maps, speech or narration -- callers supply steps whose `run()`
// does whatever that feature needs. That keeps this file reusable and keeps the two features from growing a
// second, competing copy of the app's state.
//
// A step is:
//   {
//     id:        string,                      // stable key
//     title:     string,                      // shown in the callout + progress
//     body:      string,                      // callout text
//     target:    string | null,               // CSS selector resolved at step time (null = centred callout)
//     run?:      (ctx) => void | Promise<void>,   // side effects: fly map, open panel, speak…
//     cleanup?:  () => void,                  // undo anything `run` set up
//     autoAdvanceMs?: number,                 // if set, advance automatically after this long
//     skip?:     () => boolean,               // return true to omit this step entirely (no fabricated content)
//   }
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

export function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function useTour(allSteps, { onExit } = {}) {
  // Steps whose `skip()` says they have no real data are dropped up front, so progress counts ("02 / 05")
  // stay honest and a step never renders an empty//fabricated callout.
  const steps = useMemo(() => (allSteps || []).filter((s) => !(typeof s.skip === 'function' && s.skip())), [allSteps])

  const [active, setActive] = useState(false)
  const [index, setIndex] = useState(0)
  const [paused, setPaused] = useState(false)

  const cleanupRef = useRef(null)
  const advanceTimerRef = useRef(null)
  const runTokenRef = useRef(0)
  const pausedRef = useRef(false)

  // `goTo` is only ever invoked from event handlers / async step callbacks, never during render, so keeping
  // the latest steps in a ref (written from an effect, not during render) is safe and keeps `goTo` stable.
  const stepsRef = useRef(steps)
  useEffect(() => { stepsRef.current = steps }, [steps])
  useEffect(() => { pausedRef.current = paused }, [paused])

  const clearAdvance = useCallback(() => {
    clearTimeout(advanceTimerRef.current)
    advanceTimerRef.current = null
  }, [])

  const runCleanup = useCallback(() => {
    try {
      cleanupRef.current?.()
    } catch {
      /* a failing cleanup must never trap the user inside the tour */
    }
    cleanupRef.current = null
  }, [])

  const stop = useCallback(() => {
    runTokenRef.current += 1 // invalidate any in-flight step
    clearAdvance()
    runCleanup()
    setActive(false)
    setPaused(false)
    setIndex(0)
    onExit?.()
  }, [clearAdvance, runCleanup, onExit])

  const start = useCallback(() => {
    // Starting a second tour must cleanly stop the first.
    runTokenRef.current += 1
    clearAdvance()
    runCleanup()
    setIndex(0)
    setPaused(false)
    setActive(true)
  }, [clearAdvance, runCleanup])

  const goTo = useCallback((next) => {
    const list = stepsRef.current
    if (!list.length) return
    if (next < 0) return
    if (next >= list.length) {
      stop()
      return
    }
    runTokenRef.current += 1
    clearAdvance()
    runCleanup()
    setIndex(next)
  }, [clearAdvance, runCleanup, stop])

  // The step-runner effect above needs `goTo` without taking it as a dependency (that would re-run every
  // step whenever `goTo` re-created). Written from an effect, read only inside an async callback.
  const goToRef = useRef(goTo)
  useEffect(() => { goToRef.current = goTo }, [goTo])

  const next = useCallback(() => goTo(index + 1), [goTo, index])
  const back = useCallback(() => goTo(Math.max(0, index - 1)), [goTo, index])
  const pause = useCallback(() => { clearAdvance(); setPaused(true) }, [clearAdvance])
  const resume = useCallback(() => setPaused(false), [])

  // ---- run the active step's side effects ------------------------------------------------------------
  useEffect(() => {
    if (!active) return
    const step = steps[index]
    if (!step) return

    const token = ++runTokenRef.current
    let cancelled = false

    const isCancelled = () => cancelled || token !== runTokenRef.current

    ;(async () => {
      let ok = true
      try {
        const maybeCleanup = await step.run?.({ isCancelled })
        if (isCancelled()) return
        cleanupRef.current = typeof maybeCleanup === 'function' ? maybeCleanup : step.cleanup ?? null
      } catch {
        // A step that fails (speech blocked, map not ready, panel missing) must not abort the tour --
        // the callout still shows and the user can advance manually.
        ok = false
        cleanupRef.current = step.cleanup ?? null
      }
      // `advanceOnComplete` is how a narrated step hands control back once its sentence has finished
      // speaking: the step's own `run()` resolves when speech ends, and the tour moves on from there. That
      // is what keeps narration and UI movement in step instead of racing a fixed timer. A paused tour
      // stays put -- the user resumes or presses Next.
      if (ok && step.advanceOnComplete && !isCancelled() && !pausedRef.current) {
        goToRef.current(index + 1)
      }
    })()

    return () => { cancelled = true }
  }, [active, index, steps])

  // ---- auto-advance ----------------------------------------------------------------------------------
  useEffect(() => {
    clearAdvance()
    if (!active || paused) return
    const step = steps[index]
    if (!step?.autoAdvanceMs) return
    advanceTimerRef.current = setTimeout(() => goTo(index + 1), step.autoAdvanceMs)
    return clearAdvance
  }, [active, paused, index, steps, goTo, clearAdvance])

  // ---- keyboard --------------------------------------------------------------------------------------
  useEffect(() => {
    if (!active) return
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); stop() }
      else if (e.key === 'ArrowRight') { e.preventDefault(); next() }
      else if (e.key === 'ArrowLeft') { e.preventDefault(); back() }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, stop, next, back])

  // ---- unmount ---------------------------------------------------------------------------------------
  useEffect(() => () => {
    clearAdvance()
    runCleanup()
  }, [clearAdvance, runCleanup])

  return {
    active, paused, index, steps,
    step: steps[index] ?? null,
    total: steps.length,
    start, stop, next, back, pause, resume, goTo,
  }
}
