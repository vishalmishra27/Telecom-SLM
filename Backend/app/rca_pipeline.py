"""
Multi-Step RCA Pipeline — Supervisor/Specialists/Critic

Spec Section 8: Shape 4 — Multi-Step Hybrid KG + Vector Retrieval (Per-Customer RCA)

A small, auditable, in-house planner/worker/critic pipeline.
Deliberately NOT built on a heavyweight agent framework.

Steps (Section 8.1):
1. Supervisor (plan) — decide which specialists to dispatch
2. Specialists (dispatched) — each runs one domain-scoped KG query
3. Critic (sufficiency check) — any correlating evidence found?
4. Supervisor (bounded retry) — widen window if needed, only dispatched specialists
5. Synthesis — correlate evidence into root-cause candidates by FIXED RULES
6. Critic (grounding validation) — strip ungrounded citations
7. Assemble response
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from .intent_cascade import dispatch_specialists
from .rca_retrieval_service import RCARetrievalService


# ---------------------------------------------------------------------------
# Blackboard — shared context that every step reads/writes to
# ---------------------------------------------------------------------------

@dataclass
class Blackboard:
    """Shared context for the RCA pipeline."""
    request_id: str = ""
    customer_id: str = ""
    question: str = ""
    billing_period: str | None = None
    specialists_dispatched: list[str] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    root_causes: list[RootCauseCandidate] = field(default_factory=list)
    trace: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    max_iterations: int = 2
    total_customers: int = 0


@dataclass
class EvidenceItem:
    entity_id: str
    entity_type: str
    fact: str
    source_system: str = "Knowledge Graph"
    specialist: str = ""
    similarity_score: float | None = None
    remediation_status: str | None = None  # "RESOLVED via X at T" or "unresolved"
    traversal_path: str = ""  # e.g. "CUST-1234 → ACC-5678 → INV-91011 → CHG-1213"


@dataclass
class RootCauseCandidate:
    cause_category: str   # NETWORK_FAILURE, PAYMENT_FAILURE, SERVICE_DISRUPTION, etc.
    severity: str         # CRITICAL, HIGH, MEDIUM, LOW
    evidence_entity_ids: list[str] = field(default_factory=list)
    description: str = ""
    recommended_actions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Cause-category mapping (fixed rules — Section 12.2)
# ---------------------------------------------------------------------------

_ENTITY_TYPE_TO_CAUSE = {
    "NetworkFailure": ("NETWORK_FAILURE", "HIGH"),
    "PaymentFailure": ("PAYMENT_FAILURE", "MEDIUM"),
    "Incident": ("SERVICE_DISRUPTION", "HIGH"),
    "LogEvent": ("SYSTEM_ERROR", "MEDIUM"),
    "ApiKpiBreach": ("KPI_DEGRADATION", "MEDIUM"),
    "Dispute": ("BILLING_DISPUTE", "LOW"),
}

_CAUSE_ACTIONS = {
    "NETWORK_FAILURE": [
        "Escalate to Network Operations Center (NOC) for site investigation",
        "Check PM counters for corroborating performance degradation",
        "Verify if SLA credit applies for affected service period",
    ],
    "PAYMENT_FAILURE": [
        "Verify payment method validity with the customer",
        "Check for dunning process status and overdue invoices",
        "Offer alternative payment arrangements if applicable",
    ],
    "SERVICE_DISRUPTION": [
        "Check if incident has been resolved and service restored",
        "Verify SLA credit has been applied if eligible",
        "Review incident correlation with any network failures",
    ],
    "SYSTEM_ERROR": [
        "Review system logs for error patterns and root cause",
        "Check if mediation/re-rating is needed for affected charges",
        "Escalate to platform engineering if infrastructure-level",
    ],
    "KPI_DEGRADATION": [
        "Review KPI thresholds and current breach status",
        "Correlate with network failures at the same site/time",
        "Check if performance SLA applies",
    ],
    "BILLING_DISPUTE": [
        "Review dispute details and customer's claimed resolution",
        "Verify charge validity against network/service evidence",
        "Process credit or explain charge justification to customer",
    ],
}


# ---------------------------------------------------------------------------
# Specialists — each runs one domain-scoped KG query
# ---------------------------------------------------------------------------

def _run_network_specialist(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Network specialist: Alarms/NetworkFailures for this customer."""
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        # Extract NetworkFailure roots from the roots list (categorized by type)
        for root in chain.get("roots", []):
            if isinstance(root, dict) and root.get("type") == "NetworkFailure":
                eid = root.get("id", "")
                status = None
                if root.get("remediation_status"):
                    status = f"RESOLVED: {root['remediation_status']}"
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="NetworkFailure",
                    fact=f"Network failure {eid}: type={root.get('failure_type','?')}, severity={root.get('severity','?')}, site={root.get('affected_site','?')}",
                    specialist="network",
                    remediation_status=status or "unresolved",
                ))
        # Also include PM counter evidence
        for pm in chain.get("pm_evidence", []):
            if isinstance(pm, dict):
                eid = pm.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="PmCounter",
                    fact=f"PM counter {eid}: breach_ratio={pm.get('breach_ratio','?')}, plausible={pm.get('plausible','?')}",
                    specialist="network",
                ))
    except Exception:
        pass
    return evidence


