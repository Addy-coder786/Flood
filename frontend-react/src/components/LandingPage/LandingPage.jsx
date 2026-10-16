import { useEffect, useRef, useState } from 'react'
import HeroVisual from './HeroVisual.jsx'
import LandingStory from './LandingStory.jsx'
import styles from './LandingPage.module.css'
import { PIPELINE_STAGES } from '../../lib/story/stages.js'

// Landing page, in reading order (2026-10-11): (1) the centred FLOODNET hero over the animated dotted background
// (HeroVisual), filling the first screen; (2) directly below, LandingStory -- the interactive map sequence on the
// pilot's real road network with a clearly labelled DEMO response scenario (lib/landingScenario.js); (3) the static
// sections. "Explore forecast" scrolls to (2); the "How FloodNet works" cards open the matching dashboard stage.

const CAPABILITIES = [
  { badge: 'Rainfall', title: 'Labelled rainfall inputs', body: 'IMD, radar-derived, ECMWF or design storms — each tagged by source and freshness.' },
  { badge: 'Forecast', title: 'Street depth, 0–3 hours', body: '5-minute frames: the peak, the recession and the first roads to flood.' },
  { badge: 'Roads', title: 'Road access by vehicle', body: 'Which roads an ambulance, car or truck can use when it arrives.' },
  { badge: 'Response', title: 'Rescue routes and teams', body: 'Missions planned around predicted depths and closures, rerouted as things change.' },
  { badge: 'Alerts', title: 'Public alerts, reviewed', body: 'Every message is reviewed by a person; demo sends are simulated.' },
  { badge: 'Evidence', title: 'Replay and evaluation', body: 'Replay a plan against an independent outcome run.' },
]

const SOURCES = [
  { text: 'OpenStreetMap roads', tag: 'REAL' },
  { text: 'MCGM contours → 10 m terrain', tag: 'REAL' },
  { text: 'MCGM stormwater network', tag: 'REAL' },
  { text: 'IMD observations', tag: 'REAL' },
  { text: 'IMD Mumbai-Veravali radar-derived estimate', tag: 'ESTIMATED' },
  { text: 'ECMWF open-data forecast', tag: 'NWP' },
  { text: 'Design storms and demo scenarios', tag: 'SYNTHETIC' },
]

const HAS_IO = typeof IntersectionObserver !== 'undefined'

/** true while the element is on screen; with `once`, true from the first time it is reached. Always true where
 * IntersectionObserver is missing. */
function useOnScreen(ref, { rootMargin = '0px', once = false } = {}) {
  const [on, setOn] = useState(() => !HAS_IO || !once)
  useEffect(() => {
    const el = ref.current
    if (!el || !HAS_IO) return undefined
    const io = new IntersectionObserver(([e]) => {
      if (once) {
        if (e.isIntersecting) { setOn(true); io.disconnect() }
      } else setOn(e.isIntersecting)
    }, { rootMargin })
    io.observe(el)
    return () => io.disconnect()
  }, [ref, rootMargin, once])
  return on
}

