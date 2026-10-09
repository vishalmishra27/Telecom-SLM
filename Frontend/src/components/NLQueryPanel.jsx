import { useCallback, useEffect, useImperativeHandle, forwardRef, useMemo, useRef, useState } from 'react'
import { AlertTriangle, ArrowUp, BarChart3, Bot, ChevronDown, ChevronRight, Clock, Database, ExternalLink, FileText, GitBranch, Layers, MessageSquare, RotateCcw, Route, Search, Shield, Sparkles, Square, UserRound, Users, Zap } from 'lucide-react'
import { api } from '../api'
import ReactMarkdown from 'react-markdown'
import rehypeRaw from 'rehype-raw'
import { Document, Packer, Paragraph, Table, TableRow, TableCell, TextRun, HeadingLevel, WidthType, Header, Footer, BorderStyle, PageNumber } from 'docx'
import { saveAs } from 'file-saver'

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
  huggingface: { color: '#FF9D00', label: 'GPT-OSS-120B' },
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

/* =========================================================================
   RCA Pipeline Sub-Components (integrated into RCA page)
   ========================================================================= */

function SeverityBadge({ severity }) {
  const colors = { CRITICAL: '#dc2626', HIGH: '#ea580c', MEDIUM: '#ca8a04', LOW: '#16a34a' }
  const s = (severity || '').toUpperCase()
  return (
    <span className="rca-severity" style={{ background: (colors[s] || '#6d7b78') + '14', color: colors[s] || '#6d7b78', border: `1px solid ${(colors[s] || '#6d7b78')}30` }}>
      {s || 'UNKNOWN'}
    </span>
  )
}

function RCAConfidenceBar({ value }) {
  const pct = Math.round((value || 0) * 100)
  const color = pct >= 75 ? '#16a34a' : pct >= 50 ? '#ca8a04' : '#dc2626'
  return (
    <div className="rca-confidence-bar">
      <div className="rca-confidence-fill" style={{ width: `${pct}%`, background: color }} />
      <span>{pct}%</span>
    </div>
  )
}

