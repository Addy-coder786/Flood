import { Suspense, lazy, useState, useEffect, useCallback, useReducer, useRef } from 'react'
import { FloodNetProvider, useFloodNet } from './state/FloodNetContext.jsx'
import { hashForPage, pageFromHash, pageStatus, pageTitle, resolvePageKey } from './lib/pages.js'
import { PANELS_STORAGE_KEY, deserializePanels, initialPanels, layoutFor, panelControl, panelsReducer, serializePanels } from './lib/panels.js'
import LandingPage from './components/LandingPage/LandingPage.jsx'
import Header from './components/Header/Header.jsx'
import Sidebar from './components/Shell/Sidebar.jsx'
import MapView from './components/MapView/MapView.jsx'
import NavigationMode from './components/Navigation/NavigationMode.jsx'
import ForecastTimeline from './components/ForecastTimeline/ForecastTimeline.jsx'
import LoadingOverlay from './components/LoadingOverlay/LoadingOverlay.jsx'
import NoticeBanner from './components/NoticeBanner/NoticeBanner.jsx'
import GuidedBriefing from './components/GuidedBriefing/GuidedBriefing.jsx'
import StoryStrip from './components/StoryStrip/StoryStrip.jsx'
import MapModeToggle from './components/Terrain3D/MapModeToggle.jsx'
import OverviewPage from './components/Pages/OverviewPage.jsx'
import ReportsPage from './components/Pages/ReportsPage.jsx'
import TripsPage from './components/Pages/TripsPage.jsx'
import AlertsPage from './components/Pages/AlertsPage.jsx'
import ResourcesPage from './components/Pages/ResourcesPage.jsx'
import { AccessPage, ForecastPage, ReplayPage, ResponsePage } from './components/Pages/WorkPages.jsx'

// three.js is fetched only when 3D terrain is first opened
const Terrain3D = lazy(() => import('./components/Terrain3D/Terrain3D.jsx'))
import ExploreTour from './components/ExploreTour/ExploreTour.jsx'
import styles from './App.module.css'

// ─── Navigation ──────────────────────────────────────────────────────────────
//
// Two views: landing (/) and the control centre (/dashboard). Inside the control centre, eight task pages
// (DECISIONS D-37, D-47, lib/pages.js) are selected by the URL hash: /dashboard#/access. Back/Forward, refresh and deep
// links work with no router library and no server change (the SPA fallbacks only know /static and /dashboard).

const BASE = import.meta.env.BASE_URL.replace(/\/$/, '') // e.g. "/static"

function isDashboardPath() {
  return window.location.pathname.startsWith(BASE + '/dashboard')
}

// ─── Pages ───────────────────────────────────────────────────────────────────
// One panel per page. The selected key lives in Context (`activeRightTab`) so tours, the spoken briefing and the
// story stages can open a page as a narrated step. Every page stays mounted (hidden when inactive) so a page's
// local inputs survive navigation and tour anchors stay in the document.
const PAGE_PANELS = [
  { key: 'overview', Page: OverviewPage },
  { key: 'forecast', Page: ForecastPage },
  { key: 'access', Page: AccessPage },
  { key: 'response', Page: ResponsePage },
  { key: 'trips', Page: TripsPage },
  { key: 'resources', Page: ResourcesPage },
  { key: 'reports', Page: ReportsPage },
  { key: 'alerts', Page: AlertsPage },
  { key: 'replay', Page: ReplayPage },
]

// Right-rail tab keys from before D-37. Tours, the briefing and the story stages still ask for these; each opens
// the page that now hosts that panel (lib/pages.js LEGACY_ALIASES) and keeps its `rtab-<key>` anchor.
const LEGACY_TABS = [
  { key: 'overview', page: 'overview' },
  { key: 'alerts', page: 'alerts' },
  { key: 'why', page: 'forecast' },
  { key: 'routing', page: 'access' },
  { key: 'response', page: 'response' },
  { key: 'provenance', page: 'overview' },
]

const EMPTY_TEXT = {
  forecast: 'No forecast yet.',
  access: 'Run a forecast first.',
  response: 'Run a forecast first.',
  replay: 'Run a forecast first.',
  trips: 'Run a forecast and accept assignments first.',
}