export default function LandingPage({ onEnter, onExplore, onOpenStage, isTransitioning }) {
  const scrollToSection = (id) => {
    const el = document.getElementById(id)
    if (!el) return
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  }
  const heroRef = useRef(null)
  const demoRef = useRef(null)
  const heroOnScreen = useOnScreen(heroRef)            // the background animation stops once the hero is scrolled away
  const demoShown = useOnScreen(demoRef, { rootMargin: '0px 0px -15% 0px', once: true })   // fades in once

  return (
    <div className={`${styles.page} ${isTransitioning ? styles.pageTransitioning : ''}`}>
      <nav className={styles.navbar} aria-label="Landing page">
        <div className={styles.navLeft}>
          <span className={styles.brandTitle}>FloodNet</span>
          <span className={styles.brandBadge}>URBAN FLOOD INTELLIGENCE</span>
        </div>
        <div className={styles.navLinks}>
          <button type="button" className={styles.navLink} onClick={() => scrollToSection('how-it-works')}>Physical Model</button>
          <button type="button" className={styles.navLink} onClick={() => scrollToSection('demo')}>How it works</button>
          <button type="button" className={styles.navLink} onClick={() => scrollToSection('pilot')}>Pilot</button>
          <button type="button" className={styles.navLink} onClick={() => scrollToSection('capabilities')}>Capabilities</button>
          <button type="button" className={styles.navCta} onClick={onEnter}>
            Enter Control Centre <span aria-hidden="true">&rarr;</span>
          </button>
        </div>
      </nav>

      {/* 1. Hero: the first screen. Decorative layers are aria-hidden; HeroVisual shows a static fallback with
          reduced motion and is hidden (its animation loop stops) while the hero is off screen. */}
      <header ref={heroRef} className={styles.heroSection}>
        <div className={styles.heroTerrainBackdrop} aria-hidden="true" />
        <div className={styles.heroVisualWrap} hidden={!heroOnScreen}>
          <HeroVisual />
        </div>

        <div className={styles.hudOverlay} aria-hidden="true">
          <div className={styles.hudTopLeft}>
            <div className={styles.hudTag}><span className={styles.hudBullet} />MUMBAI PILOT</div>
            <div className={styles.hudCoords}>72.835–72.855° E, 19.010–19.030° N</div>
            <div className={styles.hudSub}>MCGM F/N &amp; F/S &middot; HINDMATA DEPRESSION</div>
          </div>
          <div className={styles.hudTopRight}>
            <div className={styles.hudTag}><span className={styles.hudBullet} />0–180 MIN</div>
            <div className={styles.hudCoords}>FORECAST HORIZON // 5-MIN TIMESTEP</div>
            <div className={styles.hudSub}>STREET LEVEL &middot; 10M DEM RESOLUTION</div>
          </div>
          <div className={styles.hudBottomLeft}>
            <div className={styles.hudTag}><span className={styles.hudBullet} />HYDRAULIC MODEL</div>
            <div className={styles.hudCoords}>STORAGE-CELL // CAPACITY-LIMITED GRAPH</div>
            <div className={styles.hudSub}>SURFACE STORAGE-CELLS + CONDUIT SURCHARGE</div>
          </div>
          <div className={styles.hudBottomRight}>
            <div className={styles.hudTag}><span className={styles.hudBullet} />SYSTEM STATUS</div>
            <div className={styles.hudCoords}>GIS INTELLIGENCE // PROTOTYPE</div>
            <div className={styles.hudSub}>0&ndash;3H FORECAST DECISION SUPPORT</div>
          </div>
        </div>

        {/* data-tour marks this as the "What is FloodNet?" anchor for the Explore FloodNet walkthrough. */}
        <div className={styles.heroCenterFocal} data-tour="hero">
          <div className={styles.technicalBeacon}>
            <span className={styles.beaconPulse} aria-hidden="true" />
            <span className={styles.technicalBeaconText}>MUMBAI URBAN FLOOD INTELLIGENCE</span>
          </div>

          <h1 className={styles.heroBrandTitle}>FLOODNET</h1>

          <p className={styles.heroMainStatement}>Know where the water will be — before it arrives.</p>

          <p className={styles.heroSupportingCopy}>
            Street-level flood forecasting for Mumbai, combining rainfall, terrain, runoff and drainage behaviour across
            a 0–3 hour operational window.
          </p>

          <div className={styles.heroActionRow}>
            <button type="button" className={styles.enterControlRoomBtn} onClick={onEnter}>
              <span className={styles.pulseDot} aria-hidden="true" />
              ENTER CONTROL CENTRE
              <span className={styles.btnArrow} aria-hidden="true">&rarr;</span>
            </button>
            <button type="button" className={styles.exploreForecastBtn} onClick={() => scrollToSection('demo')}>
              EXPLORE FORECAST
              <span className={styles.btnDownArrow} aria-hidden="true">&darr;</span>
            </button>
          </div>

          {onExplore && (
            <button type="button" className={styles.exploreTourBtn} onClick={onExplore}>
              <span className={styles.exploreTourGlyph} aria-hidden="true">◎</span>
              Explore FloodNet
              <span className={styles.exploreTourHint}>guided tour · 6 steps</span>
            </button>
          )}

          <dl className={styles.telemetryMatrix}>
            <div className={styles.telemCol}><dt className={styles.telemTag}>PREDICTION WINDOW</dt><dd className={styles.telemData}>0–3 Hours</dd></div>
            <div className={styles.telemCol}><dt className={styles.telemTag}>STREET NETWORK</dt><dd className={styles.telemData}>2,978 Segments</dd></div>
            <div className={styles.telemCol}><dt className={styles.telemTag}>DEPTH UNITS</dt><dd className={styles.telemData}>Reported in cm</dd></div>
            <div className={styles.telemCol}><dt className={styles.telemTag}>DECISION SUPPORT</dt><dd className={styles.telemData}>Clearance Routing</dd></div>
          </dl>
        </div>
      </header>

      {/* 2. The demo map, directly below the hero: the same LandingStory, unchanged. It starts when it comes into
          view (LandingStory pauses while off screen). */}
      <section id="demo" ref={demoRef} className={`${styles.demoSection} ${demoShown ? styles.demoShown : ''}`} aria-labelledby="demo-title">
        <div className={styles.demoInner}>
          <div className={styles.demoHeader}>
            <p className={styles.kicker}>See it work · DEMO scenario</p>
            <h2 id="demo-title" className={styles.sectionTitle}>From the first rain to a reviewed alert</h2>
            <p className={styles.honesty}>
              <span className={styles.honestyMark} aria-hidden="true">i</span>
              <span>Real pilot roads and terrain; the storm, closure, team and alert are a scripted DEMO. Student prototype — not an official warning service.</span>
            </p>
          </div>
          <div className={styles.demoStory}>
            <LandingStory />
          </div>
        </div>
      </section>

      <section id="how-it-works" className={styles.section}>
        <div className={styles.sectionHeader}>
          <p className={styles.kicker}>How FloodNet works</p>
          <h2 className={styles.sectionTitle}>From rainfall to a decision, in seven steps</h2>
          <p className={styles.sectionLead}>Open any step to see it in the control centre.</p>
        </div>

        {/* The same seven-stage chain as the dashboard story strip (lib/story/stages.js) */}
        <ol className={styles.pipeGrid}>
          {PIPELINE_STAGES.map((st, i) => (
            <li key={st.id} className={styles.pipeItem}>
              <button
                type="button"
                className={styles.pipeCard}
                onClick={() => onOpenStage?.(st.id)}
                title="Open this stage in the control centre"
              >
                <span className={styles.pipeTop}>
                  <span className={styles.pipeGlyph} aria-hidden="true">{st.glyph}</span>
                  <span className={styles.pipeIndex}>{String(i + 1).padStart(2, '0')}</span>
                </span>
                <span className={styles.pipeTitle}>{st.title.en}</span>
                <span className={styles.pipeLine}>{st.line.en}</span>
                <span className={styles.pipeFoot}>
                  <span className={`tag-badge tag-${st.badge.tag}`}>{st.badge.text}</span>
                  <span className={styles.pipeOpen} aria-hidden="true">Open &rarr;</span>
                </span>
              </button>
            </li>
          ))}
        </ol>
      </section>

      <section id="capabilities" className={`${styles.section} ${styles.sectionAlt}`}>
        <div className={styles.sectionHeader}>
          <p className={styles.kicker}>In the control centre</p>
          <h2 className={styles.sectionTitle}>What an operator can do</h2>
        </div>
        <div className={styles.capGrid}>
          {CAPABILITIES.map((c) => (
            <div key={c.title} className={styles.capCard}>
              <span className={styles.capBadge}>{c.badge}</span>
              <h3 className={styles.capTitle}>{c.title}</h3>
              <p className={styles.capBody}>{c.body}</p>
            </div>
          ))}
        </div>
      </section>

      <section id="pilot" className={styles.section}>
        <div className={styles.pilotCard}>
          <div className={styles.pilotText}>
            <p className={styles.kicker}>Pilot area</p>
            <h2 className={styles.sectionTitle}>Hindmata / Dadar, Mumbai</h2>
            <p className={styles.sectionLead}>MCGM wards F/North and F/South — a low-lying bowl that floods in heavy monsoon rain.</p>
          </div>
          <dl className={styles.pilotMetrics}>
            <div><dt>Street segments</dt><dd>2,978</dd></div>
            <div><dt>Drainage nodes</dt><dd>1,233</dd></div>
            <div><dt>Terrain grid</dt><dd>10 m</dd></div>
            <div><dt>Forecast step</dt><dd>5 min</dd></div>
          </dl>
        </div>
      </section>

      <section id="data" className={`${styles.section} ${styles.sectionAlt}`}>
        <div className={styles.sectionHeader}>
          <p className={styles.kicker}>Data honesty</p>
          <h2 className={styles.sectionTitle}>Every input says what it is</h2>
          <p className={styles.sectionLead}>Real, estimated, forecast and synthetic inputs are tagged wherever they appear.</p>
        </div>
        <ul className={styles.sourceList}>
          {SOURCES.map((s) => (
            <li key={s.text} className={styles.sourceItem}>
              <span className={`tag-badge tag-${s.tag}`}>{s.tag}</span>
              {s.text}
            </li>
          ))}
        </ul>
      </section>

      <section className={styles.ctaSection}>
        <h2 className={styles.ctaTitle}>Run a forecast on the pilot area</h2>
        <button type="button" className={styles.primaryBtn} onClick={onEnter}>
          Open the control centre <span aria-hidden="true">&rarr;</span>
        </button>
      </section>

      <footer className={styles.footer}>
        <span className={styles.footerBrand}>FloodNet</span>
        <span>Urban flood nowcasting · student prototype, not an official warning service.</span>
        <span>Map data © OpenStreetMap contributors (ODbL) · terrain and drainage from MCGM GIS layers.</span>
      </footer>
    </div>
  )
}
