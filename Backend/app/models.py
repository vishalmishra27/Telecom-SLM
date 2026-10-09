from typing import Any, Literal

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    name: str
    ontology_class: str
    source_system: str = ""
    scenario_id: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphRelationship(BaseModel):
    id: str
    source: str
    target: str
    type: str
    ontology_property: str
    source_system: str = ""
    scenario_id: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphPayload(BaseModel):
    nodes: list[GraphNode]
    relationships: list[GraphRelationship]
    source: Literal["neo4j", "demo"]
    total_nodes: int
    total_relationships: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=2, max_length=2000)
    conversation_id: str | None = None


class IngestionFileSummary(BaseModel):
    filename: str
    sheets: int
    rows: int


class IngestionResponse(BaseModel):
    folder: str
    nodes_path: str
    relationships_path: str
    node_count: int
    relationship_count: int
    files: list[IngestionFileSummary]
    warnings: list[str] = Field(default_factory=list)


class ManualEvaluationRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    kg_answer: str = Field(default="", max_length=30000)
    claude_answer: str = Field(default="", max_length=30000)
    chatgpt_answer: str = Field(default="", max_length=30000)


class AnswerEvaluation(BaseModel):
    name: str
    score: int
    token_estimate: int
    source_fact_overlap: int
    supported_facts: list[str] = Field(default_factory=list)
    missing_facts: list[str] = Field(default_factory=list)
    unsupported_claims: list[str] = Field(default_factory=list)
    outside_content_ratio: float
    verdict: str
    # Telecom-specific benchmark metrics
    grounding_score: int = 0          # 0-100: % of claims backed by KG evidence
    entity_id_accuracy: int = 0       # 0-100: % of cited IDs that are real
    cited_ids: list[str] = Field(default_factory=list)
    invalid_ids: list[str] = Field(default_factory=list)


class ManualEvaluationResponse(BaseModel):
    question: str
    winner: str
    real_facts: list[str]
    source_answer: str
    evaluations: list[AnswerEvaluation]


class EvidenceItem(BaseModel):
    label: str
    value: str
    source: str
    confidence: str = "kg"


class TraversalStep(BaseModel):
    order: int
    from_entity: str
    relationship: str
    to_entity: str
    from_class: str = ""
    to_class: str = ""
    explanation: str = ""


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class ChatResponse(BaseModel):
    conversation_id: str
    message_id: str
    answer: str
    intent: str
    scenario_id: str
    confidence: float
    evidence: list[EvidenceItem]
    entities: list[GraphNode]
    relationships: list[GraphRelationship]
    traversal: list[TraversalStep]
    graph: GraphPayload
    generated_by: Literal["openai", "ollama", "kg"]
    token_usage: TokenUsage | None = None
    repeated_question: bool = False
    repeated_of: str = ""
    first_asked_at: str = ""


class LLMAnswerRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    kg_answer: str | None = Field(default=None, max_length=30000)
    context_mode: str = Field(default="kg_context")


class LLMAnswerResponse(BaseModel):
    success: bool
    claude_answer: str | None = None
    openai_answer: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# CSV Ingestion Models (Ingestion Pipeline Spec Sections 4, 12)
# ---------------------------------------------------------------------------

class CSVIngestionStats(BaseModel):
    domain: str
    rows_received: int = 0
    rows_written: int = 0
    rows_rejected: int = 0
    nodes_created: int = 0
    relationships_created: int = 0
    chunks_embedded: int = 0
    rejected_details: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CSVBatchIngestionResponse(BaseModel):
    domains_ingested: list[str]
    total_rows: int
    total_written: int
    total_rejected: int
    total_nodes: int
    total_relationships: int
    cross_domain_links: dict[str, int] = Field(default_factory=dict)
    per_domain: list[CSVIngestionStats] = Field(default_factory=list)
    schema_setup: list[str] = Field(default_factory=list)


class SchemaSetupResponse(BaseModel):
    constraints_created: list[str]


class GraphVerificationResponse(BaseModel):
    node_counts: dict[str, int]
    relationship_counts: dict[str, int]
    cross_domain_links: dict[str, Any]
    total_customers: int


# ---------------------------------------------------------------------------
# Customer & RCA Query Models (Ontology Spec Section 4)
# ---------------------------------------------------------------------------

class CustomerSummary(BaseModel):
    customer_id: str
    account_id: str
    customer_type: str
    total_events: int
    alarms: int = 0
    invoices: int = 0
    complaints: int = 0
    disruptions: int = 0
    pm_counters: int = 0
    kpi_observations: int = 0
    logs: int = 0
    adjustments: int = 0
    payments: int = 0


