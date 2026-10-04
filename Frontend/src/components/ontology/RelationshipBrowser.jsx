import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import VirtualTable from '../shared/VirtualTable'

export default function RelationshipBrowser({ relationshipTypes, relationshipMapping }) {
  const [expandedRel, setExpandedRel] = useState(null)

  if (!relationshipTypes) return null

  const columns = ['Relationship (KG Style)', 'Definition / Meaning', 'Occurrences', 'RCA']

  const renderCell = (row, col) => {
    if (col === 'RCA') {
      return row[col] === 'Yes' ? <span className="rca-badge">Yes</span> : '—'
    }
    if (col === 'Occurrences') {
      return row[col] || 0
    }
    return row[col] === null ? '—' : String(row[col])
  }

  const expanded = expandedRel
    ? relationshipMapping.filter(r => r['Relationship'] === expandedRel)
    : null

  return (
    <section className="ontology-relationship-browser">
      <h3 className="ontology-section-title">Relationship Types</h3>

      <div className="relationship-table-wrapper">
        <VirtualTable
          rows={relationshipTypes}
          columns={columns}
          defaultSort="Occurrences"
          onRowClick={row => setExpandedRel(expandedRel === row['Relationship (KG Style)'] ? null : row['Relationship (KG Style)'])}
          renderCellValue={renderCell}
        />
      </div>

      {expanded && (
        <div className="relationship-expansion">
          <h4>
            <ChevronDown size={16} />
            {expandedRel} — {expanded.length} mappings
          </h4>
          <div className="relationship-mappings">
            {expanded.map((r, idx) => (
              <div key={idx} className="mapping-item">
                <div className="mapping-classes">
                  <strong>{r['Source Class'] || '—'}</strong>
                  <span>→</span>
                  <strong>{r['Target Class'] || '—'}</strong>
                </div>
                <p>{r['Definition'] || '—'}</p>
                {r['Cypher Pattern'] && (
                  <pre className="cypher-pattern"><code>{r['Cypher Pattern']}</code></pre>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}
