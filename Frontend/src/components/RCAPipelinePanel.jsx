import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, AlertTriangle, ArrowRight, Bot, CheckCircle2, ChevronDown, ChevronRight, Clock, Database, FileText, GitBranch, Layers, RotateCcw, Search, Shield, Sparkles, Users, Zap } from 'lucide-react'
import { api } from '../api'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'

function SeverityBadge({ severity }) {
  const colors = {
    CRITICAL: '#dc2626', HIGH: '#ea580c', MEDIUM: '#ca8a04', LOW: '#16a34a',
  }
  const s = (severity || '').toUpperCase()
  return (
    <span className="rca-severity" style={{ background: (colors[s] || '#6d7b78') + '14', color: colors[s] || '#6d7b78', border: `1px solid ${(colors[s] || '#6d7b78')}30` }}>
      {s || 'UNKNOWN'}
    </span>
  )
}

function ConfidenceBar({ value }) {
  const pct = Math.round((value || 0) * 100)
  const color = pct >= 75 ? '#16a34a' : pct >= 50 ? '#ca8a04' : '#dc2626'
  return (
    <div className="rca-confidence-bar">
      <div className="rca-confidence-fill" style={{ width: `${pct}%`, background: color }} />
      <span>{pct}%</span>
    </div>
  )
}