class CustomerListResponse(BaseModel):
    total: int
    customers: list[CustomerSummary]


class ConversationMessage(BaseModel):
    role: str = Field(description="'user' or 'assistant'")
    content: str


class NLQueryRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    customer_id: str | None = None
    model: str | None = Field(default=None, description="Model to use: 'claude', 'openai', 'deterministic', or null for auto fallback")
    conversation_history: list[ConversationMessage] = Field(default_factory=list, description="Previous conversation messages for multi-turn context")


class NLQueryResponse(BaseModel):
    answer: str
    intent: str
    customer_id: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    graph: dict[str, Any] = Field(default_factory=dict)
    traversal: dict[str, Any] = Field(default_factory=dict)
    model_used: str = ""
    grounded: bool = False
    confidence: str = "medium"
    citations: list[dict[str, str]] = Field(default_factory=list)
    response_time_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Vector Search Models (Ingestion Pipeline Spec Section 4 — Step 5: Embed)
# ---------------------------------------------------------------------------

class VectorSearchHit(BaseModel):
    id: str
    text: str
    score: float
    canonical_id: str
    customer_id: str
    account_id: str
    domain: str
    node_label: str


class VectorSearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    top_k: int = Field(default=10, ge=1, le=50)
    domain: str | None = None
    customer_id: str | None = None


class VectorSearchResponse(BaseModel):
    query: str
    hits: list[VectorSearchHit]
    total_hits: int
    embedding_model: str = "all-MiniLM-L6-v2"


class VectorStatsResponse(BaseModel):
    collection: str
    total_chunks: int
    embedding_model: str
    embedding_dim: int
    persist_dir: str


# ---------------------------------------------------------------------------
# RCA Pipeline Models (Retrieval Pipeline Spec Sections 8, 14.2)
# ---------------------------------------------------------------------------

class RCARequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    billing_issue_description: str = Field(default="", max_length=5000)
    billing_period: str | None = None
    debug: bool = False


class RCAEvidenceItem(BaseModel):
    entity_id: str
    entity_type: str
    fact: str
    source_system: str = "Knowledge Graph"
    similarity_score: float | None = None
    remediation_status: str | None = None


class RCARootCause(BaseModel):
    cause_category: str
    severity: str
    evidence_entity_ids: list[str] = Field(default_factory=list)
    description: str = ""
    recommended_actions: list[str] = Field(default_factory=list)


class RCAResponse(BaseModel):
    request_id: str
    customer_id: str
    root_causes: list[RCARootCause] = Field(default_factory=list)
    primary_cause: dict[str, Any] | None = None
    recommended_actions: list[str] = Field(default_factory=list)
    evidence: list[RCAEvidenceItem] = Field(default_factory=list)
    narrative: str = ""
    confidence: float = 0.0
    requires_human_escalation: bool = False
    narrated_by: str = "deterministic"
    response_time_ms: int = 0
    debug: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Assistant Chat Models (Retrieval Pipeline Spec Section 12.4, 14.1)
# ---------------------------------------------------------------------------

class AssistantChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=2000)
    customer_id: str | None = None
    model: str | None = None
    conversation_history: list[ConversationMessage] = Field(default_factory=list)


class AssistantChatResponse(BaseModel):
    answer: str
    intent: str
    customer_id: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)
    recommended_next_step: str | None = None
    grounding_badge: str = "Grounded in Knowledge Graph"
    confidence: float = 0.85
    requires_human_action: bool = False
    narrated_by: str = "deterministic"
    model_used: str = ""
    response_time_ms: int = 0
    token_usage: dict[str, int] = Field(default_factory=dict)
    narrator_error: str | None = None


# ---------------------------------------------------------------------------
# KG Explorer Models (Retrieval Pipeline Spec Section 10, 14.3)
# ---------------------------------------------------------------------------

class KGExplorerNode(BaseModel):
    key: str
    label: str
    id: str
    display: str
    props: dict[str, Any] = Field(default_factory=dict)


class KGExplorerEdge(BaseModel):
    from_key: str = Field(alias="from")
    to_key: str = Field(alias="to")
    rel_type: str

    model_config = {"populate_by_name": True}


class KGSearchRequest(BaseModel):
    query: str = Field(default="", max_length=500)
    categories: list[str] = Field(default_factory=list)
    customer_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class KGCategorySubgraphRequest(BaseModel):
    categories: list[str] = Field(min_length=1)
    customer_id: str | None = None
    depth: int = Field(default=1, ge=1, le=4)
    limit_per_root: int = Field(default=15, ge=1, le=50)
    max_roots: int = Field(default=40, ge=1, le=100)
