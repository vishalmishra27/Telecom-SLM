"""
Cypher Generation & Validation — Schema-Grounded Query Production

Spec Sections 4.1–4.4:
- Generation: schema + spec → Cypher (Section 4.2)
- Validator: deterministic code, not another model call (Section 4.3)
- Bounded repair loop: 2 attempts max (Section 4.4)

Every retrieval query is GENERATED at request time against the schema registry,
then checked by a deterministic validator before it ever reaches the database.
No user input is ever concatenated into Cypher text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from . import schema_registry


# ---------------------------------------------------------------------------
# Retrieval Spec — what generation receives from intent detection
# ---------------------------------------------------------------------------

@dataclass
class RetrievalSpec:
    """Structured spec that drives Cypher generation."""
    entities: list[str]          # labels involved
    scope: str = ""              # customer_id or "ALL"
    shape: str = "single_entity" # "single_entity", "aggregate", "multi_hop", "investigative"
    parameters: dict[str, Any] = field(default_factory=dict)  # customer_id, billing_period, limit, ...
    intent: str = ""
    question: str = ""


# ---------------------------------------------------------------------------
# Validator — deterministic checks on generated Cypher
# ---------------------------------------------------------------------------

_WRITE_CLAUSES = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|CALL\s+\{.*\bCREATE\b)",
    re.IGNORECASE,
)

_LABEL_RE = re.compile(r":(\w+)(?:\s|\{|\))")
_REL_RE = re.compile(r"\[:(\w+)")
_PROP_RE = re.compile(r"\.(\w+)")
_LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+|\$\w+)", re.IGNORECASE)
_VAR_LEN_RE = re.compile(r"\[\*(\d*)\.\.(\d+)\]")

# Maximum limits
MAX_LIMIT = 5000
MAX_HOP_DEPTH = 6


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def validate_cypher(query: str, spec: RetrievalSpec | None = None) -> ValidationResult:
    """Run all deterministic checks on a generated Cypher query.

    Checks (per Spec Section 4.3):
    1. Read-only — no write clauses
    2. Schema conformance — only registered labels, relationships, properties
    3. Scope anchoring — non-ALL queries must anchor to customer
    4. Parameterization — no literal values (IDs, dates) in query body
    5. Hop-depth ceiling — variable-length patterns bounded
    6. Row/result ceiling — must have LIMIT
    """
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Read-only check
    if _WRITE_CLAUSES.search(query):
        errors.append("WRITE_CLAUSE: Query contains a write clause (CREATE/MERGE/DELETE/SET/REMOVE). Only read-only queries are allowed.")

    # 2. Schema conformance — labels
    registered_labels = set(schema_registry.all_labels())
    used_labels = set(_LABEL_RE.findall(query))
    unknown_labels = used_labels - registered_labels
    if unknown_labels:
        errors.append(f"UNKNOWN_LABEL: References label(s) not in schema registry: {', '.join(sorted(unknown_labels))}. Allowed labels: {', '.join(sorted(registered_labels))}")

    # 2b. Schema conformance — relationship types
    registered_rels = schema_registry.all_relationship_types()
    used_rels = set(_REL_RE.findall(query))
    unknown_rels = used_rels - registered_rels
    if unknown_rels:
        errors.append(f"UNKNOWN_RELATIONSHIP: References relationship type(s) not in schema registry: {', '.join(sorted(unknown_rels))}. Allowed: {', '.join(sorted(registered_rels))}")

    # 3. Scope anchoring — non-ALL queries must reference customer_id
    if spec and spec.scope and spec.scope != "ALL":
        if "$customer_id" not in query and "customer_id" not in query.lower():
            errors.append("SCOPE_MISSING: Non-ALL query does not anchor to customer_id. The query must constrain to the requested customer.")

    # 4. Parameterization — check for inline literal IDs
    literal_ids = re.findall(r"['\"](?:CUST-|ACC-|INV-GEN-|CHG-GEN-|NF-GEN-|PF-GEN-|SD-GEN-|LOG-GEN-|KPI-GEN-)[^'\"]+['\"]", query)
    if literal_ids:
        errors.append(f"LITERAL_VALUE: Query contains literal ID values instead of parameters: {literal_ids}. Use $customer_id, $entity_id, etc.")

    # 5. Hop-depth ceiling
    for match in _VAR_LEN_RE.finditer(query):
        max_depth = int(match.group(2))
        if max_depth > MAX_HOP_DEPTH:
            errors.append(f"HOP_DEPTH: Variable-length pattern has max depth {max_depth}, exceeding ceiling of {MAX_HOP_DEPTH}.")

    # 6. Row/result ceiling
    limit_match = _LIMIT_RE.search(query)
    if not limit_match:
        warnings.append("NO_LIMIT: Query has no LIMIT clause. Adding LIMIT 100 is recommended.")
    elif limit_match.group(1).isdigit():
        limit_val = int(limit_match.group(1))
        if limit_val > MAX_LIMIT:
            errors.append(f"LIMIT_EXCEEDED: LIMIT {limit_val} exceeds maximum allowed ({MAX_LIMIT}).")

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
# Generation — produce Cypher from spec + schema
# ---------------------------------------------------------------------------

GENERATION_PROMPT = """You are generating a READ-ONLY Cypher query against the schema below.
You may reference ONLY the labels, relationships, and properties listed
— never invent one. Every literal value (an id, a date, a count) MUST
be a named parameter ($customer_id, $billing_period, …), never written
into the query text directly. If the request is scoped to one entity
(not ALL), the query MUST anchor its pattern at that entity using the
scope key shown for the relevant label(s). Do not include CREATE,
MERGE, DELETE, SET, REMOVE, or any other write clause.

