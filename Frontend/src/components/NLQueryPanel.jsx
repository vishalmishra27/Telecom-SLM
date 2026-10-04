import { useCallback, useEffect, useImperativeHandle, forwardRef, useMemo, useRef, useState } from 'react'
import { ArrowUp, BarChart3, Bot, ChevronRight, Clock, Database, ExternalLink, GitBranch, MessageSquare, RotateCcw, Route, Search, Shield, Sparkles, Square, UserRound } from 'lucide-react'
import { api } from '../api'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'

/** Replace severity words and [CRITICAL]/[HIGH]/[MEDIUM]/[LOW] tags with colored badges */
function colorizeSeverity(text) {
  if (!text) return text
  return text
    .replace(/\[CRITICAL\]/gi, '<span class="severity-badge severity-critical">CRITICAL</span>')
    .replace(/\[HIGH\]/gi, '<span class="severity-badge severity-high">HIGH</span>')
    .replace(/\[MEDIUM\]/gi, '<span class="severity-badge severity-medium">MEDIUM</span>')
    .replace(/\[LOW\]/gi, '<span class="severity-badge severity-low">LOW</span>')
    .replace(/\bseverity:\s*(CRITICAL|critical)\b/gi, 'severity: <span class="severity-badge severity-critical">CRITICAL</span>')
    .replace(/\bseverity:\s*(HIGH|high)\b/gi, 'severity: <span class="severity-badge severity-high">HIGH</span>')
    .replace(/\bseverity:\s*(MEDIUM|medium)\b/gi, 'severity: <span class="severity-badge severity-medium">MEDIUM</span>')
    .replace(/\bseverity:\s*(LOW|low)\b/gi, 'severity: <span class="severity-badge severity-low">LOW</span>')
}

const MODEL_ICONS = {
  claude: { color: '#D97706', label: 'Claude' },
  openai: { color: '#10A37F', label: 'OpenAI' },
  lmstudio: { color: '#7C3AED', label: 'Local LLM' },
  groq: { color: '#F55036', label: 'Groq (Llama 70B)' },
  deterministic: { color: '#00338D', label: 'KG Traversal' },
}

/** Extract unique entity IDs from evidence keyed by type */
function extractEntityIds(evidence) {
  if (!evidence || typeof evidence !== 'object') return []
  const ids = new Map() // id → type label
  const ID_FIELDS = ['customer_id', 'alarm_id', 'kpi_id', 'invoice_id', 'charge_id', 'payment_id', 'complaint_id', 'log_id', 'disruption_id', 'dunning_id', 'id']
  const TYPE_LABELS = {
    customer_id: 'Customer', alarm_id: 'Alarm', kpi_id: 'KPI', invoice_id: 'Invoice',
    charge_id: 'Charge', payment_id: 'Payment', complaint_id: 'Complaint', log_id: 'Log',
    disruption_id: 'Disruption', dunning_id: 'Dunning', id: 'Entity',
  }
  for (const value of Object.values(evidence)) {
    if (!Array.isArray(value)) continue
    for (const item of value) {
      if (!item || typeof item !== 'object') continue
      for (const field of ID_FIELDS) {
        const v = item[field]
        if (v && typeof v === 'string' && v !== '?' && !ids.has(v)) {
          ids.set(v, TYPE_LABELS[field] || 'Entity')
        }
      }
    }
  }
  return Array.from(ids, ([id, type]) => ({ id, type }))
}

function EntityPills({ entities, onEntityClick }) {
  if (!entities?.length) return null
  // Group by type
  const grouped = {}
  for (const e of entities) {
    ;(grouped[e.type] ||= []).push(e.id)
  }
  return (
    <div className="nl-entity-pills">
      {Object.entries(grouped).map(([type, entityIds]) => (
        <div key={type} className="nl-entity-group">
          <span className="nl-entity-type">{type}</span>
          {entityIds.slice(0, 15).map((id) => (
            <button key={id} type="button" className="nl-entity-btn" onClick={() => onEntityClick(id, type)} title={`View graph for ${id}`}>
              <ExternalLink size={10} />{id}
            </button>
          ))}
          {entityIds.length > 15 && <span className="nl-entity-more">+{entityIds.length - 15} more</span>}
        </div>
      ))}
    </div>
  )
}

