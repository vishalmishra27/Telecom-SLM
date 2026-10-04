import { useState } from 'react'
import { ChevronRight } from 'lucide-react'
import VirtualTable from '../shared/VirtualTable'
import ClassDetailPanel from './ClassDetailPanel'

export default function DomainExplorer({ classes, summary, rcaOnly, relationshipMapping }) {
  const [selectedDomain, setSelectedDomain] = useState(null)
  const [selectedClass, setSelectedClass] = useState(null)

  if (!classes || !summary) return null

  const filteredByDomain = selectedDomain
    ? classes.filter(c => c['Domain Code'] === selectedDomain)
    : classes

  const columns = ['Sub-Class / Node', 'Top Class', 'Master Concept', 'Primary Standard', 'Source System', 'RCA']

  return (
    <section className="ontology-domain-explorer">
      <h3 className="ontology-section-title">Domain Explorer</h3>

      <div className="domain-chips">
        {summary.byDomain.map(domain => {
          const displayCount = rcaOnly ? domain.rcaClasses : domain.classes
          return (
            <button
              key={domain.code}
              className={`domain-chip ${selectedDomain === domain.code ? 'active' : ''}`}
              onClick={() => setSelectedDomain(selectedDomain === domain.code ? null : domain.code)}
            >
              <span className="domain-code">{domain.code}</span>
              <span className="domain-name">{domain.name}</span>
              <span className="domain-count">{displayCount}</span>
            </button>
          )
        })}
      </div>

      {selectedDomain && (
        <div className="domain-table-wrapper">
          <VirtualTable
            rows={filteredByDomain}
            columns={columns}
            defaultSort="Sub-Class / Node"
            onRowClick={setSelectedClass}
            renderCellValue={(row, col) => {
              const val = row[col]
              if (col === 'RCA') {
                return val === 'Yes' ? (
                  <span className="rca-badge">Yes</span>
                ) : val === 'Yes' ? (
                  <span className="fp-badge">Yes</span>
                ) : (
                  '—'
                )
              }
              return val === null ? '—' : String(val)
            }}
          />
        </div>
      )}

      {selectedClass && (
        <ClassDetailPanel
          classRow={selectedClass}
          relationshipMapping={relationshipMapping}
          onClose={() => setSelectedClass(null)}
        />
      )}
    </section>
  )
}
