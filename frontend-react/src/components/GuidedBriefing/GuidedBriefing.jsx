// FloodNet Guided Briefing — an operational, spoken walkthrough of the CURRENT flood situation.
//
// It does not compute anything. Every value it speaks comes from `buildBriefingFacts()`, which reads only
// data the dashboard already fetched from the real API; every UI move it makes goes through existing
// Context actions (setCurrentT, setActiveRightTab, selectSegment, issueMapCommand). If a section has no
// real data, its step is skipped rather than narrated with filler.
//
// Scientific guardrails are enforced in lib/briefing/narration.js, not here: no local causal attribution,
// no traffic claims, no AI/ML claims.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { useTour } from '../../lib/tour/useTour.js'
import { TAB_STEPS, waitForAnyTarget } from '../../lib/tour/tabSteps.js'
import TourOverlay from '../GuidedTour/TourOverlay.jsx'
import { buildBriefingFacts } from '../../lib/briefing/facts.js'
import { LANGUAGES, UI_TEXT, narrate } from '../../lib/briefing/narration.js'
import { speechAvailable } from '../../lib/briefing/speech.js'
import { DEMO_STORY, STORY_UI, TERRAIN_NARRATION, stageById, localized } from '../../lib/story/stages.js'
import * as voice from '../../lib/briefing/voice.js'
import { VOICE_MODE } from '../../lib/briefing/voice.js'
import styles from './GuidedBriefing.module.css'

const MOMENT_GAP_MS = 900 // breathing room between forecast moments when speech is unavailable

