import { useEffect, useMemo, useRef, useState } from 'react'
import { Activity, AlertTriangle, ArrowLeft, BarChart3, CheckCircle2, CircleDot, Compass, Database, FileSpreadsheet, Focus, Folder, Home, Info, Layers, Network, PanelRightOpen, RotateCcw, Search, Shield, Upload, Users, X } from 'lucide-react'
import { api } from './api'
import LandingPage from './components/LandingPage'
import CustomerExplorer from './components/CustomerExplorer'
import NLQueryPanel from './components/NLQueryPanel'
import CSVIngestionPanel from './components/CSVIngestionPanel'
import ForceGraph, { classColors } from './components/ForceGraph'
import OntologyTab from './components/ontology/OntologyTab'
import KGExplorerPanel from './components/KGExplorerPanel'
// RCA Pipeline is now integrated into NLQueryPanel
import TriageDashboard from './components/TriageDashboard'

const legendItems = [
  ['Customer', 'Customer'],
  ['Account', 'Account'],
  ['Invoice', 'Invoice'],
  ['Charge', 'Charge'],
  ['NetworkFailure', 'Network Failure'],
  ['PaymentFailure', 'Payment Failure'],
  ['Incident', 'Incident'],
  ['LogEvent', 'Log Event'],
  ['ApiKpiBreach', 'KPI Breach'],
  ['PmCounter', 'PM Counter'],
  ['Dispute', 'Dispute'],
  ['SlaCredit', 'SLA Credit'],
  ['Site', 'Site'],
  ['Service', 'Service'],
  ['RootCause', 'Root Cause'],
  ['Evidence', 'Evidence'],
]

const emptyGraph = { nodes: [], relationships: [], source: 'demo' }

function Status({ label, value, live }) {
  return <div className="system-status"><span className={live ? 'live' : ''} /><div><small>{label}</small><strong>{value}</strong></div></div>
}

function answerProviderStatus(health) {
  if (!health) return 'KG only'
  if (health.lmstudio === 'configured') return health.lmstudio_model ? `Local (${health.lmstudio_model})` : 'LM Studio'
  if (health.answer_llm !== 'configured') return 'KG only'
  if (health.llm_provider === 'ollama') return 'Ollama'
  if (health.llm_provider === 'openai') return 'OpenAI'
  return 'KG only'
}

function EntityInspector({ entity, graph, onClose }) {
  if (!entity) return null
  const nodeById = new Map((graph?.nodes || []).map((node) => [node.id, node]))
  const relations = (graph?.relationships || [])
    .filter((rel) => rel.source === entity.id || rel.target === entity.id)
    .slice(0, 10)
  const properties = Object.entries(entity.properties || {}).slice(0, 12)
  return (
    <aside className="entity-inspector">
      <header><div><span className="eyebrow">Selected entity</span><h3>{entity.name}</h3></div><button type="button" title="Close details" onClick={onClose}><X size={16} /></button></header>
      <div className="entity-class"><span style={{ background: classColors[entity.ontology_class] || '#6d7b78' }} />{entity.ontology_class}</div>
      <dl>
        <div><dt>Canonical ID</dt><dd>{entity.id}</dd></div>
        <div><dt>Source</dt><dd>{entity.source_system || 'Ontology'}</dd></div>
        {properties.map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}
      </dl>
      <section className="entity-relations">
        <h4>Direct relations</h4>
        {relations.length ? relations.map((rel) => {
          const outgoing = rel.source === entity.id
          const other = nodeById.get(outgoing ? rel.target : rel.source)
          return (
            <div key={rel.id} className="relation-row">
              <span>{outgoing ? 'OUT' : 'IN'}</span>
              <strong>{rel.type}</strong>
              <p>{other?.name || (outgoing ? rel.target : rel.source)}</p>
              <small>{other?.ontology_class || 'Entity'}</small>
            </div>
          )
        }) : <p className="muted-note">No direct relations in the current view.</p>}
      </section>
    </aside>
  )
}

