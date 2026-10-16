// Map for the public alert page. The default is an inline SVG (no tile server, no Leaflet): roads are projected into
// the bbox the API sends (equirectangular, north up; src/lib/publicAlert.js). Affected-road classes differ by line
// pattern and words, never colour alone; a text legend repeats each pattern in words.
// Interactive (2026-10-11): zoom buttons, drag to move when zoomed in, and tap a road (or pick it from the list under
// the map, which also works with a keyboard) to see what is known about it. An optional street map (Leaflet with the
// dashboard's OpenStreetMap tiles, src/public/TileMap.jsx) is downloaded only when the reader asks for it.
import { useRef, useState } from 'react'
import {
  ROAD_CLASSES, affectedRoadList, legendRows, makeProjector, mapAriaLabel, midPoint, panView, pathD, polygonD,
  roadInfo, zoomView,
} from '../lib/publicAlert.js'
import s from './PublicAlertApp.module.css'

const W = 360
const PAD = 14

function Cross({ at, scale }) {
  if (!at) return null
  const [x, y] = at
  return (
    <g className={s.cross} transform={`translate(${x} ${y}) scale(${scale})`}>
      <circle r="8" />
      <path d="M-4 -4L4 4M4 -4L-4 4" />
    </g>
  )
}

function Sample({ cls }) {
  const c = ROAD_CLASSES[cls]
  return (
    <svg className={s.sample} viewBox="0 0 48 16" width="48" height="16" aria-hidden="true">
      <path className={s.halo} d="M4 8H44" />
      <path className={`${s.affected} ${cls === 'REPORTED' ? s.dotted : ''}`} d="M4 8H44" strokeDasharray={c.dash || undefined} />
      {c.cross && <g className={s.cross} transform="translate(24 8)"><circle r="6" /><path d="M-3 -3L3 3M3 -3L-3 3" /></g>}
    </svg>
  )
}

function StreetMap({ map, selected, onSelect }) {
  const [state, setState] = useState({ phase: 'off', Comp: null })
  const open = () => {
    setState({ phase: 'loading', Comp: null })
    import('./TileMap.jsx')
      .then((m) => setState({ phase: 'on', Comp: m.default }))
      .catch(() => setState({ phase: 'failed', Comp: null }))
  }
  if (state.phase === 'on' && state.Comp) {
    const Comp = state.Comp
    return (
      <div className={s.street}>
        <Comp map={map} selected={selected} onSelect={onSelect} />
        <button type="button" className={s.toolBtn} onClick={() => setState({ phase: 'off', Comp: null })}>Hide street map</button>
      </div>
    )
  }
  return (
    <div className={s.street}>
      <button type="button" className={s.toolBtn} onClick={open} disabled={state.phase === 'loading'}>
        {state.phase === 'loading' ? 'Loading street map…' : 'Show street map'}
      </button>
      <p className={s.small}>
        {state.phase === 'failed' ? 'The street map could not be loaded. The map above still shows the affected roads. '
          : ''}
        Loads map images from OpenStreetMap (their server sees the request).
      </p>
    </div>
  )
}

