/* FloodNet dashboard — vanilla ES module, Leaflet 1.9.4. No build step.
   Talks to the FastAPI backend at /api/* (same origin). */

const $ = (id) => document.getElementById(id);
const SEV_COLOR = { clear: '#8a94a6', minor: '#ffd23f', moderate: '#ff8c1a', severe: '#ff3b3b', critical: '#8b0000' };
const FRAME_MS = 600;

const S = {
  meta: null, scenarios: [], topology: null, hotspots: null,
  run: null,            // last /api/simulate response
  frames: new Map(),    // t_min -> frame (for current run_id)
  series: null,         // /series for current run
  compare: null,        // /api/compare response
  t: 0, playing: false, timer: null,
  nearLonLat: null,     // blockage centre for "near"
  routePts: [],         // [origin, dest] as [lon,lat]
};

/* ---------------------------------------------------------------- utils */
async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  let res;
  try { res = await fetch(path, opt); }
  catch (e) { throw new Error(`Backend unreachable (${path})`); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* non-JSON */ }
  if (!res.ok) throw new Error((data && data.detail) ? `${res.status}: ${data.detail}` : `${res.status} on ${path}`);
  return data;
}
function status(msg, kind = 'error') {
  const el = $('status-bar');
  if (!msg) { el.hidden = true; return; }
  el.hidden = false; el.textContent = msg; el.className = 'status' + (kind === 'info' ? ' info' : '');
  if (kind === 'info') setTimeout(() => { if (el.textContent === msg) el.hidden = true; }, 5000);
}
function tag(p) {
  const t = (typeof p === 'string' ? p : (p && (p.tag || p.data_tag))) || 'UNKNOWN';
  const key = String(t).toUpperCase().replace(/^TAG\./, '');
  return `<span class="tag tag-${['REAL','ESTIMATED','SYNTHETIC','NWP'].includes(key) ? key : 'UNKNOWN'}">${key}</span>`;
}
function esc(s) { return String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
const fmt = (x, d = 1) => (x == null || isNaN(x)) ? '–' : Number(x).toFixed(d);
function bboxToBounds(b) { return [[b[1], b[0]], [b[3], b[2]]]; }        // [w,s,e,n] -> [[s,w],[n,e]]
function lonlatToLatLng(c) { return [c[1], c[0]]; }
function severityOf(cm) { return cm < 5 ? 'clear' : cm < 15 ? 'minor' : cm < 30 ? 'moderate' : cm < 60 ? 'severe' : 'critical'; }

/* ---------------------------------------------------------------- map & layers */
const map = L.map('map', { zoomControl: true, preferCanvas: true }).setView([19.02, 72.845], 15);
map.zoomControl.setPosition('bottomright');
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '&copy; OpenStreetMap contributors' }).addTo(map);

const LAYERS = {
  roads:    { label: 'Roads (OSM)', layer: L.layerGroup(), on: true },
  edges:    { label: 'Drainage edges', layer: L.layerGroup(), on: true },
  nodes:    { label: 'Drainage nodes', layer: L.layerGroup(), on: true },
  hotspots: { label: 'BMC flooding spots', layer: L.layerGroup(), on: true },
  terrain:  { label: 'Terrain (DEM)', layer: L.layerGroup(), on: false },
  depth:    { label: 'Water depth grid', layer: L.layerGroup(), on: true },
  streets:  { label: 'Street flooding', layer: L.layerGroup(), on: true },
  surch:    { label: 'Surcharging nodes', layer: L.layerGroup(), on: true },
  route:    { label: 'Route', layer: L.layerGroup(), on: true },
};
const roadIndex = new Map();   // seg_id -> polyline (for zoom-to)
let terrainOverlay = null, depthOverlay = null;
const svgR = L.svg();   // SVG renderer so CSS pulse animation works (map default is canvas)

function renderLayerToggles(provByLayer = {}) {
  $('layers').innerHTML = Object.entries(LAYERS).map(([k, v]) =>
    `<label><input type="checkbox" data-layer="${k}" ${v.on ? 'checked' : ''}> ${v.label} ${provByLayer[k] ? tag(provByLayer[k]) : ''}</label>`).join('');
  $('layers').querySelectorAll('input').forEach(cb => cb.addEventListener('change', e => {
    const L_ = LAYERS[e.target.dataset.layer]; L_.on = e.target.checked;
    L_.on ? L_.layer.addTo(map) : map.removeLayer(L_.layer);
  }));
  Object.values(LAYERS).forEach(v => v.on && v.layer.addTo(map));
}

