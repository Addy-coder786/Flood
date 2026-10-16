import { useState, useMemo } from 'react'
import { useFloodNet } from '../../state/FloodNetContext.jsx'
import { fmt } from '../../lib/format.js'
import SeverityBadge from '../SeverityBadge/SeverityBadge.jsx'
import styles from './WhyFloodedPanel.module.css'

// Mirrors the backend's `dominant_cause` enum (backend/floodnet/api/main.py's /explain endpoint) 1:1.
const CAUSE_SENTENCES = {
  drainage_overcapacity:
    'Nearby drainage network is over capacity: the nearest node is surcharging.',
  drainage_blockage:
    'Nearby drainage network is restricted: the nearest node is surcharging behind a blocked pipe (what-if scenario).',
  drainage_downstream_backup:
    'Nearby drainage network is backing up: downstream capacity is limiting outflow at the nearest node.',
  surface_ponding_only:
    'Surface water is ponding here; the nearest drainage node is not surcharging.',
  unknown: 'No single dominant factor stands out at this timestep.',
}

export function explainSentence(explain) {
  if (!explain) return ''
  const base = CAUSE_SENTENCES[explain.dominant_cause] || CAUSE_SENTENCES.unknown
  const node = explain.nearest_drainage_node
  const parts = []
  if (explain.rainfall_mm_h != null) parts.push(`Rain: ${fmt(explain.rainfall_mm_h, 0)} mm/h`)
  if (node?.id != null && node?.distance_m != null) {
    parts.push(`Nearest node ${node.id} (${node.distance_m.toFixed(0)}m)`)
  }
  return parts.length ? `${base} [${parts.join(' · ')}]` : base
}

const DRAINAGE_CAUSES = new Set(['drainage_overcapacity', 'drainage_blockage', 'drainage_downstream_backup'])

// Raw solver cause flags carried on each node (`f.node_cause`, surfaced by /explain as
// nearest_drainage_node.cause and aggregated by /series as cause_counts). Empty string = no cause flagged.
const NODE_CAUSE_LABELS = {
  overcapacity: 'Inflow exceeds hydraulic capacity',
  downstream: 'Downstream capacity limiting outflow (backwater)',
  blockage: 'Outgoing pipe blocked',
}

// Three-state node classification computed in main.py::_node_fill_state and serialised per node by
// serialize_frame(); reachable here through the already-fetched `frame`, with no extra API call.
const NODE_STATE_LABELS = {
  surcharging: 'Surcharging',
  at_capacity: 'Full — at capacity, not surcharging',
  normal: 'Normal / free flow',
}

const pct = (x) => (x == null ? null : `${Math.round(x * 100)}%`)

/** Row helper: a row with no real value is dropped entirely rather than filled with a placeholder. */
function row(label, value) {
  return value == null || value === '' ? null : { label, value }
}

/** Index into /series' per-frame arrays for the frame /explain actually answered for. Returns -1 when the
 *  series is absent or has no frame at that time, so callers omit the whole section instead of guessing. */
function seriesIndexForT(series, tMin) {
  if (!Array.isArray(series?.t_min) || tMin == null) return -1
  for (let i = 0; i < series.t_min.length; i++) {
    if (Math.abs(series.t_min[i] - tMin) < 0.01) return i
  }
  return -1
}

/** Rain depth accumulated from the run's own rainfall input up to `idx`, trapezoidally integrated over the
 *  series' real frame spacing. Derived arithmetic over model input only -- no assumed intensities. */
function accumulatedRainMm(series, idx) {
  if (!Array.isArray(series?.rain_mm_h) || !Array.isArray(series?.t_min) || idx <= 0) return null
  let mm = 0
  for (let i = 1; i <= idx; i++) {
    const a = series.rain_mm_h[i - 1]
    const b = series.rain_mm_h[i]
    const dtH = (series.t_min[i] - series.t_min[i - 1]) / 60
    if (a == null || b == null || !(dtH > 0)) return null
    mm += ((a + b) / 2) * dtH
  }
  return mm
}

/** Human description of the run's blockage spec, using only the fields the API echoes back
 *  (schemas.py::BlockageSpec). Never interprets fields the spec does not carry. */