function IngestionPanel() {
  const [folderName, setFolderName] = useState('')
  const [files, setFiles] = useState([])
  const [loading, setLoading] = useState(false)
  const [progress, setProgress] = useState(0)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!loading) {
      setProgress(result ? 100 : 0)
      return undefined
    }
    setProgress(8)
    const timer = window.setInterval(() => {
      setProgress((value) => Math.min(value + Math.max(1, Math.round((92 - value) / 8)), 92))
    }, 500)
    return () => window.clearInterval(timer)
  }, [loading, result])

  const submit = async (event) => {
    event.preventDefault()
    if (!folderName.trim() || !files.length || loading) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      setResult(await api.ingestExcel({ folderName: folderName.trim(), files }))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="ingestion-panel" aria-label="Excel ingestion">
      <header>
        <div><span className="eyebrow">Ontology ingestion</span><h2>Ingestion panel</h2></div>
        {result && <span className="ingestion-ok"><CheckCircle2 size={14} />Ready</span>}
      </header>
      <form onSubmit={submit}>
        <label className="folder-input">
          <Folder size={15} />
          <input value={folderName} onChange={(event) => setFolderName(event.target.value)} placeholder="Folder name inside workspace" />
        </label>
        <label className="file-picker">
          <FileSpreadsheet size={16} />
          <span>{files.length ? `${files.length} Excel file${files.length > 1 ? 's' : ''} selected` : 'Choose Excel files'}</span>
          <input type="file" accept=".xlsx,.xlsm" multiple onChange={(event) => setFiles(Array.from(event.target.files || []))} />
        </label>
        <button type="submit" disabled={!folderName.trim() || !files.length || loading}><Upload size={15} />{loading ? 'Generating' : 'Generate CSV'}</button>
      </form>
      {error && <div className="ingestion-error"><AlertTriangle size={14} />{error}</div>}
      {(loading || progress > 0) && (
        <div className="ingestion-progress" aria-label="Ingestion progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow={progress} role="progressbar">
          <span style={{ width: `${progress}%` }} />
          <small>{loading ? `Processing workbook sheets (${progress}%)` : 'CSV generation complete'}</small>
        </div>
      )}
      {result && (
        <div className="ingestion-result">
          <div><strong>{result.node_count}</strong><span>nodes</span></div>
          <div><strong>{result.relationship_count}</strong><span>relationships</span></div>
          <p>{result.nodes_path}</p>
          <p>{result.relationships_path}</p>
          {!!result.warnings?.length && <small>{result.warnings.length} warning{result.warnings.length > 1 ? 's' : ''}: {result.warnings[0]}</small>}
        </div>
      )}
    </section>
  )
}

