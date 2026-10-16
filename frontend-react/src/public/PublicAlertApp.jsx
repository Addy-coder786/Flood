// Public alert page (DECISIONS D-45; docs/LIVE_OPERATIONS.md §7; docs/LIVE_OPERATIONS_RESEARCH.md §8).
// Opened by a resident from an SMS / WhatsApp link, no login. Shows only what GET /api/public/alerts/{id} returns.
// Section order follows research §8: who issued it and the status first, then what to do, what is happening, the map,
// official contacts, the update history. Text location comes before the map. The map is an inline SVG drawn from the
// GeoJSON in the response; a street map (Leaflet + OpenStreetMap tiles) loads only if the reader asks (AlertMap.jsx).
// 2026-10-11: a plain-language summary with the time horizon, the kind of each piece of evidence (model estimate,
// public report, operator-confirmed closure; an authorised official warning only as quoted from the SACHET feed),
// confirmed closures, the latest change, and how FloodNet messages start and stop. There is no sign-up on this page.
import { useEffect, useState } from 'react'
import { apiUrl } from '../api/client.js'
import {
  apiPathFor, closedRoads, docTitle, evidenceLabel, evidenceTypes, formatIST, formatISTTime, latestChange, latestHref,
  officialItemsFor, plainRisk, stateBanners, statusDisplay, updatedLine, updatesNewestFirst, versionFromSearch,
} from '../lib/publicAlert.js'
import AlertMap from './AlertMap.jsx'
import s from './PublicAlertApp.module.css'

const REFRESH_MS = 60_000
const STRIP = 'FloodNet advisory — student prototype, not an official government warning'
// Verified in docs/LIVE_OPERATIONS_RESEARCH.md §8 (used only when the API has nothing to show).
const FALLBACK_LINKS = [
  { name: 'SACHET - official alerts (NDMA)', url: 'https://sachet.ndma.gov.in/' },
  { name: 'BMC Disaster Management', url: 'https://dm.mcgm.gov.in/' },
]
const EVIDENCE_GLYPH = { model_estimate: '≈', reported: '?', confirmed_closure: '✕' }

function Emergency112() {
  return (
    <p className={s.emergency}>
      In an emergency call <a className={s.callInline} href="tel:112">112</a>.
    </p>
  )
}

function OfficialLinks({ links }) {
  return (
    <ul className={s.links}>
      {links.map((l) => (
        <li key={l.url}><a href={l.url} rel="noopener noreferrer">{l.name}</a></li>
      ))}
    </ul>
  )
}

function NotFound() {
  useEffect(() => { document.title = 'FloodNet – link not valid' }, [])
  return (
    <div className={s.page}>
      <p className={s.strip}>{STRIP}</p>
      <main className={s.main}>
        <h1 className={s.plainTitle}>This FloodNet link is not valid or has been withdrawn</h1>
        <p>For official flood information, use:</p>
        <OfficialLinks links={FALLBACK_LINKS} />
        <Emergency112 />
      </main>
    </div>
  )
}

function LoadFailed({ onRetry }) {
  return (
    <div className={s.page}>
      <p className={s.strip}>{STRIP}</p>
      <main className={s.main}>
        <h1 className={s.plainTitle}>This FloodNet advisory could not be loaded</h1>
        <p>Check your internet connection and try again.</p>
        <button type="button" className={s.button} onClick={onRetry}>Try again</button>
        <p>For official flood information, use:</p>
        <OfficialLinks links={FALLBACK_LINKS} />
        <Emergency112 />
      </main>
    </div>
  )
}