function RootCauseCard({ cause, evidence, defaultExpanded = true }) {
  const [expanded, setExpanded] = useState(defaultExpanded)
  const relatedEvidence = (evidence || []).filter((e) => cause.evidence_entity_ids?.includes(e.entity_id))
  return (
    <div className={`rca-cause-card rca-cause-${(cause.severity || '').toLowerCase()}`}>
      <div className="rca-cause-header" onClick={() => setExpanded(!expanded)}>
        <div className="rca-cause-title">
          <SeverityBadge severity={cause.severity} />
          <strong>{cause.cause_category?.replaceAll('_', ' ')}</strong>
          <span className="rca-cause-evidence-count">{cause.evidence_entity_ids?.length || 0} evidence</span>
        </div>
        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </div>
      {expanded && (
        <div className="rca-cause-detail">
          {relatedEvidence.length > 0 && (
            <table className="rca-evidence-table">
              <thead><tr><th>Entity ID</th><th>Type</th><th>Details</th><th>Status</th></tr></thead>
              <tbody>
                {relatedEvidence.slice(0, 10).map((e, i) => (
                  <tr key={i}>
                    <td><code>{e.entity_id}</code></td>
                    <td><span className="rca-evidence-type">{e.entity_type}</span></td>
                    <td>{e.fact}</td>
                    <td>{e.remediation_status || '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {cause.recommended_actions?.length > 0 && (
            <div className="rca-actions">
              <h5>Recommended Actions</h5>
              <ul>{cause.recommended_actions.map((a, i) => <li key={i}>{a}</li>)}</ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// --- KPMG-branded Word document export ---
const KPMG = { blue: '00338D', light: '0091DA', navy: '1B1F3B', purple: '483698', accent: '00A3E0' }
const SEV_DOC_COLORS = { CRITICAL: 'DC2626', HIGH: 'EA580C', MEDIUM: 'CA8A04', LOW: '2563EB' }
const thinBorder = { style: BorderStyle.SINGLE, size: 1, color: 'D0D5DD' }
const tableBorders = { top: thinBorder, bottom: thinBorder, left: thinBorder, right: thinBorder, insideHorizontal: thinBorder, insideVertical: thinBorder }

function cell(text, opts = {}) {
  const runs = []
  if (opts.label) runs.push(new TextRun({ text: opts.label, bold: true, font: 'Calibri', size: 20, color: KPMG.navy }))
  runs.push(new TextRun({ text: text || '', bold: !!opts.bold, font: 'Calibri', size: opts.size || 20, color: opts.color || '333333' }))
  return new TableCell({
    width: opts.width ? { size: opts.width, type: WidthType.PERCENTAGE } : undefined,
    children: [new Paragraph({ spacing: { before: 50, after: 50 }, indent: { left: 80 }, children: runs })],
    shading: opts.shading ? { fill: opts.shading } : undefined,
    columnSpan: opts.colSpan,
  })
}

function hdrCell(text, width) {
  return cell(text, { bold: true, color: 'FFFFFF', shading: KPMG.blue, size: 18, width })
}

function hdrRow(cells) {
  return new TableRow({ tableHeader: true, children: cells.map((c) => typeof c === 'string' ? hdrCell(c) : hdrCell(c[0], c[1])) })
}

function sectionHeading(num, title) {
  return new Paragraph({
    spacing: { before: 300, after: 120 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: KPMG.blue, space: 4 } },
    children: [new TextRun({ text: `${num}. ${title}`, bold: true, font: 'Calibri', size: 26, color: KPMG.blue })],
  })
}

function labelValue(label, value) {
  return new Paragraph({
    spacing: { before: 40, after: 40 },
    children: [
      new TextRun({ text: `${label}:  `, bold: true, font: 'Calibri', size: 21, color: KPMG.navy }),
      new TextRun({ text: value || 'N/A', font: 'Calibri', size: 21, color: '444444' }),
    ],
  })
}

function bulletItem(text, level = 0) {
  return new Paragraph({ bullet: { level }, spacing: { before: 30, after: 30 }, children: [new TextRun({ text, font: 'Calibri', size: 20, color: '444444' })] })
}

function spacer() { return new Paragraph({ spacing: { before: 80, after: 80 }, text: '' }) }

function sevColor(sev) { return SEV_DOC_COLORS[sev] || '666666' }

async function exportRCAReport(result) {
  const rpt = result.report || {}
  const gi = rpt.general_info || {}
  const pd = rpt.problem_description || {}
  const ia = rpt.impact_assessment || {}
  const inv = rpt.investigation_details || {}
  const ca = rpt.corrective_actions || {}
  const children = []

  // ===== TITLE BANNER =====
  children.push(new Paragraph({
    spacing: { after: 0 },
    shading: { fill: KPMG.navy },
    children: [new TextRun({ text: '    ', size: 12 })],
  }))
  children.push(new Paragraph({
    spacing: { before: 0, after: 0 },
    shading: { fill: KPMG.navy },
    children: [
      new TextRun({ text: '   Root Cause Analysis Report', bold: true, font: 'Calibri', size: 36, color: 'FFFFFF' }),
    ],
  }))
  children.push(new Paragraph({
    spacing: { before: 0, after: 0 },
    shading: { fill: KPMG.navy },
    children: [
      new TextRun({ text: `   Customer: ${result.customer_id}  |  ID: ${result.request_id}  |  Confidence: ${Math.round((result.confidence || 0) * 100)}%`, font: 'Calibri', size: 18, color: KPMG.accent }),
    ],
  }))
  children.push(new Paragraph({
    spacing: { before: 0, after: 200 },
    shading: { fill: KPMG.navy },
    children: [new TextRun({ text: '    ', size: 8 })],
  }))

  // Purpose
  children.push(new Paragraph({
    spacing: { before: 100, after: 200 },
    children: [new TextRun({
      text: 'This document provides a detailed assessment of a reported issue, examines the root cause, evaluates impact, and outlines corrective actions to prevent recurrence.',
      italics: true, font: 'Calibri', size: 19, color: '666666',
    })],
  }))

  // ===== 1. GENERAL INFORMATION =====
  children.push(sectionHeading(1, 'General Information'))
  children.push(new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: tableBorders,
    rows: [
      hdrRow([['Issue ID', 25], ['Reported By', 30], ['Date Reported', 25], ['Status', 20]]),
      new TableRow({ children: [
        cell(gi.issue_id || result.request_id, { width: 25 }),
        cell(gi.reported_by || 'RCA Pipeline (Automated)', { width: 30 }),
        cell(gi.date_reported || '', { width: 25 }),
        cell(gi.status || '', { width: 20, bold: true, color: gi.status === 'Escalation Required' ? 'DC2626' : '16A34A' }),
      ] }),
    ],
  }))

  // ===== 2. PROBLEM DESCRIPTION =====
  children.push(sectionHeading(2, 'Problem Description'))
  children.push(labelValue('Summary', pd.summary || `Root cause analysis for customer ${result.customer_id}`))
  children.push(labelValue('System(s) Affected', pd.systems_affected))
  children.push(labelValue('Date/Time of Incident', pd.incident_datetime))
  if (pd.error_messages?.length && pd.error_messages[0] !== 'No system errors captured') {
    children.push(new Paragraph({ spacing: { before: 60 }, children: [new TextRun({ text: 'Error Messages:', bold: true, font: 'Calibri', size: 21, color: KPMG.navy })] }))
    pd.error_messages.forEach((msg) => children.push(bulletItem(msg)))
  }

  // ===== 3. IMPACT ASSESSMENT =====
  children.push(sectionHeading(3, 'Impact Assessment'))
  children.push(new Table({
    width: { size: 100, type: WidthType.PERCENTAGE },
    borders: tableBorders,
    rows: [
      hdrRow([['Metric', 35], ['Value', 65]]),
      new TableRow({ children: [cell('Impact Scope', { width: 35, bold: true, shading: 'F0F4FA' }), cell(ia.impact_scope, { width: 65 })] }),
      new TableRow({ children: [cell('Users Affected', { width: 35, bold: true, shading: 'F0F4FA' }), cell(ia.users_affected || result.customer_id, { width: 65 })] }),
      new TableRow({ children: [cell('Downtime Duration', { width: 35, bold: true, shading: 'F0F4FA' }), cell(ia.downtime_duration, { width: 65 })] }),
      new TableRow({ children: [cell('Confidence', { width: 35, bold: true, shading: 'F0F4FA' }), cell(ia.confidence || `${Math.round((result.confidence || 0) * 100)}%`, { width: 65, bold: true, color: KPMG.blue })] }),
    ],
  }))
  if (ia.descriptions?.length) {
    children.push(spacer())
    ia.descriptions.forEach((d) => children.push(bulletItem(d)))
  }

  // ===== 4. INVESTIGATION DETAILS =====
  children.push(sectionHeading(4, 'Investigation Details'))
  children.push(labelValue('Investigated By', inv.investigated_by || 'Multi-Agent RCA Pipeline'))
  children.push(spacer())
  if (inv.steps?.length) {
    inv.steps.forEach((step, i) => {
      children.push(new Paragraph({
        spacing: { before: 30, after: 30 },
        children: [
          new TextRun({ text: `${i + 1}.  `, bold: true, font: 'Calibri', size: 20, color: KPMG.blue }),
          new TextRun({ text: step, font: 'Calibri', size: 20, color: '444444' }),
        ],
      }))
    })
  }

  // ===== 5. ROOT CAUSE =====
  children.push(sectionHeading(5, 'Root Cause'))
  const rcList = rpt.root_causes || result.root_causes || []
  if (rcList.length > 0) {
    // Root cause summary table
    const rcRows = [hdrRow([['#', 5], ['Category', 25], ['Severity', 15], ['Evidence IDs', 55]])]
    rcList.forEach((rc, i) => {
      const cat = rc.category || rc.cause_category?.replaceAll('_', ' ') || ''
      const sev = rc.severity || ''
      const ids = (rc.evidence_ids || rc.evidence_entity_ids || []).join(', ')
      rcRows.push(new TableRow({ children: [
        cell(`${i + 1}`, { width: 5, bold: true }),
        cell(cat, { width: 25, bold: true }),
        cell(sev, { width: 15, bold: true, color: sevColor(sev) }),
        cell(ids, { width: 55, size: 18 }),
      ] }))
    })
    children.push(new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, borders: tableBorders, rows: rcRows }))
    children.push(spacer())

    // Supporting evidence per root cause
    rcList.forEach((rc) => {
      const cat = rc.category || rc.cause_category?.replaceAll('_', ' ') || ''
      const sev = rc.severity || ''
      const facts = rc.evidence_facts || []
      if (facts.length) {
        children.push(new Paragraph({
          spacing: { before: 100, after: 60 },
          children: [
            new TextRun({ text: `${cat}`, bold: true, font: 'Calibri', size: 21, color: sevColor(sev) }),
            new TextRun({ text: ` — Supporting Evidence`, font: 'Calibri', size: 21, color: '666666' }),
          ],
        }))
        facts.forEach((f) => children.push(bulletItem(f)))
      }
    })
  } else {
    children.push(new Paragraph({
      spacing: { before: 60 },
      shading: { fill: 'FEF2F2' },
      children: [new TextRun({ text: '  No correlating root causes identified. Manual investigation recommended.', italics: true, font: 'Calibri', size: 21, color: 'DC2626' })],
    }))
  }

  // Narrative
  if (result.narrative) {
    children.push(spacer())
    children.push(new Paragraph({
      spacing: { before: 80, after: 80 },
      border: { left: { style: BorderStyle.SINGLE, size: 6, color: KPMG.purple, space: 8 } },
      shading: { fill: 'F5F3FF' },
      children: [new TextRun({ text: '  Analysis Narrative', bold: true, font: 'Calibri', size: 22, color: KPMG.purple })],
    }))
    result.narrative.split('\n').filter(Boolean).forEach((line) => {
      children.push(new Paragraph({
        spacing: { before: 20, after: 20 },
        border: { left: { style: BorderStyle.SINGLE, size: 6, color: KPMG.purple, space: 8 } },
        children: [new TextRun({ text: `  ${line}`, font: 'Calibri', size: 20, color: '444444' })],
      }))
    })
  }

  // ===== 6. CORRECTIVE ACTIONS =====
  children.push(sectionHeading(6, 'Corrective Actions'))
  const shortTerm = ca.short_term || result.recommended_actions || []
  const longTerm = ca.long_term || []

  if (shortTerm.length) {
    children.push(new Paragraph({
      spacing: { before: 60, after: 60 },
      shading: { fill: 'F0FDF4' },
      children: [new TextRun({ text: '  Short-Term Actions', bold: true, font: 'Calibri', size: 21, color: '16A34A' })],
    }))
    shortTerm.forEach((a) => children.push(bulletItem(a)))
  }
  if (longTerm.length) {
    children.push(new Paragraph({
      spacing: { before: 100, after: 60 },
      shading: { fill: 'EFF6FF' },
      children: [new TextRun({ text: '  Long-Term Actions', bold: true, font: 'Calibri', size: 21, color: KPMG.blue })],
    }))
    longTerm.forEach((a) => children.push(bulletItem(a)))
  }
  if (!shortTerm.length && !longTerm.length) {
    children.push(new Paragraph({ spacing: { before: 60 }, children: [new TextRun({ text: 'No corrective actions identified.', italics: true, font: 'Calibri', size: 20, color: '999999' })] }))
  }

  // ===== 7. EVIDENCE DETAIL (if evidence exists) =====
  if (result.evidence?.length > 0) {
    children.push(sectionHeading(7, 'Evidence Detail'))
    const evRows = [hdrRow([['Entity ID', 18], ['Type', 12], ['Details', 42], ['Source', 13], ['Status', 15]])]
    result.evidence.forEach((e, i) => {
      const shading = i % 2 === 0 ? undefined : 'F8FAFC'
      evRows.push(new TableRow({ children: [
        cell(e.entity_id, { width: 18, size: 17, bold: true }),
        cell(e.entity_type, { width: 12, size: 17 }),
        cell(e.fact || '', { width: 42, size: 17 }),
        cell(e.source_system || '', { width: 13, size: 17 }),
        cell(e.remediation_status || '—', { width: 15, size: 17, color: e.remediation_status?.startsWith('RESOLVED') ? '16A34A' : '999999' }),
      ] }))
    })
    children.push(new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, borders: tableBorders, rows: evRows }))
  }

  // ===== BUILD DOCUMENT =====
  const doc = new Document({
    styles: {
      default: {
        document: { run: { font: 'Calibri', size: 22 } },
      },
    },
    sections: [{
      properties: {
        page: {
          margin: { top: 720, bottom: 720, left: 900, right: 900 },
        },
      },
      headers: {
        default: new Header({
          children: [new Paragraph({
            children: [
              new TextRun({ text: 'KPMG  |  EzInsights RCA Report', font: 'Calibri', size: 16, color: KPMG.blue, bold: true }),
              new TextRun({ text: `     ${result.customer_id}`, font: 'Calibri', size: 16, color: '999999' }),
            ],
          })],
        }),
      },
      footers: {
        default: new Footer({
          children: [new Paragraph({
            children: [
              new TextRun({ text: 'Confidential — KPMG EzInsights  |  Page ', font: 'Calibri', size: 14, color: '999999' }),
              new TextRun({ children: [PageNumber.CURRENT], font: 'Calibri', size: 14, color: '999999' }),
            ],
          })],
        }),
      },
      children,
    }],
  })

  const blob = await Packer.toBlob(doc)
  saveAs(blob, `RCA-Report-${result.customer_id}-${result.request_id}.docx`)
}

const SEVERITY_COLORS = { CRITICAL: '#dc2626', HIGH: '#ea580c', MEDIUM: '#ca8a04', LOW: '#2563eb' }

function RCAResultView({ result }) {
  if (!result) return null
  const primarySeverity = result.primary_cause?.severity || result.root_causes?.[0]?.severity || ''
  const narrativeBorderColor = SEVERITY_COLORS[primarySeverity] || '#6b7280'

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
          <RCAConfidenceBar value={result.confidence} />
          {result.response_time_ms > 0 && <span className="rca-time"><Clock size={11} />{result.response_time_ms}ms</span>}
          <button type="button" className="rca-export-btn" onClick={() => exportRCAReport(result)} title="Download RCA report as Word document">
            <ExternalLink size={12} />Export .docx
          </button>
        </div>
      </div>

      {/* 1. Narrative — on top, fully color-coded card by severity */}
      {result.narrative && (
        <div className={`rca-narrative-card rca-narrative-${primarySeverity.toLowerCase() || 'medium'}`}>
          <div className="rca-narrative-card-header">
            <FileText size={14} />
            <span>Root Cause Narrative</span>
            {primarySeverity && <SeverityBadge severity={primarySeverity} />}
          </div>
          <div className="rca-narrative-body">
            <ReactMarkdown rehypePlugins={[rehypeRaw]}>{colorizeSeverity(result.narrative)}</ReactMarkdown>
          </div>
        </div>
      )}

      {/* 2. Root Causes — expanded by default */}
      {result.root_causes?.length > 0 && (
        <div className="rca-causes-section">
          <h4><GitBranch size={13} />Root Causes ({result.root_causes.length})</h4>
          {result.root_causes.map((cause, i) => (
            <RootCauseCard key={i} cause={cause} evidence={result.evidence || []} defaultExpanded />
          ))}
        </div>
      )}

      {/* 3. KG Evidence table */}
      {result.evidence?.length > 0 && (
        <details className="rca-evidence-section" open>
          <summary><Database size={13} />KG Evidence ({result.evidence.length})</summary>
          <table className="rca-evidence-table">
            <thead><tr><th>Entity ID</th><th>Type</th><th>Details</th><th>Source</th><th>Status</th></tr></thead>
            <tbody>
              {result.evidence.map((e, i) => (
                <tr key={i}>
                  <td><code>{e.entity_id}</code></td>
                  <td><span className="rca-evidence-type">{e.entity_type}</span></td>
                  <td>{e.fact}</td>
                  <td>{e.source_system || '—'}</td>
                  <td>{e.remediation_status || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      {/* 4. Graph Traversal Paths — collapsible */}
      {result.evidence?.some((e) => e.traversal_path) && (
        <details className="rca-traversal-section">
          <summary><Route size={13} />Graph Traversal Paths ({result.evidence.filter((e) => e.traversal_path).length})</summary>
          <div className="rca-traversal-list">
            {result.evidence.filter((e) => e.traversal_path).map((e, i) => (
              <div key={i} className="rca-traversal-row">
                <span className="rca-traversal-entity">
                  <code>{e.entity_id}</code>
                  <span className="rca-evidence-type">{e.entity_type}</span>
                </span>
                <span className="rca-traversal-path">{e.traversal_path}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </div>
  )
}

const PIPELINE_AGENTS = [
  { id: 'supervisor', label: 'Supervisor', desc: 'Plans which domain specialists to dispatch based on intent and customer data' },
  { id: 'network', label: 'Network Specialist', desc: 'Searches for network failures, alarms, cell outages, signal issues' },
  { id: 'payment', label: 'Payment Specialist', desc: 'Investigates payment failures, card declines, dunning events' },
  { id: 'service', label: 'Service Specialist', desc: 'Checks for service disruptions, outages, SLA violations' },
  { id: 'performance', label: 'Performance Specialist', desc: 'Analyzes KPI breaches, API latency, PM counter anomalies' },
  { id: 'logs', label: 'Logs Specialist', desc: 'Reviews system logs, error events, infrastructure issues' },
  { id: 'semantic', label: 'Semantic Search', desc: 'Vector similarity search across embedded evidence chunks' },
  { id: 'critic', label: 'Critic', desc: 'Reviews sufficiency of evidence — triggers retry if gaps found' },
  { id: 'synthesis', label: 'Synthesis', desc: 'Correlates evidence into root causes using fixed rules (never model)' },
  { id: 'grounding', label: 'Grounding Validator', desc: 'Strips ungrounded claims, validates entity ID citations' },
]

function PipelineAgentCard({ agent, status, evidenceCount }) {
  const statusColors = { pending: '#d4d9e3', running: '#0091DA', done: '#16a34a', skipped: '#9ba2be' }
  const statusLabels = { pending: 'Pending', running: 'Running...', done: 'Done', skipped: 'Skipped' }
  return (
    <div className={`pipeline-agent-card pipeline-agent-${status}`}>
      <div className="pipeline-agent-status-dot" style={{ background: statusColors[status] || '#d4d9e3' }} />
      <div className="pipeline-agent-info">
        <strong>{agent.label}</strong>
        <span>{agent.desc}</span>
      </div>
      <div className="pipeline-agent-status-right">
        {status === 'done' && evidenceCount != null && (
          <span className="pipeline-agent-evidence-count">{evidenceCount} evidence</span>
        )}
        <span className="pipeline-agent-status-label">{statusLabels[status] || status}</span>
      </div>
    </div>
  )
}

function RCAPipelineTab({ customerId, customers, selectedModel }) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState(null)
  const [agentStatuses, setAgentStatuses] = useState({}) // id → 'pending'|'running'|'done'|'skipped'
  const [evidenceCounts, setEvidenceCounts] = useState({}) // specialist_id → count
  const [subTab, setSubTab] = useState('run') // 'run' | 'history' | 'compare'
  const [history, setHistory] = useState([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [activeHistoryId, setActiveHistoryId] = useState('')
  const [comparing, setComparing] = useState(false)
  const [compareResult, setCompareResult] = useState(null)

  // Clear all pipeline state when customer changes
  useEffect(() => {
    setResult(null)
    setAgentStatuses({})
    setEvidenceCounts({})
    setError('')
    setHistory([])
    setCompareResult(null)
    setActiveHistoryId('')
  }, [customerId])

  const loadHistory = useCallback(async () => {
    setHistoryLoading(true)
    try {
      const data = await api.rcaHistory({ customerId, limit: 50 })
      setHistory(data.history || data || [])
    } catch { setHistory([]) }
    finally { setHistoryLoading(false) }
  }, [customerId])

  useEffect(() => { if (subTab === 'history') loadHistory() }, [subTab, loadHistory])

  // Start agents as pending, then animate running during API call
  const startAgentAnimation = useCallback(() => {
    const order = ['supervisor', 'network', 'payment', 'service', 'performance', 'logs', 'semantic', 'critic', 'synthesis', 'grounding']
    const initial = {}
    order.forEach((id) => { initial[id] = 'pending' })
    setAgentStatuses(initial)
    setEvidenceCounts({})

    // Stagger "running" status during API call
    const timers = order.map((id, i) => {
      return setTimeout(() => {
        setAgentStatuses((prev) => ({ ...prev, [id]: 'running' }))
      }, i * 300)
    })
    return () => timers.forEach(clearTimeout)
  }, [])

  const runRCA = useCallback(async () => {
    if (!customerId) return
    setLoading(true); setError(''); setResult(null)
    const cleanup = startAgentAnimation()

    try {
      const rca = await api.rca({ customerId, description: '' })
      setResult(rca)

      if (rca.agent_statuses) {
        setAgentStatuses(rca.agent_statuses)
      } else {
        setAgentStatuses((prev) => {
          const updated = { ...prev }
          for (const k of Object.keys(updated)) updated[k] = 'done'
          return updated
        })
      }

      if (rca.specialist_evidence_counts) {
        setEvidenceCounts(rca.specialist_evidence_counts)
      }
    } catch (e) {
      setError(e.message)
    } finally {
      cleanup()
      setLoading(false)
    }
  }, [customerId, startAgentAnimation])

  const viewHistoryItem = useCallback(async (requestId) => {
    setActiveHistoryId(requestId); setLoading(true)
    try { setResult(await api.rcaHistoryDetail(requestId)) }
    catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }, [])

  const runComparison = useCallback(async () => {
    if (!customerId) return
    setComparing(true); setCompareResult(null)
    try { setCompareResult(await api.rcaCompareModels({ customerId, description: '' })) }
    catch (e) { setError(e.message) }
    finally { setComparing(false) }
  }, [customerId])

  const selectedCustomer = useMemo(() => customers.find((c) => c.customer_id === customerId) || null, [customerId, customers])
  const hasAgentStatuses = Object.keys(agentStatuses).length > 0

  return (
    <div className="rca-pipeline-inline">
      <div className="rca-sub-tabs">
        <button type="button" className={subTab === 'run' ? 'active' : ''} onClick={() => setSubTab('run')}><Zap size={12} />Run Pipeline</button>
        <button type="button" className={subTab === 'history' ? 'active' : ''} onClick={() => setSubTab('history')}><Clock size={12} />History</button>
        <button type="button" className={subTab === 'compare' ? 'active' : ''} onClick={() => setSubTab('compare')}><Layers size={12} />Compare</button>
      </div>

      {subTab === 'run' && (
        <div className="rca-run-content">
          {!customerId && <p className="rca-empty">Select a customer above to run the RCA pipeline.</p>}
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
          <div className="rca-form-actions">
            <button type="button" className="rca-run-btn" onClick={runRCA} disabled={!customerId || loading}>
              <Zap size={13} />{loading ? 'Running Pipeline...' : 'Run RCA Pipeline'}
            </button>
          </div>
          {error && <div className="rca-error"><AlertTriangle size={12} />{error}</div>}

          {/* Agent status cards — persist after completion */}
          {hasAgentStatuses && (
            <div className="pipeline-agents-section">
              <h4><Users size={13} />Pipeline Agents</h4>
              <div className="pipeline-agents-grid">
                {PIPELINE_AGENTS.map((agent) => (
                  <PipelineAgentCard key={agent.id} agent={agent} status={agentStatuses[agent.id] || 'pending'} evidenceCount={evidenceCounts[agent.id]} />
                ))}
              </div>
            </div>
          )}

          {/* Full RCA Result */}
          <RCAResultView result={result} />
        </div>
      )}

      {subTab === 'history' && (
        <div className="rca-history-inline">
          <div className="rca-history-header">
            <h4>Past RCA Runs</h4>
            <button type="button" onClick={loadHistory} disabled={historyLoading}><RotateCcw size={12} /></button>
          </div>
          {historyLoading && <div className="rca-loading-inline">Loading...</div>}
          {!historyLoading && history.length === 0 && <p className="rca-empty">No RCA history yet.</p>}
          <div className="rca-history-list-inline">
            {history.map((item) => (
              <button key={item.request_id} type="button" className={`rca-history-item ${activeHistoryId === item.request_id ? 'active' : ''}`} onClick={() => viewHistoryItem(item.request_id)}>
                <div className="rca-history-top"><code>{item.customer_id}</code><span className="rca-history-causes">{item.root_causes?.length || '?'} causes</span></div>
                <div className="rca-history-bottom"><span><Clock size={10} />{item.response_time_ms || 0}ms</span><span>{Math.round((item.confidence || 0) * 100)}%</span></div>
              </button>
            ))}
          </div>
          {result && <RCAResultView result={result} />}
        </div>
      )}

      {subTab === 'compare' && (
        <div className="rca-compare-inline">
          {!customerId && <p className="rca-empty">Select a customer above to compare models.</p>}
          {customerId && (
            <>
              <button type="button" className="rca-run-btn" onClick={runComparison} disabled={!customerId || comparing}>
                <Layers size={13} />{comparing ? 'Comparing...' : 'Compare Models Side-by-Side'}
              </button>
              {error && <div className="rca-error"><AlertTriangle size={12} />{error}</div>}
              {comparing && <div className="rca-loading-bar"><span /></div>}
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
            </>
          )}
        </div>
      )}
    </div>
  )
}

const NLQueryPanel = forwardRef(function NLQueryPanel({ onGraphChange, onCustomerGraphChange, onQuickEvaluate, preselectedCustomerId }, ref) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [customerId, setCustomerId] = useState('')
  const [selectedModel, setSelectedModel] = useState('huggingface')
  const [availableModels, setAvailableModels] = useState([])
  const [customers, setCustomers] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [showEvidence, setShowEvidence] = useState(null)
  const [showTraversal, setShowTraversal] = useState(null)
  const [rcaMode, setRcaMode] = useState('chat') // 'chat' | 'pipeline'
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
          <div className="nl-mode-toggle">
            <button type="button" className={rcaMode === 'chat' ? 'active' : ''} onClick={() => setRcaMode('chat')}><MessageSquare size={12} />Chat</button>
            <button type="button" className={rcaMode === 'pipeline' ? 'active' : ''} onClick={() => setRcaMode('pipeline')}><Zap size={12} />Pipeline</button>
          </div>
          <div className="nl-model-badge" style={{ color: activeModelInfo.color, borderColor: activeModelInfo.color + '40' }}>
            <Sparkles size={13} />{activeModelInfo.label}
          </div>
          {messages.length > 0 && rcaMode === 'chat' && (
            <button type="button" className="icon-button" title="Clear chat" onClick={clearChat}><RotateCcw size={15} /></button>
          )}
        </div>
      </header>
      <div className="nl-customer-bar">
        <button
          type="button"
          className={`nl-all-customers-btn ${!customerId ? 'active' : ''}`}
          onClick={() => { if (customerId) { setCustomerId(''); setSelectedModel('huggingface'); clearChat(); if (onCustomerGraphChange) onCustomerGraphChange(null) } }}
        >
          <Database size={13} />All Customers
        </button>
        <label>
          <Search size={13} />
          <select value={customerId} onChange={(e) => { setCustomerId(e.target.value); clearChat(); if (e.target.value) { setSelectedModel('huggingface'); loadCustomerGraph(e.target.value) } else { setSelectedModel('huggingface'); if (onCustomerGraphChange) onCustomerGraphChange(null) } }}>
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
          <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)}>
            <option value="">Auto (fallback chain)</option>
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.display_name} ({m.model_id})
              </option>
            ))}
          </select>
        </label>
      </div>
      {/* Pipeline mode */}
      {rcaMode === 'pipeline' && (
        <RCAPipelineTab customerId={customerId} customers={customers} selectedModel={selectedModel} />
      )}

      {/* Chat mode */}
      <div className="nl-conversation" ref={conversationRef} style={{ display: rcaMode === 'chat' ? undefined : 'none' }}>
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
      <form className="composer" onSubmit={(e) => { e.preventDefault(); submit() }} style={{ display: rcaMode === 'chat' ? undefined : 'none' }}>
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
