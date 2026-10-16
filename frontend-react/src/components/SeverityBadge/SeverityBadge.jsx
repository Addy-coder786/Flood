import { SEVERITY_LABEL, severityColor } from '../../lib/severity.js'

/** The one place a severity chip is rendered -- used by the map, street list, KPI cards and route warnings
 * so the colour/label mapping can never drift between them. */
export default function SeverityBadge({ severity, depthCm }) {
  const color = severityColor(severity)
  return (
    <span className="sev-badge" style={{ background: `${color}26`, color, border: `1px solid ${color}66` }}>
      <span className="sev-dot" style={{ background: color }} />
      {SEVERITY_LABEL[severity] || severity}
      {depthCm != null ? ` · ${Math.round(depthCm)} cm` : ''}
    </span>
  )
}
