export default function TopLevelClasses({ topLevel }) {
  if (!topLevel || topLevel.length === 0) return null

  return (
    <section className="ontology-top-level">
      <h3 className="ontology-section-title">Conceptual top-level classes</h3>
      <div className="top-level-grid">
        {topLevel.map(item => (
          <div key={item['Class']} className="top-level-card">
            <h4>{item['Class']}</h4>
            <p className="top-level-definition">{item['Definition'] || '—'}</p>
            <div className="top-level-rca">
              <strong>RCA Role:</strong>
              <p>{item['RCA Role'] || '—'}</p>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
