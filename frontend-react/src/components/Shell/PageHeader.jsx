import { pageInfo } from '../../lib/pages.js'
import InfoTip from '../ui/InfoTip.jsx'
import styles from './Shell.module.css'

// One header per page: title, one-line purpose, and the page's state band (empty / loading / stale / error) worded
// as an instruction. `children` is the page's single primary action. The step number lives in the sidebar.

const BAND = {
  empty: { glyph: '○', cls: 'bandEmpty' },
  loading: { glyph: '◌', cls: 'bandLoading' },
  stale: { glyph: '↻', cls: 'bandStale' },
  error: { glyph: '!', cls: 'bandError' },
}

export function StateBand({ kind, children, action }) {
  const b = BAND[kind]
  if (!b || !children) return null
  return (
    <div className={`${styles.band} ${styles[b.cls]}`} role={kind === 'error' ? 'alert' : 'status'}>
      <span className={styles.bandGlyph} aria-hidden="true">{b.glyph}</span>
      <span className={styles.bandText}>{children}</span>
      {action}
    </div>
  )
}

export default function PageHeader({ pageKey, status, statusText, children }) {
  const info = pageInfo(pageKey)
  return (
    <header className={styles.pageHeader}>
      <h1 className={styles.pageTitle} id={`page-title-${info.key}`} tabIndex={-1}>{info.title}</h1>
      <p className={styles.pagePurpose}>{info.purpose}</p>
      {children && <div className={styles.pageActions}>{children}</div>}
      {status !== 'ready' && <StateBand kind={status}>{statusText}</StateBand>}
    </header>
  )
}

/** A titled block inside a page. `advanced` renders it as a closed disclosure ("optional" content). `hint` is
 *  secondary detail: it sits behind a compact info toggle next to the title instead of a paragraph. */
export function Section({ title, hint, advanced = false, children, id, tour }) {
  if (advanced) {
    return (
      <details className={styles.advanced} id={id} data-tour={tour}>
        <summary className={styles.advancedSummary}>
          <span>{title}</span>
          <span className={styles.optional}>optional</span>
        </summary>
        <div className={styles.advancedBody}>{children}</div>
      </details>
    )
  }
  return (
    <section className={styles.block} id={id} data-tour={tour}>
      {title && (
        <div className={styles.blockHead}>
          <h2 className={styles.blockTitle}>{title}</h2>
          {hint && <InfoTip label={`About: ${title}`}>{hint}</InfoTip>}
        </div>
      )}
      {!title && hint && <p className={styles.blockHint}>{hint}</p>}
      {children}
    </section>
  )
}
