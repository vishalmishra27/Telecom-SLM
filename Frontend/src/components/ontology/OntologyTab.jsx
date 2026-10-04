import { useState, useEffect, useMemo, useCallback } from 'react'
import { AlertTriangle, ChevronDown, CircleDot, Filter, GitBranch, Info, Layers, Network, PanelRightOpen, RotateCcw, X } from 'lucide-react'
import * as ontologyData from '../../lib/ontologyData'
import ForceGraph, { classColors } from '../ForceGraph'
import StatStrip from './StatStrip'
import TopLevelClasses from './TopLevelClasses'
import RcaToggle from './RcaToggle'
import DomainExplorer from './DomainExplorer'
import RelationshipBrowser from './RelationshipBrowser'
import RcaTraversalPath from './RcaTraversalPath'
import RcaCatalogs from './RcaCatalogs'
import StandardsFooter from './StandardsFooter'

/* Domain color palette */
const DOMAIN_COLORS = {
  '01': '#6d7b78', '02': '#3e80c2', '03': '#5f77c8', '04': '#0091DA',
  '05': '#e05d4f', '06': '#8b67bd', '07': '#58606a', '08': '#f0a13a',
  '09': '#42c49e', '10': '#c68327', '11': '#b55a8a', '12': '#d9465f',
  '13': '#3c8c55', '14': '#486fb7', '15': '#8a7962', '16': '#7a6ea8',
  '17': '#547486', '18': '#888f41', '19': '#6f6a90', '20': '#5f9bb7',
  '21': '#237b65', '22': '#8c6a3d', '23': '#697b8f', '24': '#4f7c68',
}

const TOP_DOMAIN_COLORS = {
  Customer: '#3e80c2', Service: '#0091DA', Process: '#e05d4f',
  People: '#8b67bd', Product: '#f0a13a',
}

/** Build graph from ontology classes + relationships with filters applied */
function buildGraph(classes, rels, filters) {
  let filtered = [...classes]
  if (filters.rcaOnly) filtered = filtered.filter(c => c['RCA'] === 'Yes')
  if (filters.topDomain) filtered = filtered.filter(c => c['Top Domain'] === filters.topDomain)
  if (filters.domain) filtered = filtered.filter(c => c['Domain Code'] === filters.domain)
  if (filters.topClass) filtered = filtered.filter(c => c['Top Class'] === filters.topClass)
  if (filters.masterConcept) filtered = filtered.filter(c => c['Master Concept'] === filters.masterConcept)

  const classSet = new Set(filtered.map(c => c['Sub-Class / Node']))

  const nodes = filtered.map(c => ({
    id: c['Sub-Class / Node'],
    name: c['Sub-Class / Node'],
    ontology_class: c['Sub-Class / Node'],
    properties: {
      domain: c['Telecom Domain'],
      domainCode: c['Domain Code'],
      topClass: c['Top Class'],
      masterConcept: c['Master Concept'],
      topDomain: c['Top Domain'],
      standard: c['Primary Standard'],
      definition: c['Definition'],
      rca: c['RCA'],
      entityId: c['Entity ID'],
    },
    source_system: c['Source System'] || '',
    _domainCode: c['Domain Code'],
    _topDomain: c['Top Domain'],
    _color: DOMAIN_COLORS[c['Domain Code']] || TOP_DOMAIN_COLORS[c['Top Domain']] || '#6d7b78',
  }))

  let filteredRels = rels.filter(r => classSet.has(r['Source Class']) && classSet.has(r['Target Class']))
  if (filters.rcaOnly) filteredRels = filteredRels.filter(r => r['RCA'] === 'Yes')
  if (filters.relType) filteredRels = filteredRels.filter(r => r['Relationship'] === filters.relType)

  const relationships = filteredRels.map((r, i) => ({
    id: `ont-rel-${i}`,
    source: r['Source Class'],
    target: r['Target Class'],
    type: r['Relationship'],
  }))

  return { nodes, relationships, source: 'ontology' }
}

/** Extract unique sorted values for a field */
function uniqueVals(arr, field) {
  return [...new Set(arr.map(x => x[field]).filter(Boolean))].sort()
}