export default function GuidedBriefing() {
  const {
    run, series, frame, route, alternatives, currentT, setCurrentT,
    currentScenario, meta, setActiveRightTab, selectSegment, issueMapCommand, setPlaying,
    layers, ensureLayers, restoreLayers, mapMode, setMapMode, setTerrainFocus,
  } = useFloodNet()

  // 'briefing' = the operational situation briefing; 'story' = Demo Story, the spoken technical walkthrough
  // (rain -> terrain -> runoff -> drainage -> flood -> why -> alerts -> route). Same engine, same overlay.
  const [mode, setMode] = useState('briefing')
  const [layersAtStart, setLayersAtStart] = useState(null)   // restored when Demo Story ends
  const [modeAtStart, setModeAtStart] = useState('2d')       // 2D | 3D, restored when the tour ends

  const [lang, setLang] = useState('en')
  const [open, setOpen] = useState(false) // launcher expanded (language choice visible)
  const [voiceMode, setVoiceMode] = useState(null)   // which voice actually produced the last sentence
  const [speaking, setSpeaking] = useState(false)

  const T = UI_TEXT[lang]
  const langTag = LANGUAGES.find((l) => l.code === lang)?.voice ?? 'en-IN'

  const facts = useMemo(
    () => buildBriefingFacts({ run, series, frame, route, alternatives, currentT, scenario: currentScenario, meta }),
    [run, series, frame, route, alternatives, currentT, currentScenario, meta],
  )

  // Facts are FROZEN when the briefing starts and held for its duration: the briefing moves the timeline
  // itself, so recomputing from the live `currentT` mid-run would make step 1's numbers drift away from the
  // narration that already described them. Held in state (not a ref) so `steps` can depend on it honestly.
  const [frozenFacts, setFrozenFacts] = useState(null)

  // `speakStep` resolves only once the sentence has finished. Each step's `run()` awaits it and the tour
  // advances on completion (see `advanceOnComplete`), which is what keeps narration and UI movement in
  // step rather than racing a fixed timer.
  const speakStep = useCallback(async (text, isCancelled) => {
    if (!text) return
    setSpeaking(true)
    try {
      const mode = await voice.speak(text, { lang, langTag })
      if (!isCancelled?.()) setVoiceMode(mode)
      // With no voice at all, still pace the visual walkthrough so steps do not flash past.
      if (mode === VOICE_MODE.NONE && !isCancelled?.()) {
        await new Promise((r) => setTimeout(r, 2200))
      }
    } finally {
      setSpeaking(false)
    }
  }, [lang, langTag])

  // ---------------------------------------------------------------- steps
  const steps = useMemo(() => {
    const f = frozenFacts
    if (!f?.ready) return []

    const list = []

    if (mode === 'story') {
      const say = (...parts) => parts.filter(Boolean).join(' ')
      const enable = (stage) => ensureLayers(stage.focus.layersOn || [])
      const moments = f.moments || []
      const textFor = {
        'story-rain': say(localized(stageById('rain').line, lang), narrate.rainSource(f, lang)),
        'story-terrain': localized(TERRAIN_NARRATION, lang),
        'story-runoff': localized(stageById('runoff').line, lang),
        'story-drainage': say(localized(stageById('drainage').line, lang), narrate.drainage(f, lang)),
        'story-flood': moments.map((m) => narrate.moment(f, m, lang)).join(' '),
        'story-why': narrate.where(f, lang),
        'story-alerts': narrate.alerts(f, lang),
        'story-route': say(localized(stageById('action').line, lang), narrate.routing(f, lang)),
      }
      const targetFor = {
        'story-terrain': ['[data-tour="terrain-3d"]', '[data-tour="map"]'],
        'story-flood': '[data-tour="timeline"]',
        'story-why': ['[data-tour="panel-why"]', '#rtab-why'],
        'story-alerts': ['[data-tour="alert-top"]', '[data-tour="panel-alerts"]', '#rtab-alerts'],
        'story-route': ['[data-tour="route-result"]', '[data-tour="route-controls"]', '#rtab-routing'],
      }
      for (const item of DEMO_STORY) {
        const stage = item.stage ? stageById(item.stage) : null
        const text = textFor[item.id]
        const target = targetFor[item.id] ?? stage?.focus.target
        list.push({
          id: item.id,
          title: localized(item.title, lang),
          target,
          body: text || '',
          skip: () => !text,                      // no alert / no forecast moments -> the step is omitted
          run: async ({ isCancelled }) => {
            if (stage) {
              if (stage.focus.tab) setActiveRightTab(stage.focus.tab)
              enable(stage)
            }
            if (item.id === 'story-terrain' || item.id === 'story-runoff' || item.id === 'story-flood') setActiveRightTab('overview')
            // the 3D view of the model's own DEM, aimed at the worst-affected real location; every other
            // step goes back to the 2D map it describes
            setMapMode(item.id === 'story-terrain' ? '3d' : '2d')
            if (item.id === 'story-terrain') setTerrainFocus(f.deepest?.at ? { lat: f.deepest.at.lat, lng: f.deepest.at.lng } : null)
            if (item.id === 'story-why') {
              setActiveRightTab('why')
              if (f.deepest?.segId) {
                issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: [f.deepest.segId] })
                selectSegment(f.deepest.segId)
              }
            }
            if (item.id === 'story-alerts') setActiveRightTab('alerts')
            if (item.id === 'story-route') {
              setActiveRightTab('routing')
              const segs = f.routing?.recommended?.segments
              if (segs?.length) issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: segs, fit: false })
            }
            if (item.id === 'story-flood') {
              setPlaying(false)
              for (const m of moments) {
                if (isCancelled()) return
                setCurrentT(m.tMin)
                await speakStep(narrate.moment(f, m, lang), isCancelled)
                if (isCancelled()) return
                if (!speechAvailable()) await new Promise((r) => setTimeout(r, MOMENT_GAP_MS))
              }
              return
            }
            if (target) await waitForAnyTarget(target)
            if (isCancelled()) return
            await speakStep(text, isCancelled)
          },
          advanceOnComplete: item.id !== 'story-route',
        })
      }
      return list
    }

    // One factory for every tab-orientation step (metadata lives in lib/tour/tabSteps.js, shared with the
    // Explore tour). The sequence is mandatory and identical for all five: activate the real tab, wait for
    // its content to actually render, then narrate — so the user is always looking at the thing being
    // described, never at a nav label while another panel is discussed.
    const tabStep = (id) => {
      const t = TAB_STEPS.find((x) => x.id === id)
      return {
        id: t.id,
        title: t.title[lang] ?? t.title.en,
        target: t.target,
        body: t.narration[lang] ?? t.narration.en,
        run: async ({ isCancelled }) => {
          setActiveRightTab(t.tab)
          await waitForAnyTarget(t.target)
          if (isCancelled()) return
          await speakStep(t.narration[lang] ?? t.narration.en, isCancelled)
        },
        advanceOnComplete: true,
      }
    }

    // 01 — situation: frame the whole picture, then focus the map where it actually matters.
    list.push({
      id: 'situation',
      title: T.steps[0],
      target: '[data-tour="map"]',
      body: narrate.situation(f, lang),
      run: async ({ isCancelled }) => {
        setActiveRightTab('overview')
        if (f.deepest?.at) {
          issueMapCommand({ type: 'MAP_FLY_TO', lat: f.deepest.at.lat, lng: f.deepest.at.lng, zoom: 15 })
        }
        if (f.topSegments?.length) {
          issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: f.topSegments.map((s) => s.segId), fit: false })
        }
        await speakStep(narrate.situation(f, lang), isCancelled)
      },
      advanceOnComplete: true,
    })

    // terrain: the SAME DEM the solver runs on, in 3D, aimed at the worst-affected real location. Fixed wording;
    // no elevation value is spoken.
    list.push({
      id: 'terrain',
      title: localized(stageById('surface').title, lang),
      target: ['[data-tour="terrain-3d"]', '[data-tour="map"]'],
      body: localized(TERRAIN_NARRATION, lang),
      run: async ({ isCancelled }) => {
        setActiveRightTab('overview')
        ensureLayers(['depth'])
        setTerrainFocus(f.deepest?.at ? { lat: f.deepest.at.lat, lng: f.deepest.at.lng } : null)
        setMapMode('3d')
        await waitForAnyTarget(['[data-tour="terrain-3d"]'], 4000)
        if (isCancelled()) return undefined
        await speakStep(localized(TERRAIN_NARRATION, lang), isCancelled)
        return () => setMapMode('2d')     // the following steps describe the 2D map
      },
      cleanup: () => setMapMode('2d'),
      advanceOnComplete: true,
    })

    // 02 — OVERVIEW tab orientation.
    list.push(tabStep('tab-overview'))

    // 03 — alerts: skipped entirely when there is no alert, rather than narrating "no alerts" as if it were one.
    const alertText = narrate.alerts(f, lang)
    list.push({
      id: 'alerts',
      title: T.steps[1],
      // Point at the actual highest-priority alert card; degrade to the panel if there is no alert.
      target: ['[data-tour="alert-top"]', '[data-tour="panel-alerts"]', '#rtab-alerts'],
      body: alertText || '',
      skip: () => !alertText,
      run: async ({ isCancelled }) => {
        setActiveRightTab('alerts')
        await waitForAnyTarget(['[data-tour="alert-top"]', '[data-tour="panel-alerts"]'])
        if (isCancelled()) return
        await speakStep(alertText, isCancelled)
      },
      advanceOnComplete: true,
    })

    // 03 — forecast: walk the real timeline through MEANINGFUL moments only (band crossings + the run's
    // peak), narrating each. Not every frame — 37 frames of speech would be noise.
    const moments = f.moments || []
    list.push({
      id: 'forecast',
      title: T.steps[2],
      target: '[data-tour="timeline"]',
      body: moments.length
        ? moments.map((m) => narrate.moment(f, m, lang)).join(' ')
        : '',
      skip: () => moments.length === 0,
      run: async ({ isCancelled }) => {
        setPlaying(false) // never fight the user's own playback
        setActiveRightTab('overview')   // the metrics that move with the timeline, not the Alerts tab
        for (const m of moments) {
          if (isCancelled()) return
          setCurrentT(m.tMin)
          await speakStep(narrate.moment(f, m, lang), isCancelled)
          if (isCancelled()) return
          if (!speechAvailable()) await new Promise((r) => setTimeout(r, MOMENT_GAP_MS))
        }
      },
      advanceOnComplete: true,
    })

    // 04 — where: the single most affected real segment. Selecting it drives the existing Why-flooded flow.
    const whereText = narrate.where(f, lang)
    list.push({
      id: 'where',
      title: T.steps[3],
      target: '[data-tour="map"]',
      body: whereText || '',
      skip: () => !whereText,
      run: async ({ isCancelled }) => {
        setActiveRightTab('why')        // selecting the segment fills this tab
        if (f.deepest?.segId) {
          issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: [f.deepest.segId] })
          selectSegment(f.deepest.segId)
        }
        await speakStep(whereText, isCancelled)
      },
      advanceOnComplete: true,
    })

    // WHY FLOODED tab orientation, ahead of the drainage numbers that follow it.
    list.push(tabStep('tab-why'))

    // drainage: correlational language only (see narration.js rule 2).
    const drainText = narrate.drainage(f, lang)
    list.push({
      id: 'drainage',
      title: T.steps[4],
      target: ['[data-tour="panel-why"]', '#rtab-why'],
      body: drainText || '',
      skip: () => !drainText,
      run: async ({ isCancelled }) => {
        setActiveRightTab('why')
        ensureLayers(['drainage'])      // the network being described is visible on the map
        await waitForAnyTarget(['[data-tour="panel-why"]', '#rtab-why'])
        if (isCancelled()) return
        await speakStep(drainText, isCancelled)
      },
      advanceOnComplete: true,
    })

    // 06 — action/routing: only when a real route or candidate set exists.
    const routeText = narrate.routing(f, lang)
    list.push({
      id: 'action',
      title: T.steps[5],
      target: ['[data-tour="route-result"]', '[data-tour="route-controls"]', '#rtab-routing'],
      body: routeText || '',
      skip: () => !routeText,
      run: async ({ isCancelled }) => {
        setActiveRightTab('routing')
        await waitForAnyTarget(['[data-tour="route-result"]', '[data-tour="route-controls"]'])
        if (isCancelled()) return
        const segs = f.routing?.recommended?.segments
        if (segs?.length) issueMapCommand({ type: 'HIGHLIGHT_SEGMENTS', ids: segs, fit: false })
        await speakStep(routeText, isCancelled)
      },
      advanceOnComplete: true,
    })

    // ROUTE tab orientation. Kept separate from the route DATA step above, which is skipped entirely when
    // no route has been planned — this one always runs, so the section is explained either way.
    list.push(tabStep('tab-route'))

    // SOURCES tab orientation.
    list.push(tabStep('tab-sources'))

    // summary: return to a useful overview state.
    list.push({
      id: 'summary',
      title: T.steps[6],
      target: '#rtab-overview',
      body: narrate.summary(f, lang),
      run: async ({ isCancelled }) => {
        setActiveRightTab('overview')
        issueMapCommand({ type: 'CLEAR_HIGHLIGHT' })
        await speakStep(narrate.summary(f, lang), isCancelled)
      },
    })

    return list
  }, [frozenFacts, lang, T, mode, setActiveRightTab, setCurrentT, setPlaying, selectSegment, issueMapCommand, speakStep, ensureLayers, setMapMode, setTerrainFocus])

  const onExit = useCallback(() => {
    voice.reset()               // stops audio immediately and frees this session's cached blobs
    setSpeaking(false)
    issueMapCommand({ type: 'CLEAR_HIGHLIGHT' })
    restoreLayers(layersAtStart)      // hand back any map layer Demo Story switched on
    setLayersAtStart(null)
    setMapMode(modeAtStart)
    setTerrainFocus(null)
  }, [issueMapCommand, restoreLayers, layersAtStart, setMapMode, modeAtStart, setTerrainFocus])

  const tour = useTour(steps, { onExit })

  const start = useCallback((nextMode = 'briefing') => {
    if (!facts.ready) return
    setMode(nextMode)
    setLayersAtStart(layers)            // both modes may switch a layer on; both hand it back
    setModeAtStart(mapMode)
    setFrozenFacts(facts)   // freeze BEFORE starting so the first step narrates the numbers just shown
    setVoiceMode(null)
    voice.cancel()          // a second briefing always stops the first one's audio before starting
    tour.start()
    setOpen(false)
  }, [facts, tour, layers, mapMode])

  const handlePause = useCallback(() => { voice.pause(); tour.pause() }, [tour])
  const handleResume = useCallback(() => { voice.resume(); tour.resume() }, [tour])
  const handleNext = useCallback(() => { voice.cancel(); tour.next() }, [tour])
  const handleBack = useCallback(() => { voice.cancel(); tour.back() }, [tour])

  // Stop audio if the component unmounts (e.g. navigating back to the landing page mid-briefing).
  useEffect(() => () => voice.reset(), [])

  // Honest voice state: what is actually producing sound right now, never a guess.
  const voiceStatus = (() => {
    if (tour.paused) return { icon: '‖', text: T.voicePaused, tone: 'paused' }
    // The requested language specifically has no voice anywhere: say THAT, rather than the generic
    // "voice unavailable" — and never let English audio stand in for Hindi/Marathi.
    if (voiceMode === VOICE_MODE.LANG_UNAVAILABLE) {
      return { icon: '\u{1F507}', text: T.voiceLangUnavailable, tone: 'off' }
    }
    if (voiceMode === VOICE_MODE.NONE || (voiceMode === null && !speechAvailable())) {
      return { icon: '\u{1F507}', text: T.voiceUnavailable, tone: 'off' }
    }
    if (speaking) {
      return {
        icon: '●',
        text: voiceMode === VOICE_MODE.BROWSER ? T.voiceBrowser : T.voiceSpeaking,
        tone: 'on',
      }
    }
    return null
  })()

  if (tour.active && tour.step) {
    return (
      <TourOverlay
        step={tour.step}
        index={tour.index}
        total={tour.total}
        paused={tour.paused}
        onNext={handleNext}
        onBack={handleBack}
        onExit={tour.stop}
        onPause={handlePause}
        onResume={handleResume}
        // Safe to dim here now that dimming is a cutout: the focused panel/map region keeps full contrast
        // while its surroundings are only softened, so nothing operational becomes harder to read.
        dim
        labels={{ back: T.back, next: T.next, finish: T.finish, pause: T.pause, resume: T.resume, exit: T.exit }}
        statusLine={voiceStatus ? (
          <span className={styles.voiceState} data-tone={voiceStatus.tone}>
            <span aria-hidden="true">{voiceStatus.icon}</span> {voiceStatus.text}
          </span>
        ) : null}
      />
    )
  }

  return (
    <div className={styles.launcher}>
      {open && (
        <div className={styles.langRow} role="group" aria-label="Briefing language">
          {LANGUAGES.map((l) => (
            <button
              key={l.code}
              type="button"
              className={`${styles.langBtn} ${lang === l.code ? styles.langBtnActive : ''}`}
              onClick={() => setLang(l.code)}
              aria-pressed={lang === l.code}
            >
              {l.label}
            </button>
          ))}
        </div>
      )}

      <div className={styles.btnRow}>
        <button
          type="button"
          className={styles.startBtn}
          onClick={() => start('briefing')}
          disabled={!facts.ready}
          title={facts.ready ? undefined : T.noRun}
        >
          <span className={styles.playGlyph} aria-hidden="true">▶</span>
          {T.start}
        </button>
        <button
          type="button"
          className={`${styles.startBtn} ${styles.storyBtn}`}
          onClick={() => start('story')}
          disabled={!facts.ready}
          title={facts.ready ? STORY_UI[lang].how : T.noRun}
          data-tour="demo-story"
        >
          <span className={styles.playGlyph} aria-hidden="true">◆</span>
          {STORY_UI[lang].start}
        </button>
        <button
          type="button"
          className={styles.langToggle}
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-label="Choose briefing language"
        >
          {LANGUAGES.find((l) => l.code === lang)?.label}
        </button>
      </div>

    </div>
  )
}
