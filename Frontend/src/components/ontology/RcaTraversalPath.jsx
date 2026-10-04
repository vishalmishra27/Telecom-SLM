export default function RcaTraversalPath({ useCases }) {
  if (!useCases || useCases.length === 0) return null

  const uc001 = useCases.find(u => u['Use Case ID'] === 'UC001')
  const others = useCases.filter(u => u['Use Case ID'] !== 'UC001')

  const renderTraversal = (traversal) => {
    if (!traversal) return null
    const steps = traversal.split('->').map(s => s.trim()).filter(Boolean)
    return (
      <div className="traversal-flow">
        {steps.map((step, idx) => (
          <div key={idx} className="traversal-step">
            <span>{step}</span>
            {idx < steps.length - 1 && <span className="traversal-arrow">→</span>}
          </div>
        ))}
      </div>
    )
  }

  return (
    <section className="ontology-traversal-path">
      <h3 className="ontology-section-title">RCA Traversal Paths</h3>

      {uc001 && (
        <div className="primary-traversal">
          <h4>{uc001['Use Case']} (UC001)</h4>
          <p className="traversal-desc">{uc001['Definition'] || '—'}</p>
          {renderTraversal(uc001['Conceptual Traversal'])}
        </div>
      )}

      {others.length > 0 && (
        <div className="secondary-traversals">
          {others.map(uc => (
            <div key={uc['Use Case ID']} className="secondary-traversal">
              <h5>{uc['Use Case']} ({uc['Use Case ID']})</h5>
              <p className="traversal-desc">{uc['Definition'] || '—'}</p>
              {renderTraversal(uc['Conceptual Traversal'])}
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