function drawRoads(fc) {
  LAYERS.roads.layer.clearLayers(); roadIndex.clear();
  (fc.features || []).forEach(f => {
    const pl = L.polyline(f.geometry.coordinates.map(lonlatToLatLng), { color: '#6b7280', weight: 1.2, opacity: .7, interactive: false });
    roadIndex.set(f.properties.seg_id, pl); pl.addTo(LAYERS.roads.layer);
  });
}
function drawTopology(topo) {
  LAYERS.edges.layer.clearLayers(); LAYERS.nodes.layer.clearLayers();
  const caps = (topo.edges || []).map(e => e.capacity_m3s || 0); const cmax = Math.max(1e-6, ...caps);
  (topo.edges || []).forEach(e => {
    const w = 1 + 4 * Math.sqrt((e.capacity_m3s || 0) / cmax);
    const pl = L.polyline((e.geom || []).map(lonlatToLatLng), { color: '#3aa0ff', weight: w, opacity: .7 });
    pl.bindTooltip(`<b>Drain ${esc(e.id)}</b><br>${esc(e.shape || '')} ${fmt(e.width_m, 2)}×${fmt(e.height_m, 2)} m<br>capacity ${fmt(e.capacity_m3s, 2)} m³/s<br>status ${esc(e.status || '')}${e.blockage ? ` · blockage ${fmt(e.blockage * 100, 0)}%` : ''}`);
    pl._edgeId = e.id; pl.addTo(LAYERS.edges.layer);
  });
  (topo.nodes || []).forEach(n => {
    const m = n.is_outfall
      ? L.rectangle([[n.lat - 3e-5, n.lon - 3e-5], [n.lat + 3e-5, n.lon + 3e-5]], { color: '#00f59b', weight: 2, fillOpacity: .5 })
      : L.circleMarker([n.lat, n.lon], { radius: 2.5, color: '#7ec8ff', weight: 1, fillOpacity: .6 });
    m.bindTooltip(`<b>${n.is_outfall ? 'Outfall' : 'Node'} ${esc(n.id)}</b><br>ground ${fmt(n.ground_m, 2)} m · invert ${fmt(n.invert_m, 2)} m`);
    m.addTo(LAYERS.nodes.layer);
  });
}
function drawHotspots(fc) {
  LAYERS.hotspots.layer.clearLayers();
  (fc.features || []).forEach(f => {
    const [lon, lat] = f.geometry.coordinates; const p = f.properties || {};
    const m = L.circleMarker([lat, lon], { radius: 7, color: '#ff8c1a', weight: 2, fillColor: '#ff8c1a', fillOpacity: p.active === false ? 0 : .75 });
    m.bindTooltip(`<b>${esc(p.name)}</b> ${tag(p.provenance)}<br>ward ${esc(p.ward)} · ${esc(p.affect_road || '')}<br>${esc(p.depth_attr || '')}${p.active === false ? '<br><i>inactive</i>' : ''}`);
    m.addTo(LAYERS.hotspots.layer);
  });
}
function drawTerrain(t) {
  if (!t || !t.png_base64) return;
  terrainOverlay = L.imageOverlay('data:image/png;base64,' + t.png_base64, bboxToBounds(t.bbox_lonlat), { opacity: +$('terrain-opacity').value, interactive: false });
  terrainOverlay.addTo(LAYERS.terrain.layer);
}

