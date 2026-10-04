import { useState } from 'react'
import ReactMarkdown from 'react-markdown'
import { Box, Check, Copy, GitBranch, Route, ShieldCheck } from 'lucide-react'

const tabs = [
  { id: 'evidence', label: 'Evidence', icon: ShieldCheck },
  { id: 'entities', label: 'Entities', icon: Box },
  { id: 'traversal', label: 'Traversal', icon: Route },
]

function InlineMarkdown({ children }) {
  return <ReactMarkdown components={{ p: ({ children: content }) => <>{content}</> }}>{String(children || '')}</ReactMarkdown>
}

function parsePipeTable(lines, startIndex) {
  const rows = []
  let index = startIndex
  while (index < lines.length && /^\s*\|.*\|\s*$/.test(lines[index])) {
    const cells = lines[index].trim().slice(1, -1).split('|').map((cell) => cell.trim())
    rows.push(cells)
    index += 1
  }
  if (rows.length < 2 || !rows[1].every((cell) => /^:?-{3,}:?$/.test(cell))) {
    return null
  }
  return { headers: rows[0], body: rows.slice(2), nextIndex: index }
}

function StructuredAnswer({ text }) {
  const lines = String(text || '').split(/\r?\n/)
  const blocks = []
  let section = null

  const pushSection = () => {
    if (section) blocks.push(section)
    section = null
  }

  for (let index = 0; index < lines.length; index += 1) {
    const raw = lines[index]
    const line = raw.trim()
    if (!line) continue

    const table = parsePipeTable(lines, index)
    if (table) {
      if (!section) section = { title: '', items: [] }
      section.items.push({ type: 'table', ...table })
      index = table.nextIndex - 1
      continue
    }

    const summary = line.match(/^\*?\*?Summary:\*?\*?\s*(.+)$/i)
    if (summary) {
      pushSection()
      blocks.push({ type: 'summary', text: summary[1] })
      continue
    }

    const heading = line.match(/^\*\*(.+?)\*\*$/) || line.match(/^#{1,3}\s+(.+)$/)
    if (heading) {
      pushSection()
      section = { title: heading[1], items: [] }
      continue
    }

    if (!section) section = { title: '', items: [] }
    const bullet = line.match(/^[-*]\s+(.+)$/)
    const numbered = line.match(/^\d+\.\s+(.+)$/)
    section.items.push({ type: bullet ? 'bullet' : numbered ? 'numbered' : 'text', text: (bullet || numbered)?.[1] || line })
  }
  pushSection()

  return (
    <div className="structured-answer">
      {blocks.map((block, blockIndex) => {
        if (block.type === 'summary') {
          return <div className="answer-summary" key={`summary-${blockIndex}`}><strong>Summary</strong><InlineMarkdown>{block.text}</InlineMarkdown></div>
        }
        const bulletItems = block.items.filter((item) => item.type === 'bullet' || item.type === 'numbered')
        const textItems = block.items.filter((item) => item.type === 'text')
        const tableItems = block.items.filter((item) => item.type === 'table')
        return (
          <section className="answer-section" key={`${block.title}-${blockIndex}`}>
            {block.title && <div className="answer-section-title">{block.title}</div>}
            <div className="answer-section-body">
              {!!tableItems.length && tableItems.map((table, tableIndex) => (
                <table key={`table-${tableIndex}`}>
                  <thead><tr>{table.headers.map((cell, cellIndex) => <th key={cellIndex}><InlineMarkdown>{cell}</InlineMarkdown></th>)}</tr></thead>
                  <tbody>{table.body.map((row, rowIndex) => <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}><InlineMarkdown>{cell}</InlineMarkdown></td>)}</tr>)}</tbody>
                </table>
              ))}
              {!!bulletItems.length && (
                <div className="answer-mini-table">
                  {bulletItems.map((item, itemIndex) => {
                    const split = item.text.match(/^\*\*(.+?)\*\*:\s*(.+)$/) || item.text.match(/^`?([^`:]+)`?:\s*(.+)$/)
                    return (
                      <div className="answer-row" key={`${item.text}-${itemIndex}`}>
                        <div className="answer-cell">{split ? <InlineMarkdown>{split[1]}</InlineMarkdown> : <span className="answer-badge">{item.type === 'numbered' ? itemIndex + 1 : 'Item'}</span>}</div>
                        <div className="answer-cell"><InlineMarkdown>{split ? split[2] : item.text}</InlineMarkdown></div>
                      </div>
                    )
                  })}
                </div>
              )}
              {!!textItems.length && <div className="answer-badges">{textItems.map((item, itemIndex) => <span className="answer-badge" key={`${item.text}-${itemIndex}`}><InlineMarkdown>{item.text}</InlineMarkdown></span>)}</div>}
            </div>
          </section>
        )
      })}
    </div>
  )
}

function responseQualityNote(response) {
  const confidence = typeof response.confidence === 'number' ? `${Math.round(response.confidence * 100)}%` : 'n/a'
  const evidenceCount = response.evidence?.length || 0
  const traversalCount = response.traversal?.length || 0
  const entityCount = response.entities?.length || 0
  return `Confidence ${confidence} from ${evidenceCount} evidence item${evidenceCount === 1 ? '' : 's'}, ${traversalCount} traversal step${traversalCount === 1 ? '' : 's'}, ${entityCount} entit${entityCount === 1 ? 'y' : 'ies'}`
}

export default function AnswerCard({ response, onEntitySelect }) {
  const [activeTab, setActiveTab] = useState('evidence')
  const [copied, setCopied] = useState(false)
  const tokenUsage = response.token_usage
  const sourceLabel = response.generated_by === 'openai'
    ? 'Generated by OpenAI over KG evidence'
    : response.generated_by === 'ollama'
      ? 'Generated by Ollama over KG evidence'
      : 'Generated from KG evidence'

  const copyAnswer = async () => {
    const text = String(response.answer || '').trim()
    if (!text) return
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
      } else {
        const textarea = document.createElement('textarea')
        textarea.value = text
        textarea.setAttribute('readonly', '')
        textarea.style.position = 'fixed'
        textarea.style.opacity = '0'
        document.body.appendChild(textarea)
        textarea.select()
        document.execCommand('copy')
        document.body.removeChild(textarea)
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1200)
    } catch {
      setCopied(false)
    }
  }

  return (
    <article className="answer-card">
      <div className="answer-meta">
        <span className="intent-pill"><GitBranch size={13} />{response.intent.replaceAll('_', ' ')}</span>
        <div className="answer-meta-actions">
          <span className="confidence-note">{responseQualityNote(response)}</span>
          <button type="button" className="copy-answer-button" onClick={copyAnswer} title={copied ? 'Answer copied' : 'Copy answer'} aria-label={copied ? 'Answer copied' : 'Copy answer'}>
            {copied ? <Check size={13} /> : <Copy size={13} />}
          </button>
        </div>
      </div>
      <div className="answer-copy"><StructuredAnswer text={response.answer} /></div>
      <div className="answer-tabs" role="tablist">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button key={id} type="button" className={activeTab === id ? 'active' : ''} onClick={() => setActiveTab(id)} role="tab">
            <Icon size={14} />{label}
          </button>
        ))}
      </div>
      <div className="answer-detail">
        {activeTab === 'evidence' && response.evidence.map((item, index) => (
          <div className="evidence-row" key={`${item.label}-${item.value}-${index}`}>
            <div><strong>{item.label}</strong><span>{item.source}</span></div>
            <p>{item.value}</p>
          </div>
        ))}
        {activeTab === 'entities' && (
          <div className="entity-list">
            {response.entities.map((entity) => (
              <button type="button" key={entity.id} onClick={() => onEntitySelect(entity)}>
                <span className="entity-dot" />
                <span><strong>{entity.name}</strong><small>{entity.ontology_class}</small></span>
              </button>
            ))}
          </div>
        )}
        {activeTab === 'traversal' && (
          <ol className="traversal-list">
            {response.traversal.map((step) => (
              <li key={step.order}>
                <span>{step.order}</span>
                <div className="traversal-step">
                  <div className="traversal-chain">
                    <strong>{step.from_entity}<em>{step.from_class}</em></strong>
                    <small>{step.relationship}</small>
                    <strong>{step.to_entity}<em>{step.to_class}</em></strong>
                  </div>
                  {step.explanation && <p>{step.explanation}</p>}
                </div>
              </li>
            ))}
          </ol>
        )}
      </div>
      <footer className="answer-footer">
        <span>{sourceLabel}</span>
        <span>
          {tokenUsage?.total_tokens
            ? `Token usage: ${tokenUsage.total_tokens} total (${tokenUsage.input_tokens} input, ${tokenUsage.output_tokens} output)`
            : 'Token usage: not applicable'}
        </span>
      </footer>
    </article>
  )
}
