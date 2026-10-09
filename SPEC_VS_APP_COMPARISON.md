# Spec vs Application — Gap Analysis

> **Spec Document**: "Telecom Billing RCA — Retrieval Pipeline" (docx)
> **Current App**: Described in `PROJECT_INSTRUCTIONS.md`
> **Purpose**: Compare what the spec asks for vs what the app currently implements. Each item is marked for your decision: **ADD**, **KEEP**, or **REMOVE/SKIP**.

---

## Legend

| Symbol | Meaning |
|--------|---------|
| **APP HAS** | Feature exists in the current application |
| **SPEC WANTS** | Feature described in the spec document |
| **MISSING** | Spec wants it but app doesn't have it |
| **EXTRA** | App has it but spec doesn't mention it |

---

## 1. NODE LABELS / ONTOLOGY

The spec and app use **completely different naming** for the same concepts.

| Spec Label | App Label | Status | Notes |
|---|---|---|---|
| `Customer` | `Customer` | **Same** | Both use CUST- prefix |
| `BillingAccount` | `Account` | **Different name** | Spec says BillingAccount, app says Account |
| `Invoice` | `Invoice` | **Same** | |
| `ChargingRecord` | `Charge` | **Different name** | Spec says ChargingRecord, app says Charge |
| `Alarm` | `NetworkFailure` | **Different name** | Spec says Alarm, app says NetworkFailure |
| `Dunning` | `PaymentFailure` | **Different name** | Spec says Dunning, app says PaymentFailure |
| `ServiceProblem` | `Incident` | **Different name** | Spec says ServiceProblem, app says Incident |
| `KPIObservation` | `ApiKpiBreach` | **Different name** | Spec says KPIObservation, app says ApiKpiBreach |
| `LogEntry` | `LogEvent` | **Different name** | Spec says LogEntry, app says LogEvent |
| `Complaint` | `Dispute` | **Different name** | Spec says Complaint, app says Dispute |
| `RemediationAction` | — | **MISSING** | Spec has remediation nodes; app does not |
| `RootCauseAnalysis` | — | **MISSING** | Spec has RCA result nodes; app does not |
| — | `SlaCredit` | **EXTRA** | App has SLA credit nodes; spec doesn't mention them as a separate label |
| — | `PmCounter` | **EXTRA** | App has PM counter nodes; spec doesn't mention them as a separate label |
| — | `Site` | **EXTRA** | App has site nodes; spec doesn't mention them as a separate label |
| — | `Service` | **EXTRA** | App has service nodes; spec doesn't mention them as a separate label |