function CitationCard({ citation }) {
  return (
    <div className="citation-card">
      <div className="citation-header">
        <code>{citation.evidence_id || citation.label}</code>
        <span className="citation-type">{citation.evidence_type || citation.source}</span>
      </div>
      <p className="citation-claim">{citation.claim || citation.value}</p>
      {citation.supporting_text && <p className="citation-support">{citation.supporting_text}</p>}
    </div>
  )
}

function TraversalHops({ graph }) {
  if (!graph) return null
  const nodes = graph.nodes || []
  const rels = graph.relationships || []
  if (!nodes.length && !rels.length) return null

  // Build node lookup by id
  const nodeMap = {}
  for (const n of nodes) {
    nodeMap[n.id] = n
  }

  // Get the label/class for a node — check labels array, ontology_class, or properties
  const getClass = (node) => {
    if (!node) return '?'
    if (node.labels?.length) return node.labels[0]
    if (node.ontology_class) return node.ontology_class
    return '?'
  }

  return (
    <div className="nl-traversal-section">
      <h4><GitBranch size={13} /> Graph Traversal Path</h4>
      {rels.length > 0 ? (
        <div className="nl-traversal-hops">
          {rels.map((rel, i) => {
            const src = nodeMap[rel.source]
            const tgt = nodeMap[rel.target]
            return (
              <div key={`${rel.id || i}`} className="nl-hop">
                <div className="nl-hop-node">
                  <span className="nl-hop-class">{getClass(src)}</span>
                  <code>{src?.name || rel.source}</code>
                </div>
                <div className="nl-hop-arrow">
                  <span className="nl-hop-rel">{rel.type || rel.ontology_property}</span>
                  <span className="nl-hop-line">→</span>
                </div>
                <div className="nl-hop-node">
                  <span className="nl-hop-class">{getClass(tgt)}</span>
                  <code>{tgt?.name || rel.target}</code>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="nl-traversal-nodes">
          {nodes.map((n) => (
            <span key={n.id} className="nl-traversal-node-pill">
              <span className="nl-hop-class">{getClass(n)}</span>
              <code>{n.name}</code>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function CypherTraversalPanel({ traversal }) {
  if (!traversal || !traversal.steps?.length) return <p className="muted-note">No traversal data.</p>
  return (
    <div className="nl-traversal-panel">
      <div className="nl-traversal-header">
        <Route size={14} />
        <div>
          <strong>{traversal.description}</strong>
          <span className="nl-traversal-meta">{traversal.total_steps} hops &middot; intent: {traversal.intent}</span>
        </div>
      </div>
      <div className="nl-traversal-steps">
        {traversal.steps.map((step) => (
          <div key={step.order} className="nl-traversal-step">
            <div className="nl-traversal-step-num">{step.order}</div>
            <div className="nl-traversal-step-body">
              <div className="nl-traversal-path">
                <code className="nl-traversal-class">{step.from_class}</code>
                <span className="nl-traversal-arrow"><ChevronRight size={12} /></span>
                <code className="nl-traversal-rel">{step.rel}</code>
                <span className="nl-traversal-arrow"><ChevronRight size={12} /></span>
                <code className="nl-traversal-class">{step.to_class}</code>
                {step.nodes_found > 0 && (
                  <span className="nl-traversal-count">{step.nodes_found} found</span>
                )}
              </div>
              <p className="nl-traversal-explain">{step.explanation}</p>
            </div>
          </div>
        ))}
      </div>
      <details className="nl-traversal-cypher">
        <summary>Cypher Query</summary>
        <pre><code>{traversal.cypher}</code></pre>
      </details>
    </div>
  )
}

function EvidencePanel({ evidence, graph }) {
  if ((!evidence || !Object.keys(evidence).length) && !graph) return <p className="muted-note">No evidence retrieved.</p>

  return (
    <div className="nl-evidence-panel">
      <TraversalHops graph={graph} />
      {Object.entries(evidence).map(([key, value]) => {
        if (Array.isArray(value) && value.length > 0) {
          return (
            <div key={key} className="nl-evidence-section">
              <h4>{key.replaceAll('_', ' ')}<span>{value.length}</span></h4>
              <div className="nl-evidence-items">
                {value.slice(0, 50).map((item, i) => {
                  const id = item.id || item.alarm_id || item.kpi_id || item.invoice_id || item.charge_id || item.payment_id || item.complaint_id || item.log_id || item.disruption_id || item.dunning_id || `${key}-${i}`
                  const fields = Object.entries(item).filter(([k, v]) => v != null && k !== 'id' && k !== 'description')
                  return (
                    <div key={`${id}-${i}`} className="nl-evidence-item">
                      <code>{id}</code>
                      <div className="nl-evidence-fields">
                        {fields.slice(0, 8).map(([k, v]) => (
                          <span key={k}><strong>{k.replaceAll('_', ' ')}:</strong> {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v)}</span>
                        ))}
                      </div>
                      {item.description && <p>{item.description}</p>}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        }
        if (typeof value === 'object' && value !== null && !Array.isArray(value)) {
          const fields = Object.entries(value).filter(([, v]) => v != null)
          return (
            <div key={key} className="nl-evidence-section">
              <h4>{key.replaceAll('_', ' ')}</h4>
              <div className="nl-evidence-item">
                <div className="nl-evidence-fields">
                  {fields.map(([k, v]) => (
                    <span key={k}><strong>{k.replaceAll('_', ' ')}:</strong> {typeof v === 'number' ? (Number.isInteger(v) ? v : v.toFixed(2)) : String(v)}</span>
                  ))}
                </div>
              </div>
            </div>
          )
        }
        return null
      })}
    </div>
  )
}

function ConfidenceBadge({ confidence }) {
  const colors = { high: '#0091DA', medium: '#f0a13a', low: '#e05d4f' }
  return (
    <span className="confidence-badge" style={{ background: (colors[confidence] || '#6d7b78') + '18', color: colors[confidence] || '#6d7b78' }}>
      {confidence}
    </span>
  )
}

/** Build suggested questions dynamically from the selected customer's data profile.
 *  All questions use phrasings validated against OTel-LLM to avoid refusals. */
function buildCustomerQuestions(customer) {
  if (!customer) return []
  const id = customer.customer_id
  const groups = []

  if (customer.alarms > 0) {
    groups.push({ category: 'Network Alarms', questions: [
      `What is the root cause of the alarms for ${id} and what are your recommendations?`,
    ]})
  }
  if (customer.kpi_observations > 0 || customer.pm_counters > 0) {
    groups.push({ category: 'KPI & Performance', questions: [
      `What is the root cause of the KPI breaches for ${id} and what are your recommendations?`,
    ]})
  }
  if (customer.invoices > 0) {
    groups.push({ category: 'Billing', questions: [
      `What is the root cause of the billing issues for ${id} and what are your recommendations?`,
    ]})
  }
  if (customer.payments > 0) {
    groups.push({ category: 'Payments & Dunning', questions: [
      `What is the root cause of the payment and dunning issues for ${id} and what are your recommendations?`,
    ]})
  }
  if (customer.complaints > 0) {
    groups.push({ category: 'Complaints', questions: [
      `What is the root cause of the complaints for ${id} and what are your recommendations?`,
    ]})
  }
  if (customer.disruptions > 0) {
    groups.push({ category: 'Service Disruptions', questions: [
      `What is the root cause of the outage and service disruption for ${id} and what are your recommendations?`,
    ]})
  }
  // Full 360 — only if customer has data across multiple domains
  const domainCount = [customer.alarms, customer.kpi_observations, customer.invoices, customer.payments, customer.complaints, customer.disruptions].filter(v => v > 0).length
  if (domainCount >= 2) {
    groups.push({ category: 'Full RCA', questions: [
      `What is the root cause of all the issues for ${id} and what are your recommendations?`,
    ]})
  }
  return groups
}

const GENERIC_QUESTIONS = [
  { category: 'Network Alarms', questions: [
    'What is the root cause of the alarms across all customers and what are your recommendations?',
  ]},
  { category: 'KPI & Performance', questions: [
    'What is the root cause of the KPI breaches across all customers and what are your recommendations?',
  ]},
  { category: 'Billing & Payments', questions: [
    'What is the root cause of the billing and payment issues across all customers and what are your recommendations?',
  ]},
  { category: 'Complaints', questions: [
    'What is the root cause of the complaints across all customers and what are your recommendations?',
  ]},
  { category: 'Service Disruptions', questions: [
    'What is the root cause of the service disruptions across all customers and what are your recommendations?',
  ]},
]

const NLQueryPanel = forwardRef(function NLQueryPanel({ onGraphChange, onCustomerGraphChange, onQuickEvaluate, preselectedCustomerId }, ref) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [customerId, setCustomerId] = useState('')
  const [selectedModel, setSelectedModel] = useState('deterministic')
  const [availableModels, setAvailableModels] = useState([])
  const [customers, setCustomers] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showEvidence, setShowEvidence] = useState(null)
  const [showTraversal, setShowTraversal] = useState(null)
  const conversationRef = useRef(null)
  const abortRef = useRef(null)

  const clearChat = useCallback(() => {
    setMessages([])
    setInput('')
    setError('')
    setShowEvidence(null)
  }, [])

  useImperativeHandle(ref, () => ({ clearChat }), [clearChat])

  /** Load the full customer subgraph for visualization */
  const loadCustomerGraph = useCallback(async (cid) => {
    if (!cid || !onCustomerGraphChange) return
    try {
      const graphData = await api.customerGraph(cid)
      const normalized = {
        nodes: (graphData.nodes || []).map((n) => ({
          id: n.id, name: n.name || n.id,
          ontology_class: (n.labels || [])[0] || n.ontology_class || 'Entity',
          properties: n.properties || {}, source_system: 'neo4j',
        })),
        relationships: (graphData.relationships || []).map((r) => ({
          id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
        })),
        source: 'neo4j',
        total_nodes: (graphData.nodes || []).length,
        total_relationships: (graphData.relationships || []).length,
      }
      onCustomerGraphChange(normalized)
    } catch {
      // Silently fail — graph panel stays empty
    }
  }, [onCustomerGraphChange])

  // When navigating from Customer 360 with a preselected customer
  useEffect(() => {
    if (preselectedCustomerId && preselectedCustomerId !== customerId) {
      setCustomerId(preselectedCustomerId)
      clearChat()
      loadCustomerGraph(preselectedCustomerId)
    }
  }, [preselectedCustomerId])

  useEffect(() => {
    api.customers({ limit: 200 }).then((data) => {
      setCustomers((data.customers || []).sort((a, b) => b.total_events - a.total_events))
    }).catch(() => {})
    api.availableModels().then((data) => {
      setAvailableModels(data.models || [])
    }).catch(() => {})
  }, [])

  useEffect(() => {
    if (conversationRef.current) conversationRef.current.scrollTo({ top: conversationRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  const cancelRequest = useCallback(() => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    setLoading(false)
  }, [])

  const selectedCustomer = useMemo(() => {
    if (!customerId) return null
    return customers.find((c) => c.customer_id === customerId) || null
  }, [customerId, customers])

  const suggestedQuestions = useMemo(() => {
    if (selectedCustomer) return buildCustomerQuestions(selectedCustomer)
    return GENERIC_QUESTIONS
  }, [selectedCustomer])

  const submit = useCallback(async (questionOverride) => {
    const question = (questionOverride || input).trim()
    if (!question || loading) return
    setInput('')
    setError('')
    setShowEvidence(null)
    // Build conversation history from existing messages for multi-turn context
    const history = messages.map((msg) => ({
      role: msg.role,
      content: msg.role === 'user' ? msg.text : (msg.response?.answer || ''),
    })).filter((m) => m.content)
    setMessages((prev) => [...prev, { role: 'user', text: question }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const response = await api.nlQuery({ question, customerId: customerId || null, model: selectedModel || null, conversationHistory: history, signal: controller.signal })
      setMessages((prev) => [...prev, { role: 'assistant', response }])
      if (response.graph && onGraphChange) {
        const graphData = response.graph
        const normalized = {
          nodes: (graphData.nodes || []).map((n) => ({
            id: n.id, name: n.name || n.id,
            ontology_class: (n.labels || [])[0] || n.ontology_class || 'Entity',
            properties: n.properties || {}, source_system: 'neo4j',
          })),
          relationships: (graphData.relationships || []).map((r) => ({
            id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
          })),
          source: 'neo4j',
          total_nodes: (graphData.nodes || []).length,
          total_relationships: (graphData.relationships || []).length,
        }
        onGraphChange(normalized)
      }
    } catch (e) {
      if (e.name === 'AbortError') { setError('Request cancelled.'); return }
      setError(e.message)
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }, [input, customerId, selectedModel, loading, onGraphChange])

  const handleEntityClick = useCallback(async (entityId, entityType) => {
    if (!entityId) return
    if (entityType === 'Customer') {
      // Load the customer's full subgraph
      try {
        const graphData = await api.customerGraph(entityId)
        const normalized = {
          nodes: (graphData.nodes || []).map((n) => ({
            id: n.id, name: n.name || n.id,
            ontology_class: (n.labels || [])[0] || n.ontology_class || 'Entity',
            properties: n.properties || {}, source_system: 'neo4j',
          })),
          relationships: (graphData.relationships || []).map((r) => ({
            id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
          })),
          source: 'neo4j',
          total_nodes: (graphData.nodes || []).length,
          total_relationships: (graphData.relationships || []).length,
        }
        if (onCustomerGraphChange) onCustomerGraphChange(normalized)
        if (onGraphChange) onGraphChange(normalized)
      } catch { /* silent */ }
    } else {
      // Highlight this entity in the current graph
      if (onGraphChange) {
        // Fire a small query for just this entity to get its graph neighbourhood
        try {
          const graphData = await api.graphExpand({ nodeId: entityId, depth: 1, limit: 40 })
          const normalized = {
            nodes: (graphData.nodes || []).map((n) => ({
              id: n.id, name: n.name || n.id,
              ontology_class: (n.labels || [])[0] || n.ontology_class || 'Entity',
              properties: n.properties || {}, source_system: 'neo4j',
            })),
            relationships: (graphData.relationships || []).map((r) => ({
              id: r.id, source: r.source, target: r.target, type: r.type, ontology_property: r.type,
            })),
            source: 'neo4j',
            total_nodes: (graphData.nodes || []).length,
            total_relationships: (graphData.relationships || []).length,
          }
          onGraphChange(normalized)
        } catch { /* silent */ }
      }
    }
  }, [onGraphChange, onCustomerGraphChange])

  const activeModelInfo = MODEL_ICONS[selectedModel] || { color: '#00338D', label: 'Auto (fallback chain)' }

  return (
    <section className="nl-query-panel">
      <header className="panel-header nl-header">
        <div>
          <span className="eyebrow">Grounded Narration</span>
          <h2>Ask the Knowledge Graph</h2>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <div className="nl-model-badge" style={{ color: activeModelInfo.color, borderColor: activeModelInfo.color + '40' }}>
            <Sparkles size={13} />{activeModelInfo.label}
          </div>
          {messages.length > 0 && (
            <button type="button" className="icon-button" title="Clear chat" onClick={clearChat}><RotateCcw size={15} /></button>
          )}
        </div>
      </header>
      <div className="nl-customer-bar">
        <button
          type="button"
          className={`nl-all-customers-btn ${!customerId ? 'active' : ''}`}
          onClick={() => { if (customerId) { setCustomerId(''); setSelectedModel('deterministic'); clearChat(); if (onCustomerGraphChange) onCustomerGraphChange(null) } }}
        >
          <Database size={13} />All Customers
        </button>
        <label>
          <Search size={13} />
          <select value={customerId} onChange={(e) => { setCustomerId(e.target.value); clearChat(); if (e.target.value) { setSelectedModel('lmstudio'); loadCustomerGraph(e.target.value) } else { setSelectedModel('deterministic'); if (onCustomerGraphChange) onCustomerGraphChange(null) } }}>
            <option value="">Select a customer...</option>
            {customers.map((c) => (
              <option key={c.customer_id} value={c.customer_id}>
                {c.customer_id} — {c.customer_type.replaceAll('_', ' ')} ({c.total_events} events)
              </option>
            ))}
          </select>
        </label>
        <label className="model-picker">
          <Sparkles size={13} />
          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)} disabled={!customerId}>
            {!customerId ? (
              <option value="deterministic">KG Traversal (all customers)</option>
            ) : (
              <>
                <option value="">Auto (fallback chain)</option>
                {availableModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.display_name} ({m.model_id})
                  </option>
                ))}
              </>
            )}
          </select>
        </label>
      </div>
      <div className="nl-conversation" ref={conversationRef}>
        {messages.length === 0 && (
          <div className="nl-welcome">
            <div className="assistant-mark"><Bot size={20} /></div>
            {selectedCustomer ? (
              <>
                <h3>RCA for {selectedCustomer.customer_id}</h3>
                <p>
                  <strong>{selectedCustomer.customer_type.replaceAll('_', ' ')}</strong> with <strong>{selectedCustomer.total_events}</strong> events.
                  {' '}Questions below are tailored to this customer's data profile.
                </p>
              </>
            ) : (
              <>
                <h3>Grounded RCA narration</h3>
                <p>Ask about any customer by including their ID in the question (e.g. <strong>CUST-4367</strong>). Or select a specific customer from the dropdown for tailored questions.</p>
              </>
            )}
            <div className="nl-suggestions-grid">
              {suggestedQuestions.map((group) => (
                <div key={group.category} className="nl-suggestion-group">
                  <h5>{group.category}</h5>
                  {group.questions.map((q) => (
                    <button key={q} type="button" onClick={() => { setInput(q); submit(q) }}><MessageSquare size={11} />{q}</button>
                  ))}
                </div>
              ))}
            </div>
          </div>
        )}
        {messages.map((msg, i) => (
          <div className={`message ${msg.role}`} key={`${msg.role}-${i}`}>
            <div className="avatar">{msg.role === 'user' ? <UserRound size={16} /> : <Bot size={16} />}</div>
            <div className="message-body">
              {msg.role === 'user' ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <p style={{ margin: 0, flex: 1 }}>{msg.text}</p>
                  {onQuickEvaluate && (
                    <button
                      type="button"
                      className="evaluate-btn"
                      onClick={() => {
                        const idx = messages.findIndex((m) => m.role === 'user' && m.text === msg.text)
                        const next = messages[idx + 1]
                        onQuickEvaluate(msg.text, next?.response?.answer || '')
                      }}
                      title="Evaluate this question in Benchmark tab"
                    >
                      <BarChart3 size={12} />Evaluate
                    </button>
                  )}
                </div>
              ) : (
                <div className="nl-answer-card">
                  <div className="nl-answer-meta">
                    <span className="intent-pill"><GitBranch size={13} />{msg.response.intent?.replaceAll('_', ' ')}</span>
                    {msg.response.customer_id && <code>{msg.response.customer_id}</code>}
                    {msg.response.grounded && <span className="grounded-badge"><Shield size={11} />Grounded</span>}
                    {msg.response.confidence && <ConfidenceBadge confidence={msg.response.confidence} />}
                    <span className="nl-model-label" style={{ color: MODEL_ICONS[msg.response.model_used]?.color || '#6d7b78' }}>
                      <Sparkles size={11} />{MODEL_ICONS[msg.response.model_used]?.label || msg.response.model_used}
                    </span>
                  </div>
                  <div className="nl-answer-body">
                    <ReactMarkdown rehypePlugins={[rehypeRaw]}>{colorizeSeverity(msg.response.answer)}</ReactMarkdown>
                  </div>
                  <EntityPills entities={extractEntityIds(msg.response.evidence)} onEntityClick={handleEntityClick} />
                  {msg.response.citations?.length > 0 && (
                    <div className="nl-citations">
                      <h5>Citations ({msg.response.citations.length})</h5>
                      {msg.response.citations.map((c, ci) => <CitationCard key={ci} citation={c} />)}
                    </div>
                  )}
                  <div className="nl-answer-actions">
                    <button type="button" className={showTraversal === i ? 'active' : ''} onClick={() => setShowTraversal(showTraversal === i ? null : i)}>
                      <Route size={12} />{showTraversal === i ? 'Hide traversal' : 'Show traversal'}
                    </button>
                    <button type="button" className={showEvidence === i ? 'active' : ''} onClick={() => setShowEvidence(showEvidence === i ? null : i)}>
                      <Database size={12} />{showEvidence === i ? 'Hide evidence' : 'Show evidence'}
                    </button>
                    {msg.response.token_usage?.total_tokens > 0 && (
                      <span className="nl-token-info">{msg.response.token_usage.total_tokens} tokens</span>
                    )}
                    {msg.response.response_time_ms > 0 && (
                      <span className="nl-token-info"><Clock size={11} />{msg.response.response_time_ms}ms</span>
                    )}
                  </div>
                  {showTraversal === i && <CypherTraversalPanel traversal={msg.response.traversal} />}
                  {showEvidence === i && <EvidencePanel evidence={msg.response.evidence} graph={msg.response.graph} />}
                </div>
              )}
            </div>
          </div>
        ))}
        {loading && <div className="thinking"><span /><span /><span />Retrieving evidence and generating grounded answer</div>}
        {error && <div className="chat-error">{error}</div>}
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit() }}>
        <div className="composer-context"><Shield size={13} />Evidence-grounded — answers cite KG node IDs</div>
        <textarea value={input} onChange={(e) => setInput(e.target.value)} onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() }
        }} placeholder={selectedCustomer ? `Ask about ${selectedCustomer.customer_id}'s alarms, billing, complaints...` : 'Select a customer or ask a general question...'} rows="2" />
        {loading ? (
          <button type="button" className="composer-stop" onClick={cancelRequest} title="Stop generating"><Square size={14} /></button>
        ) : (
          <button type="submit" disabled={!input.trim()} title="Send question"><ArrowUp size={18} /></button>
        )}
      </form>
    </section>
  )
})

export default NLQueryPanel