function ShareButton({ title }) {
  const [msg, setMsg] = useState('')
  const canShare = typeof navigator !== 'undefined' && typeof navigator.share === 'function'
  const canCopy = typeof navigator !== 'undefined' && !!navigator.clipboard?.writeText && window.isSecureContext
  if (!canShare && !canCopy) return null
  const url = `${window.location.origin}${window.location.pathname}`       // always the latest version
  const onClick = async () => {
    if (canShare) {
      try {
        await navigator.share({ title, text: `${title} (student prototype, not an official warning)`, url })
      } catch { /* closed by the user */ }
      return
    }
    try {
      await navigator.clipboard.writeText(url)
      setMsg('Link copied')
    } catch {
      setMsg('Could not copy the link')
    }
  }
  return (
    <div className={s.share}>
      <button type="button" className={s.button} onClick={onClick}>{canShare ? 'Share this page' : 'Copy link to this page'}</button>
      <span className={s.shareMsg} role="status">{msg}</span>
    </div>
  )
}

function Summary({ alert }) {
  const risk = plainRisk(alert)
  const types = evidenceTypes(alert)
  return (
    <section className={s.risk} aria-labelledby="meaning">
      <h2 id="meaning" className={s.h2}>What this means</h2>
      {risk.lines.map((t) => <p key={t}>{t}</p>)}
      {risk.horizon && <p className={s.horizon}><strong>Time horizon:</strong> {risk.horizon}</p>}
      <p className={s.small}>
        Based on: {types.filter((k) => k.present).map((k) => k.label).join(' · ') || 'no evidence listed'}
      </p>
    </section>
  )
}

function OfficialFeed() {
  // FloodNet's cached copy of the public SACHET (NDMA) feed, quoted as received; never edited or re-issued.
  const [feed, setFeed] = useState({ phase: 'loading', items: [], cached: 0 })
  useEffect(() => {
    let alive = true
    fetch(apiUrl('/api/public/official-alerts'), { cache: 'no-cache' })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((d) => { if (alive) setFeed({ phase: 'ready', ...officialItemsFor(d?.items) }) })
      .catch(() => { if (alive) setFeed({ phase: 'failed', items: [], cached: 0 }) })
    return () => { alive = false }
  }, [])
  return (
    <>
      <p className={s.small}>Issued by IMD, NDMA or the state authority through SACHET; shown here as received.</p>
      {feed.phase === 'loading' && <p className={s.small} role="status">Checking FloodNet’s copy of the official feed…</p>}
      {feed.phase === 'failed' && <p>FloodNet’s copy of the official feed could not be loaded. Check SACHET directly.</p>}
      {feed.phase === 'ready' && feed.items.length > 0 && (
        <ul className={s.officialItems}>
          {feed.items.map((i) => (
            <li key={i.guid || i.link || i.title} className={s.officialItem}>
              <p className={s.small}><strong>{i.label || 'OFFICIAL (SACHET/NDMA)'}</strong> – quoted as received, not edited by FloodNet</p>
              <p>{i.link ? <a href={i.link} rel="noopener noreferrer">“{i.title || 'Untitled item'}”</a> : <>“{i.title || 'Untitled item'}”</>}</p>
              <p className={s.small}>{[i.author, i.pub_date].filter(Boolean).join(' · ')}
                {i.fetched_at ? ` · copied by FloodNet ${formatIST(i.fetched_at) || ''}` : ''}</p>
            </li>
          ))}
        </ul>
      )}
      {feed.phase === 'ready' && feed.items.length === 0 && (
        <p>{feed.cached
          ? `None of the ${feed.cached} items in FloodNet’s copy of the SACHET feed names Maharashtra or Mumbai.`
          : 'FloodNet holds no copy of the SACHET feed at the moment.'} Check SACHET directly for the latest official warnings.</p>
      )}
    </>
  )
}

function Section({ id, title, children }) {
  return (
    <section className={s.section} aria-labelledby={id}>
      <h2 id={id} className={s.h2}>{title}</h2>
      {children}
    </section>
  )
}

