import { useCallback, useEffect, useMemo, useState } from 'react'
import { ChevronRight, Filter, Layers, Network, RotateCcw, Search, X, ZoomIn } from 'lucide-react'
import { api } from '../api'
import ForceGraph, { classColors } from './ForceGraph'

function NodeCard({ node, onDrillDown }) {
  const props = node.props || {}
  const fields = Object.entries(props).filter(([k, v]) => v != null && k !== 'id' && k !== 'embedding').slice(0, 6)
  return (
    <div className="kg-node-card">
      <div className="kg-node-header">
        <span className="kg-node-label" style={{ background: (classColors[node.label] || '#6d7b78') + '18', color: classColors[node.label] || '#6d7b78' }}>{node.label}</span>
        <code className="kg-node-id">{node.id}</code>
        <button type="button" className="kg-drill-btn" onClick={() => onDrillDown(node.id)} title="Expand neighborhood">
          <ZoomIn size={12} />
        </button>
      </div>
      {node.display && node.display !== node.id && <p className="kg-node-display">{node.display}</p>}
      {fields.length > 0 && (
        <div className="kg-node-props">
          {fields.map(([k, v]) => (
            <span key={k}><strong>{k.replaceAll('_', ' ')}:</strong> {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v).slice(0, 80)}</span>
          ))}
        </div>
      )}
    </div>
  )
}

