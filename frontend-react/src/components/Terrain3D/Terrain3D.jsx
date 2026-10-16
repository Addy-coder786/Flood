// TRUE 3D view of FloodNet's OWN terrain model.
//
// The mesh is the simulation DEM itself: GET /api/terrain/dem returns `Terrain.z` losslessly (raw float32) and
// every model cell centre becomes one vertex, in the model's own metre frame. No online terrain service, no
// second dataset, no resampling. Visual relief scales the DRAWN height only (lib/terrain3d/dem.js); the
// elevation readout always reports the DEM value. Water and flooded streets come from the SAME forecast frame
// the 2D map shows, so moving the timeline changes the overlays and never the terrain.
//
// Loaded lazily (three.js is only fetched when 3D is first opened) and mounted over the Leaflet map, which
// stays mounted underneath — switching back returns to exactly the 2D view the operator left.
import { useCallback, useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { getDem } from '../../api/client.js'
import { SEVERITY_COLOR } from '../../lib/severity.js'
import {
  decodeDem, buildPositions, buildIndices, buildColors, lonLatToGrid, cellAt, surfaceAt, drawnHeight,
  legendStops, RELIEF_OPTIONS, DEFAULT_RELIEF,
} from '../../lib/terrain3d/dem.js'
import { buildLabels, LabelLayer, PinLayer } from '../../lib/terrain3d/labels.js'
import placeData from '../../lib/terrain3d/place_labels.json'
import styles from './Terrain3D.module.css'

const SKY = 0xe9edf0
const RIBBON_HALF_WIDTH_M = 5
const DRAPE_STEP_M = 12

// Module-level cache: the DEM is static for the life of the pilot, so it is fetched and decoded once.
let demPromise = null
const loadDem = () => {
  if (!demPromise) demPromise = getDem().then(decodeDem).catch((e) => { demPromise = null; throw e })
  return demPromise
}

/** Flooded street segments -> one ribbon mesh draped on the drawn surface (vertex-coloured by severity). */
function buildRibbons(dem, features, relief, selectedSegId) {
  const pos = []
  const col = []
  const idx = []
  for (const f of features || []) {
    const p = f.properties || {}
    const isSelected = selectedSegId != null && String(p.seg_id) === String(selectedSegId)
    if (!((p.depth_cm || 0) >= 1) && !isSelected) continue
    const c = new THREE.Color(isSelected ? '#111827' : SEVERITY_COLOR[p.severity] || SEVERITY_COLOR.minor)
    const line = (f.geometry?.coordinates || []).map(([lon, lat]) => lonLatToGrid(dem, lon, lat))
    // densify so the ribbon follows the terrain between the segment's own vertices
    const pts = []
    for (let k = 0; k < line.length - 1; k += 1) {
      const [x0, y0] = line[k]; const [x1, y1] = line[k + 1]
      const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / DRAPE_STEP_M))
      for (let s = 0; s < n; s += 1) pts.push([x0 + ((x1 - x0) * s) / n, y0 + ((y1 - y0) * s) / n])
    }
    if (line.length) pts.push(line[line.length - 1])
    const half = RIBBON_HALF_WIDTH_M * (isSelected ? 1.5 : 1)
    let base = -1
    for (let k = 0; k < pts.length; k += 1) {
      const [x, y] = pts[k]
      const [ax, ay] = pts[Math.max(k - 1, 0)]; const [bx, by] = pts[Math.min(k + 1, pts.length - 1)]
      const len = Math.hypot(bx - ax, by - ay) || 1
      const nxr = (-(by - ay) / len) * half; const nyr = ((bx - ax) / len) * half
      const h = drawnHeight(dem, surfaceAt(dem, x, y), relief) + 1.2 + relief * 0.12
      const v = pos.length / 3
      pos.push(x + nxr, y + nyr, h, x - nxr, y - nyr, h)
      col.push(c.r, c.g, c.b, c.r, c.g, c.b)
      if (base >= 0) idx.push(base, base + 1, v, base + 1, v + 1, v)
      base = v
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  g.setAttribute('color', new THREE.Float32BufferAttribute(col, 3))
  g.setIndex(idx)
  return g
}

/** The road network (GET /api/roads) as thin lines draped on the drawn surface: map context under the labels. */
function buildRoadLines(dem, features, relief) {
  const pos = []
  const onDem = (x, y) => x >= 0 && y >= 0 && x <= dem.widthM && y <= dem.heightM
  for (const f of features || []) {
    if (f.geometry?.type !== 'LineString') continue
    const line = f.geometry.coordinates.map(([lon, lat]) => lonLatToGrid(dem, lon, lat))
    for (let k = 0; k < line.length - 1; k += 1) {
      const [x0, y0] = line[k]; const [x1, y1] = line[k + 1]
      const n = Math.max(1, Math.ceil(Math.hypot(x1 - x0, y1 - y0) / DRAPE_STEP_M))
      for (let s = 0; s < n; s += 1) {
        const ax = x0 + ((x1 - x0) * s) / n; const ay = y0 + ((y1 - y0) * s) / n
        const bx = x0 + ((x1 - x0) * (s + 1)) / n; const by = y0 + ((y1 - y0) * (s + 1)) / n
        if (!onDem(ax, ay) || !onDem(bx, by)) continue          // roads beyond the DEM would hang off its edge
        const lift = 0.8 + relief * 0.08
        pos.push(ax, ay, drawnHeight(dem, surfaceAt(dem, ax, ay), relief) + lift,
          bx, by, drawnHeight(dem, surfaceAt(dem, bx, by), relief) + lift)
      }
    }
  }
  const g = new THREE.BufferGeometry()
  g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3))
  return g
}