function Advisory({ alert, now, checked }) {
  const st = statusDisplay(alert)
  const head = String(alert.headline || '')
  const place = head.startsWith(`${alert.status_text}: `) ? head.slice(alert.status_text.length + 2) : head
  const banners = stateBanners(alert)
  const updates = updatesNewestFirst(alert.updates)
  const contacts = alert.contacts || []
  const links = alert.official_links?.length ? alert.official_links : FALLBACK_LINKS
  const latest = latestHref(window.location.pathname)
  const change = latestChange(alert)
  const closed = closedRoads(alert.map)

  return (
    <div className={s.page}>
      <p className={s.strip}>{STRIP}</p>
      <main className={s.main}>
        {alert.demonstration && (
          <p className={`${s.banner} ${s.banner_older}`} role="note"><strong>Demonstration.</strong> {alert.demonstration}</p>
        )}
        <header className={s.header}>
          <p className={s.brand}>FloodNet</p>
          <h1 className={s.h1}>
            <span className={`${s.status} ${s[`tone_${st.tone}`] || ''}`}>
              <span className={s.glyph} aria-hidden="true">{st.glyph}</span>{st.text}
            </span>
            {place !== st.text && <span className={s.place}>{place}</span>}
          </h1>
          <p className={s.locality}>{alert.locality_text}</p>
          <p className={s.updated}>{updatedLine(alert.updated_at, now)}</p>
          {change && (
            <p className={s.change}>Latest change ({formatIST(change.time) || 'time not available'}): {change.what_changed}</p>
          )}
          <p className={s.meta}>Issued by {alert.issuer}</p>
          <p className={s.meta}>
            {checked.ok ? `Checked for updates at ${formatISTTime(checked.at)}`
              : `Could not check for updates at ${formatISTTime(checked.at)} – showing the last version received`}
          </p>
        </header>

        {banners.map((b) => (
          <div key={b.kind} className={`${s.banner} ${s[`banner_${b.kind}`] || ''}`}>
            <p>{b.text}</p>
            {b.kind === 'older' && <p><a href={latest}>See the latest version</a></p>}
          </div>
        ))}

        <Summary alert={alert} />

        <Section id="todo" title="What to do now">
          <ul className={s.precautions}>
            {(alert.precautions?.items || []).map((t) => <li key={t}>{t}</li>)}
          </ul>
          {alert.precautions?.url && (
            <p className={s.source}><a href={alert.precautions.url} rel="noopener noreferrer">{alert.precautions.source}</a></p>
          )}
        </Section>

        <Section id="happening" title="What is happening">
          <p>FloodNet combines a computer flood model with road reports. This advisory is based on:</p>
          {(alert.evidence || []).length ? (
            <ul className={s.evidence}>
              {alert.evidence.map((e) => (
                <li key={`${e.kind}-${e.time}`} className={s.evidenceItem}>
                  <p className={s.evidenceLabel}>
                    <span className={s.evidenceGlyph} aria-hidden="true">{EVIDENCE_GLYPH[e.kind] || '•'}</span>
                    {evidenceLabel(e)}
                  </p>
                  <p>{e.detail}</p>
                  <p className={s.small}>{formatIST(e.time) || 'Time not available'} · Source: {e.source}</p>
                </li>
              ))}
            </ul>
          ) : <p>No evidence is listed in this version.</p>}
          <p>{closed.length ? <><strong>Roads confirmed closed by an operator:</strong> {closed.join(', ')}.</>
            : 'No road closure has been confirmed by an operator in this version.'}</p>
          {alert.time_window?.text && <p><strong>Time window:</strong> {alert.time_window.text}</p>}
          {alert.uncertainty && <p className={s.uncertainty}><strong>How certain is this?</strong> {alert.uncertainty}</p>}
        </Section>

        <Section id="map" title="Map of affected roads">
          <AlertMap map={alert.map} />
        </Section>

        <Section id="official" title="Emergency and official information">
          {contacts.length ? (
            <ul className={s.contacts}>
              {contacts.map((c) => (
                <li key={c.number} className={s.contact}>
                  <a className={s.call} href={`tel:${c.number}`}>Call {c.number}</a>
                  <span className={s.contactText}>
                    {c.name}. Source: {c.source_url ? <a href={c.source_url} rel="noopener noreferrer">{c.source}</a> : c.source}
                  </span>
                </li>
              ))}
            </ul>
          ) : <Emergency112 />}
          <p>Official alerts and instructions:</p>
          <OfficialLinks links={links} />
        </Section>

        <Section id="sachet" title="Official warnings (SACHET)">
          <OfficialFeed />
        </Section>

        <Section id="updates" title="Updates">
          {updates.length ? (
            <ol className={s.updates}>
              {updates.map((u) => {
                const here = u.version === alert.version
                const href = u.version === alert.latest_version ? latest : `${latest}?v=${u.version}`
                return (
                  <li key={u.version} className={s.update}>
                    <p className={s.small}>
                      {here ? <strong>Version {u.version} (this page)</strong> : <a href={href}>Version {u.version}</a>}
                      {' · '}{formatIST(u.time) || 'time not available'}
                    </p>
                    <p><strong>{u.status_text}</strong>{u.what_changed ? ` – ${u.what_changed}` : ''}</p>
                  </li>
                )
              })}
            </ol>
          ) : <p>No updates yet.</p>}
          {alert.expires_at && alert.state !== 'expired' && alert.state !== 'resolved' && (
            <p className={s.small}>Unless updated, this advisory expires at {formatIST(alert.expires_at)}.</p>
          )}
        </Section>

        <Section id="messages" title="Messages from FloodNet">
          <p>Sent only to people whose consent was recorded; no sign-up here. To stop, reply STOP to any FloodNet message.</p>
        </Section>

        <ShareButton title={docTitle(alert)} />
      </main>
      <footer className={s.footer}>
        <p>{alert.disclaimer}</p>
        <p>{alert.attribution}</p>
      </footer>
    </div>
  )
}