/* ---------------------------------------------------------------- frame rendering */
function drawFrame(fr) {
  // streets
  LAYERS.streets.layer.clearLayers();
  const feats = (fr.streets && fr.streets.features) || [];
  feats.forEach(f => {
    const p = f.properties || {}; const d = p.depth_cm || 0; const sev = p.severity || severityOf(d);
    if (sev === 'clear') return;
    const pl = L.polyline(f.geometry.coordinates.map(lonlatToLatLng), { color: SEV_COLOR[sev] || '#fff', weight: 2 + Math.min(8, d / 8), opacity: .95 });
    pl.bindTooltip(`<b>${esc(p.name || p.seg_id)}</b><br>depth ${fmt(d, 0)} cm · ${sev}<br>car ${p.passable_car ? 'OK' : 'NO'} · ambulance ${p.passable_ambulance ? 'OK' : 'NO'}`);
    pl.addTo(LAYERS.streets.layer);
  });
  // surcharging nodes
  LAYERS.surch.layer.clearLayers();
  (fr.nodes || []).filter(n => n.surcharging).forEach(n => {
    const m = L.circleMarker([n.lat, n.lon], { radius: 6, color: '#ff3366', weight: 2, fillColor: '#ff3366', fillOpacity: .6, className: 'node-pulse', renderer: svgR });
    m.bindTooltip(`<b>Surcharging ${esc(n.id)}</b><br>cause: ${esc(n.cause || 'capacity exceeded')}<br>surcharge ${fmt(n.surcharge_m3, 1)} m³ · HGL ${fmt(n.hgl_m, 2)} m`);
    m.addTo(LAYERS.surch.layer);
  });
  // edge utilisation recolour
  if (fr.edges && fr.edges.length) {
    const util = new Map(fr.edges.map(e => [e.id, e.util || 0]));
    LAYERS.edges.layer.eachLayer(pl => {
      const u = Math.max(0, Math.min(1.2, util.get(pl._edgeId) ?? 0));
      const hue = 210 - 210 * Math.min(1, u);                      // blue -> red
      pl.setStyle({ color: `hsl(${hue},90%,${u > 1 ? 40 : 60}%)` });
    });
  }
  // depth grid overlay
  LAYERS.depth.layer.clearLayers(); depthOverlay = null;
  if (fr.depth_grid && fr.depth_grid.png_base64) {
    const b = fr.depth_grid.bbox_lonlat || (S.meta && S.meta.pilot.bbox_lonlat);
    if (b) { depthOverlay = L.imageOverlay('data:image/png;base64,' + fr.depth_grid.png_base64, bboxToBounds(b), { opacity: +$('depth-opacity').value, interactive: false }); depthOverlay.addTo(LAYERS.depth.layer); }
  }
  // readouts
  const depths = feats.map(f => f.properties.depth_cm || 0);
  const maxD = depths.length ? Math.max(...depths) : (fr.depth_grid && fr.depth_grid.max_depth_cm) || 0;
  $('now-t').textContent = `t = ${fr.t_min} min`;
  $('ro-rain').textContent = fmt(fr.rain_mm_h, 0);
  const dv = $('ro-depth'); dv.textContent = fmt(maxD, 0); dv.className = 'v' + (maxD >= 30 ? ' bad' : maxD >= 15 ? ' warn' : '');
  $('ro-nodes').textContent = (fr.nodes || []).filter(n => n.surcharging).length;
  $('ro-segs').textContent = depths.filter(d => d >= 15).length;
  const top = feats.slice().sort((a, b) => (b.properties.depth_cm || 0) - (a.properties.depth_cm || 0)).slice(0, 5);
  $('top5').innerHTML = top.length ? top.map(f => `<li data-seg="${esc(f.properties.seg_id)}"><span>${esc(f.properties.name || f.properties.seg_id)}</span><b style="color:${SEV_COLOR[f.properties.severity || severityOf(f.properties.depth_cm)]}">${fmt(f.properties.depth_cm, 0)} cm</b></li>`).join('') : '<li class="muted">no data</li>';
  $('top5').querySelectorAll('li[data-seg]').forEach(li => li.onclick = () => {
    const f = feats.find(x => String(x.properties.seg_id) === li.dataset.seg);
    if (f) map.fitBounds(L.polyline(f.geometry.coordinates.map(lonlatToLatLng)).getBounds().pad(1.5));
  });
  drawSpark();
}

async function showTime(t) {
  S.t = t; $('time').value = t; $('time-label').textContent = `${t} min`; moveCursor(t);
  if (!S.run) return;
  let fr = S.frames.get(t);
  if (!fr) {
    try { fr = await api(`/api/simulation/${S.run.run_id}/frame/${t}`); S.frames.set(t, fr); }
    catch (e) { status(e.message); return; }
    if (S.t !== t) return;   // user moved on while fetching
  }
  drawFrame(fr);
}
function prefetchFrames() {
  const ts = S.run.frames_t_min || [];
  (async () => { for (const t of ts) { if (S.frames.has(t)) continue; try { S.frames.set(t, await api(`/api/simulation/${S.run.run_id}/frame/${t}`)); } catch (_) { break; } } })();
}

