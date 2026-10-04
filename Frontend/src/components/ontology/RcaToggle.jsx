export default function RcaToggle({ rcaOnly, onToggle, summary }) {
  if (!summary) return null

  return (
    <section className="ontology-rca-toggle">
      <div className="toggle-header">
        <h3>RCA Lens Filter</h3>
        <label className="toggle-switch">
          <input
            type="checkbox"
            checked={rcaOnly}
            onChange={e => onToggle(e.target.checked)}
          />
          <span className="toggle-slider" />
          <span className="toggle-label">{rcaOnly ? 'RCA only' : 'All'}</span>
        </label>
      </div>

      {rcaOnly && (
        <div className="toggle-delta">
          <p>
            <strong>Classes:</strong> {summary.classes} → {summary.rca.classes}
          </p>
          <p>
            <strong>Relationship types:</strong> {summary.relationshipTypes} → {summary.rca.relationshipTypes}
          </p>
        </div>
      )}
    </section>
  )
}
