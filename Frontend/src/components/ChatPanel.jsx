import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import { ArrowUp, Bot, Clock3, Database, MessageSquare, RotateCcw, Square, UserRound, BarChart3 } from 'lucide-react'
import { api } from '../api'
import AnswerCard from './AnswerCard'

function normalizeQuestion(question) {
  return String(question || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim()
}

const ChatPanel = forwardRef(function ChatPanel({ onGraphChange, onEntitySelect, onQuickEvaluate }, ref) {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [conversationId, setConversationId] = useState(null)
  const [history, setHistory] = useState([])
  const [activeHistoryId, setActiveHistoryId] = useState('')
  const [historyLoading, setHistoryLoading] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const endRef = useRef(null)
  const conversationRef = useRef(null)
  const abortRef = useRef(null)

  useEffect(() => {
    const container = conversationRef.current
    if (container) container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  const loadHistory = async () => {
    setHistoryLoading(true)
    try {
      const payload = await api.conversations({ limit: 60 })
      const items = payload.items || []
      setHistory(items)
      return items
    } catch {
      setHistory([])
      return []
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    loadHistory()
  }, [])

  const cancelRequest = useCallback(() => {
    if (abortRef.current) { abortRef.current.abort(); abortRef.current = null }
    setLoading(false)
  }, [])

  const submit = async (question = input) => {
    const clean = question.trim()
    if (!clean || loading) return
    setInput('')
    setError('')
    setMessages((current) => [...current, { role: 'user', text: clean }])
    setLoading(true)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      const response = await fetch(`${import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'}/api/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: clean, conversation_id: conversationId }),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error('The RCA service could not answer this question.')
      const payload = await response.json()
      setConversationId(payload.conversation_id)
      setMessages((current) => [...current, { role: 'assistant', response: payload }])
      onGraphChange(payload.graph)
      const items = await loadHistory()
      const matched = items.find((item) => normalizeQuestion(item.question) === normalizeQuestion(clean))
      setActiveHistoryId(matched?.id || '')
    } catch (requestError) {
      if (requestError.name === 'AbortError') { setError('Request cancelled.'); return }
      setError(requestError.message)
    } finally {
      abortRef.current = null
      setLoading(false)
    }
  }

  const reset = useCallback(() => {
    setMessages([])
    setConversationId(null)
    setActiveHistoryId('')
    setError('')
    if (onGraphChange) onGraphChange({ nodes: [], relationships: [], source: 'demo' })
  }, [onGraphChange])

  useImperativeHandle(ref, () => ({ clearChat: reset }), [reset])

  const openHistoryItem = async (item) => {
    if (!item?.id || loading) return
    setError('')
    setActiveHistoryId(item.id)
    try {
      const payload = await api.conversationDetail(item.id)
      setConversationId(payload.conversation_id)
      setMessages([
        { role: 'user', text: item.question },
        { role: 'assistant', response: payload },
      ])
      onGraphChange(payload.graph)
    } catch (requestError) {
      setError(requestError.message)
    }
  }

  return (
    <section className="chat-panel" aria-label="RCA conversational analysis">
      <header className="panel-header chat-header">
        <div><span className="eyebrow">Conversational RCA</span><h2>Ask the network</h2></div>
        <button type="button" className="icon-button" title="New investigation" onClick={reset}><RotateCcw size={17} /></button>
      </header>
      <aside className="conversation-history" aria-label="Conversation history">
        <header>
          <span><Clock3 size={13} />History</span>
          <button type="button" onClick={loadHistory} disabled={historyLoading}>{historyLoading ? 'Loading' : 'Refresh'}</button>
        </header>
        <div>
          {history.length ? history.map((item) => (
            <button type="button" key={item.id} className={activeHistoryId === item.id ? 'active' : ''} onClick={() => openHistoryItem(item)} title={item.question} aria-current={activeHistoryId === item.id ? 'true' : undefined}>
              <strong>{item.question}</strong>
              <small>{item.intent?.replaceAll('_', ' ') || 'conversation'} | {item.created_at || 'saved'}</small>
            </button>
          )) : <p>No saved questions yet.</p>}
        </div>
      </aside>
      <div className="conversation" ref={conversationRef}>
        {messages.length === 0 && (
          <div className="chat-welcome">
            <div className="assistant-mark"><Bot size={20} /></div>
            <h3>Investigate from signal to resolution</h3>
            <p>Ask about a service symptom, incident, alarm, KPI breach, impact, or remediation path.</p>
            <div className="chat-suggestions">
              {[
                'What kinds of incidents have occurred?',
                'Show all incidents related to CELL_OUTAGE',
                'What is the impact on affected customers?',
                'What alarms and KPI evidence prove the outage?',
                'How was the service latency issue resolved?',
                'What remediation actions are needed?',
                'Show incidents with severity SEV1',
                'Which services were affected by the outage?',
              ].map((q) => (
                <button key={q} type="button" onClick={() => submit(q)}><MessageSquare size={11} />{q}</button>
              ))}
            </div>
          </div>
        )}
        {messages.map((message, index) => (
          <div className={`message ${message.role}`} key={`${message.role}-${index}`}>
            <div className="avatar">{message.role === 'user' ? <UserRound size={16} /> : <Bot size={16} />}</div>
            <div className="message-body">
              {message.role === 'user' ? (
                <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
                  <p style={{ margin: 0, flex: 1 }}>{message.text}</p>
                  {onQuickEvaluate && (
                    <button
                      type="button"
                      onClick={() => {
                        // Find the next assistant response (KG answer)
                        const messageIndex = messages.findIndex(m => m.role === 'user' && m.text === message.text)
                        const nextAssistantMessage = messages[messageIndex + 1]
                        const kgAnswer = nextAssistantMessage?.response?.answer || ''
                        onQuickEvaluate(message.text, kgAnswer)
                      }}
                      title="Evaluate this question in Benchmark tab"
                      style={{
                        display: 'inline-flex',
                        alignItems: 'center',
                        gap: '6px',
                        padding: '4px 10px',
                        fontSize: '11px',
                        fontWeight: '700',
                        background: 'rgba(0, 51, 141, 0.1)',
                        border: '1px solid #00338D',
                        color: '#00338D',
                        borderRadius: '4px',
                        cursor: 'pointer',
                        whiteSpace: 'nowrap',
                        flex: '0 0 auto',
                      }}
                      onMouseEnter={(e) => { e.target.style.background = '#00338D'; e.target.style.color = '#fff'; }}
                      onMouseLeave={(e) => { e.target.style.background = 'rgba(0, 51, 141, 0.1)'; e.target.style.color = '#00338D'; }}
                    >
                      <BarChart3 size={12} />Evaluate
                    </button>
                  )}
                </div>
              ) : (
                <AnswerCard response={message.response} onEntitySelect={onEntitySelect} />
              )}
            </div>
          </div>
        ))}
        {loading && <div className="thinking"><span /><span /><span />Traversing ontology and evidence</div>}
        {error && <div className="chat-error">{error}</div>}
        <div ref={endRef} />
      </div>
      <form className="composer" onSubmit={(event) => { event.preventDefault(); submit() }}>
        <div className="composer-context"><Database size={13} />Ontology-grounded search</div>
        <textarea value={input} onChange={(event) => setInput(event.target.value)} onKeyDown={(event) => {
          if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submit() }
        }} placeholder="Ask a follow-up question..." rows="2" />
        {loading ? (
          <button type="button" className="composer-stop" onClick={cancelRequest} title="Stop generating"><Square size={14} /></button>
        ) : (
          <button type="submit" disabled={!input.trim()} title="Send question"><ArrowUp size={18} /></button>
        )}
      </form>
    </section>
  )
})

export default ChatPanel