/* ---------------------------------------------------------------- timeline */
function setupTimeline() {
  const ts = (S.run && S.run.frames_t_min) || [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 105, 110, 115, 120, 125, 130, 135, 140, 145, 150, 155, 160, 165, 170, 175, 180];
  const sl = $('time'); sl.min = ts[0]; sl.max = ts[ts.length - 1]; sl.step = ts.length > 1 ? ts[1] - ts[0] : 5;
}
function stepPlay() {
  const ts = (S.run && S.run.frames_t_min) || [];
  const i = ts.indexOf(S.t); const next = ts[(i + 1) % ts.length];
  if (next === undefined) return togglePlay(false);
  showTime(next);
}
function togglePlay(force) {
  S.playing = force === undefined ? !S.playing : force;
  $('play').textContent = S.playing ? '❚❚' : '▶';
  clearInterval(S.timer);
  if (S.playing) { if (!S.run) { S.playing = false; $('play').textContent = '▶'; status('Run a simulation first', 'info'); return; } S.timer = setInterval(stepPlay, FRAME_MS); }
}
function drawRainChart() {
  const sc = currentScenario(); const svg = $('rain-chart');
  if (!sc || !sc.t_min || !sc.t_min.length) { svg.innerHTML = ''; return; }
  const W = 720, H = 60, t0 = +$('time').min, t1 = +$('time').max || 180, imax = Math.max(1, ...sc.intensity_mm_h);
  const n = sc.t_min.length, bw = Math.max(1, W / n - 1);
  const bars = sc.t_min.map((t, i) => { const h = (sc.intensity_mm_h[i] / imax) * (H - 14); return `<rect x="${(t - t0) / (t1 - t0) * W}" y="${H - h}" width="${bw}" height="${h}" fill="#3aa0ff" opacity=".8"/>`; }).join('');
  svg.innerHTML = `${bars}<text x="4" y="11" fill="#8a94a6" font-size="10">rain ≤ ${fmt(imax, 0)} mm/h · ${fmt(sc.total_mm, 0)} mm total</text><line id="rain-cursor" x1="0" x2="0" y1="0" y2="${H}" stroke="#00e5ff" stroke-width="2"/>`;
  moveCursor(S.t);
}
function moveCursor(t) {
  const c = document.getElementById('rain-cursor'); if (!c) return;
  const t0 = +$('time').min, t1 = +$('time').max || 180, x = (t - t0) / (t1 - t0) * 720;
  c.setAttribute('x1', x); c.setAttribute('x2', x);
}

/* ---------------------------------------------------------------- sparkline (max depth vs time) */
function drawSpark() {
  const svg = $('spark'); const W = 300, H = 90;
  const lines = [];
  if (S.compare) {
    lines.push({ t: S.compare.t_min, y: S.compare.normal.max_depth_cm, c: '#00e5ff', name: 'normal' });
    lines.push({ t: S.compare.t_min, y: S.compare.blocked.max_depth_cm, c: '#ff3366', name: 'blocked' });
  } else if (S.series) lines.push({ t: S.series.t_min, y: S.series.max_depth_cm, c: '#00e5ff', name: 'max depth' });
  if (!lines.length) { svg.innerHTML = '<text x="8" y="50" fill="#8a94a6" font-size="11">no series yet</text>'; $('spark-legend').textContent = ''; return; }
  const ymax = Math.max(5, ...lines.flatMap(l => l.y || [])); const tmax = Math.max(1, ...lines.flatMap(l => l.t || []));
  const path = l => (l.y || []).map((v, i) => `${i ? 'L' : 'M'}${(l.t[i] / tmax) * W},${H - 8 - (v / ymax) * (H - 20)}`).join(' ');
  const cx = (S.t / tmax) * W;
  svg.innerHTML = lines.map(l => `<path d="${path(l)}" fill="none" stroke="${l.c}" stroke-width="2"/>`).join('') +
    `<line x1="${cx}" x2="${cx}" y1="0" y2="${H}" stroke="#fff" stroke-opacity=".4" stroke-dasharray="3 3"/>` +
    `<text x="4" y="11" fill="#8a94a6" font-size="10">max ${fmt(ymax, 0)} cm</text>`;
  $('spark-legend').innerHTML = lines.map(l => `<span style="color:${l.c}">■</span> ${l.name}`).join(' ');
  if (S.compare) {
    const i = S.compare.t_min.indexOf(S.t); const d = i >= 0 ? (S.compare.blocked.max_depth_cm[i] - S.compare.normal.max_depth_cm[i]) : null;
    const sn = S.compare.normal.summary || {}, sb = S.compare.blocked.summary || {};
    $('compare-out').innerHTML = `Δ max depth at t=${S.t}: <b>${d == null ? '–' : (d >= 0 ? '+' : '') + fmt(d, 0)} cm</b> · peak normal <b>${fmt(sn.max_depth_cm, 0)}</b> vs blocked <b>${fmt(sb.max_depth_cm, 0)}</b> cm · flooded segs ${sn.peak_flooded_segments ?? '–'} → ${sb.peak_flooded_segments ?? '–'}`;
  }
}