function EvaluationPanel({ evalQuestion = '', evalKgAnswer = '', evalClaudeAnswer = '', evalChatgptAnswer = '', onQuestionChange, onKgAnswerChange }) {
  const [question, setQuestion] = useState(evalQuestion)
  const [kgAnswer, setKgAnswer] = useState(evalKgAnswer)
  const [claudeAnswer, setClaudeAnswer] = useState(evalClaudeAnswer)
  const [chatgptAnswer, setChatgptAnswer] = useState(evalChatgptAnswer)
  const [contextMode, setContextMode] = useState('kg_context')
  const [result, setResult] = useState(null)
  const [loading, setLoading] = useState(false)
  const [fetchingAI, setFetchingAI] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (evalQuestion) setQuestion(evalQuestion)
    if (evalKgAnswer) setKgAnswer(evalKgAnswer)
    if (evalClaudeAnswer) setClaudeAnswer(evalClaudeAnswer)
    if (evalChatgptAnswer) setChatgptAnswer(evalChatgptAnswer)
  }, [evalQuestion, evalKgAnswer, evalClaudeAnswer, evalChatgptAnswer])

  const fetchAIAnswers = async () => {
    if (!question.trim() || fetchingAI) return
    setFetchingAI(true)
    setError('')
    try {
      const response = await api.getLLMAnswers({ question, kgAnswer: contextMode === 'kg_context' ? kgAnswer : null, contextMode })
      if (response.success) {
        if (response.claude_answer) setClaudeAnswer(response.claude_answer)
        if (response.openai_answer) setChatgptAnswer(response.openai_answer)
      } else if (response.error) {
        setError(response.error)
      }
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setFetchingAI(false)
    }
  }

  const evaluate = async (event) => {
    event.preventDefault()
    if (!question.trim() || loading) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      setResult(await api.evaluateManual({ question, kgAnswer, claudeAnswer, chatgptAnswer }))
    } catch (requestError) {
      setError(requestError.message)
    } finally {
      setLoading(false)
    }
  }

  const answerInputs = [
    ['Our KG', kgAnswer, setKgAnswer],
    ['Claude', claudeAnswer, setClaudeAnswer],
    ['ChatGPT', chatgptAnswer, setChatgptAnswer],
  ]

  return (
    <section className="evaluation-panel" aria-label="Manual answer evaluation">
      <header>
        <div><span className="eyebrow">Answer benchmark</span><h2>Manual evaluation</h2></div>
        {result && <span className="ingestion-ok"><CheckCircle2 size={14} />Winner: {result.winner}</span>}
      </header>
      <form onSubmit={evaluate}>
        <label className="eval-question">
          <span>Question</span>
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Paste the question asked to all three systems" />
        </label>
        <div className="eval-context-bar">
          <span className="eval-context-label">What context do Claude & ChatGPT receive?</span>
          <div className="eval-context-options">
            <label className={`eval-context-option ${contextMode === 'kg_context' ? 'active' : ''}`}>
              <input type="radio" name="contextMode" value="kg_context" checked={contextMode === 'kg_context'} onChange={() => setContextMode('kg_context')} />
              <div>
                <strong>With KG answer</strong>
                <span>They get your KG answer as reference — tests narration quality</span>
              </div>
            </label>
            <label className={`eval-context-option ${contextMode === 'raw_data' ? 'active' : ''}`}>
              <input type="radio" name="contextMode" value="raw_data" checked={contextMode === 'raw_data'} onChange={() => setContextMode('raw_data')} />
              <div>
                <strong>With raw CSV data</strong>
                <span>They get the same source CSVs, no KG — simulates "no knowledge graph" scenario</span>
              </div>
            </label>
            <label className={`eval-context-option ${contextMode === 'question_only' ? 'active' : ''}`}>
              <input type="radio" name="contextMode" value="question_only" checked={contextMode === 'question_only'} onChange={() => setContextMode('question_only')} />
              <div>
                <strong>Question only</strong>
                <span>No data at all — tests pure LLM knowledge (baseline)</span>
              </div>
            </label>
          </div>
        </div>
        <button type="button" onClick={fetchAIAnswers} disabled={!question.trim() || fetchingAI} style={{ justifySelf: 'start', marginBottom: '12px' }}>
          <Activity size={15} />{fetchingAI ? 'Fetching AI answers...' : `Fetch Claude & ChatGPT answers (${contextMode === 'kg_context' ? 'with KG' : contextMode === 'raw_data' ? 'with raw data' : 'question only'})`}
        </button>
        <div className="eval-answer-grid">
          {answerInputs.map(([label, value, setter]) => (
            <label className="eval-answer-box" key={label}>
              <span>{label}</span>
              <textarea value={value} onChange={(event) => setter(event.target.value)} placeholder={`Paste ${label} answer`} />
            </label>
          ))}
        </div>
        {error && <div className="ingestion-error"><AlertTriangle size={14} />{error}</div>}
        <button type="submit" disabled={!question.trim() || loading}><BarChart3 size={15} />{loading ? 'Evaluating' : 'Evaluate answers'}</button>
      </form>
      {result && (
        <div className="eval-results">
          <div className="score-grid">
            {result.evaluations.map((item) => (
              <article className={`score-card ${item.name === result.winner ? 'winner' : ''}`} key={item.name}>
                <header><strong>{item.name}</strong><span>{item.verdict}</span></header>
                <div className="score-number">{item.score}<small>/100</small></div>
                <div className="eval-metrics">
                  <div className="eval-metric-row">
                    <span className="eval-metric-label">Grounding</span>
                    <div className="eval-metric-bar"><div className="eval-metric-fill" style={{ width: `${item.grounding_score || 0}%`, background: item.grounding_score >= 70 ? '#16a34a' : item.grounding_score >= 40 ? '#ca8a04' : '#dc2626' }} /></div>
                    <span className="eval-metric-val">{item.grounding_score || 0}%</span>
                  </div>
                  <div className="eval-metric-row">
                    <span className="eval-metric-label">Entity ID Accuracy</span>
                    <div className="eval-metric-bar"><div className="eval-metric-fill" style={{ width: `${item.entity_id_accuracy || 0}%`, background: item.entity_id_accuracy >= 70 ? '#16a34a' : item.entity_id_accuracy >= 40 ? '#ca8a04' : '#dc2626' }} /></div>
                    <span className="eval-metric-val">{item.entity_id_accuracy || 0}%</span>
                  </div>
                </div>
                <dl>
                  <div><dt>Tokens</dt><dd>{item.token_estimate}</dd></div>
                  <div><dt>Fact hits</dt><dd>{item.source_fact_overlap}</dd></div>
                  <div><dt>Outside content</dt><dd>{Math.round(item.outside_content_ratio * 100)}%</dd></div>
                  <div><dt>Valid IDs</dt><dd>{item.cited_ids?.length || 0}</dd></div>
                  <div><dt>Hallucinated IDs</dt><dd style={{ color: item.invalid_ids?.length ? '#dc2626' : '#16a34a' }}>{item.invalid_ids?.length || 0}</dd></div>
                </dl>
              </article>
            ))}
          </div>
          <section className="eval-facts">
            <h3>Real Facts From KG / Workbook</h3>
            <ul>{result.real_facts.map((fact, index) => <li key={`${fact}-${index}`}>{fact}</li>)}</ul>
          </section>
          <div className="eval-comparison-grid">
            {result.evaluations.map((item) => (
              <section className="eval-review" key={item.name}>
                <h3>{item.name}</h3>
                <h4>Correct / supported</h4>
                <ul>{item.supported_facts.length ? item.supported_facts.map((fact, index) => <li key={`s-${index}`}>{fact}</li>) : <li>No source fact matched.</li>}</ul>
                <h4>Missing</h4>
                <ul>{item.missing_facts.length ? item.missing_facts.map((fact, index) => <li key={`m-${index}`}>{fact}</li>) : <li>No major source fact missing.</li>}</ul>
                <h4>Unsupported / outside content</h4>
                <ul>{item.unsupported_claims.length ? item.unsupported_claims.map((claim, index) => <li key={`u-${index}`}>{claim}</li>) : <li>No major unsupported claim detected.</li>}</ul>
              </section>
            ))}
          </div>
        </div>
      )}
    </section>
  )
}

