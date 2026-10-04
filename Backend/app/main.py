from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from .config import PROJECT_ROOT, get_settings
from .csv_ingestion_service import CSVIngestionService
from .evaluation_service import ManualEvaluationService
from .ingestion_service import ExcelIngestionService
from .llm_service import LLMService
from .models import (
    CSVBatchIngestionResponse,
    CSVIngestionStats,
    ChatRequest,
    ChatResponse,
    CustomerListResponse,
    CustomerSummary,
    GraphPayload,
    GraphVerificationResponse,
    IngestionResponse,
    LLMAnswerRequest,
    LLMAnswerResponse,
    ManualEvaluationRequest,
    ManualEvaluationResponse,
    NLQueryRequest,
    NLQueryResponse,
    SchemaSetupResponse,
    VectorSearchHit,
    VectorSearchRequest,
    VectorSearchResponse,
    VectorStatsResponse,
)
from .neo4j_service import Neo4jService
from .nl_query_service import NLQueryService
from .qa_service import QAService
from .rca_retrieval_service import RCARetrievalService
from .repository import DemoRepository
from .vector_service import VectorService


settings = get_settings()
demo_repository = DemoRepository(settings.rca_data_dir)
neo4j_service = Neo4jService(settings)
qa_service = QAService(settings, demo_repository, neo4j_service)
ingestion_service = ExcelIngestionService(PROJECT_ROOT, PROJECT_ROOT / "schema.json")
evaluation_service = ManualEvaluationService(qa_service)
llm_service = LLMService(settings, PROJECT_ROOT)

# Vector embedding service (all-MiniLM-L6-v2 + ChromaDB)
vector_service: VectorService | None = None
if settings.vector_embedding_enabled:
    vector_service = VectorService(persist_dir=settings.vector_store_dir)