/* ---------------------------------------------------------------- controls */
function currentScenario() { return S.scenarios.find(s => s.id === $('scenario').value); }
function blockageSpec() {
  const mode = $('blockage').value;
  if (mode === 'fraction') return { mode, fraction: 0.5 };
  if (mode === 'random') return { mode, share: 0.3, fraction: 0.6 };
  if (mode === 'near') {
    let c = S.nearLonLat;
    if (!c && S.hotspots) { const f = (S.hotspots.features || []).find(x => x.properties.active !== false) || S.hotspots.features[0]; if (f) c = f.geometry.coordinates; }
    if (!c) { const ct = map.getCenter(); c = [ct.lng, ct.lat]; }
    return { mode, fraction: 0.9, lonlat: c, radius_m: 300 };
  }
  return { mode: 'none' };
}
async function runSimulation() {
  const btn = $('run'); btn.disabled = true; $('run-info').textContent = 'Running…'; status(null);
  try {
    const sc = currentScenario(); if (!sc) throw new Error('No scenario selected');
    const r = await api('/api/simulate', { scenario_id: sc.id, blockage: blockageSpec(), horizon_min: 180 });
    r.frames_t_min = (r.frames_t_min || []).map(Number); S.run = r; S.frames.clear(); S.series = null; S.compare = null; $('compare-out').textContent = '';
    const mb = r.mass_balance || {}, sm = r.summary || {};
    $('run-info').innerHTML = `run <b>${esc(String(r.run_id).slice(0, 8))}</b> · ${fmt(r.runtime_s, 1)} s · mass-balance error <b>${fmt(mb.error_pct, 2)}%</b> ${tag(r.provenance)}<br>peak depth <b>${fmt(sm.max_depth_cm, 0)} cm</b> · surcharging nodes <b>${sm.peak_surcharging_nodes ?? '–'}</b> · flooded segs <b>${sm.peak_flooded_segments ?? '–'}</b> · surcharge ${fmt(sm.total_surcharge_m3, 0)} m³`;
    setupTimeline(); drawRainChart(); renderProvenance();
    await showTime(r.frames_t_min ? r.frames_t_min[0] : 0);
    api(`/api/simulation/${r.run_id}/series`).then(s => { S.series = s; drawSpark(); }).catch(e => status(e.message));
    prefetchFrames();
  } catch (e) { status(e.message); $('run-info').textContent = ''; }
  btn.disabled = false;
}
async function runCompare() {
  const btn = $('compare'); btn.disabled = true; $('compare-out').textContent = 'Comparing…'; status(null);
  try {
    const sc = currentScenario(); if (!sc) throw new Error('No scenario selected');
    let bl = blockageSpec(); if (bl.mode === 'none') { $('blockage').value = 'fraction'; bl = blockageSpec(); }
    const c = await api('/api/compare', { scenario_id: sc.id, blockage: bl });
    c.t_min = (c.t_min || c.frames_t_min || []).map(Number); S.compare = c; S.series = null;
    // show the blocked run on the map
    S.run = { run_id: c.blocked.run_id, frames_t_min: c.t_min, summary: c.blocked.summary, provenance: c.provenance }; S.frames.clear();
    $('run-info').innerHTML = `compare: normal <b>${esc(String(c.normal.run_id).slice(0, 8))}</b> vs blocked <b>${esc(String(c.blocked.run_id).slice(0, 8))}</b> (map shows BLOCKED)`;
    setupTimeline(); drawRainChart(); await showTime(c.t_min[0]); drawSpark(); prefetchFrames();
  } catch (e) { status(e.message); $('compare-out').textContent = ''; }
  btn.disabled = false;
}