### DECISION NEEDED:
- [ ] **Rename app labels** to match spec? (Account→BillingAccount, Charge→ChargingRecord, etc.)
- [ ] **Keep app labels** as-is (they're cleaner/shorter)?
- [ ] **Add RemediationAction** nodes and `REMEDIATED_BY` edges?
- [ ] **Add RootCauseAnalysis** nodes?
- [ ] **Keep SlaCredit, PmCounter, Site, Service** (extra app nodes)?

---

## 2. RELATIONSHIP TYPES

| Spec Relationship | App Relationship | Status |
|---|---|---|
| `HAS_ACCOUNT` | `OWNED_BY` (reversed direction) | **Different** — Spec: Customer→HAS_ACCOUNT→BillingAccount; App: Account→OWNED_BY→Customer |
| `HAS_INVOICE` | `BILLED_TO` (reversed direction) | **Different** — Spec: BillingAccount→HAS_INVOICE→Invoice; App: Invoice→BILLED_TO→Account |
| `HAS_CHARGE` | `ON_INVOICE` (reversed direction) | **Different** — Spec: Invoice→HAS_CHARGE→ChargingRecord; App: Charge→ON_INVOICE→Invoice |
| `AFFECTED_BY` | `CAUSED_CHARGE` | **Different** — Spec: BillingAccount→AFFECTED_BY→Alarm; App: NetworkFailure→CAUSED_CHARGE→Charge |
| `TRIGGERED` | `FAILED_ON` | **Different** — Spec: BillingAccount→TRIGGERED→Dunning; App: PaymentFailure→FAILED_ON→Invoice |
| `MONITORED_BY` | `CORROBORATES` | **Different** — Spec: BillingAccount→MONITORED_BY→KPIObservation; App: ApiKpiBreach→CORROBORATES→NetworkFailure |
| `HAS_LOG_ENTRY` | `EMITTED_BY` (reversed) | **Different** — Spec: BillingAccount→HAS_LOG_ENTRY→LogEntry; App: LogEvent→EMITTED_BY→Service |
| `HAS_COMPLAINT` | `DISPUTES` | **Different** — Spec: ChargingRecord→HAS_COMPLAINT→Complaint; App: Dispute→DISPUTES→Charge |
| `REMEDIATED_BY` | — | **MISSING** | Spec: Issue→REMEDIATED_BY→RemediationAction |
| — | `COMPENSATES` | **EXTRA** | App: SlaCredit→COMPENSATES→Incident |
| — | `AT_SITE` | **EXTRA** | App: NetworkFailure→AT_SITE→Site |
| — | `MEASURED_AT` | **EXTRA** | App: PmCounter→MEASURED_AT→Site |

### Key Architectural Difference:
The spec uses a **star schema** from BillingAccount (BillingAccount→AFFECTED_BY→Alarm, BillingAccount→TRIGGERED→Dunning, etc.), while the app uses a **causal chain** (RootCause→CAUSED_CHARGE→Charge→ON_INVOICE→Invoice→BILLED_TO→Account→OWNED_BY→Customer).

### DECISION NEEDED:
- [ ] **Switch to spec's star schema** from BillingAccount? (Major refactor)
- [ ] **Keep app's causal ontology** (arguably better for RCA since it models causation)?
- [ ] **Add REMEDIATED_BY** edges (requires RemediationAction nodes)?
- [ ] **Keep COMPENSATES, AT_SITE, MEASURED_AT** (extra app edges)?

---

## 3. INTENT DETECTION

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Layer 0: Meta/capability** | Handles "what can you do", "hi", "help" with zero model dependency | **MISSING** | No meta/greeting handler |
| **Layer 1: Rule-based taxonomy** | 9 billing intents with ordered trigger phrases | **PARTIAL** — 9 intents via regex (network_rca, kpi_breach, dunning_chain, sla_credit, system_error, complaint, financial_impact, full_chain, unresolved) | Different intent names; no ordered phrase precedence |
| **Layer 2: Model-constrained classification** | Model picks from fixed taxonomy list; replies NONE if no match | **MISSING** | No model-based intent classification fallback |
| **Layer 2b: Investigative sub-routing** | Keyword-based dispatch of domain specialists for investigative intents | **MISSING** | No specialist dispatch system |
| **Layer 3: Open-domain chat fallback** | Model answers from its own knowledge, labeled as ungrounded | **MISSING** | No open-domain chat fallback |
| **Period/year extraction** | Extracts YYYY-MM, "March 2024", or bare "2024" | **MISSING** | No date/period extraction from questions |
| **Word-boundary regex** | Fixed bug where "remediation" matched "mediation" | **N/A** | App uses different patterns |
| **Configurable taxonomy** | Domain-agnostic, extensible to new domains | **NO** | Intents are hardcoded regex patterns |

### Spec Intent Names vs App Intent Names:
| Spec | App |
|---|---|
| `invoice_summary` | `financial_impact` |
| `invoice_explanation` | — (no equivalent) |
| `charge_breakdown` | — (no equivalent) |
| `payment_status` | `dunning_chain` |
| `discount_inquiry` | — (no equivalent) |
| `dispute_status` | `complaint` |
| `revenue_leakage_check` | — (no equivalent) |
| `issues_without_remediation` | `unresolved` |
| `root_cause_analysis` | `network_rca`, `kpi_breach`, `system_error`, `sla_credit`, `full_chain` |

### DECISION NEEDED:
- [ ] **Add Layer 0** (meta/capability questions)?
- [ ] **Add Layer 2** (model-constrained classification fallback)?
- [ ] **Add Layer 2b** (investigative sub-routing)?
- [ ] **Add Layer 3** (open-domain chat fallback)?
- [ ] **Add period/year extraction**?
- [ ] **Rename intents** to match spec taxonomy?
- [ ] **Keep current intents** as-is (more RCA-focused)?

---

## 4. QUERY GENERATION & VALIDATION (Major Gap)

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Schema Registry** | Structured registry of all labels, relationships, properties | **MISSING** | No schema registry; labels hardcoded in code |
| **Dynamic Cypher Generation** | LLM generates Cypher from intent spec + schema registry | **MISSING** | All Cypher queries are hand-written in `rca_retrieval_service.py` |
| **Deterministic Validator** | Checks: read-only, schema conformance, scope anchoring, parameterization, hop-depth, row ceiling | **MISSING** | No query validation layer |
| **Bounded Repair Loop** | 2-attempt regeneration with failure reason fed back | **MISSING** | No repair loop |
| **Shape Tagging** | `// shape: <key>` comment on generated queries | **MISSING** | No shape tagging |
| **Offline/Demo Mock Client** | MockGraphClient with shape-based fingerprinting | **PARTIAL** — DemoRepository exists for basic fallback | No shape-based mock dispatch |

### DECISION NEEDED:
This is the **biggest architectural gap**. The spec wants dynamic Cypher generation; the app uses hand-written queries.

- [ ] **Add schema registry + Cypher generation + validator** (large effort, spec's core design)
- [ ] **Keep hand-written Cypher** (simpler, already works, covers current intents)
- [ ] **Hybrid**: Keep hand-written queries but add the validator as a safety layer?

---

## 5. TRAVERSAL SHAPES

| Shape | Spec | App | Gap |
|---|---|---|---|
| **Shape 1: Single-Entity Lookup** | Generated Cypher for one customer, one period | **PARTIAL** — Hand-written per-intent Cypher in `rca_retrieval_service.py` | No generation; manual queries |
| **Shape 2: Aggregate/Population Scan** | ALL customers or whole year, generated | **PARTIAL** — `_retrieve_cross_customer_evidence()` in `nl_query_service.py` | Simpler implementation |
| **Shape 3: Deep Multi-Hop Union** | 6-branch UNION ALL for issues without remediation | **MISSING** | No RemediationAction concept |
| **Shape 4: Multi-Step Hybrid KG+Vector** | Supervisor→Specialists→Critic pipeline with blackboard | **MISSING** | Simple intent→retrieval→LLM narration |
| **Shape 5: Aggregate Multi-Hop RCA** | ALL-customers RCA with cause clusters | **MISSING** | Only per-customer RCA |
| **Shape 6: Interactive KG Explorer** | search, neighborhood, subgraph, category-subgraph | **MISSING** | No KG explorer endpoints |

### DECISION NEEDED:
- [ ] **Add Shape 3** (issues without remediation — requires RemediationAction)?
- [ ] **Add Shape 4** (multi-step RCA pipeline with specialists/critic)?
- [ ] **Add Shape 5** (aggregate RCA)?
- [ ] **Add Shape 6** (KG Explorer endpoints)?
- [ ] **Keep current shapes** as-is?

---

## 6. API ENDPOINTS

### Endpoints the SPEC wants that the APP is MISSING:

| Endpoint | Purpose | Priority |
|---|---|---|
| `POST /api/v1/rca` | Dedicated RCA endpoint (multi-step pipeline) | High |
| `POST /api/v1/assistant/chat` | Conversational wrapper with grounding badges | Medium |
| `GET /api/v1/rca/history` | List past RCA runs | Medium |
| `GET /api/v1/rca/history/{id}` | Fetch saved RCA result | Medium |
| `GET /api/v1/rca/{request_id}/evidence/{entity_id}` | Lookup one evidence entity | Low |
| `POST /api/v1/rca/compare-models` | Side-by-side model comparison for RCA | Low |
| `POST /api/v1/triage/scan` | Proactive anomaly scanning | Low |
| `GET /api/v1/kg/search` | KG Explorer text/category search | Medium |
| `GET /api/v1/kg/neighborhood` | 1-hop drill-down | Medium |
| `GET /api/v1/kg/subgraph` | Merged neighborhoods for entity list | Medium |
| `GET /api/v1/kg/category-subgraph` | Deep multi-hop category filter | Medium |
| `GET /api/v1/kg/filter-categories` | Category list for KG explorer | Low |
| `GET /api/v1/semantic-search` | Standalone ontology vector search | Low |
| `GET /api/v1/incidents` | Incident audit trail | Low |

### Endpoints the APP has that the SPEC doesn't mention:

| Endpoint | Purpose | Decision |
|---|---|---|
| `GET /api/v1/customers/{id}/impact` | Financial impact summary | **EXTRA** |
| `GET /api/v1/customers/{id}/dunning` | Payment failure chain | **EXTRA** |
| `GET /api/v1/customers/{id}/sla-credits` | SLA credit chain | **EXTRA** |
| `GET /api/v1/customers/{id}/system-errors` | System error chain | **EXTRA** |
| `GET /api/v1/customers/{id}/kpi-breaches` | KPI breach chain | **EXTRA** |
| `GET /api/v1/graph/overview` | Sampled KG overview | **EXTRA** |
| `GET /api/v1/customers/{id}/graph` | Customer graph payload | **EXTRA** |
| `GET /api/v1/sites/{id}/impact` | Site impact analysis | **EXTRA** |
| `GET /api/v1/unresolved` | Cross-domain unresolved issues | **EXTRA** |
| `POST /api/v1/vector/search` | ChromaDB vector search | **EXTRA** (spec uses Neo4j-native vector instead) |
| `GET /api/v1/vector/stats` | Vector store stats | **EXTRA** |
| `DELETE /api/v1/vector/clear` | Clear vector store | **EXTRA** |
| `POST /api/v1/ingest/batch` | Batch CSV ingestion | **EXTRA** (spec assumes ingestion is separate doc) |
| `POST /api/v1/schema/setup` | Neo4j schema setup | **EXTRA** |
| `POST /api/v1/synthetic/generate-and-ingest` | Synthetic data generation | **EXTRA** |

### Endpoints BOTH have:

| Endpoint | Notes |
|---|---|
| `POST /api/v1/query` | Both have this; spec calls it "billing assistant" |
| `GET /api/v1/customers` | Both have this |
| `GET /api/v1/models` | App has it; spec implies model listing in Section 13 |

### DECISION NEEDED:
- [ ] **Add missing spec endpoints** (which ones)?
- [ ] **Keep extra app endpoints** (they provide real value)?
- [ ] **Remove any extra endpoints**?

---

## 7. VECTOR / RAG LAYER

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Embedding model** | all-MiniLM-L6-v2 (384 dim) with deterministic fallback | all-MiniLM-L6-v2 via ChromaDB | **Same model**, no deterministic fallback in app |
| **Vector storage** | Neo4j-native vector indexes (one per label, 7 total) | ChromaDB (external store) | **Different storage** |
| **Neo4j vector indexes** | `db.index.vector.queryNodes()` with 7 per-label indexes | **MISSING** | App uses ChromaDB, not Neo4j vectors |
| **Standalone semantic search** | `/api/v1/semantic-search` — 250 curated billing phrases | **MISSING** | No curated ontology phrase search |
| **Customer-scoped vector search** | Scoped to one customer via $customer_id in Neo4j query | `/api/v1/vector/search` with optional `customer_id` filter | App has this via ChromaDB |
| **Hybrid retrieval** | KG structured joins + vector hits in same evidence pool | **MISSING** | Vector and KG are separate, not combined |

### DECISION NEEDED:
- [ ] **Switch to Neo4j vector indexes** (as spec requires)?
- [ ] **Keep ChromaDB** (already working, easier)?
- [ ] **Add hybrid retrieval** (combine KG + vector results)?
- [ ] **Add standalone semantic search** with curated phrases?

---

## 8. RESPONSE FORMULATION

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Narrator fallback chain** | tslam → USE_MODEL fallback → deterministic | Claude → OpenAI → Groq → HuggingFace → LM Studio → Deterministic | **App has MORE models** |
| **No-silent-fallback** | When explicit model selected, don't silently fall back | **IMPLEMENTED** | App already does this correctly |
| **Deterministic template** | Fixed sentence structure from evidence | **IMPLEMENTED** | `_fallback_answer()` in nl_query_service.py |
| **Grounding badges** | Visible badge per answer (Grounded/Not Grounded/Model's Own Knowledge/etc.) | **MISSING** | No grounding classification badges |
| **reason_codes** | GENERAL_CHAT, GENERAL_CAPABILITIES, NO_INVOICE_FOUND, etc. | **MISSING** | No reason code system |
| **recommended_next_step** | Actionable next step in every response | **MISSING** | No recommended next step field |
| **Markdown rendering** | Client-side Markdown with HTML escaping | **IMPLEMENTED** | ReactMarkdown + rehypeRaw in NLQueryPanel |
| **Evidence list in response** | entity_id, entity_type, fact, source_system, similarity_confidence | **PARTIAL** | Evidence returned but less structured |
| **narrator_error field** | Reports why a model call failed | **MISSING** | Errors shown in answer text, not separate field |
| **Rules-decide-WHAT / model-decides-HOW** | Fixed rules for correlation; model only rephrases | **PARTIAL** | Intent→evidence is deterministic, LLM narrates |

### DECISION NEEDED:
- [ ] **Add grounding badges** (Grounded in KG / Model's own knowledge / etc.)?
- [ ] **Add reason_codes** to response?
- [ ] **Add recommended_next_step** to response?
- [ ] **Add narrator_error** field?
- [ ] **Keep current multi-model chain** (more models than spec)?

---

## 9. EVALUATION FRAMEWORK

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Adherence score** | 0-1 ratio of grounded vs ungrounded claims | **PARTIAL** — `evaluation_service.py` has grounding_score | Not computed per-response automatically |
| **Relevance score** | Judge-model-based, 0-1 | **MISSING** | No judge-based relevance scoring |
| **Evaluation prompt contract** | Model-agnostic judge scores relevance + adherence | **MISSING** | No evaluation prompt |
| **Escalation threshold** | adherence < 0.8 → requires_human_escalation | **MISSING** | No automated escalation |
| **Debug trace** | debug.evaluation object in response when debug=true | **MISSING** | No debug trace in responses |

### DECISION NEEDED:
- [ ] **Add adherence/relevance scoring** per response?
- [ ] **Add escalation thresholds**?
- [ ] **Add debug trace** option?
- [ ] **Keep current evaluation** (manual only)?

---

## 10. CONFIDENCE SCORING

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Billing Assistant** | Base 0.85 from rule classifier; capped at 0.4 when evidence empty | Uses "high"/"medium"/"low" strings | **Different system** |
| **RCA per-customer** | 0.75 + 0.05 per root cause − 0.25 if ungrounded − 0.05 if retry needed, clamped [0.3, 0.98] | "high" if citations, "medium" otherwise | **Much simpler in app** |
| **RCA ALL-customers** | 0.7 + min(top_cluster_probability, 0.25) − penalties | Not implemented | **MISSING** |
| **requires_human_escalation** | Forced when no root cause, ungrounded citation, confidence < 0.6 | Not implemented | **MISSING** |

### DECISION NEEDED:
- [ ] **Switch to numeric confidence** (0-1) as spec requires?
- [ ] **Keep string confidence** (high/medium/low)?
- [ ] **Add requires_human_escalation** flag?

---

## 11. ACCESS CONTROL & SECURITY

| Feature | Spec | App | Gap |
|---|---|---|---|
| **Customer scoping at query level** | Validator checks scope anchoring on every query | Customer filtering via `customer_id` parameter | **No formal validator** |
| **No authentication** | Spec acknowledges PoC has no auth | Same | Both are PoC-level |
| **No raw user input in Cypher** | Parameterized queries only | Parameterized queries | **Same** |
| **Sensitivity flags on properties** | Registry marks sensitive fields (e.g., full card number) | **MISSING** | No sensitivity flagging |

### DECISION NEEDED:
- [ ] **Add scope-anchoring validator**?
- [ ] **Add sensitivity flags**?
- [ ] **Keep current approach** (adequate for PoC)?

---

## 12. INFRASTRUCTURE

| Feature | Spec | App | Gap |
|---|---|---|---|
| **PostgreSQL** | Incident logging, RCA history persistence | **MISSING** | App uses only Neo4j + ChromaDB |
| **Neo4j vector indexes** | 7 per-label vector indexes via db.index.vector.createNodeIndex() | **MISSING** | App uses ChromaDB for vectors |
| **In-memory fallback stores** | Falls back when PG/Neo4j unavailable | **PARTIAL** — DemoRepository for Neo4j fallback | No PG fallback (no PG at all) |

### DECISION NEEDED:
- [ ] **Add PostgreSQL** for incident logging/RCA history?
- [ ] **Add Neo4j vector indexes** (replace ChromaDB)?
- [ ] **Keep current stack** (Neo4j + ChromaDB only)?

---

## 13. THINGS THE APP HAS THAT THE SPEC DOESN'T MENTION

These are **extra features** in the app not covered by the spec:

| Feature | Where | Value |
|---|---|---|
| **Causal ontology** (RootCause→Charge→Invoice→Account→Customer) | csv_ingestion_service.py | Better for RCA causation modeling |
| **SlaCredit nodes** with COMPENSATES edges | csv_ingestion_service.py | Models SLA credit compensation |
| **PmCounter nodes** with CORROBORATES edges | csv_ingestion_service.py | Performance counter evidence |
| **Site nodes** with AT_SITE/MEASURED_AT edges | csv_ingestion_service.py | Physical location context |
| **Service nodes** with EMITTED_BY edges | csv_ingestion_service.py | Service-level tracing |
| **6 LLM models** (Claude, OpenAI, Groq, HuggingFace, LM Studio, KG Traversal) | nl_query_service.py | More models than spec's 3 |
| **Explicit no-fallback** when user selects model | nl_query_service.py | Better UX than spec's silent fallback |
| **Synthetic data generator** (9 CSVs, configurable size) | synthetic_data.py | Easy demo data creation |
| **CSV batch ingestion** with cross-link creation | csv_ingestion_service.py | Full data pipeline |
| **Graph verification endpoint** | main.py | Validates ingested data |
| **Customer 360 page** | Frontend | Not in spec's scope |
| **D3 force-directed graph visualization** | ForceGraph.jsx | Interactive KG visualization |
| **Per-customer graph payload** | rca_retrieval_service.py | Customer-scoped graph view |
| **Site impact analysis** | rca_retrieval_service.py | Cross-customer site analysis |
| **Conversation history** for multi-turn LLM | nl_query_service.py | Multi-turn context |
| **ID-prefix routing** (NF-→NetworkFailure, PF-→PaymentFailure, etc.) | rca_retrieval_service.py | Smart entity-type detection |

### DECISION NEEDED:
- [ ] **Keep all extra features**?
- [ ] **Remove any** to align with spec?

---

## SUMMARY: Priority Tiers

### Tier 1 — HIGH PRIORITY (Spec core requirements not in app)

1. **Multi-step RCA pipeline** (Shape 4: supervisor/specialists/critic) — the spec's centerpiece
2. **Schema-grounded Cypher generation + validator** — the spec's safety discipline
3. **KG Explorer endpoints** (search, neighborhood, subgraph, category-subgraph)
4. **POST /api/v1/rca** — dedicated RCA endpoint
5. **Grounding badges & reason_codes** — user-facing trust signals

### Tier 2 — MEDIUM PRIORITY

6. **Five-layer intent cascade** (meta → rules → model → sub-routing → chat)
7. **POST /api/v1/assistant/chat** with conversation + grounding badges
8. **RCA history** (persist and retrieve past RCA runs)
9. **RemediationAction nodes** + REMEDIATED_BY edges + Shape 3
10. **Numeric confidence scoring** (0-1 with formula)
11. **Hybrid retrieval** (KG + vector combined in same evidence pool)
12. **Neo4j vector indexes** (vs ChromaDB)

### Tier 3 — LOW PRIORITY / NICE-TO-HAVE

13. **Evaluation framework** (adherence + relevance scoring per response)
14. **POST /api/v1/rca/compare-models** (side-by-side model comparison)
15. **POST /api/v1/triage/scan** (proactive anomaly detection)
16. **PostgreSQL** for incident logging
17. **Standalone semantic search** with curated ontology phrases
18. **Aggregate multi-hop RCA** (Shape 5, ALL-customers RCA)
19. **Access control / scope-anchoring validator**
20. **Bounded repair loop** for failed generated queries

### KEEP AS-IS (App features worth preserving)

- Causal ontology (better than spec's star schema for RCA)
- 6-model LLM support (more than spec's 3)
- No-silent-fallback behavior
- Synthetic data generator
- CSV batch ingestion pipeline
- D3 force graph visualization
- Customer 360 page
- Site impact analysis
- PmCounter / SlaCredit / Site / Service nodes
- ID-prefix routing
