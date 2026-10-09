from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from .config import PROJECT_ROOT, get_settings
from .csv_ingestion_service import CSVIngestionService
from .evaluation_service import ManualEvaluationService
from .ingestion_service import ExcelIngestionService
from .llm_service import LLMService
from .models import (
    AssistantChatRequest,
    AssistantChatResponse,
    CSVBatchIngestionResponse,
    CSVIngestionStats,
    ChatRequest,
    ChatResponse,
    CustomerListResponse,
    CustomerSummary,
    GraphPayload,
    GraphVerificationResponse,
    IngestionResponse,
    KGCategorySubgraphRequest,
    KGSearchRequest,
    LLMAnswerRequest,
    LLMAnswerResponse,
    ManualEvaluationRequest,
    ManualEvaluationResponse,
    NLQueryRequest,
    NLQueryResponse,
    RCARequest,
    RCAResponse,
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
from .kg_explorer_service import KGExplorerService
from . import rca_pipeline
from . import intent_cascade
from . import schema_registry


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
    huggingface_api_key=settings.huggingface_api_key,
    huggingface_model=settings.huggingface_model,
    huggingface_provider=settings.huggingface_provider,
    huggingface_enabled=settings.huggingface_enabled,
    temperature=settings.openai_temperature,
)

# KG Explorer service
kg_explorer_service = KGExplorerService(
    neo4j_uri=settings.neo4j_uri,
    neo4j_user=settings.neo4j_username,
    neo4j_password=settings.neo4j_password,
    neo4j_database=settings.neo4j_database,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if neo4j_service._driver is not None:
        neo4j_service._driver.close()
    csv_ingestion_service.close()
    rca_retrieval_service.close()
    kg_explorer_service.close()


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
        "huggingface": "configured" if settings.huggingface_configured else "unconfigured",
        "huggingface_model": settings.huggingface_model if settings.huggingface_configured else None,
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


@app.get("/api/v1/synthetic/generate")
def generate_synthetic_data(
    num_customers: int = Query(default=10, ge=1, le=200),
    events_per_customer: int = Query(default=5, ge=1, le=50),
) -> Response:
    """Generate a ZIP of synthetic CSV data for all 9 domains."""
    from .synthetic_data import generate_zip

    zip_bytes = generate_zip(num_customers, events_per_customer)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=synthetic_telecom_data.zip"},
    )


@app.post("/api/v1/synthetic/generate-and-ingest")
def generate_and_ingest(
    num_customers: int = Query(default=10, ge=1, le=200),
    events_per_customer: int = Query(default=5, ge=1, le=50),
) -> dict:
    """Generate synthetic data and ingest it directly into Neo4j."""
    from .synthetic_data import generate_synthetic_data as gen_data

    csvs = gen_data(num_customers, events_per_customer)
    results = {}
    errors = []
    for domain, csv_content in csvs.items():
        try:
            stats = csv_ingestion_service.ingest_domain(domain, csv_content)
            results[domain] = {
                "nodes_created": stats.nodes_created,
                "relationships_created": stats.relationships_created,
                "rows_received": stats.rows_received,
                "rows_rejected": stats.rows_rejected,
            }
        except Exception as exc:
            errors.append({"domain": domain, "error": str(exc)})

    # Create cross-domain links
    cross_links = {}
    try:
        cross_links = csv_ingestion_service.create_cross_domain_relationships()
    except Exception as exc:
        errors.append({"domain": "cross-links", "error": str(exc)})

    total_nodes = sum(r.get("nodes_created", 0) for r in results.values())
    total_rels = sum(r.get("relationships_created", 0) for r in results.values())
    return {
        "status": "completed",
        "num_customers": num_customers,
        "events_per_customer": events_per_customer,
        "domains_ingested": list(results.keys()),
        "domain_stats": results,
        "cross_links": cross_links,
        "total_nodes_created": total_nodes,
        "total_relationships_created": total_rels,
        "errors": errors,
    }


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