// GraphPanel renders the KG visualization with legend and inspector
function GraphPanel({ graph, selected, focusedId, query, showLegend, highlightIds, onSelect, onFocus, onSetFocusedId, onSetQuery, onSetShowLegend, onLoadBase, eyebrow, showToolbar, emptyMessage }) {
  const searchedGraph = useMemo(() => {
    const normalized = (query || '').trim().toLowerCase()
    if (!normalized) return graph
    const matched = new Set(graph.nodes.filter((node) => `${node.name} ${node.id} ${node.ontology_class}`.toLowerCase().includes(normalized)).map((node) => node.id))
    graph.relationships.forEach((rel) => {
      if (matched.has(rel.source)) matched.add(rel.target)
      if (matched.has(rel.target)) matched.add(rel.source)
    })
    return { ...graph, nodes: graph.nodes.filter((node) => matched.has(node.id)), relationships: graph.relationships.filter((rel) => matched.has(rel.source) && matched.has(rel.target)) }
  }, [graph, query])

  const visibleGraph = useMemo(() => {
    if (!focusedId) return searchedGraph
    const related = new Set([focusedId])
    searchedGraph.relationships.forEach((rel) => {
      if (rel.source === focusedId) related.add(rel.target)
      if (rel.target === focusedId) related.add(rel.source)
    })
    return {
      ...searchedGraph,
      nodes: searchedGraph.nodes.filter((node) => related.has(node.id)),
      relationships: searchedGraph.relationships.filter((rel) => related.has(rel.source) && related.has(rel.target)),
    }
  }, [searchedGraph, focusedId])

  return (
    <section className={`graph-panel ${showToolbar ? '' : 'nlquery-graph-panel'}`}>
      <header className="panel-header graph-header">
        <div><span className="eyebrow">{eyebrow}</span><h1>Knowledge graph</h1></div>
        <div className="graph-metrics"><span><b>{visibleGraph.nodes.length}</b> entities</span><span><b>{visibleGraph.relationships.length}</b> relations</span></div>
      </header>
      {showToolbar && (
        <div className="graph-toolbar">
          {onLoadBase && <button type="button" className="focus-clear" onClick={onLoadBase}><RotateCcw size={14} />Base graph</button>}
          {focusedId && <button type="button" className="focus-clear" onClick={() => onSetFocusedId('')}><Focus size={14} />Clear focus</button>}
          {onSetQuery && <label className="graph-search"><Search size={15} /><input value={query} onChange={(event) => onSetQuery(event.target.value)} placeholder="Find entity" /></label>}
        </div>
      )}
      <div className="graph-canvas">
        {visibleGraph.nodes.length === 0 ? (
          <div className="graph-empty"><Network size={32} style={{ opacity: 0.3 }} /><p>{emptyMessage || 'No graph data yet'}</p></div>
        ) : (
          <ForceGraph graph={visibleGraph} selectedId={selected?.id} focusedId={focusedId} highlightIds={highlightIds} onSelect={onSelect} onFocus={onFocus} />
        )}
        {focusedId && (
          <button type="button" className="graph-back-btn" onClick={() => onSetFocusedId('')}>
            <ArrowLeft size={14} />Show full graph
          </button>
        )}
        <button type="button" className="legend-toggle" title="Show node color legend" onClick={() => onSetShowLegend((v) => !v)}><Info size={17} /></button>
        {showLegend && (
          <div className="graph-legend">
            <header><span>Node colors</span><button type="button" title="Close legend" onClick={() => onSetShowLegend(false)}><X size={13} /></button></header>
            <div>
              {legendItems.map(([key, label]) => <span key={key}><i style={{ background: classColors[key] || '#6d7b78' }} />{label}</span>)}
            </div>
          </div>
        )}
        <EntityInspector entity={selected} graph={searchedGraph} onClose={() => onSelect(null)} />
      </div>
      <footer className="graph-footer">
        <span><CircleDot size={13} />{graph.source === 'neo4j' ? 'Live Neo4j traversal' : 'No graph data yet'}</span>
        <span><PanelRightOpen size={13} />Click a node to inspect · Drag to move</span>
      </footer>
    </section>
  )
}