export default function PublicAlertApp({ publicId }) {
  const [version] = useState(() => versionFromSearch(window.location.search))
  // phase: loading | ready | notfound | error. `alert` is kept across failed refreshes.
  const [data, setData] = useState({ phase: publicId ? 'loading' : 'notfound', alert: null, checked: null })
  const [now, setNow] = useState(() => Date.now())
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    if (!publicId) return undefined
    let alive = true
    let lastCheck = 0
    const load = async () => {
      lastCheck = Date.now()
      let next
      try {
        // always revalidate: the server allows 30 s caching for crowds, but a resolved alert must show at once
        const res = await fetch(apiUrl(apiPathFor(publicId, version)), { cache: 'no-cache' })
        if (res.status === 404) next = () => ({ phase: 'notfound', alert: null, checked: null })
        else if (!res.ok) throw new Error(String(res.status))
        else {
          const alert = await res.json()
          next = () => ({ phase: 'ready', alert, checked: { ok: true, at: Date.now() } })
        }
      } catch {
        next = (prev) => (prev.alert ? { ...prev, checked: { ok: false, at: Date.now() } } : { phase: 'error', alert: null, checked: null })
      }
      if (alive) {
        setData(next)
        setNow(Date.now())
      }
    }
    load()
    const timer = setInterval(() => { if (document.visibilityState === 'visible') load() }, REFRESH_MS)
    const onVisible = () => {
      if (document.visibilityState === 'visible' && Date.now() - lastCheck > 15_000) load()
    }
    document.addEventListener('visibilitychange', onVisible)
    const clock = setInterval(() => setNow(Date.now()), 30_000)        // keeps "12 min ago" honest
    return () => {
      alive = false
      clearInterval(timer)
      clearInterval(clock)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [publicId, version, attempt])

  useEffect(() => {
    if (data.alert) document.title = docTitle(data.alert)
  }, [data.alert])

  if (data.phase === 'notfound') return <NotFound />
  if (data.phase === 'error') return <LoadFailed onRetry={() => { setData((d) => ({ ...d, phase: 'loading' })); setAttempt((n) => n + 1) }} />
  if (data.phase === 'loading' || !data.alert) {
    return (
      <div className={s.page}>
        <p className={s.strip}>{STRIP}</p>
        <main className={s.main}><p role="status">Loading the FloodNet advisory…</p></main>
      </div>
    )
  }
  return <Advisory alert={data.alert} now={now} checked={data.checked} />
}