function OntologyGraph({ classes, relationshipMapping, summary }) {
  const [selected, setSelected] = useState(null)
  const [highlightIds, setHighlightIds] = useState(null)
  const [showLegend, setShowLegend] = useState(false)

  // Filters
  const [rcaOnly, setRcaOnly] = useState(false)
  const [topDomain, setTopDomain] = useState('')
  const [domain, setDomain] = useState('')
  const [topClass, setTopClass] = useState('')
  const [masterConcept, setMasterConcept] = useState('')
  const [relType, setRelType] = useState('')

  const filters = useMemo(() => ({
    rcaOnly, topDomain, domain, topClass, masterConcept, relType,
  }), [rcaOnly, topDomain, domain, topClass, masterConcept, relType])

  const graph = useMemo(() => {
    if (!classes || !relationshipMapping) return { nodes: [], relationships: [], source: 'ontology' }
    return buildGraph(classes, relationshipMapping, filters)
  }, [classes, relationshipMapping, filters])

  // Dimension values for filters
  const topDomains = useMemo(() => uniqueVals(classes, 'Top Domain'), [classes])
  const domains = useMemo(() => summary?.byDomain || [], [summary])
  const topClasses = useMemo(() => uniqueVals(classes, 'Top Class'), [classes])
  const masterConcepts = useMemo(() => uniqueVals(classes, 'Master Concept'), [classes])

  // Relationship types present in current graph
  const relTypes = useMemo(() => {
    const classSet = new Set(graph.nodes.map(n => n.id))
    let rels = relationshipMapping.filter(r => classSet.has(r['Source Class']) && classSet.has(r['Target Class']))
    if (rcaOnly) rels = rels.filter(r => r['RCA'] === 'Yes')
    const counts = {}
    rels.forEach(r => { counts[r['Relationship']] = (counts[r['Relationship']] || 0) + 1 })
    return Object.entries(counts).sort((a, b) => b[1] - a[1])
  }, [graph.nodes, relationshipMapping, rcaOnly])

  const clearFilters = useCallback(() => {
    setRcaOnly(false)
    setTopDomain('')
    setDomain('')
    setTopClass('')
    setMasterConcept('')
    setRelType('')
    setSelected(null)
    setHighlightIds(null)
  }, [])

  const handleSelect = useCallback((node) => {
    if (!node || (selected && selected.id === node.id)) {
      setSelected(null)
      setHighlightIds(null)
      return
    }
    setSelected(node)
    const ids = new Set([node.id])
    graph.relationships.forEach((r) => {
      const sid = typeof r.source === 'object' ? r.source.id : r.source
      const tid = typeof r.target === 'object' ? r.target.id : r.target
      if (sid === node.id) ids.add(tid)
      if (tid === node.id) ids.add(sid)
    })
    setHighlightIds(ids)
  }, [selected, graph])

  // When relType filter changes, highlight all nodes connected by that rel type
  useEffect(() => {
    if (!relType) {
      if (!selected) setHighlightIds(null)
      return
    }
    const ids = new Set()
    graph.relationships.forEach((r) => {
      if (r.type === relType) {
        const sid = typeof r.source === 'object' ? r.source.id : r.source
        const tid = typeof r.target === 'object' ? r.target.id : r.target
        ids.add(sid)
        ids.add(tid)
      }
    })
    setHighlightIds(ids.size > 0 ? ids : null)
    setSelected(null)
  }, [relType, graph])

  const selectedInfo = useMemo(() => {
    if (!selected) return null
    const rels = graph.relationships.filter((r) => {
      const sid = typeof r.source === 'object' ? r.source.id : r.source
      const tid = typeof r.target === 'object' ? r.target.id : r.target
      return sid === selected.id || tid === selected.id
    })
    return { node: selected, rels }
  }, [selected, graph])

  const hasFilters = rcaOnly || topDomain || domain || topClass || masterConcept || relType

  return (
    <div className="ontology-graph-section">
      <header className="ontology-graph-header">
        <div>
          <span className="eyebrow">Full Ontology Schema — {summary?.classes || 0} classes, {summary?.domains || 0} domains</span>
          <h2>Knowledge Graph Ontology</h2>
        </div>
        <div className="graph-metrics">
          <span><b>{graph.nodes.length}</b> classes</span>
          <span><b>{graph.relationships.length}</b> relationships</span>
        </div>
      </header>

      {/* ---- Filter controls ---- */}
      <div className="ont-filters">
        <div className="ont-filter-row">
          <div className="ont-filter-dropdown">
            <label><Layers size={12} />Top Domain</label>
            <select value={topDomain} onChange={(e) => { setTopDomain(e.target.value); setSelected(null); setHighlightIds(null) }}>
              <option value="">All domains</option>
              {topDomains.map(td => <option key={td} value={td}>{td}</option>)}
            </select>
          </div>
          <div className="ont-filter-dropdown">
            <label><Network size={12} />Telecom Domain</label>
            <select value={domain} onChange={(e) => { setDomain(e.target.value); setSelected(null); setHighlightIds(null) }}>
              <option value="">All domains</option>
              {domains.map(d => <option key={d.code} value={d.code}>{d.name} ({d.classes})</option>)}
            </select>
          </div>
          <div className="ont-filter-dropdown">
            <label><Layers size={12} />Top Class</label>
            <select value={topClass} onChange={(e) => { setTopClass(e.target.value); setSelected(null); setHighlightIds(null) }}>
              <option value="">All top classes</option>
              {topClasses.map(tc => <option key={tc} value={tc}>{tc}</option>)}
            </select>
          </div>
          <div className="ont-filter-dropdown">
            <label><Filter size={12} />Master Concept</label>
            <select value={masterConcept} onChange={(e) => { setMasterConcept(e.target.value); setSelected(null); setHighlightIds(null) }}>
              <option value="">All concepts</option>
              {masterConcepts.map(mc => <option key={mc} value={mc}>{mc}</option>)}
            </select>
          </div>
          <div className="ont-filter-dropdown">
            <label><GitBranch size={12} />Relationship Type</label>
            <select value={relType} onChange={(e) => { setRelType(e.target.value) }}>
              <option value="">All types ({relTypes.length})</option>
              {relTypes.map(([rt, count]) => <option key={rt} value={rt}>{rt} ({count})</option>)}
            </select>
          </div>
          <label className="ont-rca-toggle">
            <input type="checkbox" checked={rcaOnly} onChange={(e) => { setRcaOnly(e.target.checked); setSelected(null); setHighlightIds(null) }} />
            RCA only
          </label>
          {hasFilters && (
            <button type="button" className="ont-clear-btn" onClick={clearFilters}><RotateCcw size={12} />Clear all</button>
          )}
        </div>
      </div>

      {/* ---- Graph canvas ---- */}
      <div className="ontology-graph-canvas">
        {graph.nodes.length === 0 ? (
          <div className="graph-empty"><Network size={32} style={{ opacity: 0.3 }} /><p>No classes match current filters</p></div>
        ) : (
          <ForceGraph
            graph={graph}
            selectedId={selected?.id}
            highlightIds={highlightIds}
            onSelect={handleSelect}
            onFocus={() => {}}
          />
        )}
        <button type="button" className="legend-toggle" title="Show domain color legend" onClick={() => setShowLegend((v) => !v)}><Info size={17} /></button>
        {showLegend && (
          <div className="graph-legend ontology-legend">
            <header><span>Domain colors</span><button type="button" onClick={() => setShowLegend(false)}><X size={13} /></button></header>
            <div>
              {domains.filter(d => graph.nodes.some(n => n._domainCode === d.code)).map(d => (
                <span key={d.code}><i style={{ background: DOMAIN_COLORS[d.code] || '#6d7b78' }} />{d.name.replace(/^\d+\s/, '')}</span>
              ))}
            </div>
          </div>
        )}
        {selectedInfo && (
          <div className="ontology-graph-inspector">
            <header>
              <div>
                <span className="eyebrow">{selectedInfo.node.properties?.domain}</span>
                <h3>{selectedInfo.node.name}</h3>
              </div>
              <button type="button" onClick={() => handleSelect(null)}><X size={14} /></button>
            </header>
            <div className="ontology-graph-inspector-body">
              <div className="ontology-inspector-meta">
                {selectedInfo.node.properties?.topDomain && <span><strong>Layer:</strong> {selectedInfo.node.properties.topDomain}</span>}
                {selectedInfo.node.properties?.topClass && <span><strong>Class:</strong> {selectedInfo.node.properties.topClass}</span>}
                {selectedInfo.node.properties?.masterConcept && <span><strong>Concept:</strong> {selectedInfo.node.properties.masterConcept}</span>}
                {selectedInfo.node.properties?.standard && <span><strong>Standard:</strong> {selectedInfo.node.properties.standard}</span>}
                {selectedInfo.node.properties?.entityId && <span><strong>ID:</strong> {selectedInfo.node.properties.entityId}</span>}
                {selectedInfo.node.properties?.rca === 'Yes' && <span className="rca-badge">RCA</span>}
              </div>
              {selectedInfo.node.properties?.definition && (
                <p className="ontology-inspector-def">{selectedInfo.node.properties.definition}</p>
              )}
              {selectedInfo.rels.length > 0 && (
                <div className="ontology-graph-rels">
                  <h4>Relationships ({selectedInfo.rels.length})</h4>
                  {selectedInfo.rels.map((r) => {
                    const sid = typeof r.source === 'object' ? r.source.id : r.source
                    const tid = typeof r.target === 'object' ? r.target.id : r.target
                    const outgoing = sid === selectedInfo.node.id
                    const otherId = outgoing ? tid : sid
                    return (
                      <div key={r.id} className="relation-row">
                        <span>{outgoing ? 'OUT' : 'IN'}</span>
                        <strong>{r.type}</strong>
                        <p>{otherId}</p>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
      <footer className="graph-footer">
        <span><CircleDot size={13} />Full ontology · Click a class to highlight its paths · Select a relationship type to see all connections</span>
        <span><PanelRightOpen size={13} />Drag to move · Scroll to zoom</span>
      </footer>
    </div>
  )
}

export default function OntologyTab() {
  const [rcaOnly, setRcaOnly] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    const load = async () => {
      try {
        const [summary, topLevel, classes, relationshipTypes, relationshipMapping, useCases, kpiCatalog, alarmCatalog, owlRules, owlDatatypes] = await Promise.all([
          ontologyData.loadSummary(),
          ontologyData.loadTopLevel(),
          ontologyData.loadClasses(),
          ontologyData.loadRelationshipTypes(),
          ontologyData.loadRelationshipMapping(),
          ontologyData.loadUseCases(),
          ontologyData.loadKpiCatalog(),
          ontologyData.loadAlarmCatalog(),
          ontologyData.loadOwlRules(),
          ontologyData.loadOwlDatatypes(),
        ])
        setData({ summary, topLevel, classes, relationshipTypes, relationshipMapping, useCases, kpiCatalog, alarmCatalog, owlRules, owlDatatypes })
      } catch (err) {
        setError(err.message)
      }
    }
    load()
  }, [])

  if (error) {
    return (
      <div className="ontology-workspace">
        <div className="ontology-error"><AlertTriangle size={16} />{error}</div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="ontology-workspace">
        <div className="ontology-loading">Loading ontology...</div>
      </div>
    )
  }

  const filteredClasses = rcaOnly ? data.classes.filter(c => c['RCA'] === 'Yes') : data.classes
  const filteredRelTypes = rcaOnly ? data.relationshipTypes.filter(r => r['RCA'] === 'Yes') : data.relationshipTypes

  return (
    <div className="ontology-workspace">
      <div className="ontology-panel">
        <OntologyGraph classes={data.classes} relationshipMapping={data.relationshipMapping} summary={data.summary} />
        <StatStrip summary={data.summary} />
        <TopLevelClasses topLevel={data.topLevel} />
        <RcaToggle rcaOnly={rcaOnly} onToggle={setRcaOnly} summary={data.summary} />
        <DomainExplorer classes={filteredClasses} summary={data.summary} rcaOnly={rcaOnly} relationshipMapping={data.relationshipMapping} />
        <RelationshipBrowser relationshipTypes={filteredRelTypes} relationshipMapping={data.relationshipMapping} />
        <RcaTraversalPath useCases={data.useCases} />
        <RcaCatalogs kpiCatalog={data.kpiCatalog} alarmCatalog={data.alarmCatalog} />
        <StandardsFooter summary={data.summary} />
      </div>
    </div>
  )
}