def _run_payment_specialist(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Payment specialist: Payment failures / dunning for this customer."""
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        for pf in chain.get("payment_failures", []):
            if isinstance(pf, dict):
                eid = pf.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="PaymentFailure",
                    fact=f"Payment failure {eid}: reason={pf.get('failure_reason','?')}, amount={pf.get('amount','?')}",
                    specialist="payment",
                ))
        # Also include disputes as payment-related evidence
        for d in chain.get("disputes", []):
            if isinstance(d, dict):
                eid = d.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="Dispute",
                    fact=f"Dispute {eid}: reason={d.get('reason','?')}, status={d.get('status','?')}, amount={d.get('amount','?')}",
                    specialist="payment",
                ))
    except Exception:
        pass
    return evidence


def _run_service_specialist(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Service specialist: Service disruptions / incidents."""
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        # Extract Incident roots from the roots list
        for root in chain.get("roots", []):
            if isinstance(root, dict) and root.get("type") == "Incident":
                eid = root.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="Incident",
                    fact=f"Service disruption {eid}: type={root.get('disruption_type', root.get('disruption_reason','?'))}, severity={root.get('severity','?')}",
                    specialist="service",
                ))
        # SLA credits indicate service issues that were compensated
        for sc in chain.get("sla_credits", []):
            if isinstance(sc, dict):
                eid = sc.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="SlaCredit",
                    fact=f"SLA credit {eid}: amount={sc.get('credit_amount', sc.get('amount','?'))}",
                    specialist="service",
                ))
    except Exception:
        pass
    return evidence


