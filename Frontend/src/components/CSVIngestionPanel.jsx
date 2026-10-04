import { useState } from 'react'
import { AlertTriangle, CheckCircle2, Database, FileSpreadsheet, Loader, Network, Trash2, Upload } from 'lucide-react'
import { api } from '../api'

const DOMAINS = [
  { id: 'network', label: 'Network Alarms', desc: 'SNMP traps, cell outages, fiber cuts' },
  { id: 'billing', label: 'Billing / Invoices', desc: 'Invoices, charging records, line items' },
  { id: 'complaints', label: 'Complaints', desc: 'Customer disputes, resolution tracking' },
  { id: 'incident', label: 'Service Disruptions', desc: 'Service problems, SLA breaches' },
  { id: 'pm_counters', label: 'PM Counters', desc: 'Network performance counter readings' },
  { id: 'api', label: 'API / KPI', desc: 'KPI observations, threshold breaches' },
  { id: 'logs', label: 'System Logs', desc: 'Application and infrastructure logs' },
  { id: 'sla_credits', label: 'SLA Credits', desc: 'Service credit adjustments' },
  { id: 'payment_failures', label: 'Payment Failures', desc: 'Failed payments, dunning events' },
]

function DomainUploadCard({ domain, onResult }) {
  const [file, setFile] = useState(null)
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const upload = async () => {
    if (!file || loading) return
    setLoading(true)
    setError('')
    setResult(null)
    try {
      const data = await api.ingestDomain(domain.id, file)
      setResult(data)
      if (onResult) onResult(domain.id, data)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className={`domain-upload-card ${result ? 'success' : ''} ${error ? 'errored' : ''}`}>
      <div className="domain-upload-header">
        <FileSpreadsheet size={16} />
        <div>
          <strong>{domain.label}</strong>
          <small>{domain.desc}</small>
        </div>
        {result && <CheckCircle2 size={16} className="domain-ok" />}
      </div>
      <div className="domain-upload-controls">
        <label className="domain-file-picker">
          <span>{file ? file.name : `Choose ${domain.id}.csv`}</span>
          <input type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        </label>
        <button type="button" onClick={upload} disabled={!file || loading}>
          {loading ? <Loader size={14} className="spinning" /> : <Upload size={14} />}
          {loading ? 'Ingesting' : 'Ingest'}
        </button>
      </div>
      {error && <div className="domain-upload-error"><AlertTriangle size={12} />{error}</div>}
      {result && (
        <div className="domain-upload-result">
          <span><strong>{result.rows_written ?? result.rows_received}</strong> rows</span>
          <span><strong>{result.nodes_created}</strong> nodes</span>
          <span><strong>{result.relationships_created}</strong> rels</span>
          {result.rows_rejected > 0 && <span className="rejected"><strong>{result.rows_rejected}</strong> rejected</span>}
        </div>
      )}
    </div>
  )
}

export default function CSVIngestionPanel() {
  const [schemaLoading, setSchemaLoading] = useState(false)
  const [schemaResult, setSchemaResult] = useState(null)
  const [crossLinkLoading, setCrossLinkLoading] = useState(false)
  const [crossLinkResult, setCrossLinkResult] = useState(null)
  const [verifyData, setVerifyData] = useState(null)
  const [verifyLoading, setVerifyLoading] = useState(false)
  const [clearLoading, setClearLoading] = useState(false)
  const [clearResult, setClearResult] = useState(null)
  const [showClearConfirm, setShowClearConfirm] = useState(false)
  const [domainResults, setDomainResults] = useState({})
  const [error, setError] = useState('')

  const clearGraph = async () => {
    setClearLoading(true)
    setError('')
    setShowClearConfirm(false)
    try {
      const result = await api.clearGraph()
      setClearResult(result)
      // Reset all other state since graph is now empty
      setSchemaResult(null)
      setCrossLinkResult(null)
      setVerifyData(null)
      setDomainResults({})
    } catch (e) { setError(e.message) }
    finally { setClearLoading(false) }
  }

  const setupSchema = async () => {
    setSchemaLoading(true)
    setError('')
    try {
      setSchemaResult(await api.schemaSetup())
    } catch (e) { setError(e.message) }
    finally { setSchemaLoading(false) }
  }

  const createCrossLinks = async () => {
    setCrossLinkLoading(true)
    setError('')
    try {
      setCrossLinkResult(await api.ingestCrossLinks())
    } catch (e) { setError(e.message) }
    finally { setCrossLinkLoading(false) }
  }

  const verify = async () => {
    setVerifyLoading(true)
    try {
      setVerifyData(await api.verify())
    } catch (e) { setError(e.message) }
    finally { setVerifyLoading(false) }
  }

  const handleDomainResult = (domain, data) => {
    setDomainResults((prev) => ({ ...prev, [domain]: data }))
  }

  const totalIngested = Object.values(domainResults).reduce((sum, r) => sum + (r.nodes_created || 0), 0)

  return (
    <div className="csv-ingestion-workspace">
      <div className="csv-ingestion-panel">
        <header>
          <div><span className="eyebrow">Ingestion Pipeline</span><h2>CSV Domain Ingestion</h2></div>
          {totalIngested > 0 && <span className="ingestion-ok"><CheckCircle2 size={14} />{totalIngested} nodes created</span>}
        </header>

        <section className="pipeline-steps">
          <div className="pipeline-step danger-step">
            <div className="step-number" style={{ background: '#e05d4f' }}>
              <Trash2 size={14} />
            </div>
            <div className="step-content">
              <h3>Clear Existing Graph</h3>
              <p>Delete all nodes, relationships, constraints, and indexes. Use this before re-uploading new data.</p>
              {!showClearConfirm ? (
                <button type="button" className="danger-btn" onClick={() => setShowClearConfirm(true)} disabled={clearLoading}>
                  <Trash2 size={14} />Clear entire graph
                </button>
              ) : (
                <div className="confirm-bar">
                  <span>This will delete everything. Are you sure?</span>
                  <button type="button" className="danger-btn" onClick={clearGraph} disabled={clearLoading}>
                    {clearLoading ? <Loader size={14} className="spinning" /> : <Trash2 size={14} />}
                    {clearLoading ? 'Clearing...' : 'Yes, delete all'}
                  </button>
                  <button type="button" onClick={() => setShowClearConfirm(false)}>Cancel</button>
                </div>
              )}
              {clearResult && (
                <div className="step-result">
                  <CheckCircle2 size={14} />
                  Deleted {clearResult.deleted_nodes?.toLocaleString()} nodes, {clearResult.deleted_relationships?.toLocaleString()} relationships, {clearResult.dropped_constraints} constraints
                </div>
              )}
            </div>
          </div>

          <div className="pipeline-step">
            <div className="step-number">1</div>
            <div className="step-content">
              <h3>Ingest Domain CSVs</h3>
              <p>Upload CSV files for each domain. Each row becomes a graph node with relationships.</p>
              <div className="domain-upload-grid">
                {DOMAINS.map((d) => <DomainUploadCard key={d.id} domain={d} onResult={handleDomainResult} />)}
              </div>
            </div>
          </div>

          <div className="pipeline-step">
            <div className="step-number">2</div>
            <div className="step-content">
              <h3>Verify Graph</h3>
              <p>Check node counts, relationship counts, and cross-domain link integrity.</p>
              <button type="button" onClick={verify} disabled={verifyLoading}>
                {verifyLoading ? <Loader size={14} className="spinning" /> : <CheckCircle2 size={14} />}
                {verifyLoading ? 'Verifying...' : 'Verify graph'}
              </button>
              {verifyData && (
                <div className="verify-results">
                  <div className="verify-section">
                    <h4>Node Counts</h4>
                    <div className="verify-grid">
                      {Object.entries(verifyData.node_counts || {}).slice(0, 20).map(([label, count]) => (
                        <div key={label}><strong>{count.toLocaleString()}</strong><span>{label}</span></div>
                      ))}
                    </div>
                  </div>
                  <div className="verify-section">
                    <h4>Cross-Domain Links</h4>
                    <div className="verify-grid">
                      {Object.entries(verifyData.cross_domain_links || {}).map(([label, count]) => (
                        <div key={label}><strong>{count.toLocaleString()}</strong><span>{label}</span></div>
                      ))}
                    </div>
                  </div>
                  <div className="verify-total">
                    <strong>{verifyData.total_customers?.toLocaleString()}</strong> total customers in graph
                  </div>
                </div>
              )}
            </div>
          </div>
        </section>

        {error && <div className="ingestion-error"><AlertTriangle size={14} />{error}</div>}
      </div>
    </div>
  )
}
