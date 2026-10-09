"""
Schema Registry — Single Source of Truth for the Ontology

Spec Section 4.1: Every label, relationship type, and property that generation
is allowed to reference comes from this one structured registry.

The registry is derived from the ingestion pipeline's field-mapping so it cannot
drift from what actually exists in Neo4j.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PropertyDef:
    name: str
    type: str  # "string", "number", "date", "boolean", "list"
    sensitive: bool = False  # excluded from retrieval if True


@dataclass(frozen=True)
class RelationshipDef:
    type: str
    target_label: str
    direction: str = "outgoing"  # "outgoing" or "incoming"


@dataclass(frozen=True)
class LabelDef:
    label: str
    scope_key: str | None = None  # property that anchors to a customer/tenant
    scope_via: str | None = None  # label chain to reach scope (e.g., "Account" for entities reached via Account)
    properties: tuple[PropertyDef, ...] = ()
    relationships: tuple[RelationshipDef, ...] = ()
    is_issue_bearing: bool = False  # participates in remediation lookups
    id_prefix: str = ""
    display_name: str = ""


# ---------------------------------------------------------------------------
# THE REGISTRY — all labels, relationships, properties in this ontology
# ---------------------------------------------------------------------------

LABELS: dict[str, LabelDef] = {}


def _register(label_def: LabelDef) -> LabelDef:
    LABELS[label_def.label] = label_def
    return label_def


# --- Core billing chain ---

_register(LabelDef(
    label="Customer",
    scope_key="id",
    id_prefix="CUST-",
    display_name="Customer",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_type", "string"),
        PropertyDef("name", "string"),
        PropertyDef("segment", "string"),
    ),
    relationships=(
        RelationshipDef("OWNED_BY", "Customer", "incoming"),  # Account->OWNED_BY->Customer
    ),
))

_register(LabelDef(
    label="Account",
    scope_key="customer_id",
    scope_via="Customer",
    id_prefix="ACC-",
    display_name="Account",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("account_status", "string"),
    ),
    relationships=(
        RelationshipDef("OWNED_BY", "Customer", "outgoing"),
        RelationshipDef("BILLED_TO", "Account", "incoming"),  # Invoice->BILLED_TO->Account
    ),
))

_register(LabelDef(
    label="Invoice",
    scope_key="customer_id",
    scope_via="Account",
    id_prefix="INV-GEN-",
    display_name="Invoice",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("account_id", "string"),
        PropertyDef("billing_period", "string"),
        PropertyDef("total_amount", "number"),
        PropertyDef("status", "string"),
        PropertyDef("due_date", "date"),
    ),
    relationships=(
        RelationshipDef("BILLED_TO", "Account", "outgoing"),
        RelationshipDef("ON_INVOICE", "Invoice", "incoming"),  # Charge->ON_INVOICE->Invoice
        RelationshipDef("FAILED_ON", "Invoice", "incoming"),   # PaymentFailure->FAILED_ON->Invoice
    ),
))

_register(LabelDef(
    label="Charge",
    scope_key="customer_id",
    scope_via="Invoice",
    id_prefix="CHG-GEN-",
    display_name="Charge",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("invoice_id", "string"),
        PropertyDef("charge_type", "string"),
        PropertyDef("category", "string"),
        PropertyDef("amount", "number"),
        PropertyDef("description", "string"),
    ),
    relationships=(
        RelationshipDef("ON_INVOICE", "Invoice", "outgoing"),
        RelationshipDef("CAUSED_CHARGE", "Charge", "incoming"),  # RootCause->CAUSED_CHARGE->Charge
        RelationshipDef("DISPUTES", "Charge", "incoming"),       # Dispute->DISPUTES->Charge
    ),
))

# --- Root cause nodes (issue-bearing) ---

_register(LabelDef(
    label="NetworkFailure",
    scope_key="customer_id",
    scope_via="Charge",
    id_prefix="NF-GEN-",
    display_name="Network Failure",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("failure_type", "string"),
        PropertyDef("severity", "string"),
        PropertyDef("event_date", "date"),
        PropertyDef("affected_site", "string"),
        PropertyDef("impact_description", "string"),
    ),
    relationships=(
        RelationshipDef("CAUSED_CHARGE", "Charge", "outgoing"),
        RelationshipDef("AT_SITE", "Site", "outgoing"),
        RelationshipDef("CORROBORATES", "NetworkFailure", "incoming"),  # PmCounter/ApiKpiBreach
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

_register(LabelDef(
    label="PaymentFailure",
    scope_key="customer_id",
    scope_via="Invoice",
    id_prefix="PF-GEN-",
    display_name="Payment Failure",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("failure_reason", "string"),
        PropertyDef("payment_method", "string"),
        PropertyDef("amount", "number"),
        PropertyDef("event_date", "date"),
        PropertyDef("invoice_id", "string"),
    ),
    relationships=(
        RelationshipDef("FAILED_ON", "Invoice", "outgoing"),
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

_register(LabelDef(
    label="Incident",
    scope_key="customer_id",
    scope_via="Charge",
    id_prefix="SD-GEN-",
    display_name="Service Disruption",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("disruption_type", "string"),
        PropertyDef("severity", "string"),
        PropertyDef("event_date", "date"),
        PropertyDef("impact_description", "string"),
    ),
    relationships=(
        RelationshipDef("CAUSED_CHARGE", "Charge", "outgoing"),
        RelationshipDef("COMPENSATES", "Incident", "incoming"),  # SlaCredit
        RelationshipDef("CORROBORATES", "Incident", "incoming"),
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

_register(LabelDef(
    label="LogEvent",
    scope_key="customer_id",
    scope_via="Charge",
    id_prefix="LOG-GEN-",
    display_name="Log Event",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("log_level", "string"),
        PropertyDef("component", "string"),
        PropertyDef("message", "string"),
        PropertyDef("event_date", "date"),
    ),
    relationships=(
        RelationshipDef("CAUSED_CHARGE", "Charge", "outgoing"),
        RelationshipDef("EMITTED_BY", "Service", "outgoing"),
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

_register(LabelDef(
    label="ApiKpiBreach",
    scope_key="customer_id",
    scope_via="Charge",
    id_prefix="KPI-GEN-",
    display_name="KPI Breach",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("kpi_name", "string"),
        PropertyDef("threshold", "number"),
        PropertyDef("actual_value", "number"),
        PropertyDef("severity", "string"),
        PropertyDef("event_date", "date"),
        PropertyDef("site_id", "string"),
    ),
    relationships=(
        RelationshipDef("CAUSED_CHARGE", "Charge", "outgoing"),
        RelationshipDef("CORROBORATES", "NetworkFailure", "outgoing"),
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

# --- Evidence nodes ---

_register(LabelDef(
    label="PmCounter",
    scope_key="customer_id",
    scope_via="NetworkFailure",
    id_prefix="PM-GEN-",
    display_name="PM Counter",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("counter_name", "string"),
        PropertyDef("value", "number"),
        PropertyDef("threshold", "number"),
        PropertyDef("site_id", "string"),
        PropertyDef("event_date", "date"),
    ),
    relationships=(
        RelationshipDef("CORROBORATES", "NetworkFailure", "outgoing"),
        RelationshipDef("MEASURED_AT", "Site", "outgoing"),
    ),
))

_register(LabelDef(
    label="Dispute",
    scope_key="customer_id",
    scope_via="Charge",
    id_prefix="DSP-GEN-",
    display_name="Dispute",
    is_issue_bearing=True,
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("dispute_reason", "string"),
        PropertyDef("status", "string"),
        PropertyDef("resolution_notes", "string"),
        PropertyDef("raised_date", "date"),
    ),
    relationships=(
        RelationshipDef("DISPUTES", "Charge", "outgoing"),
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "outgoing"),
    ),
))

_register(LabelDef(
    label="SlaCredit",
    scope_key="customer_id",
    scope_via="Incident",
    id_prefix="ADJ-GEN-",
    display_name="SLA Credit",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("customer_id", "string"),
        PropertyDef("credit_amount", "number"),
        PropertyDef("reason", "string"),
        PropertyDef("disruption_id", "string"),
    ),
    relationships=(
        RelationshipDef("COMPENSATES", "Incident", "outgoing"),
    ),
))

# --- Remediation ---

_register(LabelDef(
    label="RemediationAction",
    id_prefix="REM-GEN-",
    display_name="Remediation Action",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("action_type", "string"),
        PropertyDef("description", "string"),
        PropertyDef("status", "string"),
        PropertyDef("resolved_at", "date"),
        PropertyDef("assigned_to", "string"),
    ),
    relationships=(
        RelationshipDef("REMEDIATED_BY", "RemediationAction", "incoming"),  # from issue nodes
    ),
))

# --- Infrastructure ---

_register(LabelDef(
    label="Site",
    id_prefix="SITE-",
    display_name="Cell Site",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("site_name", "string"),
        PropertyDef("region", "string"),
        PropertyDef("site_type", "string"),
    ),
    relationships=(
        RelationshipDef("AT_SITE", "Site", "incoming"),
        RelationshipDef("MEASURED_AT", "Site", "incoming"),
    ),
))

_register(LabelDef(
    label="Service",
    id_prefix="SVC-",
    display_name="Telecom Service",
    properties=(
        PropertyDef("id", "string"),
        PropertyDef("service_name", "string"),
        PropertyDef("service_type", "string"),
    ),
    relationships=(
        RelationshipDef("EMITTED_BY", "Service", "incoming"),
    ),
))


# ---------------------------------------------------------------------------
# Registry query helpers
# ---------------------------------------------------------------------------

def all_labels() -> list[str]:
    """Return all registered label names."""
    return list(LABELS.keys())


def all_relationship_types() -> set[str]:
    """Return every relationship type across the whole registry."""
    types: set[str] = set()
    for ldef in LABELS.values():
        for rel in ldef.relationships:
            types.add(rel.type)
    return types


def all_properties(label: str) -> list[str]:
    """Return property names for a given label."""
    ldef = LABELS.get(label)
    if not ldef:
        return []
    return [p.name for p in ldef.properties if not p.sensitive]


def issue_bearing_labels() -> list[str]:
    """Return labels that are issue-bearing (participate in remediation lookups)."""
    return [ldef.label for ldef in LABELS.values() if ldef.is_issue_bearing]


def label_for_prefix(prefix: str) -> str | None:
    """Given an ID prefix like 'NF-', return the label name."""
    for ldef in LABELS.values():
        if ldef.id_prefix and prefix.upper().startswith(ldef.id_prefix.split("-")[0] + "-"):
            return ldef.label
    return None


def scope_chain(label: str) -> list[str]:
    """Return the chain of labels from this label up to Customer (the scope anchor)."""
    chain = [label]
    visited = {label}
    current = label
    while current != "Customer":
        ldef = LABELS.get(current)
        if not ldef or not ldef.scope_via:
            break
        if ldef.scope_via in visited:
            break
        chain.append(ldef.scope_via)
        visited.add(ldef.scope_via)
        current = ldef.scope_via
    return chain


def registry_for_generation(label_filter: list[str] | None = None) -> dict[str, Any]:
    """Return the registry slice for generation prompts.

    This is what gets passed to the LLM for schema-grounded Cypher generation.
    """
    labels_to_include = label_filter or list(LABELS.keys())
    result: dict[str, Any] = {"labels": [], "relationships": [], "properties": {}, "scope_keys": {}}

    seen_rels: set[str] = set()
    for label_name in labels_to_include:
        ldef = LABELS.get(label_name)
        if not ldef:
            continue
        result["labels"].append(label_name)
        props = [p.name for p in ldef.properties if not p.sensitive]
        result["properties"][label_name] = props
        if ldef.scope_key:
            result["scope_keys"][label_name] = ldef.scope_key

        for rel in ldef.relationships:
            rel_key = f"{label_name}-{rel.type}-{rel.target_label}"
            if rel_key not in seen_rels:
                seen_rels.add(rel_key)
                result["relationships"].append({
                    "from": label_name if rel.direction == "outgoing" else rel.target_label,
                    "type": rel.type,
                    "to": rel.target_label if rel.direction == "outgoing" else label_name,
                })

    return result


# ---------------------------------------------------------------------------
# Category groupings for KG Explorer
# ---------------------------------------------------------------------------

CATEGORY_GROUPS: dict[str, list[str]] = {
    "accounts": ["Customer", "Account"],
    "billing": ["Invoice", "Charge"],
    "network_issues": ["NetworkFailure", "ApiKpiBreach", "PmCounter"],
    "service_issues": ["Incident", "LogEvent"],
    "payments": ["PaymentFailure"],
    "disputes": ["Dispute"],
    "root_causes": ["NetworkFailure", "PaymentFailure", "Incident", "LogEvent", "ApiKpiBreach"],
    "remediations": ["RemediationAction"],
    "evidence": ["PmCounter", "SlaCredit", "Dispute"],
    "infrastructure": ["Site", "Service"],
}