function WorkspaceDivider({ containerRef }) {
  const dividerRef = useRef(null)
  const [dragging, setDragging] = useState(false)

  useEffect(() => {
    if (!dragging) return
    const container = containerRef.current
    if (!container) return

    const onMove = (e) => {
      const clientX = e.touches ? e.touches[0].clientX : e.clientX
      const rect = container.getBoundingClientRect()
      const offsetX = clientX - rect.left
      const totalW = rect.width
      const leftPct = Math.max(20, Math.min(80, (offsetX / totalW) * 100))
      const rightPct = 100 - leftPct
      const children = container.children
      // children[0] = chat, children[1] = divider, children[2] = graph
      if (children[0]) children[0].style.flex = `${leftPct} 0 0`
      if (children[2]) children[2].style.flex = `${rightPct} 0 0`
    }
    const onUp = () => setDragging(false)

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('touchmove', onMove)
    document.addEventListener('touchend', onUp)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'

    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('touchmove', onMove)
      document.removeEventListener('touchend', onUp)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
  }, [dragging, containerRef])

  return (
    <div
      ref={dividerRef}
      className={`workspace-divider ${dragging ? 'dragging' : ''}`}
      onMouseDown={() => setDragging(true)}
      onTouchStart={() => setDragging(true)}
    />
  )
}

