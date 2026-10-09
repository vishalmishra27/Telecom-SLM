const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function request(path, options = {}) {
  const isForm = options.body instanceof FormData
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { ...(isForm ? {} : { 'Content-Type': 'application/json' }), ...(options.headers || {}) },
    ...options,
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    throw new Error(payload.detail || payload.message || `Request failed (${response.status})`)
  }
  return response.json()
}

export const api = {
  // --- Existing endpoints (legacy) ---
  health: () => request('/api/health'),
  scenarios: () => request('/api/scenarios'),
  rootCauses: () => request('/api/root-causes'),
  graph: ({ scenarioId = '', rootCause = '', search = '', limit = 260 } = {}) => {
    const params = new URLSearchParams()
    if (scenarioId) params.set('scenario_id', scenarioId)
    if (rootCause) params.set('root_cause', rootCause)
    if (search) params.set('search', search)
    if (limit) params.set('limit', String(limit))
    const suffix = params.toString() ? `?${params}` : ''
    return request(`/api/graph${suffix}`)
  },
  graphBase: ({ limit = 40 } = {}) => request(`/api/graph/base?limit=${limit}`),
  graphExpand: ({ nodeId, depth = 1, limit = 80 }) => {
    const params = new URLSearchParams({ node_id: nodeId, depth: String(depth), limit: String(limit) })
    return request(`/api/graph/expand?${params}`)
  },
  chat: (message, conversationId) => request('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ message, conversation_id: conversationId || null }),
  }),
  conversations: ({ limit = 50 } = {}) => request(`/api/conversations?limit=${limit}`),
  conversationDetail: (id) => request(`/api/conversations/${encodeURIComponent(id)}`),
  getLLMAnswers: ({ question, kgAnswer, contextMode }) => request('/api/llm/answers', {
    method: 'POST',
    body: JSON.stringify({ question, kg_answer: kgAnswer || null, context_mode: contextMode || 'kg_context' }),
  }),
  evaluateManual: ({ question, kgAnswer, claudeAnswer, chatgptAnswer }) => request('/api/evaluate/manual', {
    method: 'POST',
    body: JSON.stringify({
      question,
      kg_answer: kgAnswer,
      claude_answer: claudeAnswer,
      chatgpt_answer: chatgptAnswer,
    }),
  }),
  ingestExcel: ({ folderName, files }) => {
    const form = new FormData()
    form.append('folder_name', folderName)
    Array.from(files || []).forEach((file) => form.append('files', file))
    return request('/api/ingest/excel', { method: 'POST', body: form })
  },

  // --- New v1 endpoints (Ingestion Pipeline Spec Section 12) ---

  // Schema & Ingestion
  schemaSetup: () => request('/api/v1/schema/setup', { method: 'POST' }),
  ingestDomain: (domain, file) => {
    const form = new FormData()
    form.append('file', file)
    return request(`/api/v1/ingest/${encodeURIComponent(domain)}`, { method: 'POST', body: form })
  },
  ingestBatch: (directory, { setupSchema = true, createCrossLinks = true } = {}) => {
    const form = new URLSearchParams()
    form.set('directory', directory)
    form.set('setup_schema_first', String(setupSchema))
    form.set('create_cross_links', String(createCrossLinks))
    return request('/api/v1/ingest/batch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form.toString(),
    })
  },
  ingestCrossLinks: () => request('/api/v1/ingest/cross-links', { method: 'POST' }),
  clearGraph: () => request('/api/v1/graph/clear', { method: 'DELETE' }),
  verify: () => request('/api/v1/verify'),

  // Synthetic data
  generateAndIngest: ({ numCustomers = 10, eventsPerCustomer = 5 } = {}) =>
    request(`/api/v1/synthetic/generate-and-ingest?num_customers=${numCustomers}&events_per_customer=${eventsPerCustomer}`, { method: 'POST' }),
  downloadSyntheticZip: ({ numCustomers = 10, eventsPerCustomer = 5 } = {}) =>
    `${API_BASE}/api/v1/synthetic/generate?num_customers=${numCustomers}&events_per_customer=${eventsPerCustomer}`,

  // Customer queries
  customers: ({ limit = 100 } = {}) => request(`/api/v1/customers?limit=${limit}`),
  customerRCA: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/rca`),
  customerImpact: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/impact`),
  customerDunning: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/dunning`),
  customerSLACredits: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/sla-credits`),
  customerSystemErrors: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/system-errors`),
  customerKPIBreaches: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/kpi-breaches`),
  graphOverview: ({ limit = 30 } = {}) => request(`/api/v1/graph/overview?limit=${limit}`),
  customerGraph: (id) => request(`/api/v1/customers/${encodeURIComponent(id)}/graph`),

  // Site & cross-domain
  siteImpact: (id) => request(`/api/v1/sites/${encodeURIComponent(id)}/impact`),
  unresolved: () => request('/api/v1/unresolved'),

  // Natural language query (grounded narration — Spec Section 12.2)
  availableModels: () => request('/api/v1/models'),
  nlQuery: ({ question, customerId, model, conversationHistory, signal }) => request('/api/v1/query', {
    method: 'POST',
    body: JSON.stringify({
      question,
      customer_id: customerId || null,
      model: model || null,
      conversation_history: conversationHistory || [],
    }),
    signal,
  }),

  // --- RCA Pipeline (Retrieval Pipeline Spec Section 8, 14.2) ---
  rca: ({ customerId, description, billingPeriod, debug = false }) => request('/api/v1/rca', {
    method: 'POST',
    body: JSON.stringify({
      customer_id: customerId,
      billing_issue_description: description || '',
      billing_period: billingPeriod || null,
      debug,
    }),
  }),
  rcaHistory: ({ mode, customerId, limit = 50 } = {}) => {
    const params = new URLSearchParams()
    if (mode) params.set('mode', mode)
    if (customerId) params.set('customer_id', customerId)
    params.set('limit', String(limit))
    return request(`/api/v1/rca/history?${params}`)
  },
  rcaHistoryDetail: (requestId) => request(`/api/v1/rca/history/${encodeURIComponent(requestId)}`),
  rcaEvidence: (requestId, entityId) => request(`/api/v1/rca/${encodeURIComponent(requestId)}/evidence/${encodeURIComponent(entityId)}`),
  rcaCompareModels: ({ customerId, description }) => request('/api/v1/rca/compare-models', {
    method: 'POST',
    body: JSON.stringify({
      customer_id: customerId,
      billing_issue_description: description || '',
    }),
  }),

  // --- Assistant Chat (Retrieval Pipeline Spec Section 12.4) ---
  assistantChat: ({ question, customerId, model, conversationHistory, signal }) => request('/api/v1/assistant/chat', {
    method: 'POST',
    body: JSON.stringify({
      question,
      customer_id: customerId || null,
      model: model || null,
      conversation_history: conversationHistory || [],
    }),
    signal,
  }),

  // --- KG Explorer (Retrieval Pipeline Spec Section 10) ---
  kgSearch: ({ query = '', categories = '', customerId, limit = 50 } = {}) => {
    const params = new URLSearchParams()
    if (query) params.set('query', query)
    if (categories) params.set('categories', categories)
    if (customerId) params.set('customer_id', customerId)
    params.set('limit', String(limit))
    return request(`/api/v1/kg/search?${params}`)
  },
  kgNeighborhood: (nodeId, { limit = 50 } = {}) =>
    request(`/api/v1/kg/neighborhood?node_id=${encodeURIComponent(nodeId)}&limit=${limit}`),
  kgSubgraph: (entityIds, { limit = 100 } = {}) =>
    request(`/api/v1/kg/subgraph?entity_ids=${encodeURIComponent(entityIds.join(','))}&limit=${limit}`),
  kgCategorySubgraph: ({ categories, customerId, depth = 1, limitPerRoot = 15, maxRoots = 40 }) => {
    const params = new URLSearchParams()
    params.set('categories', categories.join(','))
    if (customerId) params.set('customer_id', customerId)
    params.set('depth', String(depth))
    params.set('limit_per_root', String(limitPerRoot))
    params.set('max_roots', String(maxRoots))
    return request(`/api/v1/kg/category-subgraph?${params}`)
  },
  kgFilterCategories: () => request('/api/v1/kg/filter-categories'),

  // --- Triage & Incidents (Retrieval Pipeline Spec Section 14) ---
  triageScan: ({ limit = 10 } = {}) => request(`/api/v1/triage/scan?limit=${limit}`, { method: 'POST' }),
  incidents: ({ limit = 50 } = {}) => request(`/api/v1/incidents?limit=${limit}`),

  // --- Schema Registry & Semantic Search ---
  schemaRegistry: () => request('/api/v1/schema/registry'),
  semanticSearch: ({ query, customerId, limit = 10 }) => {
    const params = new URLSearchParams({ query })
    if (customerId) params.set('customer_id', customerId)
    params.set('limit', String(limit))
    return request(`/api/v1/semantic-search?${params}`)
  },
}
