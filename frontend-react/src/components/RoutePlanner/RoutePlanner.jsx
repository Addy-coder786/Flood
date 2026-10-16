import { useEffect, useMemo, useRef, useState } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { fmt } from '../../lib/format.js'
import { buildLocationIndex, searchLocations, findLocationByCoords, PILOT_AREAS } from '../../lib/locations.js'
import { exposureOf } from '../../lib/routeExposure.js'
import styles from './RoutePlanner.module.css'

// Matches the backend's VEHICLE_LIMIT_CM keys (see lib/severity.js / config.py).
const VEHICLES = [
  { value: 'car', label: 'Car (30 cm limit)' },
  { value: 'ambulance', label: 'Ambulance (40 cm limit)' },
  { value: 'motorcycle', label: 'Motorcycle (30 cm limit)' },
  { value: 'truck', label: 'Truck (60 cm limit)' },
  { value: 'pedestrian', label: 'Pedestrian (60 cm limit)' },
]

function parseLonLat(text) {
  const parts = String(text ?? '')
    .split(',')
    .map((s) => Number(s.trim()))
  return parts.length === 2 && parts.every(Number.isFinite) ? parts : null
}

function fmtLonLat(lonlat) {
  return lonlat ? `${lonlat[0].toFixed(5)},${lonlat[1].toFixed(5)}` : ''
}

// Labels must stay honest about what each objective actually measures: "fastest" is DISTANCE only -- there
// is no travel-time/speed model anywhere in the graph (see backend/floodnet/routing/router.py), so it must
// never read just "Fastest" anywhere in this UI.
const OBJECTIVE_LABEL = { safest: 'Lowest flood-risk', fastest: 'Shortest (by distance)', balanced: 'Balanced' }