function blockageNote(blockage) {
  const mode = blockage?.mode
  if (!mode || mode === 'none') return null
  const bits = [`mode=${mode}`]
  if (blockage.fraction != null) bits.push(`fraction=${fmt(blockage.fraction, 2)}`)
  if (blockage.share != null) bits.push(`share=${fmt(blockage.share, 2)}`)
  if (blockage.radius_m != null) bits.push(`radius=${fmt(blockage.radius_m, 0)} m`)
  if (Array.isArray(blockage.edge_ids) && blockage.edge_ids.length) bits.push(`${blockage.edge_ids.length} edges`)
  return bits.join(' · ')
}

/**
 * Contributor breakdown for the selected segment at the selected timestep.
 *
 * Every value below is read from real model output: `explain` (the /explain response for this segment and
 * frame), `nodeLive` (that same node's entry in the already-fetched frame, only when the frame is the SAME
 * timestep /explain answered for), and `netStress` (the /series aggregates at that frame index).
 *
 * Honesty constraints this function must not violate:
 *  - the drainage contributor is evidence-gated on the model's own `dominant_cause`; it is visibly
 *    de-emphasised (and says so) whenever drainage is not the classified limiting factor;
 *  - the nearest node is a straight-line-proximity association, never "this street's cause";
 *  - surcharge is a refund of water the same cell's inlet just captured; it never delivers water from
 *    elsewhere in the pipe network onto the street.
 */
function buildContributors({ explain, nodeLive, netStress, totals, rainAccumMm }) {
  const node = explain.nearest_drainage_node
  const hasDrainageIssue = DRAINAGE_CAUSES.has(explain.dominant_cause)
  const isSurfaceOnly = explain.dominant_cause === 'surface_ponding_only'
  const depth = explain.depth_cm
  const rain = explain.rainfall_mm_h
  const groundM = explain.terrain_context?.ground_elevation_m
  const out = []

  // ── 01 Surface accumulation ────────────────────────────────────────────────
  out.push({
    id: 'surface',
    phase: '01',
    label: 'SURFACE ACCUMULATION',
    sublabel: 'Rainfall input · terrain routing at this segment',
    chip: isSurfaceOnly ? 'MODEL-ATTRIBUTED' : null,
    chipTone: 'attributed',
    active: (rain != null && rain > 0) || (depth != null && depth > 0),
    headline: rain != null ? `${fmt(rain, 1)} mm/h rainfall at this timestep` : null,
    rows: [
      row('Modelled depth here', depth != null ? `${fmt(depth, 1)} cm` : null),
      row('Rain to this timestep', rainAccumMm != null ? `≈ ${fmt(rainAccumMm, 1)} mm` : null),
      row('Ground elevation (DEM)', groundM != null ? `${fmt(groundM, 2)} m MSL` : null),
    ].filter(Boolean),
    note: isSurfaceOnly
      ? 'Model classified this segment as surface ponding only: depth accumulated here without surcharge flagged at the nearest node.'
      : null,
  })

  // ── 02 Drainage state nearby (evidence-gated, proximity-qualified) ─────────
  if (node) {
    const stateKey = nodeLive?.state
    const stateLabel = stateKey
      ? NODE_STATE_LABELS[stateKey] || stateKey
      : node.surcharging
        ? NODE_STATE_LABELS.surcharging
        : 'Not flagged surcharging this frame'
    const causeLabel = NODE_CAUSE_LABELS[node.cause] || null
    out.push({
      id: 'drainage',
      phase: '02',
      label: 'DRAINAGE STATE NEARBY',
      sublabel: 'Nearest node by straight-line distance',
      chip: hasDrainageIssue ? 'MODEL-ATTRIBUTED' : 'NOT THE LIMITING FACTOR',
      chipTone: hasDrainageIssue ? 'attributed' : 'neutral',
      active: hasDrainageIssue,
      headline: stateLabel,
      rows: [
        row('Node', node.id != null ? `${node.id}` : null),
        row('Straight-line distance', node.distance_m != null ? `${fmt(node.distance_m, 0)} m` : null),
        row('Conduit load (max incident)', pct(node.utilization)),
        row('Shaft fill', pct(nodeLive?.fill_frac)),
        row('Freeboard to street level', nodeLive?.freeboard_m != null ? `${fmt(nodeLive.freeboard_m, 2)} m` : null),
        row('Solver cause flag', causeLabel),
      ].filter(Boolean),
      note: hasDrainageIssue
        ? 'Surcharge here returns water this cell\'s own inlet just captured — the model never pushes water from elsewhere in the pipe network onto the street.'
        : 'The model did not classify drainage as the limiting factor for this segment at this timestep.',
      disclaimer:
        'Proximity association only: this node is simply the closest one to the segment midpoint, not a computed flow path to this street. Treat it as a nearby hydraulic signal, not this street\'s cause.',
    })
  }

  // ── 03 Network-wide stress (aggregate -- the claim class the model does support) ──
  if (netStress) {
    const rows = [
      row(
        'Nodes surcharging',
        netStress.surcharging != null
          ? totals.nodes != null
            ? `${netStress.surcharging} of ${totals.nodes}`
            : `${netStress.surcharging}`
          : null,
      ),
      row('Nodes full, not surcharging', netStress.nodesAtCapacity != null ? `${netStress.nodesAtCapacity}` : null),
      row(
        'Conduits at capacity',
        netStress.edgesAtCapacity != null
          ? totals.edges != null
            ? `${netStress.edgesAtCapacity} of ${totals.edges}`
            : `${netStress.edgesAtCapacity}`
          : null,
      ),
      row('Conduits ≥80% design flow (descriptive)', netStress.edgesNearCapacity != null ? `${netStress.edgesNearCapacity}` : null),
      row('Peak conduit load', pct(netStress.maxEdgeUtil)),
      row('Mean conduit load', pct(netStress.meanEdgeUtil)),
      row('Segments flooded ward-wide', netStress.floodedSegments != null ? `${netStress.floodedSegments}` : null),
      row('Node cause mix', netStress.causeMixLabel),
    ].filter(Boolean)
    if (rows.length) {
      out.push({
        id: 'network',
        phase: '03',
        label: 'NETWORK-WIDE STRESS',
        sublabel: 'Ward-wide drainage state at this timestep',
        chip: 'AGGREGATE',
        chipTone: 'neutral',
        active: (netStress.surcharging || 0) > 0 || (netStress.edgesAtCapacity || 0) > 0,
        headline:
          netStress.surcharging != null
            ? `${netStress.surcharging} node${netStress.surcharging === 1 ? '' : 's'} surcharging pilot-wide`
            : null,
        rows,
        note: netStress.blockage
          ? `Ward-wide counts, not an attribution to this street. This run applies a modelled blockage (${netStress.blockage}).`
          : 'Ward-wide counts from the run\'s own per-frame drainage state — a network-level statement, not an attribution to this street.',
      })
    }
  }

  return out
}

