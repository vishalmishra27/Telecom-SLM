import { useState, useEffect } from 'react'
import { X } from 'lucide-react'
import { loadFieldsForDomain } from '../../lib/ontologyData'

export default function ClassDetailPanel({ classRow, relationshipMapping, onClose }) {
  const [fields, setFields] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!classRow) return
    const topDomain = classRow['Top Domain']
    if (!topDomain) return

    setLoading(true)
    loadFieldsForDomain(topDomain)
      .then(allFields => {
        const entityId = classRow['Entity ID']
        const classFields = allFields.filter(f => f['Entity ID'] === entityId)
        setFields(classFields)
      })
      .catch(err => {
        console.error('Failed to load fields:', err)
        setFields([])
      })
      .finally(() => setLoading(false))
  }, [classRow])

  if (!classRow) return null

  const incomingRels = relationshipMapping.filter(r => r['Target Class'] === classRow['Sub-Class / Node'])
  const outgoingRels = relationshipMapping.filter(r => r['Source Class'] === classRow['Sub-Class / Node'])

  return (
    <aside className="class-detail-panel">
      <header>
        <div>
          <h3>{classRow['Sub-Class / Node']}</h3>
          <small>{classRow['Top Class']}</small>
        </div>
        <button type="button" onClick={onClose} title="Close">
          <X size={16} />
        </button>
      </header>

      <section>
        <h4>Definition</h4>
        <p>{classRow['Definition'] || '—'}</p>
      </section>

      <section>
        <h4>Details</h4>
        <dl>
          <div>
            <dt>Entity ID</dt>
            <dd>{classRow['Entity ID'] || '—'}</dd>
          </div>
          <div>
            <dt>Master Concept</dt>
            <dd>{classRow['Master Concept'] || '—'}</dd>
          </div>
          <div>
            <dt>Primary Standard</dt>
            <dd>{classRow['Primary Standard'] || '—'}</dd>
          </div>
          <div>
            <dt>Source System</dt>
            <dd>{classRow['Source System'] || '—'}</dd>
          </div>
          <div>
            <dt>RCA</dt>
            <dd>{classRow['RCA'] === 'Yes' ? '✓ Yes' : '—'}</dd>
          </div>
        </dl>
      </section>

      {loading ? (
        <section>
          <h4>Properties</h4>
          <p>Loading...</p>
        </section>
      ) : fields && fields.length > 0 ? (
        <section>
          <h4>Properties ({fields.length})</h4>
          <div className="class-fields-list">
            {fields.map((f, idx) => (
              <div key={idx} className="field-item">
                <strong>{f['Field / Property'] || '—'}</strong>
                <small>{f['Type'] || '—'}</small>
                <p>{f['Definition'] || '—'}</p>
              </div>
            ))}
          </div>
        </section>
      ) : (
        <section>
          <h4>Properties</h4>
          <p>No properties found for this class.</p>
        </section>
      )}

      {incomingRels.length > 0 && (
        <section>
          <h4>Incoming Relationships ({incomingRels.length})</h4>
          <div className="relationships-list">
            {incomingRels.slice(0, 10).map((r, idx) => (
              <div key={idx} className="rel-item">
                <small>{r['Source Class']} →</small>
                <strong>{r['Relationship']}</strong>
                <p>{r['Definition'] || '—'}</p>
              </div>
            ))}
            {incomingRels.length > 10 && <small>...and {incomingRels.length - 10} more</small>}
          </div>
        </section>
      )}

      {outgoingRels.length > 0 && (
        <section>
          <h4>Outgoing Relationships ({outgoingRels.length})</h4>
          <div className="relationships-list">
            {outgoingRels.slice(0, 10).map((r, idx) => (
              <div key={idx} className="rel-item">
                <strong>{r['Relationship']}</strong>
                <small>→ {r['Target Class']}</small>
                <p>{r['Definition'] || '—'}</p>
              </div>
            ))}
            {outgoingRels.length > 10 && <small>...and {outgoingRels.length - 10} more</small>}
          </div>
        </section>
      )}
    </aside>
  )
}
