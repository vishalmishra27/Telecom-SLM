import { useEffect, useMemo, useState } from 'react'
import { Activity, ArrowRight, BarChart3, Database, GitBranch, Network, Search, Shield, Users, Zap } from 'lucide-react'
import { api } from '../api'

function StatCard({ icon: Icon, value, label, color }) {
  return (
    <div className="landing-stat">
      <Icon size={20} style={{ color }} />
      <strong>{value}</strong>
      <span>{label}</span>
    </div>
  )
}

export default function LandingPage({ onNavigate, health }) {
  const [stats, setStats] = useState(null)

  useEffect(() => {
    api.verify().then(setStats).catch(() => {})
  }, [])

  const neo4jLive = health?.neo4j === 'connected'

  const totalNodes = useMemo(() => stats?.node_counts ? Object.values(stats.node_counts).reduce((a, b) => a + b, 0) : 0, [stats])
  const totalRels = useMemo(() => stats?.relationship_counts ? Object.values(stats.relationship_counts).reduce((a, b) => a + b, 0) : 0, [stats])

  return (
    <div className="landing-page">
      <section className="landing-hero">
        <div className="landing-hero-content">
          <h1>Telecom RCA Intelligence</h1>
          <p className="landing-subtitle">
            Knowledge Graph + Local LLM powered Root Cause Analysis for telecom operations.
            Every answer is grounded in graph evidence — traceable, auditable, and citable.
          </p>
          <div className="landing-status-row">
            <span className={`landing-status ${neo4jLive ? 'live' : ''}`}>
              <Database size={14} />{neo4jLive ? 'Neo4j Connected' : 'Neo4j Offline'}
            </span>
            {stats && (
              <>
                <span className="landing-status live"><Network size={14} />{totalNodes.toLocaleString()} nodes</span>
                <span className="landing-status live"><GitBranch size={14} />{totalRels.toLocaleString()} relationships</span>
              </>
            )}
          </div>
        </div>
      </section>

      <section className="landing-value">
        <h2>Why Knowledge Graph?</h2>
        <div className="landing-value-grid">
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#00338D18', color: '#00338D' }}><GitBranch size={22} /></div>
            <h3>KG Traversal</h3>
            <p>Every answer follows a graph path: Customer → Account → Alarm → Service → Root Cause. The graph evidence grounds every claim.</p>
          </div>
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#0091DA18', color: '#0091DA' }}><Shield size={22} /></div>
            <h3>Zero hallucination</h3>
            <p>Unlike LLMs that can invent incident IDs, timestamps, or remediation steps — the KG only returns facts that exist in the data.</p>
          </div>
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#e05d4f18', color: '#e05d4f' }}><Search size={22} /></div>
            <h3>Full traceability</h3>
            <p>Every claim cites specific node IDs and workbook rows. Auditors can verify any answer back to the source data.</p>
          </div>
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#f0a13a18', color: '#f0a13a' }}><Zap size={22} /></div>
            <h3>Local LLM + KG</h3>
            <p>A telecom-specific local LLM (OTel-LLM) narrates KG evidence into actionable RCA — no data leaves your environment, zero per-query API cost.</p>
          </div>
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#8b67bd18', color: '#8b67bd' }}><Activity size={22} /></div>
            <h3>Telecom-native intelligence</h3>
            <p>Purpose-built for telecom with OTel-LLM — a domain-specific model that understands alarms, KPIs, SLA breaches, and network fault patterns.</p>
          </div>
          <div className="landing-value-card">
            <div className="landing-value-icon" style={{ background: '#b55a8a18', color: '#b55a8a' }}><BarChart3 size={22} /></div>
            <h3>Benchmark against LLMs</h3>
            <p>Compare KG-grounded answers against Claude and ChatGPT operating on raw CSV data only — measure grounding, entity accuracy, and hallucination.</p>
          </div>
        </div>
      </section>

      <section className="landing-flow">
        <h2>How it works</h2>
        <div className="landing-flow-steps">
          <div className="landing-flow-step">
            <div className="landing-flow-number">1</div>
            <h3>Explore customers</h3>
            <p>Browse the customer list. See each customer's data profile — alarms, billing, complaints, payments — and impact summary across domains.</p>
            <button type="button" onClick={() => onNavigate('customers')}><Users size={14} />Customer 360<ArrowRight size={14} /></button>
          </div>
          <div className="landing-flow-arrow"><ArrowRight size={20} /></div>
          <div className="landing-flow-step">
            <div className="landing-flow-number">2</div>
            <h3>Run RCA queries</h3>
            <p>Select a customer and ask natural language questions. The KG traverses related nodes and a local telecom LLM narrates the root cause with cited evidence.</p>
            <button type="button" onClick={() => onNavigate('nlquery')}><Database size={14} />RCA Query<ArrowRight size={14} /></button>
          </div>
          <div className="landing-flow-arrow"><ArrowRight size={20} /></div>
          <div className="landing-flow-step">
            <div className="landing-flow-number">3</div>
            <h3>Benchmark & validate</h3>
            <p>Compare KG-grounded answers against Claude and ChatGPT (given only raw CSV data). Evaluate grounding, entity ID accuracy, and hallucination rates.</p>
            <button type="button" onClick={() => onNavigate('evaluation')}><BarChart3 size={14} />Benchmark<ArrowRight size={14} /></button>
          </div>
        </div>
      </section>

      {stats && (
        <section className="landing-stats-section">
          <h2>Knowledge Graph at a glance</h2>
          <div className="landing-stats-grid">
            <StatCard icon={Network} value={totalNodes.toLocaleString()} label="Total nodes" color="#00338D" />
            <StatCard icon={GitBranch} value={totalRels.toLocaleString()} label="Relationships" color="#0091DA" />
            {stats.total_customers > 0 && <StatCard icon={Users} value={stats.total_customers} label="Customers" color="#8b67bd" />}
          </div>
        </section>
      )}

      <section className="landing-top-classes">
        <h2>Conceptual top-level classes</h2>
        <div className="landing-top-class-grid">
          <div className="landing-top-class-card">
            <div className="landing-top-class-icon" style={{ background: '#00338D18', color: '#00338D' }}><Users size={22} /></div>
            <h3>Customer</h3>
            <p>Party / account / subscriber / customer-impact layer.</p>
            <div className="landing-top-class-rca"><strong>RCA Role:</strong> Impact, SLA exposure, customer context</div>
          </div>
          <div className="landing-top-class-card">
            <div className="landing-top-class-icon" style={{ background: '#0091DA18', color: '#0091DA' }}><Network size={22} /></div>
            <h3>Service</h3>
            <p>Delivered telecom capability and service assurance layer.</p>
            <div className="landing-top-class-rca"><strong>RCA Role:</strong> Service degradation, outage, restoration</div>
          </div>
          <div className="landing-top-class-card">
            <div className="landing-top-class-icon" style={{ background: '#e05d4f18', color: '#e05d4f' }}><GitBranch size={22} /></div>
            <h3>Process</h3>
            <p>Operational, assurance, remediation, prediction and governance layer.</p>
            <div className="landing-top-class-rca"><strong>RCA Role:</strong> RCA chain and remediation workflow</div>
          </div>
          <div className="landing-top-class-card">
            <div className="landing-top-class-icon" style={{ background: '#8b67bd18', color: '#8b67bd' }}><Shield size={22} /></div>
            <h3>People</h3>
            <p>Ownership, support, vendor, custodian and escalation layer.</p>
            <div className="landing-top-class-rca"><strong>RCA Role:</strong> Assignment, escalation, remediation owner</div>
          </div>
          <div className="landing-top-class-card">
            <div className="landing-top-class-icon" style={{ background: '#f0a13a18', color: '#f0a13a' }}><Database size={22} /></div>
            <h3>Product</h3>
            <p>Commercial product plus technical resource / device / infrastructure layer.</p>
            <div className="landing-top-class-rca"><strong>RCA Role:</strong> Fault location and product / resource impact</div>
          </div>
        </div>
      </section>
    </div>
  )
}