function statusTextFor(key, status, s) {
  if (status === 'loading') return `Running the flood forecast: ${s.simStage}…`
  if (status === 'error') return `Forecast failed: ${s.simError}. The previous run is unchanged.`
  if (status === 'empty') return EMPTY_TEXT[key] || null
  if (status === 'stale') {
    if (s.isStale) return 'Inputs changed since the last run. Run the forecast again.'
    if (key === 'response') return 'Plan made at another decision time or forecast. Replan to update.'
  }
  return null
}

function stepStates(s) {
  const ran = s.run && !s.isStale
  const out = {
    overview: s.run ? `Decision time T+${s.currentT} min` : 'Start here',
    forecast: s.simulating ? 'Running…' : s.run ? (s.isStale ? 'Inputs changed' : 'Forecast ready') : 'Not run yet',
    access: s.access.result ? `Checked at T+${s.access.result.dispatch_t_min}` : (ran ? 'Ready to check' : 'Needs a forecast'),
    response: s.response.version ? `Plan v${s.response.version}${s.response.staleReason ? ' · replan' : ''}` : (ran ? 'Ready to plan' : 'Needs a forecast'),
    trips: s.ops?.available ? `${(s.ops.trips || []).filter((t) => !['COMPLETED', 'ABORTED'].includes(t.stage)).length} active trip(s)` : 'Live operations off',
    resources: s.ops?.available ? `${(s.ops.resources || []).length} record(s)` : 'Teams, vehicles, shelters',
    reports: `${s.roadEvents.length + s.response.events.length} report(s)`,
    alerts: 'Advisories and official feed',
    replay: s.response.evaluation?.result ? 'Evaluated' : 'Not evaluated',
  }
  out.forecastDone = Boolean(ran)
  out.accessDone = Boolean(s.access.result)
  out.responseDone = Boolean(s.response.version)
  out.reportsDone = s.roadEvents.length + s.response.events.length > 0
  out.replayDone = Boolean(s.response.evaluation?.result)
  out.tripsDone = Boolean(s.ops?.trips?.length)
  return out
}