function RootCauseCard({ cause, evidence, onViewEvidence }) {
  const [expanded, setExpanded] = useState(false)
  const relatedEvidence = evidence.filter((e) => cause.evidence_entity_ids?.includes(e.entity_id))
  return (
    <div className="rca-cause-card">
      <div className="rca-cause-header" onClick={() => setExpanded(!expanded)}>
        <div className="rca-cause-title">
          <SeverityBadge severity={cause.severity} />
          <strong>{cause.cause_category?.replaceAll('_', ' ')}</strong>
        </div>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </div>
      {cause.description && <p className="rca-cause-desc">{cause.description}</p>}
      {expanded && (
        <div className="rca-cause-detail">
          {cause.recommended_actions?.length > 0 && (
            <div className="rca-actions">
              <h5>Recommended Actions</h5>
              <ul>{cause.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
          {relatedEvidence.length > 0 && (
            <div className="rca-cause-evidence">
              <h5>Supporting Evidence ({relatedEvidence.length})</h5>
              {relatedEvidence.slice(0, 8).map((e, i) => (
                <div key={i} className="rca-evidence-row">
                  <code>{e.entity_id}</code>
                  <span className="rca-evidence-type">{e.entity_type}</span>
                  <p>{e.fact}</p>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function RCAResultView({ result }) {
  if (!result) return null
  return (
    <div className="rca-result-view">
      <div className="rca-result-header">
        <div className="rca-result-meta">
          <code>{result.request_id}</code>
          <span>Customer: <strong>{result.customer_id}</strong></span>
          <span className="rca-narrated-by"><Sparkles size={11} />{result.narrated_by}</span>
          {result.requires_human_escalation && (
            <span className="rca-escalation"><AlertTriangle size={11} />Human Escalation Required</span>
          )}
        </div>
        <div className="rca-result-stats">
          <ConfidenceBar value={result.confidence} />
          {result.response_time_ms > 0 && <span className="rca-time"><Clock size={11} />{result.response_time_ms}ms</span>}
        </div>
      </div>

      {/* Narrative */}
      {result.narrative && (
        <div className="rca-narrative">
          <h4><FileText size={13} />Narrative</h4>
          <div className="rca-narrative-body">
            <ReactMarkdown rehypePlugins={[rehypeRaw]}>{result.narrative}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* Root Causes */}
      {result.root_causes?.length > 0 && (
        <div className="rca-causes-section">
          <h4><GitBranch size={13} />Root Causes ({result.root_causes.length})</h4>
          {result.root_causes.map((cause, i) => (
            <RootCauseCard key={i} cause={cause} evidence={result.evidence || []} />
          ))}
        </div>
      )}

      {/* Recommended Actions (top-level) */}
      {result.recommended_actions?.length > 0 && (
        <div className="rca-top-actions">
          <h4><Zap size={13} />Top Recommended Actions</h4>
          <ul>{result.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
        </div>
      )}

      {/* Evidence */}
      {result.evidence?.length > 0 && (
        <details className="rca-evidence-section">
          <summary><Database size={13} />All Evidence ({result.evidence.length})</summary>
          <div className="rca-evidence-list">
            {result.evidence.map((e, i) => (
              <div key={i} className="rca-evidence-item">
                <div className="rca-evidence-header">
                  <code>{e.entity_id}</code>
                  <span className="rca-evidence-type">{e.entity_type}</span>
                  {e.remediation_status && <span className="rca-remediation">{e.remediation_status}</span>}
                  {e.similarity_score != null && <span className="rca-sim-score">sim: {e.similarity_score.toFixed(2)}</span>}
                </div>
                <p>{e.fact}</p>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

function HistoryItem({ item, onSelect, isActive }) {
  return (
    <button type="button" className={`rca-history-item ${isActive ? 'active' : ''}`} onClick={() => onSelect(item.request_id)}>
      <div className="rca-history-top">
        <code>{item.customer_id}</code>
        <span className="rca-history-causes">{item.root_causes_count || '?'} causes</span>
      </div>
      <p className="rca-history-desc">{item.billing_issue_description || 'General RCA'}</p>
      <div className="rca-history-bottom">
        <span><Clock size={10} />{item.response_time_ms || 0}ms</span>
        <span>{Math.round((item.confidence || 0) * 100)}%</span>
      </div>
    </button>
  )
}

export default function RCAPipelinePanel() {
  const [customers, setCustomers] = useState([])
  const [customerId, setCustomerId] = useState('')
  const [description, setDescription] = useState('')
  const [debug, setDebug] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)

  // History
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [activeHistoryId, setActiveHistoryId] = useState('')

  // Model comparison
  const [comparing, setComparing] = useState(false)
  const [compareResult, setCompareResult] = useState(null)

  const [tab, setTab] = useState('run') // 'run' | 'history' | 'compare'

  useEffect(() => {
    api.customers({ limit: 200 }).then((data) => {
      setCustomers((data.customers || []).sort((a, b) => b.total_events - a.total_events))
    }).catch(() => {})
  }, [])

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const data = await api.rcaHistory({ limit: 50 })
      setHistory(data.history || data || [])
    } catch (e) {
      setHistory([])
    } finally {
      setHistoryLoading(false)
    }
  }, [])

  useEffect(() => {
    if (tab === 'history') loadHistory()
  }, [tab, loadHistory])

  const runRCA = useCallback(async () => {
    if (!customerId) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await api.rca({ customerId, description: description.trim(), debug })
      setResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [customerId, description, debug])

  const viewHistoryItem = useCallback(async (requestId) => {
    setActiveHistoryId(requestId)
    setLoading(true)
    try {
      const data = await api.rcaHistoryDetail(requestId)
      setResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const runComparison = useCallback(async () => {
    if (!customerId) return
    setComparing(true)
    setCompareResult(null)
    try {
      const data = await api.rcaCompareModels({ customerId, description: description.trim() })
      setCompareResult(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setComparing(false)
    }
  }, [customerId, description])

  const selectedCustomer = useMemo(() => {
    if (!customerId) return null
    return customers.find((c) => c.customer_id === customerId) || null
  }, [customerId, customers])

  return (
    <section className="rca-pipeline-panel">
      <header className="panel-header">
        <div>
          <span className="eyebrow">Multi-Step Pipeline</span>
          <h2>RCA Pipeline</h2>
        </div>
        <div className="rca-tabs">
          <button type="button" className={tab === 'run' ? 'active' : ''} onClick={() => setTab('run')}>
            <Zap size={13} />Run RCA
          </button>
          <button type="button" className={tab === 'history' ? 'active' : ''} onClick={() => setTab('history')}>
            <Clock size={13} />History
          </button>
          <button type="button" className={tab === 'compare' ? 'active' : ''} onClick={() => setTab('compare')}>
            <Layers size={13} />Compare Models
          </button>
        </div>
      </header>

      {/* Run RCA Tab */}
      {tab === 'run' && (
        <div className="rca-run-tab">
          <div className="rca-form">
            <label className="rca-form-row">
              <span><Users size={12} />Customer</span>
              <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                <option value="">Select a customer...</option>
                {customers.map((c) => (
                  <option key={c.customer_id} value={c.customer_id}>
                    {c.customer_id} — {c.customer_type.replaceAll('_', ' ')} ({c.total_events} events)
                  </option>
                ))}
              </select>
            </label>
            <label className="rca-form-row">
              <span><FileText size={12} />Issue Description (optional)</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="e.g., Customer reports unexpected charges on March invoice..."
                rows={2}
              />
            </label>
            <div className="rca-form-actions">
              <label className="rca-debug-toggle">
                <input type="checkbox" checked={debug} onChange={(e) => setDebug(e.target.checked)} />
                <span>Debug mode</span>
              </label>
              <button type="button" className="rca-run-btn" onClick={runRCA} disabled={!customerId || loading}>
                <Zap size={14} />{loading ? 'Running Pipeline...' : 'Run RCA Pipeline'}
              </button>
            </div>
          </div>

          {/* Customer summary */}
          {selectedCustomer && (
            <div className="rca-customer-summary">
              <span><strong>{selectedCustomer.customer_id}</strong></span>
              <span>{selectedCustomer.customer_type.replaceAll('_', ' ')}</span>
              <span>{selectedCustomer.total_events} events</span>
              {selectedCustomer.alarms > 0 && <span className="rca-badge">{selectedCustomer.alarms} alarms</span>}
              {selectedCustomer.invoices > 0 && <span className="rca-badge">{selectedCustomer.invoices} invoices</span>}
              {selectedCustomer.complaints > 0 && <span className="rca-badge">{selectedCustomer.complaints} complaints</span>}
            </div>
          )}

          {error && <div className="rca-error"><AlertTriangle size={13} />{error}</div>}

          {loading && (
            <div className="rca-pipeline-progress">
              <div className="rca-pipeline-steps">
                <div className="rca-pipeline-step active"><span>1</span>Supervisor Planning</div>
                <div className="rca-pipeline-step"><span>2</span>Domain Specialists</div>
                <div className="rca-pipeline-step"><span>3</span>Critic Review</div>
                <div className="rca-pipeline-step"><span>4</span>Synthesis</div>
                <div className="rca-pipeline-step"><span>5</span>Grounding Validation</div>
              </div>
              <div className="rca-loading-bar"><span /></div>
            </div>
          )}

          <RCAResultView result={result} />
        </div>
      )}

      {/* History Tab */}
      {tab === 'history' && (
        <div className="rca-history-tab">
          <div className="rca-history-sidebar">
            <div className="rca-history-header">
              <h4>Past RCA Runs</h4>
              <button type="button" onClick={loadHistory} disabled={historyLoading}><RotateCcw size={12} /></button>
            </div>
            {historyLoading && <div className="rca-loading-inline">Loading...</div>}
            {!historyLoading && history.length === 0 && <p className="rca-empty">No RCA history yet. Run an RCA analysis first.</p>}
            <div className="rca-history-list">
              {history.map((item) => (
                <HistoryItem
                  key={item.request_id}
                  item={item}
                  isActive={activeHistoryId === item.request_id}
                  onSelect={viewHistoryItem}
                />
              ))}
            </div>
          </div>
          <div className="rca-history-detail">
            {result ? <RCAResultView result={result} /> : (
              <div className="rca-empty-detail">
                <Database size={28} style={{ opacity: 0.3 }} />
                <p>Select an RCA run from the sidebar to view details</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Compare Models Tab */}
      {tab === 'compare' && (
        <div className="rca-compare-tab">
          <div className="rca-form">
            <label className="rca-form-row">
              <span><Users size={12} />Customer</span>
              <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
                <option value="">Select a customer...</option>
                {customers.map((c) => (
                  <option key={c.customer_id} value={c.customer_id}>
                    {c.customer_id} — {c.customer_type.replaceAll('_', ' ')} ({c.total_events} events)
                  </option>
                ))}
              </select>
            </label>
            <label className="rca-form-row">
              <span><FileText size={12} />Issue Description (optional)</span>
              <textarea
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Describe the billing issue..."
                rows={2}
              />
            </label>
            <button type="button" className="rca-run-btn" onClick={runComparison} disabled={!customerId || comparing}>
              <Layers size={14} />{comparing ? 'Comparing Models...' : 'Compare Models Side-by-Side'}
            </button>
          </div>

          {error && <div className="rca-error"><AlertTriangle size={13} />{error}</div>}

          {comparing && (
            <div className="rca-loading-bar"><span /></div>
          )}

          {compareResult && (
            <div className="rca-compare-results">
              {Object.entries(compareResult).map(([model, modelResult]) => (
                <div key={model} className="rca-compare-card">
                  <h4><Sparkles size={13} />{model}</h4>
                  <RCAResultView result={modelResult} />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </section>
  )
}