export default function WhyFloodedPanel() {
  const { selectedSegId, explain, explainLoading, explainError, series, frame, currentT, run } = useFloodNet()
  const [showEvidence, setShowEvidence] = useState(false)

  // Compute peak depth and time for this specific road segment across the whole run
  const segPeak = useMemo(() => {
    if (!series?.streets?.[selectedSegId] || !series?.t_min) return null
    const depths = series.streets[selectedSegId]
    let maxD = 0
    let maxT = 0
    for (let i = 0; i < depths.length; i++) {
      if (depths[i] > maxD) {
        maxD = depths[i]
        maxT = series.t_min[i]
      }
    }
    return { depthCm: maxD, tMin: maxT }
  }, [series, selectedSegId])

  // The nearest node's full hydraulic state (fill_frac / freeboard_m / state) is carried by every node in a
  // frame (serialize_frame), so it is reachable from the frame already in context -- no new API call. Only
  // used when the loaded frame is the SAME timestep /explain answered for; otherwise those rows are omitted
  // rather than mixed across timesteps.
  const nodeLive = useMemo(() => {
    const id = explain?.nearest_drainage_node?.id
    if (!id || !Array.isArray(frame?.nodes)) return null
    if (explain.t_min == null || frame.t_min == null) return null
    if (Math.abs(frame.t_min - explain.t_min) > 0.01) return null
    return frame.nodes.find((n) => n.id === id) || null
  }, [explain, frame])

  // Ward-wide aggregates for exactly this timestep, from /series (already in context).
  const netStress = useMemo(() => {
    const i = seriesIndexForT(series, explain?.t_min)
    if (i < 0) return null
    const at = (arr) => (Array.isArray(arr) && arr[i] != null ? arr[i] : null)
    const cc = series.cause_counts || {}
    const over = at(cc.overcapacity)
    const down = at(cc.downstream)
    const blocked = at(cc.blockage)
    const causeMixLabel =
      over != null || down != null || blocked != null
        ? [
            over != null ? `over-capacity ${over}` : null,
            down != null ? `backwater ${down}` : null,
            blocked != null ? `blocked ${blocked}` : null,
          ]
            .filter(Boolean)
            .join(' · ')
        : null
    return {
      surcharging: at(series.surcharging_count),
      nodesAtCapacity: at(series.nodes_at_capacity),
      edgesAtCapacity: at(series.edges_at_capacity),
      edgesNearCapacity: at(series.edges_near_capacity),
      maxEdgeUtil: at(series.max_edge_util),
      meanEdgeUtil: at(series.mean_edge_util),
      floodedSegments: at(series.flooded_segments),
      causeMixLabel,
      blockage: blockageNote(run?.blockage),
    }
  }, [series, explain, run])

  const rainAccumMm = useMemo(
    () => accumulatedRainMm(series, seriesIndexForT(series, explain?.t_min)),
    [series, explain],
  )

  const totals = useMemo(
    () => ({
      nodes: Array.isArray(frame?.nodes) ? frame.nodes.length : null,
      edges: Array.isArray(frame?.edges) ? frame.edges.length : null,
    }),
    [frame],
  )

  const contributors = useMemo(
    () => (explain ? buildContributors({ explain, nodeLive, netStress, totals, rainAccumMm }) : []),
    [explain, nodeLive, netStress, totals, rainAccumMm],
  )

  return (
    <section className={styles.section} data-tour="panel-why">
      <div className="panel-heading">Why this area floods</div>

      {!selectedSegId ? (
        <div className={styles.hint}>Select a street on the map to see its surface water and drainage state.</div>
      ) : explainError ? (
        <div className={styles.errBox}>{explainError}</div>
      ) : explainLoading && !explain ? (
        <div className={styles.loading}>Loading&hellip;</div>
      ) : explain ? (
        <div className={styles.body}>
          <div className={styles.headRow}>
            <span className={styles.segName}>{explain.seg_name || explain.seg_id}</span>
            <SeverityBadge severity={explain.severity} depthCm={explain.depth_cm} />
          </div>

          {/* Explicit Distinction: Current Frame Depth vs Forecast Peak */}
          <div className={styles.depthContextRow}>
            <div className={styles.depthContextItem}>
              <span className={styles.depthContextLabel}>
                CURRENT FRAME (T+{explain.t_min != null ? Math.round(explain.t_min) : currentT}m)
              </span>
              <span className={`${styles.depthContextVal} ${explain.depth_cm > 0 ? styles.depthActive : styles.depthDry}`}>
                {explain.depth_cm > 0 ? `${fmt(explain.depth_cm, 1)} cm` : 'No street flooding (0.0 cm)'}
              </span>
            </div>
            {explain.timing && (
              <div className={styles.depthContextItem}>
                <span className={styles.depthContextLabel}>ONSET</span>
                <span className={styles.depthContextVal}>
                  {explain.timing.onset_t_min != null ? `T+${Math.round(explain.timing.onset_t_min)}m` : 'Not reached'}
                </span>
              </div>
            )}
            {segPeak && (
              <div className={styles.depthContextItem}>
                <span className={styles.depthContextLabel}>FORECAST PEAK</span>
                <span className={styles.depthContextVal}>
                  {segPeak.depthCm > 0
                    ? `${fmt(segPeak.depthCm, 1)} cm @ T+${Math.round(segPeak.tMin)}m`
                    : 'Remains dry over 3h'}
                </span>
              </div>
            )}
          </div>

          {/* Primary Human Explanation */}
          <div className={styles.explanationBox}>
            <div className={styles.explanationTitle}>Model summary</div>
            <p className={styles.sentence}>{explainSentence(explain)}</p>
          </div>

          {/* Contributor breakdown -- every value below is model output for this segment/timestep */}
          <div className={styles.chainHeading}>Contributors at this timestep</div>
          <ol className={styles.contribList}>
            {contributors.map((c) => (
              <li
                key={c.id}
                className={`${styles.contrib} ${c.active ? styles.contribActive : styles.contribMuted}`}
              >
                <div className={styles.contribHead}>
                  <span className={styles.contribPhase}>{c.phase}</span>
                  <span className={styles.contribLabel}>{c.label}</span>
                  {c.chip && (
                    <span
                      className={`${styles.contribChip} ${
                        c.chipTone === 'attributed' ? styles.chipAttributed : styles.chipNeutral
                      }`}
                    >
                      {c.chip}
                    </span>
                  )}
                </div>
                <div className={styles.contribSub}>{c.sublabel}</div>
                {c.headline && <div className={styles.contribHeadline}>{c.headline}</div>}
                {c.rows.length > 0 && (
                  <div className={styles.contribRows}>
                    {c.rows.map((r) => (
                      <div key={r.label} className={styles.contribRow}>
                        <span className={styles.contribRowLabel}>{r.label}</span>
                        <span className={styles.contribRowVal}>{r.value}</span>
                      </div>
                    ))}
                  </div>
                )}
                {showEvidence && c.note && <div className={styles.contribNote}>{c.note}</div>}
                {showEvidence && c.disclaimer && <div className={styles.proximityNote}>{c.disclaimer}</div>}
              </li>
            ))}
          </ol>

          {/* Progressive Disclosure: Technical Evidence */}
          <div className={styles.evidenceToggleRow}>
            <button
              type="button"
              className={styles.evidenceToggle}
              onClick={() => setShowEvidence((v) => !v)}
              aria-expanded={showEvidence}
            >
              <span>{showEvidence ? '▾ Hide hydrodynamic evidence' : '▸ Show hydrodynamic evidence'}</span>
            </button>
          </div>

          {showEvidence && (
            <div className={styles.evidenceTable}>
              <div className={styles.factRow}>
                <span className={styles.factLabel}>Explained frame</span>
                <span className={styles.factValue}>
                  T+{explain.t_min != null ? Math.round(explain.t_min) : currentT} min
                </span>
              </div>
              <div className={styles.factRow}>
                <span className={styles.factLabel}>Precipitation Rate</span>
                <span className={styles.factValue}>{fmt(explain.rainfall_mm_h, 0)} mm/h</span>
              </div>
              <div className={styles.factRow}>
                <span className={styles.factLabel}>Ground Elevation (DEM)</span>
                <span className={styles.factValue}>
                  {explain.terrain_context?.ground_elevation_m != null
                    ? `${fmt(explain.terrain_context.ground_elevation_m, 2)} m MSL`
                    : 'N/A'}
                </span>
              </div>
              <div className={styles.factRow}>
                <span className={styles.factLabel}>Nearest Drain Node</span>
                <span className={styles.factValue}>
                  {explain.nearest_drainage_node?.id ?? 'N/A'}
                  {explain.nearest_drainage_node?.distance_m != null
                    ? ` (${fmt(explain.nearest_drainage_node.distance_m, 0)}m)`
                    : ''}
                </span>
              </div>
              <div className={styles.factRow}>
                <span className={styles.factLabel}>Hydraulic Surcharge</span>
                <span className={styles.factValue}>
                  {explain.nearest_drainage_node ? (explain.nearest_drainage_node.surcharging ? 'Active Surcharge' : 'Normal / Free Flow') : 'N/A'}
                  {explain.nearest_drainage_node?.utilization != null
                    ? ` · ${Math.round(explain.nearest_drainage_node.utilization * 100)}% load`
                    : ''}
                </span>
              </div>
              {nodeLive?.state && (
                <div className={styles.factRow}>
                  <span className={styles.factLabel}>Node State (frame)</span>
                  <span className={styles.factValue}>{NODE_STATE_LABELS[nodeLive.state] || nodeLive.state}</span>
                </div>
              )}
              {nodeLive?.hgl_m != null && (
                <div className={styles.factRow}>
                  <span className={styles.factLabel}>Node HGL</span>
                  <span className={styles.factValue}>{fmt(nodeLive.hgl_m, 2)} m</span>
                </div>
              )}
              {nodeLive?.surcharge_m3 != null && (
                <div className={styles.factRow}>
                  <span className={styles.factLabel}>Surcharge Volume (frame)</span>
                  <span className={styles.factValue}>{fmt(nodeLive.surcharge_m3, 2)} m³</span>
                </div>
              )}
              <div className={styles.evidenceNote}>
                Node state uses the frame&apos;s interval-aware definition (solver flag OR surcharge volume &gt; 0
                over the frame interval), so it can differ from the last-sub-step surcharge flag above.
                Conduit load is the highest utilisation among conduits incident to that node.
              </div>
            </div>
          )}

          {explainLoading && <div className={styles.refreshing}>Updating for timestep&hellip;</div>}
        </div>
      ) : (
        <div className={styles.hint}>No explanation available for this segment.</div>
      )}
    </section>
  )
}
