import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, Clock, Database, FileText, RotateCcw, Shield, Zap } from 'lucide-react'
import { api } from '../api'

function SeverityDot({ severity }) {
  const colors = { CRITICAL: '#dc2626', HIGH: '#ea580c', MEDIUM: '#ca8a04', LOW: '#16a34a' }
  const s = (severity || '').toUpperCase()
  return <span className="triage-severity-dot" style={{ background: colors[s] || '#6d7b78' }} title={s} />
}

function TriageCard({ item }) {
  const [expanded, setExpanded] = useState(false)
  // Handle both flat anomaly items and { customer_id, trigger, rca_result } shape
  const rca = item.rca_result || item
  const primaryCause = rca.primary_cause || rca.root_causes?.[0] || {}
  const severity = primaryCause.severity || item.severity || 'MEDIUM'
  const custId = item.customer_id || rca.customer_id
  const trigger = item.trigger || ''
  const narrative = rca.narrative || ''
  const causes = rca.root_causes || []
  const actions = rca.recommended_actions || []

  return (
    <div className={`triage-card triage-${severity.toLowerCase()}`}>
      <div className="triage-card-header" onClick={() => setExpanded(!expanded)}>
        <SeverityDot severity={severity} />
        <div className="triage-card-title">
          <strong>{primaryCause.cause_category?.replaceAll('_', ' ') || item.entity_type?.replaceAll('_', ' ') || 'Anomaly'}</strong>
          {trigger && <code>{trigger.replaceAll('_', ' ')}</code>}
        </div>
        <span className="triage-customer">{custId}</span>
      </div>
      <p className="triage-card-fact">
        {narrative
          ? narrative.replace(/\*\*/g, '').split('\n').filter(Boolean)[0]?.slice(0, 200)
          : item.fact || item.description || 'No description'}
      </p>
      {expanded && (
        <div className="triage-card-detail">
          {rca.confidence > 0 && <span><Shield size={10} />Confidence: {Math.round(rca.confidence * 100)}%</span>}
          {rca.response_time_ms > 0 && <span><Clock size={10} />{rca.response_time_ms}ms</span>}
          {causes.length > 0 && (
            <div className="triage-actions" style={{ width: '100%' }}>
              <h5>Root Causes ({causes.length})</h5>
              <ul>{causes.map((c, i) => <li key={i}><strong>{c.cause_category?.replaceAll('_', ' ')}</strong> — {c.severity}</li>)}</ul>
            </div>
          )}
          {actions.length > 0 && (
            <div className="triage-actions" style={{ width: '100%' }}>
              <h5>Recommended Actions</h5>
              <ul>{actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
          {rca.evidence?.length > 0 && (
            <div className="triage-actions" style={{ width: '100%' }}>
              <h5>Evidence ({rca.evidence.length})</h5>
              <ul>{rca.evidence.slice(0, 5).map((e, i) => (
                <li key={i}><code>{e.entity_id}</code> ({e.entity_type}) — {e.fact}</li>
              ))}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function IncidentRow({ incident }) {
  return (
    <tr className="incident-row">
      <td><code>{incident.id || incident.incident_id}</code></td>
      <td><SeverityDot severity={incident.severity} /> {(incident.severity || '').toUpperCase()}</td>
      <td>{incident.entity_type || incident.type || '-'}</td>
      <td>{incident.customer_id || '-'}</td>
      <td>{incident.description || incident.summary || '-'}</td>
      <td>{incident.status || incident.remediation_status || '-'}</td>
      <td>{incident.created_at || incident.event_date || '-'}</td>
    </tr>
  )
}

export default function TriageDashboard() {
  const [triageResults, setTriageResults] = useState(null)
  const [incidents, setIncidents] = useState([])
  const [scanning, setScanning] = useState(false)
  const [loadingIncidents, setLoadingIncidents] = useState(false)
  const [error, setError] = useState('')
  const [tab, setTab] = useState('scan') // 'scan' | 'incidents'
  const [scanLimit, setScanLimit] = useState(10)

  const runScan = useCallback(async () => {
    setScanning(true)
    setError('')
    setTriageResults(null)
    try {
      const data = await api.triageScan({ limit: scanLimit })
      setTriageResults(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setScanning(false)
    }
  }, [scanLimit])

  const loadIncidents = useCallback(async () => {
    setLoadingIncidents(true)
    try {
      const data = await api.incidents({ limit: 50 })
      setIncidents(data.incidents || data || [])
    } catch (e) {
      setIncidents([])
    } finally {
      setLoadingIncidents(false)
    }
  }, [])

  useEffect(() => {
    if (tab === 'incidents') loadIncidents()
  }, [tab, loadIncidents])

  // Aggregate stats from triage results
  // Backend returns { scanned, triage_results: [{ customer_id, trigger, rca_result }] }
  const triageItems = triageResults?.triage_results || triageResults?.anomalies || triageResults?.items || triageResults?.results || []

  const stats = triageResults ? (() => {
    const bySev = { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 }
    for (const item of triageItems) {
      const rca = item.rca_result || item
      const causes = rca.root_causes || []
      for (const c of causes) {
        const s = (c.severity || 'MEDIUM').toUpperCase()
        bySev[s] = (bySev[s] || 0) + 1
      }
      if (causes.length === 0) {
        const s = (item.severity || rca.primary_cause?.severity || 'MEDIUM').toUpperCase()
        bySev[s] = (bySev[s] || 0) + 1
      }
    }
    const customers = new Set(triageItems.map((i) => i.customer_id || i.rca_result?.customer_id).filter(Boolean))
    return { total: triageItems.length, bySev, uniqueCustomers: customers.size }
  })() : null

  return (
    <section className="triage-dashboard">
      <header className="panel-header">
        <div>
          <span className="eyebrow">Proactive Monitoring</span>
          <h2>Triage & Incidents</h2>
        </div>
        <div className="triage-tabs">
          <button type="button" className={tab === 'scan' ? 'active' : ''} onClick={() => setTab('scan')}>
            <Zap size={13} />Anomaly Scan
          </button>
          <button type="button" className={tab === 'incidents' ? 'active' : ''} onClick={() => setTab('incidents')}>
            <FileText size={13} />Incident Log
          </button>
        </div>
      </header>

      {/* Anomaly Scan Tab */}
      {tab === 'scan' && (
        <div className="triage-scan-tab">
          <div className="triage-scan-controls">
            <label className="triage-limit">
              <span>Scan limit</span>
              <input type="number" min={1} max={50} value={scanLimit} onChange={(e) => setScanLimit(Number(e.target.value))} />
            </label>
            <button type="button" className="triage-scan-btn" onClick={runScan} disabled={scanning}>
              <Zap size={14} />{scanning ? 'Scanning...' : 'Run Anomaly Scan'}
            </button>
          </div>

          {error && <div className="triage-error"><AlertTriangle size={13} />{error}</div>}

          {scanning && (
            <div className="triage-scanning">
              <div className="rca-loading-bar"><span /></div>
              <p>Scanning Knowledge Graph for anomalies and unresolved issues...</p>
            </div>
          )}

          {/* Stats summary */}
          {stats && (
            <div className="triage-stats">
              <div className="triage-stat-card">
                <strong>{stats.total}</strong>
                <span>Total Anomalies</span>
              </div>
              <div className="triage-stat-card triage-critical">
                <strong>{stats.bySev.CRITICAL}</strong>
                <span>Critical</span>
              </div>
              <div className="triage-stat-card triage-high">
                <strong>{stats.bySev.HIGH}</strong>
                <span>High</span>
              </div>
              <div className="triage-stat-card triage-medium">
                <strong>{stats.bySev.MEDIUM}</strong>
                <span>Medium</span>
              </div>
              <div className="triage-stat-card triage-low">
                <strong>{stats.bySev.LOW}</strong>
                <span>Low</span>
              </div>
              <div className="triage-stat-card">
                <strong>{stats.uniqueCustomers}</strong>
                <span>Customers Affected</span>
              </div>
            </div>
          )}

          {/* Anomaly cards */}
          {triageItems.length > 0 && (
            <div className="triage-results">
              {triageItems.map((item, i) => (
                <TriageCard key={item.entity_id || item.id || i} item={item} />
              ))}
            </div>
          )}

          {!scanning && !triageResults && !error && (
            <div className="triage-welcome">
              <AlertTriangle size={28} style={{ opacity: 0.3 }} />
              <h3>Proactive Anomaly Detection</h3>
              <p>Scan the Knowledge Graph for unresolved issues, unremediated failures, and anomalous patterns across all customers.</p>
              <button type="button" onClick={runScan}>
                <Zap size={14} />Start Scan
              </button>
            </div>
          )}
        </div>
      )}

      {/* Incidents Tab */}
      {tab === 'incidents' && (
        <div className="triage-incidents-tab">
          <div className="triage-incidents-header">
            <h4>Incident Audit Trail</h4>
            <button type="button" onClick={loadIncidents} disabled={loadingIncidents}><RotateCcw size={12} /></button>
          </div>

          {loadingIncidents && <div className="rca-loading-inline">Loading incidents...</div>}

          {!loadingIncidents && incidents.length === 0 && (
            <div className="triage-welcome">
              <FileText size={28} style={{ opacity: 0.3 }} />
              <p>No incidents recorded yet. Run a triage scan or RCA to generate incident records.</p>
            </div>
          )}

          {incidents.length > 0 && (
            <div className="triage-incidents-table-wrap">
              <table className="triage-incidents-table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Severity</th>
                    <th>Type</th>
                    <th>Customer</th>
                    <th>Description</th>
                    <th>Status</th>
                    <th>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {incidents.map((inc, i) => (
                    <IncidentRow key={inc.id || inc.incident_id || i} incident={inc} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