def _run_performance_specialist(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Performance/API specialist: KPI breaches."""
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        # Extract ApiKpiBreach from roots
        for root in chain.get("roots", []):
            if isinstance(root, dict) and root.get("type") == "ApiKpiBreach":
                eid = root.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="ApiKpiBreach",
                    fact=f"KPI breach {eid}: kpi={root.get('kpi_name','?')}, threshold={root.get('threshold_value','?')}, actual={root.get('kpi_value','?')}",
                    specialist="performance",
                ))
        # Also include corroborating API evidence
        for api_ev in chain.get("api_evidence", []):
            if isinstance(api_ev, dict):
                eid = api_ev.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="ApiKpiBreach",
                    fact=f"API KPI evidence {eid}: kpi={api_ev.get('kpi_name','?')}, value={api_ev.get('kpi_value','?')}",
                    specialist="performance",
                ))
        # Also try the dedicated kpi_breach query
        kpi_rows = retrieval.kpi_breach_to_incident(customer_id)
        seen = {e.entity_id for e in evidence}
        for kpi in kpi_rows if isinstance(kpi_rows, list) else []:
            if isinstance(kpi, dict):
                eid = kpi.get("kpi_id", "")
                if eid and eid not in seen:
                    evidence.append(EvidenceItem(
                        entity_id=eid,
                        entity_type="ApiKpiBreach",
                        fact=f"KPI breach {eid}: kpi={kpi.get('kpi_name','?')}, threshold={kpi.get('threshold','?')}, actual={kpi.get('kpi_value','?')}",
                        specialist="performance",
                    ))
                    seen.add(eid)
    except Exception:
        pass
    return evidence


def _run_logs_specialist(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Logs specialist: System log errors."""
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        for root in chain.get("roots", []):
            if isinstance(root, dict) and root.get("type") == "LogEvent":
                eid = root.get("id", "")
                evidence.append(EvidenceItem(
                    entity_id=eid,
                    entity_type="LogEvent",
                    fact=f"Log event {eid}: level={root.get('log_level','?')}, message={root.get('message','?')}",
                    specialist="logs",
                ))
        # Also use the dedicated system_error_chain query
        seen = {e.entity_id for e in evidence}
        errors = retrieval.system_error_chain(customer_id)
        for log in errors if isinstance(errors, list) else []:
            if isinstance(log, dict):
                eid = log.get("id", log.get("log_id", ""))
                if eid and eid not in seen:
                    evidence.append(EvidenceItem(
                        entity_id=eid,
                        entity_type="LogEvent",
                        fact=f"Log event {eid}: level={log.get('level','?')}, service={log.get('service','?')}, message={log.get('message','?')}",
                        specialist="logs",
                    ))
                    seen.add(eid)
    except Exception:
        pass
    return evidence


def _run_semantic_specialist(
    retrieval: RCARetrievalService,
    customer_id: str,
    question: str,
    vector_search_fn: Callable | None = None,
) -> list[EvidenceItem]:
    """Semantic/vector specialist: always runs, adds recall. (Section 8.4)"""
    evidence = []
    if vector_search_fn is None:
        return evidence
    try:
        hits = vector_search_fn(query=question, customer_id=customer_id, top_k=10)
        for hit in hits:
            evidence.append(EvidenceItem(
                entity_id=hit.get("canonical_id", hit.get("id", "")),
                entity_type=hit.get("node_label", "Entity"),
                fact=hit.get("text", ""),
                source_system="Vector Index",
                specialist="semantic",
                similarity_score=hit.get("score"),
            ))
    except Exception:
        pass
    return evidence


_SPECIALIST_FNS = {
    "network": _run_network_specialist,
    "payment": _run_payment_specialist,
    "service": _run_service_specialist,
    "performance": _run_performance_specialist,
    "logs": _run_logs_specialist,
}


# ---------------------------------------------------------------------------
# Synthesis — correlate evidence into root causes (FIXED RULES, never model)
# ---------------------------------------------------------------------------

def _synthesize(evidence: list[EvidenceItem]) -> list[RootCauseCandidate]:
    """Correlate evidence into root-cause candidates using fixed rules.

    Rules-decide-WHAT / model-decides-HOW split (Section 12.2):
    Fixed rules decide what correlates; the narrator only rephrases.
    """
    cause_map: dict[str, RootCauseCandidate] = {}

    for ev in evidence:
        mapping = _ENTITY_TYPE_TO_CAUSE.get(ev.entity_type)
        if not mapping:
            continue
        cause_cat, severity = mapping
        if cause_cat not in cause_map:
            cause_map[cause_cat] = RootCauseCandidate(
                cause_category=cause_cat,
                severity=severity,
                evidence_entity_ids=[],
                recommended_actions=_CAUSE_ACTIONS.get(cause_cat, []),
            )
        if ev.entity_id and ev.entity_id not in cause_map[cause_cat].evidence_entity_ids:
            cause_map[cause_cat].evidence_entity_ids.append(ev.entity_id)

    # Rank by severity
    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    causes = sorted(cause_map.values(), key=lambda c: severity_order.get(c.severity, 4))
    return causes


# ---------------------------------------------------------------------------
# Grounding validation — critic strips ungrounded citations (Section 8.3/12.3)
# ---------------------------------------------------------------------------

def _validate_grounding(
    root_causes: list[RootCauseCandidate],
    evidence_ids: set[str],
) -> tuple[list[RootCauseCandidate], int]:
    """Strip any cited entity_id not in the retrieved evidence pool.

    Returns (validated_root_causes, stripped_count).
    """
    stripped_total = 0
    for rc in root_causes:
        original = rc.evidence_entity_ids[:]
        rc.evidence_entity_ids = [eid for eid in original if eid in evidence_ids]
        stripped_total += len(original) - len(rc.evidence_entity_ids)
    # Remove root causes with zero evidence
    root_causes = [rc for rc in root_causes if rc.evidence_entity_ids]
    return root_causes, stripped_total


# ---------------------------------------------------------------------------
# Confidence scoring (Spec Section 16)
# ---------------------------------------------------------------------------

def compute_rca_confidence(
    root_causes: list[RootCauseCandidate],
    stripped_count: int,
    retry_needed: bool,
) -> tuple[float, bool]:
    """Compute RCA confidence per Spec Section 16.

    RCA per-customer: 0.75 + 0.05 per root cause (capped at 3),
                      - 0.25 if ungrounded,
                      - 0.05 if retry needed,
                      clamped [0.3, 0.98].

    Returns (confidence, requires_human_escalation).
    """
    if not root_causes:
        return 0.35, True

    confidence = 0.75 + 0.05 * min(len(root_causes), 3)
    if stripped_count > 0:
        confidence -= 0.25
    if retry_needed:
        confidence -= 0.05

    confidence = max(0.3, min(0.98, confidence))
    requires_escalation = (
        not root_causes
        or stripped_count > 0
        or confidence < 0.6
    )
    return confidence, requires_escalation


def compute_aggregate_confidence(
    clusters: list[dict],
    stripped_count: int,
    total_customers: int,
) -> tuple[float, bool]:
    """Compute aggregate RCA confidence (Shape 5, Section 16).

    0.7 + min(top_cluster_probability, 0.25), - 0.25 if ungrounded, clamped [0.3, 0.98].
    """
    if not clusters:
        return 0.35, True

    top_prob = clusters[0].get("probability", 0.0) if clusters else 0.0
    confidence = 0.7 + min(top_prob, 0.25)
    if stripped_count > 0:
        confidence -= 0.25

    confidence = max(0.3, min(0.98, confidence))
    requires_escalation = not clusters or stripped_count > 0
    return confidence, requires_escalation


# ---------------------------------------------------------------------------
# The pipeline — main entry point
# ---------------------------------------------------------------------------

# In-memory RCA history store
_rca_history: dict[str, dict] = {}


def run_rca_pipeline(
    retrieval: RCARetrievalService,
    customer_id: str,
    question: str,
    specialists_to_dispatch: list[str] | None = None,
    vector_search_fn: Callable | None = None,
    narrator_fn: Callable | None = None,
    debug: bool = False,
) -> dict[str, Any]:
    """Run the full multi-step RCA pipeline.

    Args:
        retrieval: RCA retrieval service for KG queries.
        customer_id: Customer to investigate (or "ALL").
        question: The user's billing issue description.
        specialists_to_dispatch: Override specialist dispatch (from Layer 2b).
        vector_search_fn: Optional vector search callable.
        narrator_fn: Optional LLM for narrating the root cause.
        debug: Include full per-step trace in response.

    Returns:
        Full RCA response dict.
    """
    start_time = time.time()
    request_id = str(uuid.uuid4())[:8]

    bb = Blackboard(
        request_id=request_id,
        customer_id=customer_id,
        question=question,
    )

    # Handle ALL-customers aggregate (Shape 5)
    if customer_id.upper() == "ALL":
        return _run_aggregate_rca(retrieval, bb, question, debug)

    # --- Step 1: Supervisor — plan specialist dispatch ---
    if specialists_to_dispatch:
        bb.specialists_dispatched = specialists_to_dispatch
    else:
        bb.specialists_dispatched = dispatch_specialists(question)
    bb.trace.append({
        "step": 1, "stage": "supervisor_plan",
        "specialists": bb.specialists_dispatched,
    })

    retry_needed = False
    specialist_evidence_counts: dict[str, int] = {}

    for iteration in range(bb.max_iterations):
        bb.iteration = iteration + 1

        # --- Step 2: Run dispatched specialists ---
        # Pre-fetch the full chain once to avoid redundant Neo4j queries
        chain = retrieval.rca_full_chain(customer_id)

        # Base context always runs first
        base_evidence = _run_base_context(retrieval, customer_id, chain=chain)
        bb.evidence.extend(base_evidence)
        specialist_evidence_counts["base_context"] = len(base_evidence)

        for specialist in bb.specialists_dispatched:
            fn = _SPECIALIST_FNS.get(specialist)
            if fn:
                sp_evidence = fn(retrieval, customer_id, chain=chain)
                bb.evidence.extend(sp_evidence)
                specialist_evidence_counts[specialist] = specialist_evidence_counts.get(specialist, 0) + len(sp_evidence)

        # Semantic specialist always runs (Section 8.4)
        sem_evidence = _run_semantic_specialist(retrieval, customer_id, question, vector_search_fn)
        bb.evidence.extend(sem_evidence)
        specialist_evidence_counts["semantic"] = len(sem_evidence)

        bb.trace.append({
            "step": 2, "stage": "specialists_dispatched",
            "iteration": bb.iteration,
            "evidence_count": len(bb.evidence),
        })

        # --- Step 3: Critic — sufficiency check ---
        correlating = [e for e in bb.evidence if e.entity_type in _ENTITY_TYPE_TO_CAUSE]
        bb.trace.append({
            "step": 3, "stage": "critic_sufficiency",
            "correlating_evidence": len(correlating),
        })

        if correlating:
            break  # Sufficient evidence found

        # --- Step 4: Supervisor retry — widen window, same specialists ---
        if iteration < bb.max_iterations - 1:
            retry_needed = True
            bb.trace.append({
                "step": 4, "stage": "supervisor_retry",
                "reason": "No correlating evidence found, widening scope",
            })
        # (only dispatched specialists are retried — Section 8.3)

    # --- Attach KG traversal paths to evidence ---
    traversals = chain.get("traversals", {}) if isinstance(chain, dict) else {}
    for e in bb.evidence:
        if e.entity_id and e.entity_id in traversals:
            e.traversal_path = traversals[e.entity_id]

    # --- Step 5: Synthesis — fixed rules, never model ---
    bb.root_causes = _synthesize(bb.evidence)
    bb.trace.append({
        "step": 5, "stage": "synthesis",
        "root_causes": len(bb.root_causes),
    })

    # --- Step 6: Critic — grounding validation ---
    all_evidence_ids = {e.entity_id for e in bb.evidence if e.entity_id}
    bb.root_causes, stripped = _validate_grounding(bb.root_causes, all_evidence_ids)
    bb.trace.append({
        "step": 6, "stage": "critic_grounding",
        "stripped_count": stripped,
        "requires_escalation": stripped > 0,
    })

    # --- Confidence scoring ---
    confidence, requires_escalation = compute_rca_confidence(bb.root_causes, stripped, retry_needed)

    # --- Build structured report sections (all deterministic) ---
    report = _build_report_sections(bb, confidence, requires_escalation, specialist_evidence_counts, retry_needed, stripped)

    # --- Narrate (optional, model-decides-HOW) — only the root cause narrative ---
    narrative = ""
    narrated_by = "deterministic"
    if narrator_fn and bb.root_causes:
        try:
            narrative = _narrate_rca(narrator_fn, bb)
            if narrative and narrative.strip():
                narrated_by = "model"
            else:
                narrative = _deterministic_narrative(bb)
        except Exception:
            narrative = _deterministic_narrative(bb)
    else:
        narrative = _deterministic_narrative(bb)

    # --- Step 7: Assemble response ---
    # Build agent status map for frontend
    all_specialist_ids = ["network", "payment", "service", "performance", "logs"]
    agent_statuses = {
        "supervisor": "done",
    }
    for sp_id in all_specialist_ids:
        if sp_id in bb.specialists_dispatched:
            agent_statuses[sp_id] = "done"
        else:
            agent_statuses[sp_id] = "skipped"
    agent_statuses["semantic"] = "done"
    agent_statuses["critic"] = "done"
    agent_statuses["synthesis"] = "done"
    agent_statuses["grounding"] = "done"

    response = {
        "request_id": request_id,
        "customer_id": customer_id,
        "specialists_dispatched": bb.specialists_dispatched,
        "agent_statuses": agent_statuses,
        "specialist_evidence_counts": specialist_evidence_counts,
        "root_causes": [
            {
                "cause_category": rc.cause_category,
                "severity": rc.severity,
                "evidence_entity_ids": rc.evidence_entity_ids,
                "description": rc.description,
                "recommended_actions": rc.recommended_actions,
            }
            for rc in bb.root_causes
        ],
        "primary_cause": (
            {
                "cause_category": bb.root_causes[0].cause_category,
                "severity": bb.root_causes[0].severity,
            }
            if bb.root_causes else None
        ),
        "recommended_actions": bb.root_causes[0].recommended_actions if bb.root_causes else [],
        "evidence": [
            {
                "entity_id": e.entity_id,
                "entity_type": e.entity_type,
                "fact": e.fact,
                "source_system": e.source_system,
                "similarity_score": e.similarity_score,
                "remediation_status": e.remediation_status,
                "traversal_path": e.traversal_path,
            }
            for e in bb.evidence if e.entity_id and e.source_system != "Vector Index"
        ],
        "narrative": narrative,
        "report": report,
        "confidence": round(confidence, 2),
        "requires_human_escalation": requires_escalation,
        "narrated_by": narrated_by,
        "response_time_ms": int((time.time() - start_time) * 1000),
    }

    if debug:
        response["debug"] = {
            "agent_trace": bb.trace,
            "grounding_check": {
                "stripped_count": stripped,
                "total_evidence": len(bb.evidence),
            },
        }

    # Persist to history
    _rca_history[request_id] = {
        **response,
        "timestamp": datetime.utcnow().isoformat(),
    }

    return response


def _run_base_context(retrieval: RCARetrievalService, customer_id: str, chain: dict | None = None) -> list[EvidenceItem]:
    """Base context specialist: Charges from the billing chain (always runs first).

    Note: rca_full_chain returns 'charges' (not 'invoices' — invoice data
    is embedded in the Charge→Invoice→Account→Customer traversal).
    """
    evidence = []
    try:
        if chain is None:
            chain = retrieval.rca_full_chain(customer_id)
        for ch in chain.get("charges", []):
            if isinstance(ch, dict):
                evidence.append(EvidenceItem(
                    entity_id=ch.get("id", ""),
                    entity_type="Charge",
                    fact=f"Charge {ch.get('id','')}: type={ch.get('charge_type','?')}, amount={ch.get('amount','?')}",
                    specialist="base_context",
                ))
    except Exception:
        pass
    return evidence


def _run_aggregate_rca(
    retrieval: RCARetrievalService,
    bb: Blackboard,
    question: str,
    debug: bool,
) -> dict[str, Any]:
    """Shape 5 — Aggregate multi-hop RCA across ALL customers."""
    start_time = time.time()
    clusters = []

    try:
        nf_data = retrieval.all_network_failures(limit=50)
        if isinstance(nf_data, list):
            # Group by failure_type
            type_counts: dict[str, list] = {}
            for nf in nf_data:
                if isinstance(nf, dict):
                    ft = nf.get("failure_type", "UNKNOWN")
                    type_counts.setdefault(ft, []).append(nf)
            total = sum(len(v) for v in type_counts.values())
            for ft, items in type_counts.items():
                clusters.append({
                    "failure_type": ft,
                    "affected_customer_count": len({i.get("customer_id") for i in items if i.get("customer_id")}),
                    "total_events": len(items),
                    "max_severity": max((i.get("severity", "LOW") for i in items), default="LOW"),
                    "sample_evidence_ids": [i.get("id") for i in items[:5] if i.get("id")],
                    "probability": len(items) / max(total, 1),
                })
            clusters.sort(key=lambda c: c["affected_customer_count"], reverse=True)
    except Exception:
        pass

    confidence, requires_escalation = compute_aggregate_confidence(clusters, 0, bb.total_customers or 1)

    response = {
        "request_id": bb.request_id,
        "customer_id": "ALL",
        "mode": "aggregate",
        "clusters": clusters,
        "primary_cause": clusters[0] if clusters else None,
        "confidence": round(confidence, 2),
        "requires_human_escalation": requires_escalation,
        "response_time_ms": int((time.time() - start_time) * 1000),
    }
    if debug:
        response["debug"] = {"agent_trace": bb.trace}

    _rca_history[bb.request_id] = {
        **response,
        "timestamp": datetime.utcnow().isoformat(),
    }
    return response


def _build_report_sections(
    bb: Blackboard,
    confidence: float,
    requires_escalation: bool,
    specialist_evidence_counts: dict[str, int],
    retry_needed: bool,
    stripped_count: int,
) -> dict[str, Any]:
    """Build structured report sections deterministically for the Excel template.

    Template sections:
    1. General Information
    2. Problem Description
    3. Impact Assessment
    4. Investigation Details
    5. Root Cause
    6. Corrective Actions
    """
    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # --- 1. General Information ---
    status = "Closed" if not requires_escalation else "Escalation Required"
    general_info = {
        "issue_id": bb.request_id,
        "reported_by": "RCA Pipeline (Automated)",
        "date_reported": now,
        "status": status,
    }

    # --- 2. Problem Description ---
    # Determine affected systems from evidence types
    affected_systems = sorted({e.entity_type for e in bb.evidence if e.entity_id})
    error_messages = []
    for e in bb.evidence:
        if e.entity_type == "LogEvent" and e.entity_id:
            msg = e.fact.split("message=")[-1] if "message=" in e.fact else e.fact
            if msg and len(error_messages) < 5:
                error_messages.append(msg)

    problem_description = {
        "summary": bb.question or f"Root cause analysis for customer {bb.customer_id}",
        "systems_affected": ", ".join(affected_systems) if affected_systems else "N/A",
        "error_messages": error_messages if error_messages else ["No system errors captured"],
        "incident_datetime": now,
    }

    # --- 3. Impact Assessment ---
    severity_counts = {}
    for rc in bb.root_causes:
        severity_counts[rc.severity] = severity_counts.get(rc.severity, 0) + 1

    impact_descriptions = []
    for rc in bb.root_causes:
        ev_count = len(rc.evidence_entity_ids)
        impact_descriptions.append(
            f"{rc.cause_category.replace('_', ' ')} ({rc.severity}): "
            f"{ev_count} evidence item(s) found"
        )
    if not impact_descriptions:
        impact_descriptions.append("No correlating root causes identified. Manual review recommended.")

    impact_assessment = {
        "impact_scope": f"{len(bb.root_causes)} root cause(s) identified across {len(affected_systems)} system(s)",
        "users_affected": bb.customer_id,
        "downtime_duration": "N/A (derived from KG evidence)",
        "descriptions": impact_descriptions,
        "confidence": f"{round(confidence * 100)}%",
    }

    # --- 4. Investigation Details ---
    investigation_steps = [
        f"Supervisor dispatched specialists: {', '.join(bb.specialists_dispatched)}",
    ]
    for sp in bb.specialists_dispatched:
        count = specialist_evidence_counts.get(sp, 0)
        investigation_steps.append(f"{sp.capitalize()} specialist collected {count} evidence item(s)")
    sem_count = specialist_evidence_counts.get("semantic", 0)
    investigation_steps.append(f"Semantic search added {sem_count} vector-matched item(s)")
    investigation_steps.append(f"Synthesis produced {len(bb.root_causes)} root cause(s) from fixed correlation rules")
    investigation_steps.append(f"Grounding validator stripped {stripped_count} ungrounded citation(s)")
    if retry_needed:
        investigation_steps.append("Retry was triggered due to insufficient initial evidence")

    investigation_details = {
        "investigated_by": "Multi-Agent RCA Pipeline (Deterministic KG + Rules)",
        "steps": investigation_steps,
    }

    # --- 5. Root Cause ---
    root_cause_details = []
    for rc in bb.root_causes:
        related = [e for e in bb.evidence if e.entity_id in rc.evidence_entity_ids]
        root_cause_details.append({
            "category": rc.cause_category.replace("_", " "),
            "severity": rc.severity,
            "evidence_ids": rc.evidence_entity_ids,
            "evidence_facts": [e.fact for e in related],
        })

    # --- 6. Corrective Actions ---
    short_term = []
    long_term = []
    for rc in bb.root_causes:
        for i, action in enumerate(rc.recommended_actions):
            if i == 0:
                short_term.append(action)
            else:
                long_term.append(action)

    corrective_actions = {
        "short_term": short_term if short_term else ["No immediate actions identified"],
        "long_term": long_term if long_term else ["Continue monitoring through KG pipeline"],
    }

    return {
        "general_info": general_info,
        "problem_description": problem_description,
        "impact_assessment": impact_assessment,
        "investigation_details": investigation_details,
        "root_causes": root_cause_details,
        "corrective_actions": corrective_actions,
    }


def _deterministic_narrative(bb: Blackboard) -> str:
    """Deterministic narrative template — no model call."""
    if not bb.root_causes:
        return f"No correlating root cause found for customer {bb.customer_id}. Manual investigation recommended."

    parts = [f"**Root Cause Analysis for {bb.customer_id}**\n"]
    for i, rc in enumerate(bb.root_causes, 1):
        parts.append(f"\n**Cause {i}: {rc.cause_category}** (Severity: {rc.severity})")
        parts.append(f"Evidence: {', '.join(rc.evidence_entity_ids)}")
        if rc.recommended_actions:
            parts.append("Recommended actions:")
            for action in rc.recommended_actions:
                parts.append(f"  - {action}")
    return "\n".join(parts)


def _narrate_rca(narrator_fn: Callable, bb: Blackboard) -> str:
    """Use a model to narrate the RCA results (model-decides-HOW).

    Only sends deduped, correlating evidence to minimize token usage.
    The KG already did the heavy lifting — the LLM just narrates.
    """
    # Only include evidence that maps to root causes (skip noise like raw charges)
    correlating_ids = set()
    for rc in bb.root_causes:
        correlating_ids.update(rc.evidence_entity_ids)

    # Deduped, correlating evidence first; then a brief summary of other evidence
    seen = set()
    correlating_lines = []
    other_count = 0
    for e in bb.evidence:
        if not e.entity_id or e.entity_id in seen:
            continue
        seen.add(e.entity_id)
        if e.entity_id in correlating_ids:
            correlating_lines.append(f"- {e.entity_type} {e.entity_id}: {e.fact}")
        else:
            other_count += 1

    evidence_text = "\n".join(correlating_lines)
    if other_count:
        evidence_text += f"\n(+ {other_count} additional context items)"

    causes_text = "\n".join(
        f"- {rc.cause_category} ({rc.severity}): {rc.evidence_entity_ids}"
        for rc in bb.root_causes
    )

    prompt = (
        f"Customer {bb.customer_id} RCA. Write ONLY a short root cause narrative (max 150 words).\n\n"
        f"Evidence:\n{evidence_text}\n\n"
        f"Root causes:\n{causes_text}\n\n"
        f"Explain the causal chain: what happened, why, and impact. "
        f"Cite entity IDs. No headings, no tables, no recommendations — just the narrative."
    )
    return narrator_fn(prompt)


# ---------------------------------------------------------------------------
# History access
# ---------------------------------------------------------------------------

def get_rca_history(mode: str | None = None, customer_id: str | None = None, limit: int = 50) -> list[dict]:
    """List past RCA runs, optionally filtered."""
    results = list(_rca_history.values())
    if mode:
        results = [r for r in results if r.get("mode", "per_customer") == mode]
    if customer_id:
        results = [r for r in results if r.get("customer_id") == customer_id]
    results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    return results[:limit]


def get_rca_by_id(request_id: str) -> dict | None:
    """Fetch one saved RCA history entry."""
    return _rca_history.get(request_id)


def get_rca_evidence_entity(request_id: str, entity_id: str) -> dict | None:
    """Look up one specific evidence entity from a past RCA response."""
    rca = _rca_history.get(request_id)
    if not rca:
        return None
    for ev in rca.get("evidence", []):
        if ev.get("entity_id") == entity_id:
            return ev
    return None
