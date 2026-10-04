import { useCallback, useEffect, useState } from 'react'
import { AlertTriangle, ArrowRight, ChevronDown, CreditCard, Database, DollarSign, FileWarning, GitBranch, Network, Phone, Search, Shield, Users, Zap } from 'lucide-react'
import { api } from '../api'

const TYPE_ICONS = {
  NETWORK_RCA: Network,
  PAYMENT_DUNNING: CreditCard,
  SERVICE_DISRUPTION: Zap,
  SYSTEM_ERROR: FileWarning,
  API_KPI_ANOMALY: Shield,
  COMPLAINT: Phone,
  MULTI_ISSUE: AlertTriangle,
  NETWORK_IMPACT: Network,
  BILLING_ONLY: DollarSign,
}

const TYPE_COLORS = {
  NETWORK_RCA: '#e05d4f',
  PAYMENT_DUNNING: '#f0a13a',
  SERVICE_DISRUPTION: '#3e80c2',
  SYSTEM_ERROR: '#8b67bd',
  API_KPI_ANOMALY: '#0091DA',
  COMPLAINT: '#b55a8a',
  MULTI_ISSUE: '#17201e',
  NETWORK_IMPACT: '#547486',
  BILLING_ONLY: '#6d7b78',
}

function CustomerCard({ customer, isActive, onClick }) {
  const Icon = TYPE_ICONS[customer.customer_type] || Users
  const color = TYPE_COLORS[customer.customer_type] || '#6d7b78'
  return (
    <button type="button" className={`customer-card ${isActive ? 'active' : ''}`} onClick={onClick}>
      <div className="customer-card-icon" style={{ background: color + '18', color }}>
        <Icon size={16} />
      </div>
      <div className="customer-card-info">
        <strong>{customer.customer_id}</strong>
        <small>{customer.customer_type.replaceAll('_', ' ')}</small>
      </div>
      <div className="customer-card-stats">
        <span>{customer.total_events} events</span>
      </div>
    </button>
  )
}

function ImpactSummary({ impact }) {
  if (!impact) return null
  return (
    <div className="impact-grid">
      <div className="impact-card">
        <DollarSign size={16} />
        <div><strong>${impact.total_charges?.toFixed(2)}</strong><span>Total charges</span></div>
      </div>
      <div className="impact-card">
        <DollarSign size={16} />
        <div><strong>${impact.total_credits?.toFixed(2)}</strong><span>Credits</span></div>
      </div>
      <div className="impact-card">
        <DollarSign size={16} />
        <div><strong>${impact.net_charges?.toFixed(2)}</strong><span>Net charges</span></div>
      </div>
      <div className="impact-card">
        <Phone size={16} />
        <div><strong>{impact.total_complaints}</strong><span>Complaints ({impact.open_complaints} open)</span></div>
      </div>
      <div className="impact-card">
        <Network size={16} />
        <div><strong>{impact.total_alarms}</strong><span>Alarms</span></div>
      </div>
      <div className="impact-card">
        <Zap size={16} />
        <div><strong>{impact.total_disruptions}</strong><span>Disruptions</span></div>
      </div>
      <div className="impact-card">
        <CreditCard size={16} />
        <div><strong>{impact.total_payments}</strong><span>Payments ({impact.failed_payments} failed)</span></div>
      </div>
    </div>
  )
}

/** Shows a summary of what data domains are available for this customer */
function DataProfile({ customer }) {
  if (!customer) return null
  const domains = [
    { key: 'alarms', label: 'Network Alarms', count: customer.alarms, icon: Network, color: '#e05d4f' },
    { key: 'kpi_observations', label: 'KPI Observations', count: customer.kpi_observations, icon: Shield, color: '#0091DA' },
    { key: 'pm_counters', label: 'PM Counters', count: customer.pm_counters, icon: Database, color: '#3e80c2' },
    { key: 'invoices', label: 'Invoices', count: customer.invoices, icon: DollarSign, color: '#17201e' },
    { key: 'payments', label: 'Payments', count: customer.payments, icon: CreditCard, color: '#f0a13a' },
    { key: 'complaints', label: 'Complaints', count: customer.complaints, icon: Phone, color: '#b55a8a' },
    { key: 'disruptions', label: 'Service Disruptions', count: customer.disruptions, icon: Zap, color: '#3e80c2' },
    { key: 'adjustments', label: 'SLA Credits', count: customer.adjustments, icon: DollarSign, color: '#0091DA' },
    { key: 'logs', label: 'System Logs', count: customer.logs, icon: FileWarning, color: '#8b67bd' },
  ].filter((d) => d.count > 0)

  return (
    <div className="data-profile">
      <h4><Database size={14} />Data available in Knowledge Graph</h4>
      <div className="data-profile-grid">
        {domains.map((d) => (
          <div key={d.key} className="data-profile-item">
            <d.icon size={14} style={{ color: d.color }} />
            <span className="data-profile-label">{d.label}</span>
            <span className="data-profile-count">{d.count}</span>
          </div>
        ))}
      </div>
      {domains.length === 0 && <p className="muted-note">No event data found for this customer.</p>}
    </div>
  )
}