export default function App() {
  const [health, setHealth] = useState(null)
  const [page, setPage] = useState('landing')
  const workspaceRef = useRef(null)

  // RCA graph state
  const [nlGraph, setNlGraph] = useState(emptyGraph)
  const [nlCustomerGraph, setNlCustomerGraph] = useState(null) // full customer subgraph
  const [nlHighlightIds, setNlHighlightIds] = useState(null)   // node IDs from query traversal
  const [nlSelected, setNlSelected] = useState(null)
  const [nlFocusedId, setNlFocusedId] = useState('')
  const [nlShowLegend, setNlShowLegend] = useState(false)

  const [evalQuestion, setEvalQuestion] = useState('')
  const [evalKgAnswer, setEvalKgAnswer] = useState('')
  const [evalClaudeAnswer, setEvalClaudeAnswer] = useState('')
  const [evalChatgptAnswer, setEvalChatgptAnswer] = useState('')

  // Track which customer to pre-select in RCA when navigating from Customer 360
  const [rcaCustomerId, setRcaCustomerId] = useState('')

  const nlPanelRef = useRef(null)
  const nlCustomerGraphRef = useRef(null)

  const expandNlNode = async (node) => {
    if (!node?.id) return
    setNlSelected(node)
    setNlFocusedId(node.id)
  }

  const handleQuickEvaluate = async (question, kgAnswer = '') => {
    setEvalQuestion(question)
    setEvalKgAnswer(kgAnswer)
    setEvalClaudeAnswer('')
    setEvalChatgptAnswer('')
    setPage('evaluation')
    setTimeout(async () => {
      try {
        const response = await api.getLLMAnswers({ question, kgAnswer })
        if (response.success) {
          if (response.claude_answer) setEvalClaudeAnswer(response.claude_answer)
          if (response.openai_answer) setEvalChatgptAnswer(response.openai_answer)
        }
      } catch (err) {
        console.error('Failed to fetch LLM answers:', err)
      }
    }, 100)
  }

  const handleNavigateToRCA = (customerId) => {
    setRcaCustomerId(customerId)
    setPage('nlquery')
  }

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ neo4j: 'offline', openai: 'unconfigured', ollama: 'unconfigured', llm_provider: 'kg', data: 'unavailable' }))
    // Load causal ontology overview graph for the RCA page default view
    api.graphOverview({ limit: 60 }).then((data) => {
      const normalized = {
        nodes: (data.nodes || []).map((n) => ({
          id: n.id, name: n.name || n.id,
          ontology_class: n.ontology_class || 'Entity',
          properties: n.properties || {}, source_system: 'neo4j',
        })),
        relationships: (data.relationships || []).map((r) => ({
          id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
        })),
        source: 'neo4j',
        total_nodes: (data.nodes || []).length,
        total_relationships: (data.relationships || []).length,
      }
      setNlGraph(normalized)
    }).catch(() => {})
  }, [])

  return (
    <main className="app-shell">
      <header className="app-header">
        <div className="brand"><div className="brand-logo"><img src="/kpmg-logo.png" alt="KPMG" /></div><div><strong>Telecom RCA Intelligence</strong></div></div>
        <nav className="top-nav" aria-label="Workspace pages">
          <button type="button" className={page === 'landing' ? 'active' : ''} onClick={() => setPage('landing')}><Home size={15} />Home</button>
          <button type="button" className={page === 'customers' ? 'active' : ''} onClick={() => setPage('customers')}><Users size={15} />Customer 360</button>
          <button type="button" className={page === 'nlquery' ? 'active' : ''} onClick={() => setPage('nlquery')}><Database size={15} />RCA</button>
          <button type="button" className={page === 'evaluation' ? 'active' : ''} onClick={() => setPage('evaluation')}><BarChart3 size={15} />Benchmark</button>
          <button type="button" className={page === 'kg-explorer' ? 'active' : ''} onClick={() => setPage('kg-explorer')}><Compass size={15} />KG Explorer</button>
          <button type="button" className={page === 'triage' ? 'active' : ''} onClick={() => setPage('triage')}><Shield size={15} />Triage</button>
          <button type="button" className={page === 'csv-ingestion' ? 'active' : ''} onClick={() => setPage('csv-ingestion')}><Upload size={15} />Ingestion</button>
          <button type="button" className={page === 'ontology' ? 'active' : ''} onClick={() => setPage('ontology')}><Layers size={15} />Ontology</button>
        </nav>
        <div className="header-status">
          <Status label="Knowledge graph" value={health?.neo4j === 'connected' ? 'Neo4j live' : 'Neo4j offline'} live={health?.neo4j === 'connected'} />
          <Status label="Answer LLM" value={answerProviderStatus(health)} live={health?.answer_llm === 'configured'} />
          <div className="environment"><Activity size={14} />Dynamic workspace</div>
        </div>
      </header>

      {/* Landing page */}
      <div style={{ display: page === 'landing' ? undefined : 'none' }}>
        <LandingPage onNavigate={setPage} health={health} />
      </div>

      {/* Customer 360 */}
      <div className="customer-workspace" style={{ display: page === 'customers' ? undefined : 'none' }}>
        <CustomerExplorer onGraphChange={() => {}} onNavigateToRCA={handleNavigateToRCA} />
      </div>

      {/* RCA — always mounted, hidden when not active */}
      <div className="workspace" ref={workspaceRef} style={{ display: page === 'nlquery' ? undefined : 'none' }}>
        <NLQueryPanel
          ref={nlPanelRef}
          preselectedCustomerId={rcaCustomerId}
          onCustomerGraphChange={(custGraph) => {
            if (!custGraph) {
              // Cleared selection — reload the ontology graph
              nlCustomerGraphRef.current = null
              setNlCustomerGraph(null)
              setNlHighlightIds(null)
              setNlSelected(null)
              setNlFocusedId('')
              api.graphOverview({ limit: 60 }).then((data) => {
                setNlGraph({
                  nodes: (data.nodes || []).map((n) => ({
                    id: n.id, name: n.name || n.id,
                    ontology_class: n.ontology_class || 'Entity',
                    properties: n.properties || {}, source_system: 'neo4j',
                  })),
                  relationships: (data.relationships || []).map((r) => ({
                    id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
                  })),
                  source: 'neo4j',
                })
              }).catch(() => {})
              return
            }
            nlCustomerGraphRef.current = custGraph
            setNlCustomerGraph(custGraph)
            setNlGraph(custGraph)
            setNlHighlightIds(null)
            setNlSelected(null)
            setNlFocusedId('')
          }}
          onGraphChange={(queryGraph) => {
            if (!queryGraph.nodes?.length) return
            const custGraph = nlCustomerGraphRef.current
            // Merge: keep customer graph as base, highlight query traversal nodes
            if (custGraph && custGraph.nodes.length > 0) {
              const queryNodeIds = new Set(queryGraph.nodes.map((n) => n.id))
              // Add any query nodes/rels not in customer graph
              const mergedNodes = [...custGraph.nodes]
              const mergedNodeIds = new Set(mergedNodes.map((n) => n.id))
              queryGraph.nodes.forEach((n) => { if (!mergedNodeIds.has(n.id)) mergedNodes.push(n) })
              const mergedRels = [...custGraph.relationships]
              const mergedRelIds = new Set(mergedRels.map((r) => r.id))
              queryGraph.relationships.forEach((r) => { if (!mergedRelIds.has(r.id)) mergedRels.push(r) })
              setNlGraph({ ...custGraph, nodes: mergedNodes, relationships: mergedRels })
              setNlHighlightIds(queryNodeIds)
            } else {
              setNlGraph(queryGraph)
              setNlHighlightIds(null)
            }
            setNlSelected(null)
            setNlFocusedId('')
          }}
          onQuickEvaluate={handleQuickEvaluate}
        />
        <WorkspaceDivider containerRef={workspaceRef} />
        <GraphPanel
          graph={nlGraph}
          selected={nlSelected}
          focusedId={nlFocusedId}
          showLegend={nlShowLegend}
          highlightIds={nlHighlightIds}
          onSelect={setNlSelected}
          onFocus={expandNlNode}
          onSetFocusedId={setNlFocusedId}
          onSetShowLegend={setNlShowLegend}
          showToolbar={true}
          eyebrow={nlHighlightIds ? 'Query traversal highlighted' : nlCustomerGraph ? 'Customer subgraph' : 'Ontology'}
          emptyMessage="Loading Knowledge Graph..."
        />
      </div>

      {/* CSV Ingestion */}
      <div style={{ display: page === 'csv-ingestion' ? undefined : 'none' }}>
        <CSVIngestionPanel />
      </div>

      {/* Evaluation */}
      <div className="evaluation-workspace" style={{ display: page === 'evaluation' ? undefined : 'none' }}>
        <EvaluationPanel evalQuestion={evalQuestion} evalKgAnswer={evalKgAnswer} evalClaudeAnswer={evalClaudeAnswer} evalChatgptAnswer={evalChatgptAnswer} onQuestionChange={setEvalQuestion} onKgAnswerChange={setEvalKgAnswer} />
      </div>

      {/* Ontology */}
      <div style={{ display: page === 'ontology' ? undefined : 'none' }}>
        <OntologyTab />
      </div>

      {/* KG Explorer */}
      <div className="kg-explorer-outer" style={{ display: page === 'kg-explorer' ? undefined : 'none' }}>
        <KGExplorerPanel />
      </div>

      {/* Triage & Incidents */}
      <div className="triage-workspace" style={{ display: page === 'triage' ? undefined : 'none' }}>
        <TriageDashboard />
      </div>

      {/* Excel Ingestion */}
      <div className="ingestion-workspace" style={{ display: page === 'ingestion' ? undefined : 'none' }}>
        <IngestionPanel />
      </div>
    </main>
  )
}