function ControlCentre({ onHome, onHelp, pendingStage, onStageOpened }) {
  const fn = useFloodNet()
  const { activeRightTab, setActiveRightTab, mapMode, setMapMode, navigation } = fn
  const page = resolvePageKey(activeRightTab)

  // Hash → page on load and on Back/Forward; page → hash when a tour or a button changes the page.
  useEffect(() => {
    const fromHash = () => {
      if (window.location.hash) setActiveRightTab(pageFromHash(window.location.hash))
    }
    fromHash()
    window.addEventListener('hashchange', fromHash)
    return () => window.removeEventListener('hashchange', fromHash)
  }, [setActiveRightTab])
  useEffect(() => {
    const want = hashForPage(page)
    if (window.location.hash !== want) window.history.replaceState(window.history.state, '', want)
    document.title = `${pageTitle(page)} · FloodNet`
  }, [page])

  // Collapsible panels (D-47): the sidebar and the work panel slide in and out; pages stay mounted, so nothing a
  // page holds (forecast, routes, incident, assignments) is lost when a panel closes.
  const [layout, setLayout] = useState(() => layoutFor(window.innerWidth))
  const [panels, dispatchPanels] = useReducer(panelsReducer, null, () => {
    let stored = null
    try { stored = deserializePanels(window.localStorage.getItem(PANELS_STORAGE_KEY)) } catch { /* storage blocked */ }
    return initialPanels(window.innerWidth, stored)
  })
  useEffect(() => {
    const onResize = () => {
      const l = layoutFor(window.innerWidth)
      setLayout(l)
      dispatchPanels({ type: 'layout', layout: l })
    }
    window.addEventListener('resize', onResize)
    return () => window.removeEventListener('resize', onResize)
  }, [])
  useEffect(() => {
    try { window.localStorage.setItem(PANELS_STORAGE_KEY, serializePanels(panels)) } catch { /* storage blocked */ }
  }, [panels])
  // Navigation mode (Roads page): the work panel slides away so the map and the turn cards fill the screen, and
  // comes back as it was on exit. Nothing else changes: the page, trip and route are untouched.
  const navActive = Boolean(navigation)
  const workBeforeNavRef = useRef(null)
  const panelsRef = useRef(panels)
  useEffect(() => { panelsRef.current = panels }, [panels])
  useEffect(() => {
    if (navActive) {
      if (workBeforeNavRef.current == null) workBeforeNavRef.current = panelsRef.current.work
      dispatchPanels({ type: 'set', panel: 'work', value: 'closed' })
      if (layoutFor(window.innerWidth) === 'narrow') dispatchPanels({ type: 'set', panel: 'nav', value: 'collapsed' })
    } else if (workBeforeNavRef.current != null) {
      dispatchPanels({ type: 'set', panel: 'work', value: workBeforeNavRef.current })
      workBeforeNavRef.current = null
    }
  }, [navActive])
  const navCtl = panelControl(panels, 'nav')
  const workCtl = panelControl(panels, 'work')

  const navigate = useCallback((key) => {
    const want = hashForPage(key)
    if (window.location.hash !== want) window.history.pushState(window.history.state, '', want)
    setActiveRightTab(resolvePageKey(key))
    dispatchPanels({ type: 'navigate', layout: layoutFor(window.innerWidth) })
    document.getElementById(`page-title-${resolvePageKey(key)}`)?.focus?.()
  }, [setActiveRightTab])

  const statusState = {
    run: fn.run, simulating: fn.simulating, simError: fn.simError, isStale: fn.isStale,
    response: fn.response, currentT: fn.currentT, roadEvents: fn.roadEvents, simStage: fn.simStage, ops: fn.ops,
  }
  const steps = stepStates({ ...statusState, access: fn.access })

  // Escape cancels a pending "click a road on the map" report.
  const { closeRoadMode, setCloseRoadMode } = fn
  useEffect(() => {
    if (!closeRoadMode) return undefined
    const onKey = (e) => { if (e.key === 'Escape') setCloseRoadMode(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [closeRoadMode, setCloseRoadMode])

  return (
    <div className={styles.shell} data-nav={panels.nav} data-work={panels.work} data-layout={layout}>
      <a className={styles.skip} href={hashForPage(page)} onClick={(e) => { e.preventDefault(); document.getElementById(`page-${page}`)?.focus() }}>
        Skip to page content
      </a>
      <Sidebar page={page} onNavigate={navigate} stepState={steps} onHome={onHome} onHelp={onHelp}
        navControl={{ ...navCtl, onToggle: () => dispatchPanels({ type: 'toggle', panel: 'nav' }) }} />
      {layout === 'narrow' && panels.nav === 'expanded' && (
        <button type="button" className={styles.scrim} aria-label="Close navigation" onClick={() => dispatchPanels({ type: 'set', panel: 'nav', value: 'collapsed' })} />
      )}
      <Header leading={layout === 'narrow' ? (
        <button type="button" className={styles.menuBtn} aria-expanded={navCtl.expanded} aria-controls="app-navigation"
          aria-label={navCtl.expanded ? 'Close navigation' : 'Open navigation'} title={navCtl.expanded ? 'Close navigation' : 'Open navigation'}
          onClick={() => dispatchPanels({ type: 'toggle', panel: 'nav' })}>
          <svg viewBox="0 0 20 20" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true" focusable="false"><path d="M3 5h14M3 10h14M3 15h14" /></svg>
        </button>
      ) : null} />

      <main className={styles.main}>
        {/* The page comes first in the DOM (keyboard and screen-reader order: navigation, context, page task,
            then the map); the grid places the map on the left. */}
        <aside id="page-panel" className={styles.pagePanel} data-tour="right-panel" aria-label="Page" inert={panels.work === 'closed'}>
          <div className={styles.pageInner}>
          {PAGE_PANELS.map(({ key, Page }) => {
            const status = pageStatus(key, statusState)
            return (
              <div
                key={key}
                id={`page-${key}`}
                role="region"
                aria-labelledby={`page-title-${key}`}
                tabIndex={-1}
                className={`${styles.pageBody} scroll-y`}
                hidden={page !== key}
              >
                {LEGACY_TABS.filter((t) => t.page === key).map((t) => (
                  <span key={t.key} id={`rtab-${t.key}`} className={styles.anchor} aria-hidden="true" />
                ))}
                <Page status={status} statusText={statusTextFor(key, status, statusState)} onNavigate={navigate} stepState={steps} />
              </div>
            )
          })}
          </div>
        </aside>
        <button type="button" className={styles.workToggle} aria-controls="page-panel" aria-expanded={workCtl.expanded}
          aria-label={workCtl.label} title={workCtl.title} onClick={() => dispatchPanels({ type: 'toggle', panel: 'work' })}>
          <svg viewBox="0 0 20 20" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
            {layout === 'narrow'
              ? <path d={workCtl.expanded ? 'M5 8l5 5 5-5' : 'M5 12l5-5 5 5'} />
              : <path d={workCtl.expanded ? 'M8 5l5 5-5 5' : 'M12 5l-5 5 5 5'} />}
          </svg>
          <span className={styles.workToggleText}>{workCtl.expanded ? 'Hide panel' : 'Show panel'}</span>
        </button>
        <section className={styles.stage} aria-label="Map" data-tour="map">
          {/* Leaflet stays mounted (hidden) in 3D so returning to 2D restores the exact view */}
          <div className={styles.mapFill} style={mapMode === '3d' ? { visibility: 'hidden' } : undefined}>
            <MapView />
          </div>
          {navActive && mapMode === '2d' && <NavigationMode />}
          {mapMode === '3d' && (
            <Suspense fallback={<div className={styles.mapLoading}>Loading 3D terrain…</div>}>
              <Terrain3D onClose={() => setMapMode('2d')} />
            </Suspense>
          )}
          {/* the floating map controls give way to the navigation cards (the story strip keeps its stage logic mounted) */}
          {!navActive && <MapModeToggle />}
          <StoryStrip openStage={pendingStage} onOpened={onStageOpened} showStrip={page === 'overview'} />
          <ForecastTimeline />
          <NoticeBanner />
          {!navActive && <GuidedBriefing />}
          <LoadingOverlay />
        </section>
      </main>
    </div>
  )
}

function MainApp() {
  const [view, setView] = useState(() =>
    isDashboardPath() ? 'dashboard' : 'landing'
  )
  const [isTransitioning, setIsTransitioning] = useState(false)

  // Listen for popstate (Browser Back / Forward)
  useEffect(() => {
    const handlePop = () => {
      setView(isDashboardPath() ? 'dashboard' : 'landing')
    }
    window.addEventListener('popstate', handlePop)
    return () => window.removeEventListener('popstate', handlePop)
  }, [])

  const navigateToDashboard = useCallback(() => {
    setIsTransitioning(true)
    setTimeout(() => {
      if (!isDashboardPath()) {
        window.history.pushState({ view: 'dashboard' }, '', BASE + '/dashboard' + (window.location.hash || ''))
      }
      setView('dashboard')
      setIsTransitioning(false)
    }, 360)
  }, [])

  const navigateToLanding = useCallback(() => {
    if (isDashboardPath()) {
      window.history.pushState({ view: 'landing' }, '', BASE + '/')
    }
    document.title = 'FloodNet'
    setView('landing')
  }, [])

  // "Explore FloodNet" tour. It starts on the landing page or from the sidebar's Guided tour button and walks
  // through the real pages, so its active flag lives here.
  const [exploring, setExploring] = useState(false)
  // A "How FloodNet Works" card on the landing page opens that stage in the live dashboard.
  const [pendingStage, setPendingStage] = useState(null)
  const { setActiveRightTab } = useFloodNet()

  return (
    <div className={view === 'landing' ? styles.landingContainer : styles.dashboardContainer}>
      {view === 'landing' ? (
        <LandingPage
          onEnter={navigateToDashboard}
          onExplore={() => setExploring(true)}
          onOpenStage={(id) => { setPendingStage(id); navigateToDashboard() }}
          isTransitioning={isTransitioning}
        />
      ) : (
        <ControlCentre
          onHome={navigateToLanding}
          onHelp={() => setExploring(true)}
          pendingStage={pendingStage}
          onStageOpened={() => setPendingStage(null)}
        />
      )}

      <ExploreTour
        active={exploring}
        view={view}
        onGoDashboard={navigateToDashboard}
        onGoLanding={navigateToLanding}
        setTab={setActiveRightTab}
        onClose={() => setExploring(false)}
      />
    </div>
  )
}

export default function App() {
  return (
    <FloodNetProvider>
      <MainApp />
    </FloodNetProvider>
  )
}

