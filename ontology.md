# Task: add a "Telecom Ontology" tab to the frontend

## Context
Existing tabs: Q&A, Ingestion, Benchmark, Data Foundation.
Add a 5th tab, "Telecom Ontology", matching the existing tab components, styling and
routing conventions. Read the current tab implementations first and follow them —
do not introduce a new UI library or styling approach.

Source file: `Telecom_Conceptual_Ontology_v4.xlsx` (KPMG Telecom Conceptual Ontology
v1.0.0, IRI `urn:kpmg:telecom:ontology`).

## Step 1 — Build-time conversion, not runtime parsing

Write `scripts/build-ontology.js` (or `.py`) that reads the xlsx once and emits
static JSON into `public/data/ontology/`. The frontend must NOT parse xlsx at runtime.

**Critical parsing detail — header rows differ per sheet:**
- Sheets 1–6: row 1 = title, row 2 = subtitle, row 3 = blank, **row 4 = header**, data from row 5
- Sheets 7–10: **row 1 = header**, data from row 2

| Sheet | Emit as | Rows | Key columns |
|---|---|---|---|
| Top-Level Classes | `top-level.json` | 5 | Level, Layer, Class, Definition, Example / Sample Data, RCA Role, Failure Prediction Role |
| Level 2 - Subclasses | `classes.json` | 630 | Top Domain, Entity ID, Domain Code, Telecom Domain, Sub-Class / Node, Top Class, Master Concept, Primary Standard, Definition, Source System, RCA, Failure Prediction, Network Assurance |
| Level 3 - Fields & Sub-Types | `fields.json` | 8,583 | Top Domain, Entity ID, Entity / Class, Field / Property, Type, Definition, RCA |
| Relationship Types | `relationship-types.json` | 339 | Relationship (KG Style), Original Predicate, Definition / Meaning, Occurrences, RCA |
| Relationship Mapping | `relationship-mapping.json` | 603 | Source Class, Relationship, Target Class, Definition, RCA, Cypher Pattern |
| Use Case Segmentation | `use-cases.json` | 4 | Use Case ID, Use Case, Definition, Conceptual Traversal, Primary Domains, Current Relevance |
| RCA KPI Catalog | `kpi-catalog.json` | 19 | KPI Name, Domain, Unit, RCA Usage, Possible RCA Hypotheses |
| RCA Alarm Cause Catalog | `alarm-catalog.json` | 14 | Probable Cause, Domain, Default Severity, Definition, Recommended First Check |
| OWL Conversion Rules | `owl-rules.json` | 24 | Setting, Value, Implementation meaning |
| OWL Datatype Mapping | `owl-datatypes.json` | 7 | Workbook Type, OWL/XSD Range, SHACL Datatype, Notes |

Also emit a precomputed `summary.json` so the UI does no aggregation on load:
```json
{
  "version": "1.0.0",
  "iri": "urn:kpmg:telecom:ontology",
  "topLevelClasses": 5,
  "domains": 24,
  "classes": 630,
  "fields": 8583,
  "relationshipTypes": 339,
  "relationshipMappings": 603,
  "rca": { "classes": 163, "fields": 1985, "relationshipTypes": 85, "relationshipMappings": 185 },
  "byDomain": [ { "code": "05", "name": "05 Network", "classes": 55, "rcaClasses": 15 }, ... ]
}
```

`fields.json` is 8.5k rows — split it per Top Domain (`fields/customer.json`,
`fields/service.json`, etc.) and lazy-load on class expand. Do not ship it in the
initial bundle.

## Step 2 — Tab layout (top to bottom)

**1. Stat strip** — from `summary.json`: 5 top-level classes · 24 domains · 630
classes · 8,583 properties · 339 relationship types · 603 mapped relationships.
Small caption: `Telecom Conceptual Ontology v1.0.0 — urn:kpmg:telecom:ontology`.

**2. Five top-level classes** — five cards, the hero visual. For each show the class
name, definition, and the RCA Role verbatim from the sheet:
| Class | RCA Role |
|---|---|
| Customer | Impact, SLA exposure, customer context |
| Service | Service degradation, outage, restoration |
| Process | RCA chain and remediation workflow |
| People | Assignment, escalation, remediation owner |
| Product | Fault location and product/resource impact |

**3. RCA lens toggle** — a single prominent switch, default OFF. When ON, filter
every section below to rows where `RCA === "Yes"`. Show the delta live:
630 → 163 classes, 339 → 85 relationship types. This is the most important
interaction on the tab; make it obvious.

**4. Domain explorer** — 24 domain chips from `byDomain` with class counts.
Selecting a chip filters the class table. Class table columns: Sub-Class / Node,
Top Class, Master Concept, Primary Standard, Source System, RCA badge.
Row click opens a side panel with the definition and that class's Level 3
properties (Field, Type, Definition) plus its incoming/outgoing relationships
from `relationship-mapping.json`.

**5. Relationship browser** — searchable table of the 339 predicates:
Relationship (KG Style), Definition, Occurrences, RCA badge. Sort by Occurrences
desc by default. Row expands to the source→target pairs and the Cypher Pattern
string, rendered in a monospace code block.

**6. RCA traversal path** — render UC001 from `use-cases.json` as a horizontal
chevron flow:
`Alarm → Incident/ServiceProblem → RootCauseAnalysis → Resolution → RemediationAction → VerificationCheck`
Caption it as the traversal the Q&A tab actually executes. Show UC002 (Failure
Prediction) and UC003 (Network Assurance) as smaller secondary rows.

**7. RCA catalogs** — two side-by-side tables, collapsed by default:
19 KPIs (name, domain, unit, RCA usage, hypotheses) and 14 alarm probable causes
(cause, domain, severity, recommended first check). These are the ontology's
grounded diagnostic vocabulary — label them as such.

**8. Standards & OWL footer** — badge row of distinct Primary Standard values from
`classes.json` (TM Forum SID/eTOM/ODA, 3GPP TS 28.541, 3GPP TS 33.x, O-RAN,
ETSI NFV, MEF LSO, oneM2M/SAREF, ITIL 4, GSMA Open Telco, TRAI/DoT/DPDP).
Below it, a collapsed panel rendering `owl-rules.json` and `owl-datatypes.json`
as plain tables, with a "Download OWL/JSON" button serving the raw JSON.

## Step 3 — Constraints
- Client-side filtering only, no backend calls
- Virtualise the class and relationship tables (630 and 603 rows)
- Every table needs a text search box and a CSV export button
- Presentation context: this will be shown on a projector to a client. Minimum
  14px body text, high contrast, no dense tooltips that require hover
- Do not invent content. Every string rendered must trace to a cell in the xlsx.
  If a cell is blank, render an em dash, not a placeholder sentence

## Step 4 — Verify before you finish
Assert in the build script and fail loudly on mismatch:
`5 / 630 / 8583 / 339 / 603 / 4 / 19 / 14 / 24 / 163 / 85`
(top-level, classes, fields, rel types, rel mappings, use cases, KPIs,
alarm causes, domains, RCA classes, RCA rel types)