export default function StatStrip({ summary }) {
  if (!summary) return null

  return (
    <section className="ontology-stat-strip">
      <div className="stat-item">
        <strong>{summary.topLevelClasses}</strong>
        <span>Top-level classes</span>
      </div>
      <div className="stat-item">
        <strong>{summary.domains}</strong>
        <span>Domains</span>
      </div>
      <div className="stat-item">
        <strong>{summary.classes}</strong>
        <span>Classes</span>
      </div>
      <div className="stat-item">
        <strong>{summary.fields}</strong>
        <span>Properties</span>
      </div>
      <div className="stat-item">
        <strong>{summary.relationshipTypes}</strong>
        <span>Relationship types</span>
      </div>
      <div className="stat-item">
        <strong>{summary.relationshipMappings}</strong>
        <span>Mapped relationships</span>
      </div>
      <div className="stat-caption">
        Telecom Conceptual Ontology {summary.version}
      </div>
    </section>
  )
}