# ===========================================================================
# RCA Pipeline (Retrieval Pipeline Spec Section 8, 14.2)
# ===========================================================================

@app.post("/api/v1/rca")
def rca_analyze(request: RCARequest) -> dict:
    """Run the multi-step RCA pipeline for one customer or ALL (Sections 8-9)."""
    try:
        # Build vector search function if available
        vs_fn = None
        if vector_service is not None:
            def _vs(query, customer_id=None, top_k=10):
                hits = vector_service.search(query=query, customer_id=customer_id, top_k=top_k)
                return hits
            vs_fn = _vs

        specialists = intent_cascade.dispatch_specialists(request.billing_issue_description or "full investigation")

        # Build narrator function from the NL query service's LLM
        narrator = nl_query_service.narrate

        result = rca_pipeline.run_rca_pipeline(
            retrieval=rca_retrieval_service,
            customer_id=request.customer_id,
            question=request.billing_issue_description or "What is the root cause?",
            specialists_to_dispatch=specialists,
            vector_search_fn=vs_fn,
            narrator_fn=narrator,
            debug=request.debug,
        )
        return result
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"RCA pipeline failed: {exc}") from exc


@app.get("/api/v1/rca/history")
def rca_history(
    mode: str | None = Query(default=None),
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """List past RCA runs, filterable by mode/customer."""
    results = rca_pipeline.get_rca_history(mode=mode, customer_id=customer_id, limit=limit)
    return {"total": len(results), "history": results}


@app.get("/api/v1/rca/history/{request_id}")
def rca_history_detail(request_id: str) -> dict:
    """Fetch one saved RCA history entry."""
    result = rca_pipeline.get_rca_by_id(request_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"RCA request {request_id} not found")
    return result


@app.get("/api/v1/rca/{request_id}/evidence/{entity_id}")
def rca_evidence_entity(request_id: str, entity_id: str) -> dict:
    """Look up one specific evidence entity from a past RCA response."""
    result = rca_pipeline.get_rca_evidence_entity(request_id, entity_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Evidence entity {entity_id} not found in RCA {request_id}")
    return result


@app.post("/api/v1/rca/compare-models")
def rca_compare_models(request: RCARequest) -> dict:
    """Run the same RCA through multiple models for comparison."""
    try:
        results = {}
        for model_id in ["deterministic", "claude", "openai", "huggingface"]:
            try:
                # Use the NL query service with each model
                result = nl_query_service.query(
                    question=request.billing_issue_description or "What is the root cause?",
                    customer_id=request.customer_id,
                    model=model_id,
                )
                results[model_id] = {
                    "answer": result.get("answer", ""),
                    "model_used": result.get("model_used", model_id),
                    "confidence": result.get("confidence", "low"),
                    "response_time_ms": result.get("response_time_ms", 0),
                }
            except Exception as exc:
                results[model_id] = {"error": str(exc)}
        return {"customer_id": request.customer_id, "comparisons": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Model comparison failed: {exc}") from exc


# ===========================================================================
# Assistant Chat (Retrieval Pipeline Spec Section 12.4, 14.1)
# ===========================================================================

@app.post("/api/v1/assistant/chat")
def assistant_chat(request: AssistantChatRequest) -> dict:
    """Conversational wrapper with grounding badges and reason_codes."""
    import time as _time
    start = _time.time()

    try:
        # Run intent cascade
        intent_result = intent_cascade.detect_intent(request.question)

        # Meta/capability questions — answer without model
        if intent_result.is_meta:
            badge = "About this app"
            answer = (
                "I'm the EzInsights Telecom RCA Assistant. I can help you with:\n\n"
                "- **Invoice & billing questions** — totals, breakdowns, charge explanations\n"
                "- **Root cause analysis** — why charges appeared, network/payment failures\n"
                "- **Dispute & complaint status** — track open issues\n"
                "- **KPI & performance** — threshold breaches, degradation\n"
                "- **SLA credits** — compensation for service disruptions\n\n"
                "Ask me anything about a specific customer (e.g., CUST-4367) or across all customers."
            )
            return {
                "answer": answer,
                "intent": "meta",
                "customer_id": request.customer_id,
                "evidence": [],
                "reason_codes": intent_result.reason_codes,
                "recommended_next_step": "Try asking about a specific customer's billing or network issues.",
                "grounding_badge": badge,
                "confidence": 1.0,
                "requires_human_action": False,
                "narrated_by": "deterministic",
                "model_used": "none",
                "response_time_ms": int((_time.time() - start) * 1000),
                "token_usage": {},
            }

        # Open-domain chat fallback — model's own knowledge
        if intent_result.is_open_domain:
            history = [{"role": m.role, "content": m.content} for m in request.conversation_history] if request.conversation_history else []
            result = nl_query_service.query(
                request.question, request.customer_id, request.model,
                conversation_history=history,
            )
            return {
                **result,
                "reason_codes": ["GENERAL_CHAT"],
                "grounding_badge": "Model's own knowledge (not grounded in the KG)",
                "recommended_next_step": "For grounded answers, ask about specific billing or network issues.",
                "requires_human_action": False,
                "narrator_error": None,
            }

        # Grounded query — run through NL query service
        history = [{"role": m.role, "content": m.content} for m in request.conversation_history] if request.conversation_history else []
        result = nl_query_service.query(
            request.question, request.customer_id, request.model,
            conversation_history=history,
        )

        # Determine grounding badge
        evidence = result.get("evidence", {})
        has_evidence = any(
            (isinstance(v, list) and len(v) > 0) or (isinstance(v, dict) and v)
            for v in evidence.values()
        ) if isinstance(evidence, dict) else bool(evidence)

        if has_evidence:
            badge = "Grounded in Knowledge Graph"
            reason_codes = [intent_result.intent.upper()]
        else:
            badge = "Knowledge Graph lookup — no matching data found"
            reason_codes = ["NO_DATA_FOUND"]

        # Confidence: cap at 0.4 when evidence is empty (Section 16)
        confidence = 0.85 if has_evidence else 0.4
        requires_human_action = not has_evidence

        # Recommended next step
        next_step = None
        if not has_evidence:
            next_step = "Try a more specific question or check the customer ID."
        elif intent_result.is_investigative:
            next_step = "Use the RCA pipeline for a deeper multi-step investigation."

        return {
            **result,
            "reason_codes": reason_codes,
            "grounding_badge": badge,
            "recommended_next_step": next_step,
            "confidence": confidence,
            "requires_human_action": requires_human_action,
            "narrator_error": None,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Assistant chat failed: {exc}") from exc


# ===========================================================================
# KG Explorer (Retrieval Pipeline Spec Section 10, 14.3)
# ===========================================================================

@app.get("/api/v1/kg/search")
def kg_search(
    query: str = Query(default=""),
    categories: str = Query(default=""),
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """KG Explorer text/category search (Section 10)."""
    try:
        cats = [c.strip() for c in categories.split(",") if c.strip()] if categories else None
        return kg_explorer_service.search(query=query, categories=cats, customer_id=customer_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KG search failed: {exc}") from exc


@app.get("/api/v1/kg/neighborhood")
def kg_neighborhood(
    node_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """1-hop drill-down from one node (Section 10)."""
    try:
        return kg_explorer_service.neighborhood(node_id=node_id, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KG neighborhood failed: {exc}") from exc


@app.get("/api/v1/kg/subgraph")
def kg_subgraph(
    entity_ids: str = Query(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=300),
) -> dict:
    """Merged 1-hop neighborhoods for a list of entity IDs (Section 10)."""
    try:
        ids = [eid.strip() for eid in entity_ids.split(",") if eid.strip()]
        return kg_explorer_service.subgraph(entity_ids=ids, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KG subgraph failed: {exc}") from exc


@app.get("/api/v1/kg/category-subgraph")
def kg_category_subgraph(
    categories: str = Query(..., min_length=1),
    customer_id: str | None = Query(default=None),
    depth: int = Query(default=1, ge=1, le=4),
    limit_per_root: int = Query(default=15, ge=1, le=50),
    max_roots: int = Query(default=40, ge=1, le=100),
) -> dict:
    """Deep, user-adjustable multi-hop category filter (Section 10.1)."""
    try:
        cats = [c.strip() for c in categories.split(",") if c.strip()]
        return kg_explorer_service.category_subgraph(
            categories=cats, customer_id=customer_id,
            depth=depth, limit_per_root=limit_per_root, max_roots=max_roots,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"KG category subgraph failed: {exc}") from exc


@app.get("/api/v1/kg/filter-categories")
def kg_filter_categories() -> dict:
    """The curated category list for the KG Explorer UI (Section 10.1)."""
    return {"categories": KGExplorerService.filter_categories()}


# ===========================================================================
# Schema Registry (Retrieval Pipeline Spec Section 4.1)
# ===========================================================================

@app.get("/api/v1/schema/registry")
def get_schema_registry() -> dict:
    """Return the full schema registry for inspection/debugging."""
    return schema_registry.registry_for_generation()


# ===========================================================================
# Semantic Search (Retrieval Pipeline Spec Section 11)
# ===========================================================================

@app.get("/api/v1/semantic-search")
def semantic_search(
    query: str = Query(..., min_length=2, max_length=500),
    customer_id: str | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Standalone ontology vector search (Section 11)."""
    if vector_service is None:
        raise HTTPException(status_code=503, detail="Vector embedding is disabled")
    try:
        hits = vector_service.search(query=query, customer_id=customer_id, top_k=limit)
        return {
            "query": query,
            "hits": hits,
            "total_hits": len(hits),
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Semantic search failed: {exc}") from exc


# ===========================================================================
# Triage / Proactive Scan (Retrieval Pipeline Spec Section 14)
# ===========================================================================

@app.post("/api/v1/triage/scan")
def triage_scan(
    limit: int = Query(default=10, ge=1, le=50),
) -> dict:
    """Proactively scan for untriaged anomalies and run RCA on each."""
    try:
        # Find unresolved issues
        unresolved = rca_retrieval_service.unresolved_issues()

        # Run RCA on top issues
        results = []
        customers_seen = set()
        for category in ["open_disputes", "past_due_invoices", "failed_payments"]:
            items = unresolved.get(category, [])
            for item in items[:limit]:
                if isinstance(item, dict):
                    cid = item.get("customer_id", "")
                    if cid and cid not in customers_seen:
                        customers_seen.add(cid)
                        try:
                            rca_result = rca_pipeline.run_rca_pipeline(
                                retrieval=rca_retrieval_service,
                                customer_id=cid,
                                question="Proactive triage scan: what is the root cause of unresolved issues?",
                            )
                            results.append({
                                "customer_id": cid,
                                "trigger": category,
                                "rca_result": rca_result,
                            })
                        except Exception:
                            pass
                if len(results) >= limit:
                    break

        return {"scanned": len(results), "triage_results": results}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Triage scan failed: {exc}") from exc


# ===========================================================================
# Incidents Audit Trail (Retrieval Pipeline Spec Section 14)
# ===========================================================================

@app.get("/api/v1/incidents")
def list_incidents(
    limit: int = Query(default=50, ge=1, le=200),
) -> dict:
    """Audit trail of logged incidents (RCA requests + triage results)."""
    # Use RCA history as the incident store (in-memory for PoC)
    history = rca_pipeline.get_rca_history(limit=limit)
    incidents = []
    for h in history:
        incidents.append({
            "incident_id": h.get("request_id", ""),
            "customer_id": h.get("customer_id", ""),
            "type": "rca_request",
            "primary_cause": h.get("primary_cause"),
            "confidence": h.get("confidence", 0),
            "requires_escalation": h.get("requires_human_escalation", False),
            "timestamp": h.get("timestamp", ""),
        })
    return {"total": len(incidents), "incidents": incidents}
