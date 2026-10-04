export default function StandardsFooter({ summary }) {
  return (
    <section className="ontology-standards-footer">
      <div className="standards-badges">
        <h4>Standards & Specifications</h4>
        <div className="badges-grid">
          {summary?.standards?.slice(0, 20)?.map((std, idx) => (
            <span key={idx} className="standard-badge" title={std.value}>
              {std.value}
            </span>
          ))}
          {summary?.standards?.length > 20 && (
            <span className="standard-badge">+{summary.standards.length - 20} more</span>
          )}
        </div>
      </div>
    </section>
  )
}