/* ---------------------------------------------------------------- routing */
const routeMarkers = [];
function setRoutePoint(lonlat) {
  if (S.routePts.length >= 2) clearRoute();
  S.routePts.push(lonlat);
  const m = L.marker(lonlatToLatLng(lonlat), { icon: L.divIcon({ className: 'marker-label', html: S.routePts.length === 1 ? 'A' : 'B' }) }).addTo(LAYERS.route.layer);
  routeMarkers.push(m);
  $(S.routePts.length === 1 ? 'origin' : 'dest').value = `${lonlat[0].toFixed(5)},${lonlat[1].toFixed(5)}`;
}
function clearRoute() { S.routePts = []; routeMarkers.length = 0; LAYERS.route.layer.clearLayers(); $('origin').value = ''; $('dest').value = ''; $('route-out').textContent = ''; }
function parseLonLat(s) { const p = String(s).split(',').map(Number); return p.length === 2 && p.every(Number.isFinite) ? p : null; }
async function findRoute() {
  const o = parseLonLat($('origin').value), d = parseLonLat($('dest').value);
  if (!o || !d) return status('Set origin and destination first (click the map twice).', 'info');
  $('route-out').textContent = 'Routing…';
  try {
    const r = await api('/api/route', { origin: o, dest: d, t_min: S.t, vehicle: $('vehicle').value, run_id: S.run ? S.run.run_id : null });
    LAYERS.route.layer.eachLayer(l => { if (!(l instanceof L.Marker)) LAYERS.route.layer.removeLayer(l); });
    if (r.baseline_route) L.geoJSON(r.baseline_route, { style: { color: '#9ca3af', weight: 3, dashArray: '6 6', opacity: .8 } }).addTo(LAYERS.route.layer);
    if (r.route && r.reachable !== false) { const g = L.geoJSON(r.route, { style: { color: '#00f59b', weight: 5, opacity: .95 } }).addTo(LAYERS.route.layer); map.fitBounds(g.getBounds().pad(.3)); }
    const av = r.avoided_segments || [];
    $('route-out').innerHTML = r.reachable === false
      ? `<b style="color:#ff3366">Destination NOT reachable</b> for ${esc($('vehicle').value)} at t=${S.t} min (all paths exceed depth limit).`
      : `Safe route <b>${fmt(r.length_m, 0)} m</b> (baseline ${fmt(r.baseline_length_m, 0)} m, +${fmt((r.length_m || 0) - (r.baseline_length_m || 0), 0)} m) · max depth on route <b>${fmt(r.max_depth_on_route_cm, 0)} cm</b><br>avoided ${av.length} segment(s)${av.length ? ': ' + esc(av.slice(0, 8).map(a => a.name || a.seg_id || a).join(', ')) + (av.length > 8 ? '…' : '') : ''}`;
  } catch (e) { status(e.message); $('route-out').textContent = ''; }
}

/* ---------------------------------------------------------------- provenance */
const provRows = new Map();
function addProv(scope, p) {
  if (!p) return;
  if (p.tag || p.source || p.note) { provRows.set(scope, p); return; }
  if (typeof p === 'object') Object.entries(p).forEach(([k, v]) => addProv(`${scope} · ${k}`, v));
}
function renderProvenance() {
  if (S.run && S.run.provenance) addProv('last run', S.run.provenance);
  $('prov-table').querySelector('tbody').innerHTML = [...provRows].map(([k, p]) =>
    `<tr><td>${esc(k)}${tag(p)}</td><td>${esc(p.source || '')}${p.note ? `<br><i>${esc(p.note)}</i>` : ''}</td></tr>`).join('') || '<tr><td colspan="2" class="muted">none</td></tr>';
}