export default function Terrain3D({ onClose }) {
  const { frame, run, isStale, layers, selectedSegId, mapView, currentT, terrainFocus, roads, hotspots } = useFloodNet()
  const hostRef = useRef(null)
  const overlayRef = useRef(null) // geographic labels + flood-spot pins, re-projected on every rendered frame
  const three = useRef(null) // { renderer, scene, camera, controls, terrain, water, ribbons, markers, dem }
  const [dem, setDem] = useState(null)
  const [error, setError] = useState(null)
  const [relief, setRelief] = useState(DEFAULT_RELIEF)
  const [probe, setProbe] = useState(null)

  useEffect(() => {
    let alive = true
    loadDem().then((d) => alive && setDem(d)).catch((e) => alive && setError(e.message || 'Terrain could not be loaded'))
    return () => { alive = false }
  }, [])

  const frameCamera = useCallback((target, reset) => {
    const t = three.current
    if (!t) return
    const { dem: d, camera, controls } = t
    const tx = target?.[0] ?? d.widthM / 2
    const ty = target?.[1] ?? d.heightM / 2
    const span = Math.max(d.widthM, d.heightM)
    const dist = reset ? span * 0.95 : span * 0.55
    controls.target.set(tx, ty, drawnHeight(d, surfaceAt(d, tx, ty), t.relief))
    // look from the south-west, pitched ~35 degrees above the ground
    camera.position.set(tx - dist * 0.55, ty - dist * 0.75, controls.target.z + dist * 0.55)
    controls.update()
    t.render()
  }, [])

  // ---- scene: built once per DEM ---------------------------------------------------------------------------
  useEffect(() => {
    const host = hostRef.current
    if (!dem || !host) return undefined
    let renderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false })
    } catch {
      setError('3D terrain needs WebGL, which this browser has not made available.')
      return undefined
    }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2))
    renderer.setClearColor(SKY)
    host.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    scene.fog = new THREE.Fog(SKY, dem.widthM * 1.6, dem.widthM * 4.5)
    const camera = new THREE.PerspectiveCamera(42, 1, 5, dem.widthM * 12)
    camera.up.set(0, 0, 1) // z is elevation
    scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f96, 0.85))
    const sun = new THREE.DirectionalLight(0xfff2dd, 1.6) // low north-west sun: the cartographic convention
    sun.position.set(-dem.widthM, dem.heightM * 1.2, dem.widthM * 0.55)
    scene.add(sun)

    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.BufferAttribute(buildPositions(dem, DEFAULT_RELIEF), 3))
    geometry.setAttribute('color', new THREE.BufferAttribute(buildColors(dem), 3))
    const uv = new Float32Array(dem.nx * dem.ny * 2) // 1 forecast-grid pixel == 1 model cell
    for (let j = 0; j < dem.ny; j += 1) {
      for (let i = 0; i < dem.nx; i += 1) {
        uv[(j * dem.nx + i) * 2] = (i + 0.5) / dem.nx
        uv[(j * dem.nx + i) * 2 + 1] = (j + 0.5) / dem.ny
      }
    }
    geometry.setAttribute('uv', new THREE.BufferAttribute(uv, 2))
    geometry.setIndex(new THREE.BufferAttribute(buildIndices(dem), 1))
    geometry.computeVertexNormals()
    const terrain = new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({
      vertexColors: true, roughness: 0.95, metalness: 0, flatShading: false, side: THREE.DoubleSide,
    }))
    scene.add(terrain)

    // modelled water: the forecast's own depth grid, draped on the same vertices
    const water = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial({
      transparent: true, depthWrite: false, polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2,
      side: THREE.DoubleSide,
    }))
    water.visible = false
    scene.add(water)

    const ribbons = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial({
      vertexColors: true, side: THREE.DoubleSide, polygonOffset: true, polygonOffsetFactor: -4, polygonOffsetUnits: -4,
    }))
    scene.add(ribbons)

    const markers = new THREE.Group() // lowest / highest DEM cell
    scene.add(markers)

    const controls = new OrbitControls(camera, renderer.domElement)
    controls.enableDamping = true
    controls.dampingFactor = 0.08
    controls.maxPolarAngle = Math.PI / 2 - 0.06 // never below the ground
    controls.minDistance = 120
    controls.maxDistance = dem.widthM * 3.5
    controls.screenSpacePanning = false

    const roadLines = new THREE.LineSegments(new THREE.BufferGeometry(), new THREE.LineBasicMaterial({
      color: 0x5c5046, transparent: true, opacity: 0.4, depthWrite: false,
    }))
    scene.add(roadLines)

    // UI chrome floating over the map (side panels, toolbars, the terrain key): the label overlay, pins and the
    // elevation readout ignore the pointer, so whatever else is topmost at a point is covering the terrain there
    const coveredTest = () => {
      const r = renderer.domElement.getBoundingClientRect()           // once per frame
      return (x, y) => document.elementFromPoint(r.left + x, r.top + y) !== renderer.domElement
    }

    let raf = 0
    const render = () => {
      renderer.render(scene, camera)
      const t = three.current
      if (!t) return
      const w = host.clientWidth; const h = host.clientHeight
      const taken = t.pins ? t.pins.update(THREE, camera, dem, t.relief, w, h) : []
      t.labels?.update(THREE, camera, dem, t.relief, w, h, taken, coveredTest())
    }
    const tick = () => { raf = 0; if (controls.update()) { render(); raf = requestAnimationFrame(tick) } }
    const kick = () => { render(); if (!raf) raf = requestAnimationFrame(tick) }
    controls.addEventListener('change', kick)

    const resize = () => {
      const w = host.clientWidth || 1
      const h = host.clientHeight || 1
      renderer.setSize(w, h, false)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
      render()
    }
    const ro = new ResizeObserver(resize)
    ro.observe(host)

    // elevation inspector: ray -> terrain -> model cell -> the DEM's own value
    const ray = new THREE.Raycaster()
    const ndc = new THREE.Vector2()
    let lastMove = 0
    const onMove = (e) => {
      const now = performance.now()
      if (now - lastMove < 40) return
      lastMove = now
      const r = renderer.domElement.getBoundingClientRect()
      ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1)
      ray.setFromCamera(ndc, camera)
      const hit = ray.intersectObject(terrain, false)[0]
      const cell = hit ? cellAt(dem, hit.point.x, hit.point.y) : null
      setProbe(cell ? { elevation: cell.elevation, x: e.clientX - r.left, y: e.clientY - r.top } : null)
    }
    const onLeave = () => setProbe(null)
    renderer.domElement.addEventListener('pointermove', onMove)
    renderer.domElement.addEventListener('pointerleave', onLeave)

    three.current = {
      renderer, scene, camera, controls, terrain, water, ribbons, roadLines, markers, dem, relief: DEFAULT_RELIEF, render: kick,
      labels: null, pins: null,
    }
    resize()

    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.domElement.removeEventListener('pointermove', onMove)
      renderer.domElement.removeEventListener('pointerleave', onLeave)
      controls.dispose()
      geometry.dispose()
      ribbons.geometry.dispose()
      roadLines.geometry.dispose()
      water.material.map?.dispose()
      renderer.dispose()
      renderer.domElement.remove()
      three.current = null
    }
  }, [dem])

  // ---- initial / requested camera: keep the place the operator was looking at --------------------------------
  useEffect(() => {
    if (!dem || !three.current) return
    const focus = terrainFocus || mapView
    const inside = focus && focus.lng >= dem.bbox[0] && focus.lng <= dem.bbox[2] && focus.lat >= dem.bbox[1] && focus.lat <= dem.bbox[3]
    frameCamera(inside ? lonLatToGrid(dem, focus.lng, focus.lat) : null, !inside)
    // mapView is read once when 3D opens; later 2D pans are irrelevant while 3D is on screen
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dem, terrainFocus, frameCamera])

  // ---- visual relief: drawn heights only --------------------------------------------------------------------
  useEffect(() => {
    const t = three.current
    if (!t || !dem) return
    t.relief = relief
    const attr = t.terrain.geometry.getAttribute('position')
    attr.array.set(buildPositions(dem, relief))
    attr.needsUpdate = true
    t.terrain.geometry.computeVertexNormals()
    t.terrain.geometry.computeBoundingSphere()

    t.markers.clear()
    for (const [cell, color] of [[dem.lowest, 0x1d4ed8], [dem.highest, 0x9a3412]]) {
      if (!cell) continue
      const h = drawnHeight(dem, cell.elevation, relief)
      const pin = new THREE.Mesh(new THREE.ConeGeometry(14, 60, 12), new THREE.MeshBasicMaterial({ color }))
      pin.rotation.x = -Math.PI / 2 // point down at the cell
      pin.position.set(cell.x, cell.y, h + 34)
      t.markers.add(pin)
    }
    t.controls.target.z = drawnHeight(dem, surfaceAt(dem, t.controls.target.x, t.controls.target.y), relief)
    t.controls.update()
    t.render()
  }, [dem, relief])

  // ---- forecast overlays: follow the timeline; the terrain never changes ------------------------------------
  const live = run && !isStale ? frame : null
  useEffect(() => {
    const t = three.current
    if (!t || !dem) return undefined
    let cancelled = false
    const png = layers.depth ? live?.depth_grid?.png_base64 : null
    if (!png) {
      t.water.visible = false
      t.render()
    } else {
      new THREE.TextureLoader().load(`data:image/png;base64,${png}`, (tex) => {
        if (cancelled || !three.current) { tex.dispose(); return }
        tex.colorSpace = THREE.SRGBColorSpace
        tex.magFilter = THREE.LinearFilter
        t.water.material.map?.dispose()
        t.water.material.map = tex
        t.water.material.needsUpdate = true
        t.water.visible = true
        t.render()
      })
    }
    return () => { cancelled = true }
  }, [dem, live, layers.depth])

  useEffect(() => {
    const t = three.current
    if (!t || !dem) return
    t.ribbons.geometry.dispose()
    t.ribbons.geometry = buildRibbons(dem, layers.streets ? live?.streets?.features : [], relief, selectedSegId)
    t.render()
  }, [dem, live, layers.streets, relief, selectedSegId])

  // ---- map context: road network + geographic labels (OSM) + known flood spots (MCGM) --------------------------
  useEffect(() => {
    const t = three.current
    if (!t || !dem) return
    t.roadLines.geometry.dispose()
    t.roadLines.geometry = buildRoadLines(dem, layers.roads ? roads?.features : [], relief)
    t.render()
  }, [dem, roads, layers.roads, relief])

  useEffect(() => {
    const t = three.current
    const host = overlayRef.current
    if (!t || !dem || !host) return undefined
    const layer = new LabelLayer(host, buildLabels(dem, roads, placeData.labels), styles)
    t.labels = layer
    t.render()
    return () => { layer.dispose(); if (three.current) three.current.labels = null }
  }, [dem, roads])

  useEffect(() => {
    const t = three.current
    const host = overlayRef.current
    if (!t || !dem || !host || !layers.hotspots) return undefined
    const points = (hotspots?.features || [])
      .filter((f) => f.geometry?.type === 'Point')
      .map((f) => {
        const [x, y] = lonLatToGrid(dem, ...f.geometry.coordinates)
        return { x, y, active: f.properties?.active !== false, title: f.properties?.name || 'Known flooding spot (MCGM)' }
      })
      .filter((p) => p.x >= 0 && p.y >= 0 && p.x <= dem.widthM && p.y <= dem.heightM)
    const layer = new PinLayer(host, points, styles)
    t.pins = layer
    t.render()
    return () => { layer.dispose(); if (three.current) { three.current.pins = null; three.current.render() } }
  }, [dem, hotspots, layers.hotspots])

  const stops = dem ? legendStops(dem) : []

  return (
    <div className={styles.root} data-tour="terrain-3d">
      <div ref={hostRef} className={styles.canvasHost} />
      <div ref={overlayRef} className={styles.overlay} aria-hidden="true" />

      {!dem && !error && <div className={styles.state}>Loading terrain model…</div>}
      {error && (
        <div className={styles.state} role="alert">
          {error}
          <button type="button" className={styles.linkBtn} onClick={onClose}>Back to 2D map</button>
        </div>
      )}

      {dem && (
        <>
          <div className={`${styles.panel} glass-panel`}>
            <div className={styles.panelTitle}>Terrain · MCGM DTM</div>
            <div className={styles.ramp} style={{ background: `linear-gradient(90deg, ${stops.map((s) => `rgb(${s.color.map((v) => Math.round(v * 255)).join(',')})`).join(',')})` }} />
            <div className={styles.rampLabels}>
              {stops.map((s) => <span key={s.elevation}>{s.elevation.toFixed(1)} m</span>)}
            </div>
            <div className={styles.row}>
              <span className={styles.rowLabel}>{relief}× visual relief</span>
              <span className={styles.seg}>
                {RELIEF_OPTIONS.map((r) => (
                  <button key={r} type="button" className={`${styles.segBtn} ${r === relief ? styles.segBtnOn : ''}`} onClick={() => setRelief(r)} aria-pressed={r === relief}>
                    {r}×
                  </button>
                ))}
              </span>
            </div>
            <div className={styles.row}>
              <span className={styles.rowLabel}>{live ? `Flood at T+${currentT} min` : 'No forecast on screen'}</span>
              <button type="button" className={styles.resetBtn} onClick={() => frameCamera(null, true)}>Reset terrain view</button>
            </div>
          </div>

          {probe && (
            <div className={styles.probe} style={{ left: probe.x + 14, top: probe.y + 14 }}>
              Elevation <strong>{probe.elevation.toFixed(1)} m</strong>
            </div>
          )}
        </>
      )}
    </div>
  )
}