// FROM/TO search box: a plain text input (also still accepts a typed "lon,lat") plus a live-filtered
// dropdown of real pilot locations (named OSM roads + the 4 pilot landmarks -- see lib/locations.js). Kept
// local to RoutePlanner since it's only ever used for its two fields. Open/close mirrors LocationSelector's
// own click-outside pattern (Header/../LocationSelector/LocationSelector.jsx) for a consistent feel.
export function LocationSearchField({ id, label, placeholder, value, onChange, results, onSelect, selectedLabel }) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef(null)

  useEffect(() => {
    function handleClickOutside(e) {
      if (wrapRef.current && !wrapRef.current.contains(e.target)) setOpen(false)
    }
    if (open) {
      document.addEventListener('mousedown', handleClickOutside)
      return () => document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [open])

  return (
    <div className={styles.field}>
      <label className="field-label" htmlFor={id}>
        {label}
      </label>
      <div className={styles.searchWrap} ref={wrapRef}>
        <input
          id={id}
          type="text"
          autoComplete="off"
          placeholder={placeholder}
          value={value}
          onChange={(e) => {
            onChange(e.target.value)
            setOpen(true)
          }}
          onFocus={(e) => {
            setOpen(true)
            e.target.select()
          }}
        />
        {open && results.length > 0 && (
          <div className={styles.searchDropdown} role="listbox">
            {results.map((r) => (
              <button
                key={r.id}
                type="button"
                className={styles.searchItem}
                role="option"
                aria-selected="false"
                onClick={() => {
                  onSelect(r)
                  setOpen(false)
                }}
              >
                <span className={styles.searchItemName}>{r.name}</span>
                {r.context && <span className={styles.searchItemContext}>{r.context}</span>}
              </button>
            ))}
          </div>
        )}
      </div>
      {selectedLabel && <div className={styles.selectedCaption}>{selectedLabel}</div>}
    </div>
  )
}

export default function RoutePlanner() {
  const {
    route, planRoute, clearRoute, setRouteVehicle, pickPoint, roads, currentT,
    alternatives, alternativesStale, planRouteAlternatives, selectAlternative,
  } = useFloodNet()

  const [originText, setOriginText] = useState('')
  const [destText, setDestText] = useState('')
  const vehicle = route.vehicle || 'car'
  const [formError, setFormError] = useState(null)

  // Map-click picking (MapView -> Context.pickPoint) is the primary flow; mirror whatever it sets into the
  // manual text inputs so both entry paths always show the same current origin/destination.
  useEffect(() => {
    setOriginText(fmtLonLat(route.origin))
    setDestText(fmtLonLat(route.dest))
  }, [route.origin, route.dest])

  // seg_id -> name, for showing readable avoided-street names (avoided_segments from /api/route is just a
  // list of id strings) -- built from the roads FeatureCollection already loaded into Context.
  const segNameById = useMemo(() => {
    const m = new Map()
    for (const f of roads?.features || []) {
      if (f.properties?.seg_id != null) m.set(String(f.properties.seg_id), f.properties.name)
    }
    return m
  }, [roads])

  // FROM/TO search index: named OSM roads (from the same `roads` FeatureCollection above) + the 4 pilot
  // landmarks already used by Header's LocationSelector. Real data only -- see lib/locations.js.
  const locationIndex = useMemo(() => buildLocationIndex(roads, PILOT_AREAS), [roads])
  const originResults = useMemo(() => searchLocations(locationIndex, originText), [locationIndex, originText])
  const destResults = useMemo(() => searchLocations(locationIndex, destText), [locationIndex, destText])
  const originMatch = useMemo(() => findLocationByCoords(locationIndex, route.origin), [locationIndex, route.origin])
  const destMatch = useMemo(() => findLocationByCoords(locationIndex, route.dest), [locationIndex, route.dest])

  // Selecting a search result picks that point exactly the way a map click does -- same pickPoint(), same
  // "first pick = origin, second pick = destination + auto-plan" state machine (state/FloodNetContext.jsx),
  // so search and map-click are just two ways of feeding the same flow and can be freely mixed.
  const handleSelectOrigin = (loc) => pickPoint(loc.coords)
  const handleSelectDest = (loc) => pickPoint(loc.coords)

  const handleFind = () => {
    const origin = parseLonLat(originText)
    const dest = parseLonLat(destText)
    if (!origin || !dest) {
      setFormError('Select a FROM and TO location from search (or click the map twice, or type "lon,lat"), then try again.')
      return
    }
    setFormError(null)
    planRoute({ origin, dest, vehicle, tMin: currentT })
  }

  const handleClear = () => {
    setFormError(null)
    clearRoute()
  }

  const handleCompare = () => {
    const origin = parseLonLat(originText)
    const dest = parseLonLat(destText)
    if (!origin || !dest) {
      setFormError('Select a FROM and TO location from search (or click the map twice, or type "lon,lat"), then try again.')
      return
    }
    setFormError(null)
    planRouteAlternatives({ origin, dest, vehicle, tMin: currentT })
  }

  const result = route.result
  const evalT = result?.t_min != null ? result.t_min : currentT
  const unreachable = Boolean(result && result.reachable === false)
  const reachableResult = Boolean(result && result.reachable !== false)

  const avoided = result?.avoided_segments || []
  const avoidedNames = avoided.map((id) => segNameById.get(String(id)) || id)
  const detourM = result?.length_m != null && result?.baseline_length_m != null ? result.length_m - result.baseline_length_m : null

  const altResult = alternatives.result
  const candidates = altResult?.candidates || []
  const altUnreachable = Boolean(altResult && altResult.reachable === false)
  const altEvalT = altResult?.t_min != null ? altResult.t_min : currentT

  return (
    <section className={styles.section} data-tour="panel-route">
      <div className={styles.headRow}>
        <div className="panel-heading">Emergency Route</div>
        <span className={`tag-badge tag-ESTIMATED ${styles.capBadge}`} title="Traffic conditions are not included.">
          FLOOD-RISK ROUTING
        </span>
      </div>

      <div className={styles.field}>
        <label className="field-label" htmlFor="rp-vehicle">
          Vehicle type
        </label>
        <select id="rp-vehicle" value={vehicle} onChange={(e) => setRouteVehicle(e.target.value)}>
          {VEHICLES.map((v) => (
            <option key={v.value} value={v.value}>
              {v.label}
            </option>
          ))}
        </select>
      </div>

      <LocationSearchField
        id="rp-origin"
        label="From"
        placeholder="Search a pilot street or landmark…"
        value={originText}
        onChange={setOriginText}
        results={originResults}
        onSelect={handleSelectOrigin}
        selectedLabel={
          originMatch ? `Selected: ${originMatch.name}` : route.origin ? `Selected: ${fmtLonLat(route.origin)}` : null
        }
      />
      <LocationSearchField
        id="rp-dest"
        label="To"
        placeholder="Search a pilot street or landmark…"
        value={destText}
        onChange={setDestText}
        results={destResults}
        onSelect={handleSelectDest}
        selectedLabel={destMatch ? `Selected: ${destMatch.name}` : route.dest ? `Selected: ${fmtLonLat(route.dest)}` : null}
      />
      <div className={styles.hint}>Search a pilot street or landmark, or click the map twice.</div>

      <div className={styles.buttons} data-tour="route-controls">
        <button className="btn btn-primary btn-block" onClick={handleFind} disabled={route.loading}>
          {route.loading ? 'Evaluating route…' : `Assess lower flood-risk route at T+${currentT} min`}
        </button>
        <button className="btn" onClick={handleClear}>
          Clear
        </button>
      </div>
      <div className={styles.buttons}>
        <button className="btn btn-block" onClick={handleCompare} disabled={alternatives.loading}>
          {alternatives.loading
            ? 'Comparing routes…'
            : alternativesStale
              ? `Recompute options at T+${currentT} min`
              : `Compare route options at T+${currentT} min`}
        </button>
      </div>

      {formError && <div className={styles.errBox}>{formError}</div>}
      {route.error && <div className={styles.errBox}>{route.error}</div>}
      {alternatives.error && <div className={styles.errBox}>{alternatives.error}</div>}

      {!result && !route.loading && !route.error && (route.origin || route.dest) && (
        <div className={styles.pickStatus}>
          {route.origin && !route.dest ? 'Origin set — click map for destination.' : `Ready to evaluate route at T+${currentT} min.`}
        </div>
      )}

      {unreachable && (
        <div className={styles.alertUnsafe} role="alert">
          <div className={styles.alertHeader}>
            <span className={styles.alertBadge}>NO PASSABLE ROUTE</span>
            <span className={styles.alertTime}>ASSESSED AT T+{evalT} MIN</span>
          </div>
          <div className={styles.alertBody}>
            All available paths exceed the {result.vehicle || vehicle} depth threshold
            {result.vehicle_limit_cm != null ? ` (${Math.round(result.vehicle_limit_cm)} cm)` : ''} at T+{evalT} min.
          </div>
        </div>
      )}

      {reachableResult && (
        <div className={styles.resultBox} data-tour="route-result">
          <div className={styles.odRow}>
            <span className={styles.odEnd}>{originMatch?.name || fmtLonLat(route.origin) || 'Origin'}</span>
            <span className={styles.odArrow} aria-hidden="true">→</span>
            <span className={styles.odEnd}>{destMatch?.name || fmtLonLat(route.dest) || 'Destination'}</span>
          </div>
          <div className={styles.resultHeader}>
            <span className={styles.safeBadge}>LOWER FLOOD-RISK ROUTE · T+{evalT} MIN</span>
            {result.max_depth_on_route_cm != null && result.vehicle_limit_cm != null && (
              <span className={styles.decisionRule}>
                {Math.round(result.max_depth_on_route_cm)} cm / {Math.round(result.vehicle_limit_cm)} cm max
              </span>
            )}
          </div>
          {(() => {
            const direct = exposureOf(result.baseline_max_depth_cm, result.vehicle_limit_cm)
            const chosen = exposureOf(result.max_depth_on_route_cm, result.vehicle_limit_cm)
            if (!direct || !chosen) return null
            const same = detourM == null || detourM <= 5
            return (
              <div className={styles.compare}>
                <div className={styles.compareRow}>
                  <span className={styles.compareName}>A · Direct route</span>
                  <span className={styles.compareMeta}>{fmt(result.baseline_length_m, 0)} m · {fmt(result.baseline_max_depth_cm, 0)} cm</span>
                  <span className={`${styles.expo} ${styles[`expo_${direct.tone}`]}`}>Flood exposure: {direct.label}</span>
                </div>
                <div className={`${styles.compareRow} ${styles.compareRowOn}`}>
                  <span className={styles.compareName}>B · {same ? 'Same path' : 'Alternative'}</span>
                  <span className={styles.compareMeta}>{fmt(result.length_m, 0)} m · {fmt(result.max_depth_on_route_cm, 0)} cm</span>
                  <span className={`${styles.expo} ${styles[`expo_${chosen.tone}`]}`}>Flood exposure: {chosen.label}</span>
                </div>
                <div className={styles.compareWhy}>
                  <strong>Why:</strong>{' '}
                  {same
                    ? 'the direct route is already within this vehicle’s depth limit.'
                    : avoided.length > 0
                      ? `avoids ${avoided.length} segment${avoided.length > 1 ? 's' : ''} above the ${Math.round(result.vehicle_limit_cm)} cm limit${avoidedNames[0] ? ` (${avoidedNames.slice(0, 2).join(', ')})` : ''}.`
                      : 'passes through shallower forecast water than the direct route.'}
                </div>
              </div>
            )
          })()}
          <div className={styles.resultRow}>
            <span className={styles.resultLabel}>Route distance</span>
            <span className={styles.resultValue}>{fmt(result.length_m, 0)} m</span>
          </div>
          <div className={styles.resultRow}>
            <span className={styles.resultLabel}>Direct (baseline) distance</span>
            <span className={styles.resultValue}>
              {fmt(result.baseline_length_m, 0)} m
              {detourM != null && detourM > 5 ? ` (+${fmt(detourM, 0)} m detour)` : ''}
            </span>
          </div>
          <div className={styles.resultRow}>
            <span className={styles.resultLabel}>Max depth on route</span>
            <span className={styles.resultValue}>
              {result.max_depth_on_route_cm != null ? `${fmt(result.max_depth_on_route_cm, 0)} cm` : 'not available'}
            </span>
          </div>
          <div className={styles.resultRow}>
            <span className={styles.resultLabel}>Flooded segments avoided</span>
            <span className={styles.resultValue}>
              {avoided.length}
              {avoided.length > 0 && (
                <span className={styles.avoidedList}>
                  {': '}
                  {avoidedNames.slice(0, 3).join(', ')}
                  {avoidedNames.length > 3 ? ` +${avoidedNames.length - 3} more` : ''}
                </span>
              )}
            </span>
          </div>
        </div>
      )}

      {altUnreachable && (
        <div className={styles.alertUnsafe} role="alert">
          <div className={styles.alertHeader}>
            <span className={styles.alertBadge}>NO CANDIDATES REACHABLE</span>
            <span className={styles.alertTime}>ASSESSED AT T+{altEvalT} MIN</span>
          </div>
          <div className={styles.alertBody}>
            Every candidate path exceeds the {altResult.vehicle || vehicle} depth threshold
            {altResult.vehicle_limit_cm != null ? ` (${Math.round(altResult.vehicle_limit_cm)} cm)` : ''} at T+{altEvalT} min.
          </div>
        </div>
      )}

      {candidates.length > 0 && (
        <div className={`${styles.resultBox}${alternativesStale ? ` ${styles.resultBoxStale}` : ''}`}>
          <div className={styles.resultHeader}>
            <span className={alternativesStale ? styles.staleBadge : styles.safeBadge}>
              {candidates.length} ROUTE OPTIONS AT T+{altEvalT} MIN
            </span>
          </div>
          {/* Alternatives are never auto-recomputed on scrub (deliberate -- too costly), so once the
              timeline moves they disagree with everything else on screen. Say so explicitly rather than
              letting an old result keep the styling of a fresh one. */}
          {alternativesStale && (
            <div className={styles.snapshotBar} role="status">
              <span>Route snapshot: T+{altEvalT}</span>
              <button type="button" className={styles.snapshotBtn} onClick={handleCompare} disabled={alternatives.loading}>
                {alternatives.loading ? 'Updating…' : `Update for T+${currentT}`}
              </button>
            </div>
          )}
          {candidates.map((c, i) => {
            const isSelected = alternatives.selected === i
            const labels = (c.objectives || []).map((o) => OBJECTIVE_LABEL[o] || o).join(' + ')
            return (
              <button
                key={i}
                type="button"
                className={`${styles.candidateCard}${isSelected ? ` ${styles.candidateCardSelected}` : ''}`}
                onClick={() => selectAlternative(i)}
              >
                <div className={styles.candidateLabel}>{labels || 'Alternative'}</div>
                <div className={`${styles.candidateMetrics}${alternativesStale ? ` ${styles.candidateMetricsStale}` : ''}`}>
                  {fmt(c.length_m, 0)} m &middot; max depth {fmt(c.max_depth_on_route_cm, 0)} cm
                  {alternativesStale ? ` (at T+${altEvalT} min)` : ''}
                </div>
                {c.time_safety && <div className={styles.candidateTimeSafety}>{c.time_safety.status}</div>}
              </button>
            )
          })}
        </div>
      )}
    </section>
  )
}