/* ---------------------------------------------------------------- init */
async function init() {
  renderLayerToggles();
  setupTimeline();
  // events
  $('run').onclick = runSimulation; $('compare').onclick = runCompare; $('play').onclick = () => togglePlay();
  $('time').oninput = e => showTime(+e.target.value);
  $('terrain-opacity').oninput = e => terrainOverlay && terrainOverlay.setOpacity(+e.target.value);
  $('depth-opacity').oninput = e => depthOverlay && depthOverlay.setOpacity(+e.target.value);
  $('scenario').onchange = () => { const sc = currentScenario(); $('scenario-desc').innerHTML = sc ? `${esc(sc.description || '')} ${tag(sc.provenance)}` : ''; drawRainChart(); };
  $('blockage').onchange = () => { $('near-hint').hidden = $('blockage').value !== 'near'; };
  $('route').onclick = findRoute; $('route-clear').onclick = clearRoute;
  map.on('click', e => {
    if (e.originalEvent.shiftKey) { S.nearLonLat = [e.latlng.lng, e.latlng.lat]; $('near-hint').innerHTML = `Blockage centre: <b>${e.latlng.lng.toFixed(5)}, ${e.latlng.lat.toFixed(5)}</b> (shift+click to move)`; return; }
    setRoutePoint([e.latlng.lng, e.latlng.lat]);
  });
  document.addEventListener('keydown', e => { if (e.code === 'Space' && !/input|select|textarea/i.test(e.target.tagName)) { e.preventDefault(); togglePlay(); } });

  // meta
  try {
    const m = await api('/api/meta'); S.meta = m;
    $('pilot-name').textContent = `${m.pilot.name} · ${m.pilot.crs || ''} · grid ${m.pilot.grid && m.pilot.grid.res ? m.pilot.grid.res + ' m' : ''}`;
    const real = m.data_mode === 'REAL'; const b = $('data-mode');
    b.textContent = real ? 'REAL Mumbai pilot data (MCGM/OSM)' : 'DEMONSTRATION — synthetic fixture';
    b.className = 'badge ' + (real ? 'badge-real' : 'badge-demo');
    if (m.pilot.bbox_lonlat) { map.fitBounds(bboxToBounds(m.pilot.bbox_lonlat)); L.rectangle(bboxToBounds(m.pilot.bbox_lonlat), { color: '#00e5ff', weight: 1, dashArray: '4 4', fill: false, interactive: false }).addTo(map); }
    if (Array.isArray(m.attribution)) m.attribution.forEach(a => map.attributionControl.addAttribution(esc(a)));
    addProv('meta', m.provenance); renderProvenance();
  } catch (e) { status(e.message); $('data-mode').textContent = 'backend offline'; }

  // scenarios
  try {
    S.scenarios = await api('/api/scenarios');
    $('scenario').innerHTML = S.scenarios.map(s => `<option value="${esc(s.id)}">${esc(s.name)} — ${fmt(s.total_mm, 0)} mm [${esc((s.provenance && s.provenance.tag) || '?')}]</option>`).join('');
    $('scenario').onchange();
  } catch (e) { status(e.message); }

  // static layers (independent; each fails gracefully)
  const provByLayer = {};
  const loads = [
    ['roads', () => api('/api/roads').then(fc => { drawRoads(fc); provByLayer.roads = fc.provenance; addProv('roads', fc.provenance); })],
    ['topology', () => api('/api/topology').then(t => { S.topology = t; drawTopology(t); provByLayer.edges = provByLayer.nodes = t.provenance; addProv('topology', t.provenance); })],
    ['hotspots', () => api('/api/hotspots').then(fc => { S.hotspots = fc; drawHotspots(fc); provByLayer.hotspots = fc.provenance || (fc.features[0] && fc.features[0].properties.provenance); addProv('hotspots', provByLayer.hotspots); })],
    ['terrain', () => api('/api/terrain').then(t => { drawTerrain(t); provByLayer.terrain = t.provenance; addProv('terrain', t.provenance); })],
  ];
  const results = await Promise.allSettled(loads.map(([, f]) => f()));
  const failed = results.map((r, i) => r.status === 'rejected' ? `${loads[i][0]}: ${r.reason.message}` : null).filter(Boolean);
  if (failed.length) status('Not ready — ' + failed.join(' | '));
  renderLayerToggles(provByLayer); renderProvenance(); drawSpark();
}
init();