/** Shows the KG traversal paths available for this customer */
function TraversalPaths({ customer }) {
  if (!customer) return null
  const paths = []
  if (customer.alarms > 0) {
    paths.push({ label: 'Alarm Root Cause', path: 'Customer → Account → Alarm → Service → Site', available: true })
  }
  if (customer.kpi_observations > 0) {
    paths.push({ label: 'KPI Breach', path: 'Customer → Account → KPI Observation → Threshold → Alarm', available: true })
  }
  if (customer.invoices > 0 && customer.complaints > 0) {
    paths.push({ label: 'Billing Dispute', path: 'Customer → Account → Invoice → Charge → Complaint → Dispute', available: true })
  }
  if (customer.payments > 0) {
    paths.push({ label: 'Payment Dunning', path: 'Customer → Account → Payment → Invoice → Dunning', available: true })
  }
  if (customer.disruptions > 0 && customer.adjustments > 0) {
    paths.push({ label: 'SLA Credit Chain', path: 'Customer → Account → Disruption → Adjustment → Credit', available: true })
  }
  if (customer.logs > 0) {
    paths.push({ label: 'System Error Trace', path: 'Customer → Account → Log Event → Service → Disruption', available: true })
  }

  if (paths.length === 0) return null

  return (
    <div className="traversal-paths">
      <h4><GitBranch size={14} />KG traversal paths available</h4>
      <div className="traversal-paths-list">
        {paths.map((p) => (
          <div key={p.label} className="traversal-path-item">
            <strong>{p.label}</strong>
            <code>{p.path}</code>
          </div>
        ))}
      </div>
    </div>
  )
}

export default function CustomerExplorer({ onGraphChange, onNavigateToRCA }) {
  const [customers, setCustomers] = useState([])
  const [filtered, setFiltered] = useState([])
  const [search, setSearch] = useState('')
  const [typeFilter, setTypeFilter] = useState('ALL')
  const [selectedId, setSelectedId] = useState(null)
  const [impact, setImpact] = useState(null)
  const [loading, setLoading] = useState(false)
  const [listLoading, setListLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    setListLoading(true)
    api.customers({ limit: 200 })
      .then((data) => {
        const sorted = (data.customers || []).sort((a, b) => b.total_events - a.total_events)
        setCustomers(sorted)
        setFiltered(sorted)
      })
      .catch((e) => setError(e.message))
      .finally(() => setListLoading(false))
  }, [])

  useEffect(() => {
    let result = customers
    if (typeFilter !== 'ALL') result = result.filter((c) => c.customer_type === typeFilter)
    if (search.trim()) {
      const q = search.toLowerCase()
      result = result.filter((c) => c.customer_id.toLowerCase().includes(q) || c.account_id.toLowerCase().includes(q))
    }
    setFiltered(result)
  }, [customers, search, typeFilter])

  const selectCustomer = useCallback(async (customerId) => {
    setSelectedId(customerId)
    setImpact(null)
    setLoading(true)
    setError('')
    try {
      const impactData = await api.customerImpact(customerId)
      setImpact(impactData)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }, [])

  const selectedCustomer = customers.find((c) => c.customer_id === selectedId) || null
  const customerTypes = ['ALL', ...new Set(customers.map((c) => c.customer_type))]

  return (
    <div className="customer-explorer">
      <aside className="customer-list-panel">
        <header>
          <div><span className="eyebrow">Customer 360</span><h2>Customers</h2></div>
          <span className="customer-count">{filtered.length} / {customers.length}</span>
        </header>
        <div className="customer-filters">
          <label className="customer-search">
            <Search size={14} />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search customer ID..." />
          </label>
          <div className="customer-type-filter">
            <ChevronDown size={12} />
            <select value={typeFilter} onChange={(e) => setTypeFilter(e.target.value)}>
              {customerTypes.map((t) => <option key={t} value={t}>{t === 'ALL' ? 'All types' : t.replaceAll('_', ' ')}</option>)}
            </select>
          </div>
        </div>
        <div className="customer-scroll">
          {listLoading ? <div className="graph-empty"><span className="loader" />Loading customers</div> : (
            filtered.map((c) => (
              <CustomerCard key={c.customer_id} customer={c} isActive={selectedId === c.customer_id} onClick={() => selectCustomer(c.customer_id)} />
            ))
          )}
        </div>
      </aside>
      <section className="customer-detail-panel">
        {!selectedId ? (
          <div className="customer-empty">
            <Users size={32} />
            <h3>Select a customer</h3>
            <p>Choose a customer to see their data profile, financial impact, and available KG traversal paths.</p>
          </div>
        ) : loading ? (
          <div className="customer-empty"><span className="loader" />Loading data for {selectedId}</div>
        ) : error ? (
          <div className="customer-empty" style={{ color: '#a44337' }}><AlertTriangle size={24} />{error}</div>
        ) : (
          <div className="customer-detail-content">
            <header className="customer-detail-header">
              <div>
                <span className="eyebrow">Customer Profile</span>
                <h2>{selectedId}</h2>
                {selectedCustomer && <span className="customer-type-pill" style={{ color: TYPE_COLORS[selectedCustomer.customer_type] || '#6d7b78' }}>
                  {selectedCustomer.customer_type.replaceAll('_', ' ')}
                </span>}
              </div>
              {onNavigateToRCA && (
                <button type="button" className="investigate-btn" onClick={() => onNavigateToRCA(selectedId)}>
                  <GitBranch size={14} />Investigate in RCA<ArrowRight size={14} />
                </button>
              )}
            </header>

            <div className="customer-detail-scroll">
              <ImpactSummary impact={impact} />
              <DataProfile customer={selectedCustomer} />

              {onNavigateToRCA && (
                <div className="investigate-callout">
                  <div>
                    <h4>Ready to investigate?</h4>
                    <p>Use the RCA page to ask natural language questions about this customer. The Knowledge Graph will traverse the paths above to find grounded, citable answers.</p>
                  </div>
                  <button type="button" onClick={() => onNavigateToRCA(selectedId)}>
                    <GitBranch size={14} />Open RCA for {selectedId}<ArrowRight size={14} />
                  </button>
                </div>
              )}
            </div>
          </div>
        )}
      </section>
    </div>
  )
}
