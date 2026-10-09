# Telecom RCA Intelligence — Project Instructions

> **Project**: EzInsights Telecom RCA (Root Cause Analysis) Platform
> **Client**: KPMG POC
> **Stack**: FastAPI (Python) + React (Vite) + Neo4j + Multi-Model LLM
> **Last Updated**: October 2026

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Causal Ontology (Knowledge Graph)](#2-causal-ontology-knowledge-graph)
3. [Data Ingestion Pipeline](#3-data-ingestion-pipeline)
4. [LLM Model Integration](#4-llm-model-integration)
5. [Backend API Endpoints](#5-backend-api-endpoints)
6. [Frontend Components](#6-frontend-components)
7. [Configuration & Environment](#7-configuration--environment)
8. [Key Design Decisions](#8-key-design-decisions)
9. [Known Issues & Fixes](#9-known-issues--fixes)
10. [Running the Project](#10-running-the-project)
11. [Validation & Testing](#11-validation--testing)

---

## 1. Architecture Overview

```
┌──────────────┐     ┌──────────────────┐     ┌────────────┐
│   React UI   │────▶│  FastAPI Backend  │────▶│   Neo4j    │
│  (Vite/JSX)  │◀────│  (Python 3.11+)  │◀────│  (bolt://) │
└──────────────┘     └──────────────────┘     └────────────┘
                            │
                     ┌──────┴──────────────────────────┐
                     │  Multi-Model LLM Narration Layer │
                     │  Claude │ OpenAI │ HuggingFace   │
                     │  Groq  │ LM Studio │ KG Direct   │
                     └─────────────────────────────────┘
```

### Directory Structure

```
Code v4 - KPMG POC/
├── Backend/
│   ├── app/
│   │   ├── main.py                 # FastAPI app, all endpoints
│   │   ├── config.py               # Pydantic settings from .env
│   │   ├── csv_ingestion_service.py # CSV → Neo4j causal ontology pipeline
│   │   ├── rca_retrieval_service.py # Cypher traversals for RCA queries
│   │   ├── nl_query_service.py     # Multi-model NL query + fallback logic
│   │   ├── neo4j_service.py        # Legacy Neo4j queries
│   │   ├── models.py               # Pydantic request/response models
│   │   ├── vector_service.py       # ChromaDB vector embeddings
│   │   ├── synthetic_data.py       # Synthetic data generator (9 CSVs)
│   │   ├── llm_service.py          # Claude + OpenAI dual-answer service
│   │   ├── qa_service.py           # Chat Q&A with conversation memory
│   │   └── repository.py           # Demo data repository
│   ├── .env                        # Environment variables (API keys, Neo4j)
│   └── venv/                       # Python virtual environment
├── Frontend/
│   ├── src/
│   │   ├── App.jsx                 # Main app with legend, graph, tabs
│   │   ├── api.js                  # API client (all endpoint wrappers)
│   │   ├── components/
│   │   │   ├── ForceGraph.jsx      # D3 force-directed graph visualization
│   │   │   ├── NLQueryPanel.jsx    # RCA chat panel with model picker
│   │   │   ├── CSVIngestionPanel.jsx # Data ingestion UI
│   │   │   ├── CustomerExplorer.jsx  # Customer 360 view
│   │   │   └── LandingPage.jsx     # Landing/home page
│   │   └── styles.css              # Global styles
│   └── package.json
└── scenario_batch_*/               # Generated synthetic CSV data
```

---

## 2. Causal Ontology (Knowledge Graph)

### CRITICAL: Causal Chain, NOT Star Schema

The graph follows a **causal ontology** — NOT a star schema. Events do NOT connect directly to Customer/Account. Instead, they flow through a causal chain:

```
RootCause → CAUSED_CHARGE → Charge → ON_INVOICE → Invoice → BILLED_TO → Account → OWNED_BY → Customer
```

### Node Types

| Label | ID Prefix | Description | Example |
|-------|-----------|-------------|---------|
| `Customer` | `CUST-` | End customer | `CUST-4367` |
| `Account` | `ACC-` | Billing account | `ACC-4367` |
| `Invoice` | `INV-GEN-` | Monthly bill | `INV-GEN-13D0FF` |
| `Charge` | `CHG-GEN-` | Line item charge | `CHG-GEN-A1B2C3` |
| `NetworkFailure` | `NF-GEN-` | Cell outage/alarm | `NF-GEN-E4E3D5` |
| `PaymentFailure` | `PF-GEN-` | Failed payment | `PF-GEN-7890AB` |
| `Incident` | `SD-GEN-` | Service disruption | `SD-GEN-F1E2D3` |
| `LogEvent` | `LOG-GEN-` | System log entry | `LOG-GEN-C4D5E6` |
| `ApiKpiBreach` | `KPI-GEN-` | KPI threshold breach | `KPI-GEN-B7A8C9` |
| `PmCounter` | `PM-GEN-` | Performance counter | `PM-GEN-1A2B3C` |
| `Dispute` | `DSP-GEN-` | Customer complaint | `DSP-GEN-D4E5F6` |
| `SlaCredit` | `ADJ-GEN-` | SLA adjustment/credit | `ADJ-GEN-7G8H9I` |
| `Site` | `SITE-` | Cell tower site | `SITE-UK-LON-014` |
| `Service` | `SVC-` | Telecom service | `SVC-VOICE` |

### Edge Types (Relationships)

| Relationship | From | To | Description |
|---|---|---|---|
| `CAUSED_CHARGE` | RootCause (NF/SD/LOG/KPI) | Charge | Root cause generated this charge |
| `FAILED_ON` | PaymentFailure | Invoice | Payment failed on this invoice |
| `ON_INVOICE` | Charge | Invoice | Charge appears on this invoice |
| `BILLED_TO` | Invoice | Account | Invoice sent to this account |
| `OWNED_BY` | Account | Customer | Account belongs to customer |
| `AT_SITE` | NetworkFailure | Site | Alarm occurred at this site |
| `MEASURED_AT` | PmCounter | Site | Counter measured at this site |
| `EMITTED_BY` | LogEvent | Service | Log emitted by this service |
| `CORROBORATES` | PmCounter/ApiKpiBreach | RootCause | Evidence supports root cause |
| `DISPUTES` | Dispute | Charge | Customer disputes this charge |
| `COMPENSATES` | SlaCredit | Incident | Credit compensates this disruption |

### ID-Prefix Routing (RCA Retrieval)

The `rca_retrieval_service.py` routes queries by ID prefix:

- `NF-` → NetworkFailure → CAUSED_CHARGE → Chain B + AT_SITE + PM/API CORROBORATES
- `PF-` → PaymentFailure → FAILED_ON → Invoice, Charge ON_INVOICE → Chain B
- `SD-` → Incident → CAUSED_CHARGE → Chain B + SlaCredit COMPENSATES + API CORROBORATES
- `LOG-` → LogEvent → CAUSED_CHARGE → Chain B + EMITTED_BY Service
- `KPI-` → ApiKpiBreach → root: CAUSED_CHARGE→B; evidence: CORROBORATES→root→B
- `PM-` → PmCounter → CORROBORATES → NF → B + MEASURED_AT Site
- `DSP-` → Dispute → DISPUTES → Charge → Chain B + root + evidence
- `ADJ-` → SlaCredit → COMPENSATES → Incident → Chain B
- `CUST-` → Customer ← Account ← Invoice ← Charge, then root per charge

**Chain B** = `Charge → ON_INVOICE → Invoice → BILLED_TO → Account → OWNED_BY → Customer`

### IMPORTANT: No Direct Event→Customer Edges

Events do NOT have edges to Customer or Account nodes. The `customer_id` exists only as a **property** on event nodes, used for filtering. The graph path always goes through Charge → Invoice → Account → Customer.

---

## 3. Data Ingestion Pipeline

### CSV Domains (9 files)

| Domain Name | CSV File | Node Label | Key Edges Created |
|---|---|---|---|
| `network` | `network.csv` | `NetworkFailure` | AT_SITE → Site |
| `billing` | `billing.csv` | `Invoice`, `Charge`, `Account`, `Customer` | ON_INVOICE, BILLED_TO, OWNED_BY |
| `complaints` | `complaints.csv` | `Dispute` | — (cross-links later) |
| `incident` | `incident.csv` | `Incident` | — (cross-links later) |
| `pm_counters` | `pm_counters.csv` | `PmCounter` | MEASURED_AT → Site |
| `api` | `api.csv` | `ApiKpiBreach` | — (cross-links later) |
| `logs` | `logs.csv` | `LogEvent` | EMITTED_BY → Service |
| `sla_credits` | `sla_credits.csv` | `SlaCredit` | — (cross-links later) |
| `payment_failures` | `payment_failures.csv` | `PaymentFailure` | — (cross-links later) |

### Cross-Domain Relationship Creation

After all CSVs are loaded, `create_cross_domain_relationships()` creates causal edges:

1. **CAUSED_CHARGE**: Regex-scan `impact_description` fields for `CHG-GEN-XXXX` patterns
2. **FAILED_ON**: Match PaymentFailure → Invoice by `invoice_id` property
3. **CORROBORATES**: PmCounter/ApiKpiBreach → NetworkFailure using plausible KPI→failure-type map
4. **DISPUTES**: Dispute → Charge by scanning `resolution_notes` for `CHG-GEN-XXXX`
5. **COMPENSATES**: SlaCredit → Incident by `disruption_id` property

### Regex Patterns for Edge Creation

```python
_CHG_RE = re.compile(r"CHG-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_NF_RE  = re.compile(r"NF-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_PF_RE  = re.compile(r"PF-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_SD_RE  = re.compile(r"SD-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_INV_RE = re.compile(r"INV-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
```

### Ingestion Endpoints

- `POST /api/v1/schema/setup` — Create Neo4j constraints/indexes
- `POST /api/v1/ingest/batch` — Batch ingest all CSVs from a directory
- `POST /api/v1/ingest/{domain}` — Ingest a single domain CSV
- `POST /api/v1/ingest/cross-links` — Create cross-domain relationships
- `DELETE /api/v1/graph/clear` — Delete all nodes, edges, constraints, indexes
- `GET /api/v1/verify` — Verify graph node/edge counts

### Expected Counts (957-customer synthetic dataset)

| Node Type | Expected Count |
|---|---|
| Customer | 957 |
| Account | 957 |
| Invoice | ~1000 |
| Charge | ~1000 |
| NetworkFailure | ~263 |
| PaymentFailure | ~266 |
| Incident | ~198 |
| LogEvent | ~153 |
| ApiKpiBreach | ~440 |
| PmCounter | ~101 |
| Dispute | ~227 |
| SlaCredit | ~143 |
| Site | 5 |
| Service | 4 |

| Edge Type | Expected Count |
|---|---|
| CAUSED_CHARGE | ~734 |
| FAILED_ON | ~266 |
| CORROBORATES | ~421 |
| DISPUTES | ~227 |
| COMPENSATES | ~143 |
| ON_INVOICE | ~1000 |
| BILLED_TO | ~1000 |
| OWNED_BY | ~957 |
| AT_SITE | varies |
| MEASURED_AT | varies |
| EMITTED_BY | varies |

---

## 4. LLM Model Integration

### Available Models (Dropdown in RCA Page)

| Model ID | Display Name | Provider | Config Keys |
|---|---|---|---|
| `huggingface` | GPT-OSS-120B (novita) | HuggingFace Inference API | `HUGGINGFACE_API_KEY`, `HUGGINGFACE_ENABLED` |
| `claude` | Claude (Anthropic) | Anthropic | `CLAUDE_API_KEY`, `CLAUDE_ENABLED` |
| `openai` | OpenAI GPT-4o | OpenAI | `OPENAI_API_KEY` |
| `groq` | Groq (Llama 70B) | Groq | `GROQ_API_KEY`, `GROQ_ENABLED` |
| `lmstudio` | Local LLM | LM Studio | `LMSTUDIO_URL`, `LMSTUDIO_ENABLED` |
| `deterministic` | KG Traversal | None (template) | Always available |

### Default Model

**GPT-OSS-120B (HuggingFace)** is the default model in the RCA page dropdown. This is set in `NLQueryPanel.jsx`:
- Default state: `useState('huggingface')`
- Customer select handler sets model to `'huggingface'`
- "All Customers" button sets model to `'huggingface'`

### No Silent Fallback (CRITICAL)

When a user **explicitly selects a model**, ONLY that model is used. If it fails, an error message is returned — the system does NOT silently fall back to deterministic/KG Traversal.

```
User selects "GPT-OSS-120B" → Only tries HuggingFace → Error if it fails
User selects "Auto" (or null) → Uses fallback chain: Claude → OpenAI → Groq → HuggingFace → LM Studio → Deterministic
```

This is implemented in `nl_query_service.py` → `_generate_with_fallback()`:
- Lines 340-359: Explicit model selection path (no fallback)
- Lines 361-389: Auto/fallback chain path

### HuggingFace API Configuration

- **Provider**: novita (routed via `https://router.huggingface.co/novita/v3/openai`)
- **Model**: `openai/gpt-oss-120b`
- **API Key**: Set in `.env` as `HUGGINGFACE_API_KEY`
- **Client**: OpenAI-compatible SDK with custom `base_url`

### Multi-Turn Conversation

All LLM generators support conversation history:
- First message includes full KG traversal data as context
- Follow-up messages maintain conversation history
- Evidence is flattened to plain-text bullets for smaller models

---

## 5. Backend API Endpoints

### Health & Status
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Health check with model/neo4j status |

### Graph & Visualization
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/graph` | Full graph query (legacy) |
| `GET` | `/api/graph/base` | Base graph (legacy, `:Entity` nodes) |
| `GET` | `/api/v1/graph/overview` | Sampled overview of full KG (new) |
| `GET` | `/api/v1/customers/{id}/graph` | Customer-specific graph payload |

### Customer & RCA
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/customers` | List all customers with data profile |
| `GET` | `/api/v1/customers/{id}/rca` | Full RCA chain for customer |
| `GET` | `/api/v1/customers/{id}/impact` | Financial & service impact |
| `GET` | `/api/v1/customers/{id}/dunning` | Payment failure chain |
| `GET` | `/api/v1/customers/{id}/sla-credits` | SLA credit chain |
| `GET` | `/api/v1/customers/{id}/system-errors` | System log error chain |
| `GET` | `/api/v1/customers/{id}/kpi-breaches` | KPI breach chain |

### Natural Language Query
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/models` | List available narration models |
| `POST` | `/api/v1/query` | NL question → graph traversal → grounded answer |

### Site & Cross-Domain
| Method | Path | Description |
|---|---|---|
| `GET` | `/api/v1/sites/{id}/impact` | Site impact (KPIs, alarms, customers) |
| `GET` | `/api/v1/unresolved` | Cross-domain unresolved issues |

### Vector Search
| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/vector/search` | Semantic search over embedded data |
| `GET` | `/api/v1/vector/stats` | Vector store statistics |
| `DELETE` | `/api/v1/vector/clear` | Clear all vector embeddings |

---

## 6. Frontend Components

### App.jsx
- **Legend**: Uses causal ontology labels (Customer, Account, Invoice, Charge, NetworkFailure, PaymentFailure, Incident, LogEvent, ApiKpiBreach, PmCounter, Dispute, SlaCredit, Site, Service)
- **Initial graph load**: Calls `api.graphOverview()` (NOT `api.graphBase()`)
- **Customer deselect**: Reloads via `api.graphOverview()` (NOT `api.graphBase()`)

### ForceGraph.jsx
- **Node colors**: Each ontology class has a distinct color (see `classColors` map)
- **Node sizes**: Customer/RootCause = 24px, Account/Invoice = 20px, NetworkFailure/Incident/PaymentFailure = 18px
- **Node abbreviations**: 2-3 letter codes for each type

### NLQueryPanel.jsx
- **Model picker**: Dropdown with all configured models, always visible (not disabled when no customer selected)
- **Default model**: `'huggingface'` (GPT-OSS-120B)
- **Model icons**: Color-coded per model (Claude=amber, OpenAI=green, HuggingFace=orange, etc.)
- **Severity badges**: Colorized CRITICAL/HIGH/MEDIUM/LOW tags in answers
- **Entity pills**: Clickable entity ID badges extracted from evidence

### CSVIngestionPanel.jsx
- Batch ingestion UI for CSV files
- Progress tracking per domain
- Schema setup toggle

---

## 7. Configuration & Environment

### Backend/.env

```env
# App
APP_NAME=Telecom RCA Intelligence
APP_ENV=development
FRONTEND_ORIGINS=*

# Neo4j
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=Neo4j2026
NEO4J_DATABASE=neo4j

# LLM Provider Selection
ANSWER_LLM_PROVIDER=openai

# OpenAI
OPENAI_API_KEY=<your-key>
OPENAI_MODEL=gpt-4o-mini
OPENAI_TEMPERATURE=0.4

# Claude (Anthropic)
CLAUDE_API_KEY=<your-key>
CLAUDE_MODEL=claude-sonnet-4-6
CLAUDE_ENABLED=true

# LM Studio (local)
LMSTUDIO_URL=http://192.168.0.114:1234/v1
LMSTUDIO_MODEL=auto
LMSTUDIO_ENABLED=true

# Groq (free cloud, Llama 70B)
GROQ_API_KEY=replace_me
GROQ_MODEL=llama-3.1-70b-versatile
GROQ_ENABLED=false

# HuggingFace (GPT-OSS-120B via novita)
HUGGINGFACE_API_KEY=<your-key>
HUGGINGFACE_MODEL=openai/gpt-oss-120b
HUGGINGFACE_PROVIDER=novita
HUGGINGFACE_ENABLED=true

# Demo fallback
ALLOW_DEMO_FALLBACK=false
```

### Config Priorities

Settings load from `Backend/.env` via `pydantic-settings`. The `config.py` file defines defaults that are overridden by `.env` values.

---

## 8. Key Design Decisions

### 1. Graph is Narration-Only (Grounding Guarantee)

The LLM is a **narration layer only**. It cannot add facts that weren't retrieved from the Knowledge Graph. This is the grounding guarantee. The LLM turns retrieved graph evidence into readable sentences.

### 2. No Event→Customer Direct Edges

Events (NetworkFailure, Incident, etc.) connect to Customer only through the causal chain: `Event → CAUSED_CHARGE → Charge → ON_INVOICE → Invoice → BILLED_TO → Account → OWNED_BY → Customer`. The `customer_id` on events is a **property for filtering**, not an edge.

### 3. Intent Classification

Natural language questions are classified into intents via regex patterns:
- `network_rca`, `kpi_breach`, `dunning_chain`, `sla_credit`, `system_error`, `complaint`, `financial_impact`, `full_chain`, `unresolved`

Each intent maps to specific Cypher traversal queries.

### 4. Neo4j Node Serialization

Raw Neo4j Node objects must be converted to plain dicts via `_props()` helper before JSON serialization. FastAPI's Pydantic serializer cannot handle Neo4j types directly. Use `_props(obj)` (which calls `dict(node)` safely) instead of `dict(obj)`.

### 5. Graph Overview vs Base Graph

- `graphOverview()` → `rca_retrieval_service.overview_graph()` — Uses causal ontology labels (correct)
- `graphBase()` → `neo4j_service.base_graph()` — Uses legacy `:Entity` labels (wrong for new schema)

Always use `graphOverview()` for initial graph loads and customer deselects.

---

## 9. Known Issues & Fixes

### Issue: Graph shows no data / old labels
**Cause**: Graph was ingested with old star-schema labels (BillingAccount, ChargingRecord, Alarm) but code queries new labels (Account, Charge, NetworkFailure).
**Fix**: Clear graph via `DELETE /api/v1/graph/clear` and re-ingest all 9 CSVs via `POST /api/v1/ingest/batch`.

### Issue: PydanticSerializationError for neo4j.graph.Node
**Cause**: Raw Neo4j Node objects passed in response dicts.
**Fix**: Use `_props(obj)` helper in `rca_retrieval_service.py` for all node serialization.

### Issue: Silent fallback to KG Traversal when model fails
**Cause**: Original `_generate_with_fallback()` always fell through to deterministic template.
**Fix**: Rewrote to have two paths — explicit model selection (error on failure) vs auto mode (fallback chain).

### Issue: LM Studio timeout
**Cause**: LM Studio at `192.168.0.114:1234` not reachable (30s timeout).
**Fix**: Start LM Studio with a loaded model before selecting it.

### Issue: HuggingFace 401 Unauthorized
**Cause**: Invalid API key.
**Fix**: Replace `HUGGINGFACE_API_KEY` in `.env` with a valid key.

---

## 10. Running the Project

### Prerequisites
- Python 3.11+
- Node.js 18+
- Neo4j 5.x running on `bolt://localhost:7687`

### Backend

```bash
cd Backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Edit .env with your API keys and Neo4j credentials

python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### Frontend

```bash
cd Frontend
npm install
npm run dev
```

### Data Ingestion

1. Generate synthetic data and ingest:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/synthetic/generate-and-ingest?num_customers=200&events_per_customer=5"
   ```

2. Or ingest from CSV directory:
   ```bash
   curl -X POST http://localhost:8000/api/v1/ingest/batch \
     -H "Content-Type: application/x-www-form-urlencoded" \
     -d "directory=/path/to/csv/folder&setup_schema_first=true&create_cross_links=true"
   ```

3. Verify the graph:
   ```bash
   curl http://localhost:8000/api/v1/verify
   ```

### Clear & Re-ingest (if schema changes)

```bash
# Clear everything
curl -X DELETE http://localhost:8000/api/v1/graph/clear

# Re-ingest
curl -X POST http://localhost:8000/api/v1/ingest/batch \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "directory=/path/to/csv/folder&setup_schema_first=true&create_cross_links=true"
```

---

## 11. Validation & Testing

### Verify Graph Counts

```bash
curl http://localhost:8000/api/v1/verify | python3 -m json.tool
```

### Test Model Endpoints

```bash
# List available models
curl http://localhost:8000/api/v1/models | python3 -m json.tool

# Test specific model
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"What issues does this customer have?","customer_id":"CUST-2343","model":"huggingface"}'

# Test deterministic (KG Traversal)
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"Show all issues","customer_id":"CUST-2343","model":"deterministic"}'
```

### Check Health & Model Status

```bash
curl http://localhost:8000/api/health | python3 -m json.tool
```

Expected output shows each model as "configured" or "unconfigured".

---

## Appendix: File Quick Reference

| File | Purpose |
|---|---|
| `Backend/app/main.py` | All API endpoints, service wiring |
| `Backend/app/config.py` | Pydantic settings, `.env` loading |
| `Backend/app/csv_ingestion_service.py` | CSV → Neo4j causal ontology pipeline |
| `Backend/app/rca_retrieval_service.py` | Cypher traversals, ID-prefix routing |
| `Backend/app/nl_query_service.py` | Multi-model NL query, fallback logic |
| `Backend/app/models.py` | Pydantic request/response models |
| `Backend/app/synthetic_data.py` | Synthetic CSV data generator |
| `Backend/app/vector_service.py` | ChromaDB vector search |
| `Backend/.env` | Environment variables (API keys) |
| `Frontend/src/App.jsx` | Main app, legend, graph, routing |
| `Frontend/src/api.js` | All API endpoint wrappers |
| `Frontend/src/components/ForceGraph.jsx` | D3 force graph visualization |
| `Frontend/src/components/NLQueryPanel.jsx` | RCA chat panel, model picker |
| `Frontend/src/components/CSVIngestionPanel.jsx` | Data ingestion UI |
| `Frontend/src/components/CustomerExplorer.jsx` | Customer 360 view |