export default function AlertMap({ map }) {
  const [view, setView] = useState(null)              // null: the whole map
  const [selected, setSelected] = useState(null)      // key of an affectedRoadList group
  const drag = useRef(null)
  const dragged = useRef(false)
  const svgRef = useRef(null)
  const affected = map?.affected_roads || []
  const proj = makeProjector(map?.bbox, W, PAD)
  if (!proj || !affected.length) {
    return <p>No map for this version: it lists no affected roads.</p>
  }
  const h = Math.round(proj.height * 10) / 10
  const full = { x: 0, y: 0, w: W, h }
  const v = view || full
  const zoomed = v.w < full.w - 0.01
  const scale = v.w / full.w
  const rows = legendRows(map)
  const groups = affectedRoadList(map)
  const sel = groups.find((g) => g.key === selected) || null
  const selIdx = new Set(sel ? sel.indices : [])
  const info = sel ? roadInfo({ name: sel.name, class: sel.cls, depth_cm: sel.depth_cm }) : null

  const selectIndex = (i) => {
    const g = groups.find((x) => x.indices.includes(i))
    if (g) setSelected(g.key)
  }
  const zoomBy = (f) => setView(zoomView(v, full, f, [v.x + v.w / 2, v.y + v.h / 2]))
  const onPointerDown = (e) => {
    dragged.current = false
    if (!zoomed) return
    drag.current = { x: e.clientX, y: e.clientY, view: v }
  }
  const onPointerMove = (e) => {
    const d = drag.current
    if (!d || !svgRef.current) return
    const k = d.view.w / (svgRef.current.getBoundingClientRect().width || 1)
    const dx = e.clientX - d.x
    const dy = e.clientY - d.y
    if (Math.hypot(dx, dy) > 4) dragged.current = true
    setView(panView(d.view, full, -dx * k, -dy * k))
  }
  const endDrag = () => { drag.current = null }
  const onRoadClick = (i) => {
    if (dragged.current) { dragged.current = false; return }
    selectIndex(i)
  }

  return (
    <figure className={s.figure}>
      <div className={s.mapTools}>
        <button type="button" className={s.toolBtn} onClick={() => zoomBy(2)} disabled={v.w <= full.w / 8 + 0.01}>Zoom in</button>
        <button type="button" className={s.toolBtn} onClick={() => zoomBy(0.5)} disabled={!zoomed}>Zoom out</button>
        <button type="button" className={s.toolBtn} onClick={() => setView(null)} disabled={!zoomed}>Whole area</button>
      </div>
      <svg ref={svgRef} className={`${s.map} ${zoomed ? s.mapZoomed : ''}`} viewBox={`${v.x} ${v.y} ${v.w} ${v.h}`}
        width="100%" role="img" aria-label={mapAriaLabel(map)} preserveAspectRatio="xMidYMid meet"
        onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={endDrag} onPointerLeave={endDrag}
        onPointerCancel={endDrag}>
        <rect className={s.mapBg} x="0" y="0" width={W} height={h} />
        {map.area_outline && <path className={s.area} vectorEffect="non-scaling-stroke" d={polygonD(map.area_outline.coordinates, proj)} />}
        {(map.context_roads || []).map((r, i) => (
          <path key={`c${i}`} className={s.context} vectorEffect="non-scaling-stroke" d={pathD(r.coordinates, proj)} />
        ))}
        {affected.map((r, i) => (
          <path key={`h${i}`} className={`${s.halo} ${selIdx.has(i) ? s.haloOn : ''}`} vectorEffect="non-scaling-stroke"
            d={pathD(r.coordinates, proj)} />
        ))}
        {affected.map((r, i) => {
          const c = ROAD_CLASSES[r.class] || ROAD_CLASSES.MODEL_ESTIMATE
          return (
            <path key={`a${i}`} className={`${s.affected} ${r.class === 'REPORTED' ? s.dotted : ''} ${selIdx.has(i) ? s.affectedOn : ''}`}
              vectorEffect="non-scaling-stroke" d={pathD(r.coordinates, proj)} strokeDasharray={c.dash || undefined} />
          )
        })}
        {affected.filter((r) => r.class === 'CONFIRMED_CLOSURE').map((r, i) => (
          <Cross key={`x${i}`} at={midPoint(r.coordinates, proj)} scale={scale} />
        ))}
        {affected.map((r, i) => (
          <path key={`t${i}`} className={s.hit} vectorEffect="non-scaling-stroke" d={pathD(r.coordinates, proj)}
            onClick={() => onRoadClick(i)} />
        ))}
      </svg>
      <p className={s.small}>Tap a road for details{zoomed ? '; drag to move the map' : ''}. North is up.</p>
      <div className={s.roadInfo} role="status" aria-live="polite">
        {info ? (
          <>
            <p><strong>{info.title}</strong> · {info.label}</p>
            {info.lines.map((t) => <p key={t}>{t}</p>)}
          </>
        ) : <p className={s.small}>No road selected.</p>}
      </div>
      <ul className={s.roadList} aria-label="Affected roads">
        {groups.map((g) => (
          <li key={g.key}>
            <button type="button" className={`${s.roadBtn} ${g.key === selected ? s.roadBtnOn : ''}`} aria-pressed={g.key === selected}
              onClick={() => setSelected(g.key === selected ? null : g.key)}>
              <span>{g.name}</span>
              <span className={s.small}>{ROAD_CLASSES[g.cls]?.label || 'Affected road'}{g.depth_cm != null ? ` · up to ${g.depth_cm} cm (model)` : ''}</span>
            </button>
          </li>
        ))}
      </ul>
      <StreetMap map={map} selected={sel ? sel.indices.join(',') : ''} onSelect={selectIndex} />
      <figcaption>
        <ul className={s.legend}>
          {rows.map((r) => (
            <li key={r.key} className={s.legendRow}>
              <Sample cls={r.key} />
              <span>
                <strong>{r.label}</strong>: {r.words}
                {r.count === 0 && <em className={s.none}> – none in this version</em>}
              </span>
            </li>
          ))}
          <li className={s.legendRow}>
            <svg className={s.sample} viewBox="0 0 48 16" width="48" height="16" aria-hidden="true"><path className={s.context} d="M4 8H44" /></svg>
            <span>Thin grey lines: other main roads</span>
          </li>
          <li className={s.legendRow}>
            <svg className={s.sample} viewBox="0 0 48 16" width="48" height="16" aria-hidden="true"><rect className={s.area} x="5" y="2" width="38" height="12" /></svg>
            <span>Shaded area: advisory area</span>
          </li>
        </ul>
        <p className={s.small}>Map data © OpenStreetMap contributors (ODbL).</p>
      </figcaption>
    </figure>
  )
}
