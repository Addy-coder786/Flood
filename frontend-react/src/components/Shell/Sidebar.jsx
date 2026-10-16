import { PAGES } from '../../lib/pages.js'
import styles from './Shell.module.css'

// Six task pages in journey order (DECISIONS D-37). Each item shows its step number, its label and, as TEXT, the
// state of that step (never colour alone), so a first-time operator can see where they are and what is next.

const ICON = {
  overview: <path d="M3 10.5 10 4l7 6.5V17a1 1 0 0 1-1 1h-4v-5H8v5H4a1 1 0 0 1-1-1z" />,
  forecast: <path d="M5 13a4 4 0 0 1 .6-7.95A5 5 0 0 1 15 7a3 3 0 0 1 0 6zM7 15l-1 2m4-2-1 2m4-2-1 2" />,
  access: <path d="M6 18 9 2m2 0 3 16M10 5v2m0 3v2m0 3v2" />,
  response: <path d="M7 8a3 3 0 1 1 6 0 3 3 0 0 1-6 0zM3 18a7 7 0 0 1 14 0" />,
  reports: <path d="M10 3a5 5 0 0 1 5 5v3l1.5 3h-13L5 11V8a5 5 0 0 1 5-5zm-2 14a2 2 0 0 0 4 0" />,
  replay: <path d="M4 10a6 6 0 1 0 2-4.5M4 3v3h3m3 1v3l2 2" />,
  trips: <path d="M3 13V8l2-3h7l3 4h2v4H3zM6 15.5a1.5 1.5 0 1 0 0 .01M14 15.5a1.5 1.5 0 1 0 0 .01" />,
  resources: <path d="M3 17V8l7-4 7 4v9M7 17v-5h6v5M3 17h14" />,
  alerts: <path d="M3 8v4h3l5 4V4L6 8zm11-1.5a4 4 0 0 1 0 7" />,
}

function Icon({ k }) {
  return (
    <svg className={styles.navIcon} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      {ICON[k]}
    </svg>
  )
}

export default function Sidebar({ page, onNavigate, stepState = {}, onHome, onHelp, navControl = null }) {
  return (
    <aside id="app-navigation" className={styles.sidebar} data-tour="left-panel">
      <button type="button" className={styles.brand} onClick={onHome} aria-label="FloodNet: back to the home page">
        <img src={`${import.meta.env.BASE_URL}favicon-32x32.png`} alt="" width="28" height="28" className={styles.brandMark} />
        <span className={styles.brandText}>
          <span className={styles.brandName}>FloodNet</span>
          <span className={styles.brandSub}>Urban Flood Command Centre</span>
        </span>
      </button>

      <nav aria-label="Pages" className={styles.nav}>
        <ol className={styles.navList}>
          {PAGES.map((p, i) => {
            const active = p.key === page
            const state = stepState[p.key]
            return (
              <li key={p.key}>
                <a
                  href={p.hash}
                  className={`${styles.navItem} ${active ? styles.navItemActive : ''}`}
                  aria-current={active ? 'page' : undefined}
                  title={p.purpose}
                  onClick={(e) => { e.preventDefault(); onNavigate(p.key) }}
                >
                  <span className={styles.navStep} aria-hidden="true">{i + 1}</span>
                  <Icon k={p.key} />
                  <span className={styles.navText}>
                    <span className={styles.navLabel}>{p.label}</span>
                    {state && <span className={styles.navState}>{state}</span>}
                  </span>
                </a>
              </li>
            )
          })}
        </ol>
      </nav>

      <div className={styles.sideFoot}>
        {navControl && (
          <button type="button" className={`${styles.footBtn} ${styles.collapseBtn}`} onClick={navControl.onToggle}
            aria-expanded={navControl.expanded} aria-controls="app-navigation" aria-label={navControl.label} title={navControl.title}>
            <svg className={styles.navIcon} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
              <path d={navControl.expanded ? 'M12 5l-5 5 5 5' : 'M8 5l5 5-5 5'} />
            </svg>
            <span className={styles.navText}><span className={styles.navLabel}>{navControl.expanded ? 'Collapse' : 'Expand'}</span></span>
          </button>
        )}
        <button type="button" className={styles.footBtn} onClick={onHelp}>
          <svg className={styles.navIcon} viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" focusable="false">
            <circle cx="10" cy="10" r="7.5" /><path d="M8 8a2 2 0 1 1 3 1.7c-.7.4-1 .9-1 1.6M10 14h.01" strokeLinecap="round" />
          </svg>
          <span className={styles.navText}><span className={styles.navLabel}>Guided tour</span></span>
        </button>
        <p className={styles.sideNote}>Student prototype · not an official warning service</p>
      </div>
    </aside>
  )
}