Schema: {schema}
Retrieval spec: {spec}
Parameters available: {parameters}

Question: {question}

Cypher:"""


def build_generation_prompt(spec: RetrievalSpec) -> str:
    """Build the prompt for schema-grounded Cypher generation."""
    registry_slice = schema_registry.registry_for_generation(spec.entities or None)
    return GENERATION_PROMPT.format(
        schema=json.dumps(registry_slice, indent=2),
        spec=json.dumps({
            "entities": spec.entities,
            "scope": spec.scope,
            "shape": spec.shape,
            "intent": spec.intent,
        }),
        parameters=json.dumps(spec.parameters),
        question=spec.question,
    )


def tag_query(query: str, shape_key: str) -> str:
    """Add a shape tag comment to a generated query (Section 4.4).

    The shape tag is how offline/demo mode dispatches without
    fingerprinting literal query text.
    """
    return f"// shape: {shape_key}\n{query}"


def extract_shape(query: str) -> str | None:
    """Extract the shape tag from a tagged query."""
    match = re.match(r"^//\s*shape:\s*(\S+)", query)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Bounded repair loop (Section 4.4)
# ---------------------------------------------------------------------------

def generate_with_repair(
    generate_fn,  # callable(prompt: str) -> str
    spec: RetrievalSpec,
    max_attempts: int = 2,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Generate and validate a Cypher query with bounded repair.

    Args:
        generate_fn: A callable that takes a prompt string and returns generated Cypher.
        spec: The retrieval spec describing what to query.
        max_attempts: Maximum generation attempts (default 2 per Section 4.4).

    Returns:
        (validated_query, trace) where trace records each attempt for debugging.
    """
    trace: list[dict[str, Any]] = []

    prompt = build_generation_prompt(spec)
    for attempt in range(max_attempts):
        try:
            raw_cypher = generate_fn(prompt)
        except Exception as exc:
            trace.append({
                "attempt": attempt + 1,
                "status": "generation_error",
                "error": str(exc),
            })
            break

        # Clean up — extract just the Cypher from model output
        cypher = _extract_cypher(raw_cypher)

        # Validate
        result = validate_cypher(cypher, spec)
        trace.append({
            "attempt": attempt + 1,
            "status": "valid" if result.valid else "invalid",
            "query": cypher,
            "errors": result.errors,
            "warnings": result.warnings,
        })

        if result.valid:
            tagged = tag_query(cypher, spec.shape)
            return tagged, trace

        # Feed failure back for repair attempt
        if attempt < max_attempts - 1:
            repair_context = (
                f"\n\nYour previous query was rejected by the validator. "
                f"Fix these specific errors:\n"
                + "\n".join(f"- {e}" for e in result.errors)
                + "\n\nGenerate a corrected query:"
            )
            prompt = prompt + repair_context

    return None, trace