# CSV ingestion pipeline (now with vector embedding step)
csv_ingestion_service = CSVIngestionService(
    neo4j_uri=settings.neo4j_uri,
    neo4j_user=settings.neo4j_username,
    neo4j_password=settings.neo4j_password,
    neo4j_database=settings.neo4j_database,
    vector_service=vector_service,
)
rca_retrieval_service = RCARetrievalService(
    neo4j_uri=settings.neo4j_uri,
    neo4j_user=settings.neo4j_username,
    neo4j_password=settings.neo4j_password,
    neo4j_database=settings.neo4j_database,
)
nl_query_service = NLQueryService(
    retrieval=rca_retrieval_service,
    openai_api_key=settings.openai_api_key,
    openai_model=settings.openai_model,
    claude_api_key=settings.claude_api_key,
    claude_model=settings.claude_model,
    lmstudio_url=settings.lmstudio_url,
    lmstudio_model=settings.lmstudio_model,
    lmstudio_enabled=settings.lmstudio_enabled,
    groq_api_key=settings.groq_api_key,
    groq_model=settings.groq_model,
    groq_enabled=settings.groq_enabled,
    temperature=settings.openai_temperature,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if neo4j_service._driver is not None:
        neo4j_service._driver.close()
    csv_ingestion_service.close()
    rca_retrieval_service.close()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    neo4j_live = neo4j_service.verify()
    return {
        "status": "healthy",
        "neo4j": "connected" if neo4j_live else "offline",
        "llm_provider": settings.llm_provider,
        "openai": "configured" if settings.llm_provider == "openai" and settings.openai_configured else "unconfigured",
        "ollama": "configured" if settings.ollama_configured else "unconfigured",
        "lmstudio": "configured" if settings.lmstudio_configured else "unconfigured",
        "lmstudio_model": nl_query_service._lmstudio_model if settings.lmstudio_configured else None,
        "groq": "configured" if settings.groq_configured else "unconfigured",
        "groq_model": settings.groq_model if settings.groq_configured else None,
        "answer_llm": "configured" if settings.answer_llm_configured or settings.lmstudio_configured or settings.groq_configured else "kg",
        "vector_store": "enabled" if vector_service is not None else "disabled",
        "embedding_model": "all-MiniLM-L6-v2" if vector_service is not None else None,
        "data": "neo4j" if neo4j_live else "empty",
    }


@app.get("/api/graph", response_model=GraphPayload)
def graph(
    scenario_id: str | None = Query(default=None),
    root_cause: str | None = Query(default=None),
    search: str | None = Query(default=None),
    limit: int = Query(default=180, ge=10, le=1200),
) -> GraphPayload:
    scenario_id = scenario_id if isinstance(scenario_id, str) else None
    root_cause = root_cause if isinstance(root_cause, str) else None
    search = search if isinstance(search, str) else None
    limit = limit if isinstance(limit, int) else 180
    if neo4j_service.verify():
        try:
            return neo4j_service.graph(
                scenario_id=scenario_id,
                root_cause=root_cause,
                search=search,
                limit=limit,
            )
        except Exception as exc:
            if not settings.allow_demo_fallback:
                raise HTTPException(status_code=503, detail="Neo4j query failed") from exc
    return demo_repository.graph(scenario_id=scenario_id, limit=limit)


@app.get("/api/graph/base", response_model=GraphPayload)
def graph_base(limit: int = Query(default=40, ge=5, le=120)) -> GraphPayload:
    if neo4j_service.verify():
        try:
            return neo4j_service.base_graph(limit=limit)
        except Exception as exc:
            if not settings.allow_demo_fallback:
                raise HTTPException(status_code=503, detail="Neo4j base graph query failed") from exc
    return demo_repository.graph(limit=limit)


@app.get("/api/graph/expand", response_model=GraphPayload)
def graph_expand(
    node_id: str = Query(..., min_length=1),
    depth: int = Query(default=1, ge=1, le=2),
    limit: int = Query(default=80, ge=5, le=300),
) -> GraphPayload:
    if neo4j_service.verify():
        try:
            return neo4j_service.expand_graph(node_id=node_id, depth=depth, limit=limit)
        except Exception as exc:
            if not settings.allow_demo_fallback:
                raise HTTPException(status_code=503, detail="Neo4j expansion query failed") from exc
    return demo_repository.graph(limit=limit)


@app.get("/api/scenarios")
def scenarios() -> dict:
    if neo4j_service.verify():
        try:
            return {"source": "neo4j", "scenarios": neo4j_service.scenarios()}
        except Exception as exc:
            if not settings.allow_demo_fallback:
                raise HTTPException(status_code=503, detail="Neo4j scenario query failed") from exc
    return {
        "source": "empty",
        "scenarios": [],
    }


@app.get("/api/root-causes")
def root_causes() -> dict:
    if neo4j_service.verify():
        try:
            return {"source": "neo4j", "root_causes": neo4j_service.root_causes()}
        except Exception as exc:
            if not settings.allow_demo_fallback:
                raise HTTPException(status_code=503, detail="Neo4j root-cause query failed") from exc
    return {
        "source": "empty",
        "root_causes": [],
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest) -> ChatResponse:
    try:
        return qa_service.ask(request.message, request.conversation_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Unable to produce a grounded RCA answer") from exc


@app.get("/api/conversations")
def conversations(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    return {
        "enabled": qa_service.memory.enabled,
        "items": qa_service.memory.list_recent(limit=limit),
    }


@app.get("/api/conversations/{conversation_memory_id}", response_model=ChatResponse)
def conversation_detail(conversation_memory_id: str) -> ChatResponse:
    response = qa_service.memory.response_by_id(conversation_memory_id)
    if not response:
        raise HTTPException(status_code=404, detail="Conversation was not found in memory.")
    return response


@app.post("/api/evaluate/manual", response_model=ManualEvaluationResponse)
def evaluate_manual(request: ManualEvaluationRequest) -> ManualEvaluationResponse:
    try:
        return evaluation_service.evaluate(
            question=request.question,
            kg_answer=request.kg_answer,
            claude_answer=request.claude_answer,
            chatgpt_answer=request.chatgpt_answer,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Unable to evaluate pasted answers") from exc


@app.post("/api/llm/answers", response_model=LLMAnswerResponse)
async def get_llm_answers(request: LLMAnswerRequest) -> LLMAnswerResponse:
    """Get answers from Claude and OpenAI for a given question."""
    try:
        result = await llm_service.get_both_answers(request.question, request.kg_answer, request.context_mode)
        return LLMAnswerResponse(
            success=result.get("success", False),
            claude_answer=result.get("claude"),
            openai_answer=result.get("openai"),
            error=result.get("error"),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to get LLM answers: {str(exc)}") from exc


@app.post("/api/ingest/excel", response_model=IngestionResponse)
def ingest_excel(
    folder_name: str = Form(...),
    files: list[UploadFile] = File(...),
) -> IngestionResponse:
    if not files:
        raise HTTPException(status_code=400, detail="Upload at least one Excel file.")
    try:
        return ingestion_service.ingest(folder_name, files)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Excel ingestion failed.") from exc


# ===========================================================================
# CSV Ingestion Pipeline (Ingestion Pipeline Spec Sections 4, 11, 12)
# ===========================================================================

@app.post("/api/v1/schema/setup", response_model=SchemaSetupResponse)
def setup_schema() -> SchemaSetupResponse:
    """Create Neo4j constraints and indexes for all domain node labels."""
    try:
        constraints = csv_ingestion_service.setup_schema()
        return SchemaSetupResponse(constraints_created=constraints)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Schema setup failed: {exc}") from exc


@app.post("/api/v1/ingest/batch", response_model=CSVBatchIngestionResponse)
def ingest_csv_batch(
    directory: str = Form(...),
    setup_schema_first: bool = Form(default=True),
    create_cross_links: bool = Form(default=True),
) -> CSVBatchIngestionResponse:
    """Batch ingest all CSVs from a directory, setup schema, and create cross-domain links."""
    dir_path = Path(directory)
    if not dir_path.exists():
        raise HTTPException(status_code=400, detail=f"Directory not found: {directory}")

    try:
        schema_setup = []
        if setup_schema_first:
            schema_setup = csv_ingestion_service.setup_schema()

        results = csv_ingestion_service.ingest_directory(dir_path)

        cross_links = {}
        if create_cross_links:
            cross_links = csv_ingestion_service.create_cross_domain_relationships()

        per_domain = []
        total_rows = total_written = total_rejected = total_nodes = total_rels = 0
        for domain, stats in results.items():
            per_domain.append(CSVIngestionStats(**stats.__dict__))
            total_rows += stats.rows_received
            total_written += stats.rows_written
            total_rejected += stats.rows_rejected
            total_nodes += stats.nodes_created
            total_rels += stats.relationships_created

        return CSVBatchIngestionResponse(
            domains_ingested=list(results.keys()),
            total_rows=total_rows,
            total_written=total_written,
            total_rejected=total_rejected,
            total_nodes=total_nodes,
            total_relationships=total_rels,
            cross_domain_links=cross_links,
            per_domain=per_domain,
            schema_setup=schema_setup,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Batch ingestion failed: {exc}") from exc


@app.post("/api/v1/ingest/{domain}", response_model=CSVIngestionStats)
async def ingest_csv_domain(domain: str, file: UploadFile = File(...)) -> CSVIngestionStats:
    """Ingest a single domain CSV into the knowledge graph.

    Valid domains: network, billing, complaints, incident, pm_counters,
    api, logs, sla_credits, payment_failures
    """
    try:
        content = await file.read()
        stats = csv_ingestion_service.ingest_domain(domain, content)
        return CSVIngestionStats(**stats.__dict__)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}") from exc


@app.post("/api/v1/ingest/cross-links")
def create_cross_links() -> dict:
    """Create cross-domain relationships after all CSVs are loaded."""
    try:
        return csv_ingestion_service.create_cross_domain_relationships()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Cross-link creation failed: {exc}") from exc


@app.delete("/api/v1/graph/clear")
def clear_graph() -> dict:
    """Delete all nodes, relationships, constraints, and indexes from the graph."""
    try:
        return csv_ingestion_service.clear_graph()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph clear failed: {exc}") from exc


@app.get("/api/v1/verify", response_model=GraphVerificationResponse)
def verify_graph() -> GraphVerificationResponse:
    """Verify the knowledge graph is loaded correctly."""
    try:
        result = csv_ingestion_service.verify_graph()
        return GraphVerificationResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Verification failed: {exc}") from exc


# ===========================================================================
# Customer & RCA Queries (Ontology Spec Section 4)
# ===========================================================================

@app.get("/api/v1/customers", response_model=CustomerListResponse)
def list_customers() -> CustomerListResponse:
    """List all customers with their data profile and customer type."""
    try:
        rows = csv_ingestion_service.list_customers()
        customers = [CustomerSummary(**r) for r in rows]
        return CustomerListResponse(total=len(customers), customers=customers)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Customer query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/rca")
def customer_rca(customer_id: str) -> dict:
    """Full RCA chain for a customer: alarm -> KPI -> incident -> billing -> complaint -> credit."""
    try:
        return rca_retrieval_service.rca_full_chain(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RCA query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/impact")
def customer_impact(customer_id: str) -> dict:
    """Financial and service impact summary for a customer."""
    try:
        return rca_retrieval_service.customer_impact(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Impact query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/dunning")
def customer_dunning(customer_id: str) -> list:
    """Payment failure -> overdue invoice -> complaint chain."""
    try:
        return rca_retrieval_service.dunning_chain(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Dunning query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/sla-credits")
def customer_sla_credits(customer_id: str) -> list:
    """SLA credit -> incident -> billing charges chain."""
    try:
        return rca_retrieval_service.sla_credit_chain(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"SLA credit query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/system-errors")
def customer_system_errors(customer_id: str) -> list:
    """System log errors -> service disruptions -> billing impact chain."""
    try:
        return rca_retrieval_service.system_error_chain(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"System error query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/kpi-breaches")
def customer_kpi_breaches(customer_id: str) -> list:
    """KPI threshold breaches correlated with incidents."""
    try:
        return rca_retrieval_service.kpi_breach_to_incident(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KPI breach query failed: {exc}") from exc


@app.get("/api/v1/graph/overview")
def graph_overview(limit: int = Query(default=30, ge=5, le=100)) -> dict:
    """Sampled overview of the full KG: top customers with their connected entities."""
    try:
        return rca_retrieval_service.overview_graph(limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Overview graph query failed: {exc}") from exc


@app.get("/api/v1/customers/{customer_id}/graph")
def customer_graph(customer_id: str) -> dict:
    """Raw graph nodes and relationships for visualization."""
    try:
        return rca_retrieval_service.customer_graph_payload(customer_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Graph query failed: {exc}") from exc


@app.get("/api/v1/sites/{site_id}/impact")
def site_impact(site_id: str) -> dict:
    """All KPI breaches, alarms, and affected customers at a site."""
    try:
        return rca_retrieval_service.site_impact(site_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Site impact query failed: {exc}") from exc


@app.get("/api/v1/unresolved")
def unresolved_issues() -> dict:
    """Cross-domain: open complaints + past-due invoices + failed payments."""
    try:
        return rca_retrieval_service.unresolved_issues()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unresolved query failed: {exc}") from exc


# ===========================================================================
# Natural Language Query (Ingestion Pipeline Spec Sections 8, 9.3, 10.3)
# ===========================================================================

@app.get("/api/v1/models")
def list_models() -> dict:
    """List available narration models (Spec Section 8, Table 26)."""
    return {"models": nl_query_service.available_models()}


@app.post("/api/v1/query", response_model=NLQueryResponse)
def nl_query(request: NLQueryRequest) -> NLQueryResponse:
    """Natural language question -> graph traversal -> grounded answer with model selection."""
    try:
        history = [{"role": m.role, "content": m.content} for m in request.conversation_history] if request.conversation_history else []
        result = nl_query_service.query(request.question, request.customer_id, request.model, conversation_history=history)
        return NLQueryResponse(**result)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Query failed: {exc}") from exc


# ===========================================================================
# Vector Search (Ingestion Pipeline Spec Section 4 — Step 5: Embed)
# ===========================================================================

@app.post("/api/v1/vector/search", response_model=VectorSearchResponse)
def vector_search(request: VectorSearchRequest) -> VectorSearchResponse:
    """Semantic similarity search over embedded CSV data using all-MiniLM-L6-v2."""
    if vector_service is None:
        raise HTTPException(status_code=503, detail="Vector embedding is disabled")
    try:
        hits = vector_service.search(
            query=request.query,
            top_k=request.top_k,
            domain=request.domain,
            customer_id=request.customer_id,
        )
        return VectorSearchResponse(
            query=request.query,
            hits=[
                VectorSearchHit(
                    id=h["id"],
                    text=h["text"],
                    score=h["score"],
                    canonical_id=h["metadata"]["canonical_id"],
                    customer_id=h["metadata"]["customer_id"],
                    account_id=h["metadata"]["account_id"],
                    domain=h["metadata"]["domain"],
                    node_label=h["metadata"]["node_label"],
                )
                for h in hits
            ],
            total_hits=len(hits),
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector search failed: {exc}") from exc


@app.get("/api/v1/vector/stats", response_model=VectorStatsResponse)
def vector_stats() -> VectorStatsResponse:
    """Return vector store statistics (chunk count, model, dimensions)."""
    if vector_service is None:
        raise HTTPException(status_code=503, detail="Vector embedding is disabled")
    try:
        return VectorStatsResponse(**vector_service.stats())
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector stats failed: {exc}") from exc


@app.delete("/api/v1/vector/clear")
def vector_clear() -> dict:
    """Clear all vector embeddings from the store."""
    if vector_service is None:
        raise HTTPException(status_code=503, detail="Vector embedding is disabled")
    try:
        count = vector_service.clear()
        return {"deleted_chunks": count}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Vector clear failed: {exc}") from exc