export default function KGExplorerPanel() {
  const [query, setQuery] = useState('')
  const [availableCategories, setAvailableCategories] = useState([])
  const [selectedCategories, setSelectedCategories] = useState([])
  const [customerId, setCustomerId] = useState('')
  const [customers, setCustomers] = useState([])
  const [depth, setDepth] = useState(1)

  const [searchResults, setSearchResults] = useState(null)
  const [graphData, setGraphData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [mode, setMode] = useState('search') // 'search' | 'neighborhood' | 'category'
  const [breadcrumbs, setBreadcrumbs] = useState([])
  const [graphSelected, setGraphSelected] = useState(null)

  // Load categories and customers on mount
  useEffect(() => {
    api.kgFilterCategories().then((data) => {
      setAvailableCategories(data.categories || data || [])
    }).catch(() => {})
    api.customers({ limit: 200 }).then((data) => {
      setCustomers((data.customers || []).sort((a, b) => b.total_events - a.total_events))
    }).catch(() => {})
  }, [])

  const handleSearch = useCallback(async () => {
    if (!query.trim() && selectedCategories.length === 0) return
    setLoading(true)
    setError('')
    setMode('search')
    setBreadcrumbs([{ label: 'Search', action: () => {} }])
    try {
      const result = await api.kgSearch({
        query: query.trim(),
        categories: selectedCategories.join(','),
        customerId: customerId || undefined,
        limit: 50,
      })
      setSearchResults(result)
      // Build graph from search results
      if (result.nodes?.length) {
        setGraphData({
          nodes: result.nodes.map((n) => ({
            id: n.id, name: n.display || n.id,
            ontology_class: n.label || 'Entity',
            properties: n.props || {}, source_system: 'neo4j',
          })),
          relationships: (result.edges || []).map((e, i) => ({
            id: `e-${i}`, source: e.from?.split(':').slice(1).join(':') || e.from,
            target: e.to?.split(':').slice(1).join(':') || e.to,
            type: e.rel_type, ontology_property: e.rel_type,
          })),
          source: 'neo4j',
        })
      } else {
        setGraphData(null)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [query, selectedCategories, customerId])

  const handleDrillDown = useCallback(async (nodeId) => {
    setLoading(true)
    setError('')
    setMode('neighborhood')
    setBreadcrumbs((prev) => [...prev, { label: nodeId, nodeId }])
    try {
      const result = await api.kgNeighborhood(nodeId, { limit: 50 })
      setSearchResults(result)
      if (result.nodes?.length) {
        setGraphData({
          nodes: result.nodes.map((n) => ({
            id: n.id, name: n.display || n.id,
            ontology_class: n.label || 'Entity',
            properties: n.props || {}, source_system: 'neo4j',
          })),
          relationships: (result.edges || []).map((e, i) => ({
            id: `e-${i}`,
            source: e.from?.includes(':') ? e.from.split(':').slice(1).join(':') : e.from,
            target: e.to?.includes(':') ? e.to.split(':').slice(1).join(':') : e.to,
            type: e.rel_type, ontology_property: e.rel_type,
          })),
          source: 'neo4j',
        })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const handleCategorySubgraph = useCallback(async (overrideCategories) => {
    const cats = overrideCategories || selectedCategories
    if (cats.length === 0) return
    setLoading(true)
    setError('')
    setMode('category')
    setBreadcrumbs([{ label: `Category: ${cats.join(', ')}` }])
    try {
      const result = await api.kgCategorySubgraph({
        categories: cats,
        customerId: customerId || undefined,
        depth,
        limitPerRoot: 15,
        maxRoots: 40,
      })
      setSearchResults(result)
      if (result.nodes?.length) {
        setGraphData({
          nodes: result.nodes.map((n) => ({
            id: n.id, name: n.display || n.id,
            ontology_class: n.label || 'Entity',
            properties: n.props || {}, source_system: 'neo4j',
          })),
          relationships: (result.edges || []).map((e, i) => ({
            id: `e-${i}`,
            source: e.from?.includes(':') ? e.from.split(':').slice(1).join(':') : e.from,
            target: e.to?.includes(':') ? e.to.split(':').slice(1).join(':') : e.to,
            type: e.rel_type, ontology_property: e.rel_type,
          })),
          source: 'neo4j',
        })
      }
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [selectedCategories, customerId, depth])

  const reset = useCallback(() => {
    setQuery('')
    setSelectedCategories([])
    setSearchResults(null)
    setGraphData(null)
    setError('')
    setMode('search')
    setBreadcrumbs([])
    setGraphSelected(null)
  }, [])

  const toggleCategory = useCallback((catId) => {
    setSelectedCategories((prev) =>
      prev.includes(catId) ? prev.filter((c) => c !== catId) : [...prev, catId]
    )
  }, [])

  const visibleGraph = useMemo(() => {
    if (!graphData) return { nodes: [], relationships: [], source: 'neo4j' }
    return graphData
  }, [graphData])

  const nodeCount = searchResults?.nodes?.length || 0
  const edgeCount = searchResults?.edges?.length || 0

  return (
    <div className="kg-explorer-workspace">
      <section className="kg-explorer-sidebar">
        <header className="panel-header">
          <div>
            <span className="eyebrow">Knowledge Graph</span>
            <h2>KG Explorer</h2>
          </div>
          {(searchResults || error) && (
            <button type="button" className="icon-button" title="Reset" onClick={reset}><RotateCcw size={15} /></button>
          )}
        </header>

        {/* Search bar */}
        <div className="kg-search-bar">
          <div className="kg-search-input-row">
            <label className="kg-search-field">
              <Search size={14} />
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') handleSearch() }}
                placeholder="Search entities by ID or text..."
              />
            </label>
            <button type="button" onClick={handleSearch} disabled={loading || (!query.trim() && selectedCategories.length === 0)}>
              <Search size={13} />Search
            </button>
          </div>

          {/* Customer filter */}
          <label className="kg-filter-row">
            <span>Customer</span>
            <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
              <option value="">All customers</option>
              {customers.map((c) => (
                <option key={c.customer_id} value={c.customer_id}>{c.customer_id}</option>
              ))}
            </select>
          </label>

          {/* Category filter chips */}
          <div className="kg-category-section">
            <div className="kg-category-header">
              <Filter size={12} /><span>Category Filters</span>
            </div>
            <div className="kg-category-chips">
              {availableCategories.map((cat) => (
                <button
                  key={cat.id}
                  type="button"
                  className={`kg-category-chip ${selectedCategories.includes(cat.id) ? 'active' : ''}`}
                  onClick={() => toggleCategory(cat.id)}
                >
                  {cat.display_name}
                  <small>{cat.count}</small>
                </button>
              ))}
            </div>
            {selectedCategories.length > 0 && (
              <div className="kg-depth-row">
                <label>
                  <span>Hop depth</span>
                  <input type="range" min={1} max={4} value={depth} onChange={(e) => setDepth(Number(e.target.value))} />
                  <strong>{depth}</strong>
                </label>
                <button type="button" onClick={handleCategorySubgraph} disabled={loading}>
                  <Layers size={13} />Category Subgraph
                </button>
              </div>
            )}
          </div>
        </div>

        {/* Breadcrumbs */}
        {breadcrumbs.length > 0 && (
          <div className="kg-breadcrumbs">
            <button type="button" onClick={reset}>Explorer</button>
            {breadcrumbs.map((bc, i) => (
              <span key={i}>
                <ChevronRight size={11} />
                <span className="kg-bc-label">{bc.label}</span>
              </span>
            ))}
          </div>
        )}

        {/* Error */}
        {error && <div className="kg-error">{error}</div>}

        {/* Loading */}
        {loading && <div className="kg-loading"><span /><span /><span />Querying Knowledge Graph...</div>}

        {/* Results list */}
        {searchResults && !loading && (
          <div className="kg-results">
            <div className="kg-results-header">
              <span>{nodeCount} node{nodeCount !== 1 ? 's' : ''}</span>
              {edgeCount > 0 && <span>{edgeCount} edge{edgeCount !== 1 ? 's' : ''}</span>}
              {searchResults.truncated && <span className="kg-truncated">Truncated</span>}
            </div>
            <div className="kg-results-list">
              {(searchResults.nodes || []).slice(0, 100).map((node) => (
                <NodeCard key={node.key || node.id} node={node} onDrillDown={handleDrillDown} />
              ))}
              {nodeCount === 0 && <p className="kg-empty">No nodes found matching your query.</p>}
            </div>
          </div>
        )}

        {/* Empty state */}
        {!searchResults && !loading && !error && (
          <div className="kg-welcome">
            <Network size={28} style={{ opacity: 0.3 }} />
            <h3>Interactive Graph Explorer</h3>
            <p>Search for entities by ID or text, filter by category, then drill down into neighborhoods.</p>
            <div className="kg-welcome-actions">
              <button type="button" onClick={() => { setSelectedCategories(['network_issues']); handleCategorySubgraph(['network_issues']) }}>
                Network Issues
              </button>
              <button type="button" onClick={() => { setSelectedCategories(['billing']); handleCategorySubgraph(['billing']) }}>
                Billing
              </button>
              <button type="button" onClick={() => { setSelectedCategories(['disputes']); handleCategorySubgraph(['disputes']) }}>
                Disputes
              </button>
            </div>
          </div>
        )}
      </section>

      {/* Graph visualization */}
      <section className="kg-explorer-graph">
        <header className="panel-header graph-header">
          <div>
            <span className="eyebrow">{mode === 'neighborhood' ? '1-Hop Neighborhood' : mode === 'category' ? 'Category Subgraph' : 'Search Results'}</span>
            <h1>Knowledge graph</h1>
          </div>
          <div className="graph-metrics">
            <span><b>{visibleGraph.nodes.length}</b> entities</span>
            <span><b>{visibleGraph.relationships.length}</b> relations</span>
          </div>
        </header>
        <div className="kg-graph-canvas">
          {visibleGraph.nodes.length === 0 ? (
            <div className="graph-empty">
              <Network size={32} style={{ opacity: 0.3 }} />
              <p>Search or select categories to visualize the graph</p>
            </div>
          ) : (
            <ForceGraph
              graph={visibleGraph}
              selectedId={graphSelected?.id}
              onSelect={setGraphSelected}
              onFocus={(node) => { if (node?.id) handleDrillDown(node.id) }}
            />
          )}
          {graphSelected && (
            <aside className="entity-inspector">
              <header>
                <div><span className="eyebrow">Selected entity</span><h3>{graphSelected.name}</h3></div>
                <button type="button" title="Close" onClick={() => setGraphSelected(null)}><X size={16} /></button>
              </header>
              <div className="entity-class">
                <span style={{ background: classColors[graphSelected.ontology_class] || '#6d7b78' }} />
                {graphSelected.ontology_class}
              </div>
              <dl>
                <div><dt>ID</dt><dd>{graphSelected.id}</dd></div>
                {Object.entries(graphSelected.properties || {}).filter(([k, v]) => v != null && k !== 'id' && k !== 'embedding').slice(0, 10).map(([k, v]) => (
                  <div key={k}><dt>{k}</dt><dd>{typeof v === 'object' ? JSON.stringify(v) : String(v)}</dd></div>
                ))}
              </dl>
              <button type="button" className="kg-inspect-drill" onClick={() => handleDrillDown(graphSelected.id)}>
                <ZoomIn size={13} />Expand neighborhood
              </button>
            </aside>
          )}
        </div>
      </section>
    </div>
  )
}