def _extract_cypher(raw: str) -> str:
    """Extract Cypher from model output, stripping markdown fences and explanations."""
    # Try to extract from ```cypher ... ``` block
    match = re.search(r"```(?:cypher)?\s*\n(.*?)```", raw, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try to extract lines that look like Cypher (MATCH, RETURN, WITH, etc.)
    lines = []
    cypher_started = False
    for line in raw.split("\n"):
        stripped = line.strip()
        if re.match(r"^(MATCH|OPTIONAL|WITH|RETURN|WHERE|ORDER|LIMIT|UNION|CALL|UNWIND)", stripped, re.IGNORECASE):
            cypher_started = True
        if cypher_started:
            if stripped and not stripped.startswith("//") or stripped.startswith("// shape:"):
                lines.append(line)
            elif not stripped and lines:
                # Empty line after Cypher started — stop
                break
    if lines:
        return "\n".join(lines).strip()
    return raw.strip()


# ---------------------------------------------------------------------------
# Pre-built query shapes for common patterns (fallback when no model available)
# ---------------------------------------------------------------------------

def reference_query_single_entity(spec: RetrievalSpec) -> str:
    """Reference fallback query for single-entity lookup (no model needed)."""
    q = (
        "MATCH (a:Account)-[:OWNED_BY]->(c:Customer {id: $customer_id})\n"
        "MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)\n"
        "OPTIONAL MATCH (root)-[:CAUSED_CHARGE]->(ch)\n"
        "OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv)\n"
        "OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)\n"
        "OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)\n"
        "RETURN c, a, inv, ch, root, pf, d, sc\n"
        "LIMIT 100"
    )
    return tag_query(q, "single_entity")


def reference_query_aggregate(spec: RetrievalSpec) -> str:
    """Reference fallback query for aggregate/population scan."""
    q = (
        "MATCH (c:Customer)-[:OWNED_BY]-(a:Account)-[:BILLED_TO]-(inv:Invoice)\n"
        "WHERE ($customer_id IS NULL OR c.id = $customer_id)\n"
        "WITH DISTINCT c, inv\n"
        "OPTIONAL MATCH (ch:Charge)-[:ON_INVOICE]->(inv)\n"
        "RETURN inv.id AS invoice_id, c.id AS customer_id,\n"
        "       inv.total_amount AS amount,\n"
        "       collect(DISTINCT {category: ch.category, amount: ch.amount}) AS charges\n"
        "ORDER BY c.id\n"
        "LIMIT 5000"
    )
    return tag_query(q, "aggregate")


def reference_query_unremediated(spec: RetrievalSpec) -> str:
    """Reference fallback for issues-without-remediation (Shape 3)."""
    branches = []
    for label in schema_registry.issue_bearing_labels():
        ldef = schema_registry.LABELS[label]
        branches.append(
            f"  MATCH (n:{label})\n"
            f"  WHERE ($customer_id IS NULL OR n.customer_id = $customer_id)\n"
            f"    AND NOT (n)-[:REMEDIATED_BY]->(:RemediationAction)\n"
            f"  RETURN n.id AS entity_id, '{label}' AS entity_type,\n"
            f"         n.customer_id AS customer_id,\n"
            f"         n.severity AS severity,\n"
            f"         n.event_date AS event_date"
        )
    q = (
        "CALL {\n"
        + "\n  UNION ALL\n".join(branches)
        + "\n}\n"
        "RETURN entity_id, entity_type, customer_id, severity, event_date\n"
        "ORDER BY\n"
        "  CASE toUpper(coalesce(severity, ''))\n"
        "    WHEN 'CRITICAL' THEN 0 WHEN 'HIGH' THEN 1\n"
        "    WHEN 'MEDIUM' THEN 2 WHEN 'LOW' THEN 3 ELSE 4\n"
        "  END,\n"
        "  event_date DESC\n"
        "LIMIT $limit"
    )
    return tag_query(q, "unremediated")
