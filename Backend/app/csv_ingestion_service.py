"""
CSV Ingestion Service — Telecom Billing RCA Pipeline

Implements the deterministic ingestion pipeline from the Ingestion Pipeline Spec:
- Section 4: 6-step ingestion process (receive, validate, map, write, embed, index)
- Section 7: Data → Ontology Mapping Table
- Section 9: Deterministic entity extraction (no LLM)
- Section 11: Full Write Cypher for all 9 domains
- Section 15: Data Quality Thresholds
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

from .vector_service import VectorService

# ---------------------------------------------------------------------------
# Domain Configuration — Section 7 & 11 of the Ingestion Pipeline Spec
# ---------------------------------------------------------------------------
# Each domain defines:
#   - node_label: Neo4j label(s) for the primary node
#   - id_column: which CSV column is the canonical_id
#   - property_map: CSV column → Neo4j property name
#   - required_columns: columns that must be non-empty
#   - enum_validations: columns with allowed values
#   - additional_nodes: secondary nodes created from the same row
#   - relationships: edges created from the row

DOMAIN_CONFIGS: dict[str, dict[str, Any]] = {
    "network": {
        "node_label": "Alarm",
        "id_column": "failure_id",
        "property_map": {
            "failure_id": "canonical_id",
            "failure_type": "alarm_type",
            "severity": "severity",
            "affected_service": "affected_service",
            "affected_site": "affected_site",
            "event_date": "raised_at",
            "duration_minutes": "duration_minutes",
            "impact_description": "description",
        },
        "required_columns": ["customer_id", "account_id", "failure_id"],
        "enum_validations": {
            "severity": {"LOW", "MEDIUM", "HIGH", "CRITICAL"},
        },
        "date_columns": ["event_date"],
    },
    "billing": {
        "node_label": "Invoice",
        "id_column": "invoice_id",
        "property_map": {
            "invoice_id": "canonical_id",
            "billing_period": "billing_period",
            "invoice_amount": "amount",
            "currency": "currency",
            "invoice_status": "status",
            "due_date": "due_date",
            "source_system": "source_system",
        },
        "required_columns": ["customer_id", "account_id", "invoice_id", "charge_id"],
        "enum_validations": {
            "invoice_status": {"ISSUED", "PAID", "PAST_DUE", "CANCELLED", "DISPUTED"},
        },
        "date_columns": ["due_date"],
        "additional_nodes": {
            "ChargingRecord": {
                "id_column": "charge_id",
                "property_map": {
                    "charge_id": "canonical_id",
                    "charge_category": "charge_category",
                    "charge_description": "description",
                    "charge_amount": "amount",
                },
            }
        },
    },
    "complaints": {
        "node_label": "Complaint",
        "id_column": "dispute_id",
        "property_map": {
            "dispute_id": "canonical_id",
            "charge_id": "disputed_charge_id",
            "reason": "complaint_type",
            "status": "status",
            "raised_date": "opened_at",
            "resolution_details": "resolution_text",
        },
        "required_columns": ["customer_id", "account_id", "dispute_id"],
        "enum_validations": {
            "status": {"OPEN", "IN_REVIEW", "RESOLVED", "CLOSED", "ESCALATED"},
        },
        "date_columns": ["raised_date"],
    },
    "incident": {
        "node_label": "ServiceProblem",
        "id_column": "disruption_id",
        "property_map": {
            "disruption_id": "canonical_id",
            "service_type": "service_type",
            "disruption_reason": "description",
            "severity": "severity",
            "event_date": "opened_at",
            "resolution_date": "resolved_at",
            "impact_on_charges": "impact_description",
        },
        "required_columns": ["customer_id", "account_id", "disruption_id"],
        "enum_validations": {
            "severity": {"LOW", "MEDIUM", "HIGH", "CRITICAL"},
        },
        "date_columns": ["event_date", "resolution_date"],
    },
    "pm_counters": {
        "node_label": "PMCounter",
        "id_column": "counter_id",
        "property_map": {
            "counter_id": "canonical_id",
            "site_id": "site_id",
            "kpi_name": "counter_name",
            "value": "value",
            "unit": "unit",
            "threshold": "threshold_value",
            "severity": "severity",
            "observed_at": "observed_at",
            "impact_description": "description",
        },
        "required_columns": ["customer_id", "account_id", "counter_id"],
        "date_columns": ["observed_at"],
    },
    "api": {
        "node_label": "KPIObservation",
        "id_column": "kpi_observation_id",
        "property_map": {
            "kpi_observation_id": "canonical_id",
            "kpi_name": "kpi_name",
            "kpi_value": "kpi_value",
            "threshold_value": "threshold_value",
            "unit": "unit",
            "severity": "severity",
            "measurement_window": "measurement_window",
            "observed_at": "observed_at",
            "source_system": "source_system",
            "impact_description": "description",
        },
        "required_columns": ["customer_id", "account_id", "kpi_observation_id"],
        "date_columns": ["observed_at"],
    },
    "logs": {
        "node_label": "LogEvent",
        "id_column": "log_id",
        "property_map": {
            "log_id": "canonical_id",
            "log_source": "log_source",
            "service_name": "service_name",
            "log_level": "level",
            "message": "message",
            "trace_id": "trace_id",
            "host_or_pod": "host",
            "event_time": "event_time",
            "source_system": "source_system",
            "impact_description": "description",
        },
        "required_columns": ["customer_id", "account_id", "log_id"],
        "enum_validations": {
            "log_level": {"DEBUG", "INFO", "WARN", "ERROR", "FATAL", "CRITICAL"},
        },
        "date_columns": ["event_time"],
    },
    "sla_credits": {
        "node_label": "Adjustment",
        "id_column": "adjustment_id",
        "property_map": {
            "adjustment_id": "canonical_id",
            "adjustment_type": "adjustment_type",
            "amount": "amount",
            "currency": "currency",
            "reason": "reason",
            "related_incident_id": "related_incident_id",
            "issued_date": "issued_at",
            "status": "status",
        },
        "required_columns": ["customer_id", "account_id", "adjustment_id"],
        "date_columns": ["issued_date"],
    },
    "payment_failures": {
        "node_label": "Payment",
        "id_column": "payment_id",
        "property_map": {
            "payment_id": "canonical_id",
            "invoice_id": "invoice_id",
            "payment_amount": "amount",
            "payment_date": "payment_date",
            "payment_status": "status",
            "card_last_four": "card_last_four",
            "issuer_response": "issuer_response",
        },
        "required_columns": ["customer_id", "account_id", "payment_id"],
        "date_columns": ["payment_date", "failure_date"],
        "additional_nodes": {
            "Dunning": {
                "id_column": "payment_failure_id",
                "property_map": {
                    "payment_failure_id": "canonical_id",
                    "failure_type": "failure_type",
                    "failure_reason": "reason",
                    "failure_date": "failed_at",
                },
            }
        },
    },
}


# ---------------------------------------------------------------------------
# Section 11.9 — Relationship Patterns per Domain
# ---------------------------------------------------------------------------
# Account-centric shape: every event links to Customer → BillingAccount

CROSS_DOMAIN_RELATIONSHIPS = [
    # complaints.charge_id → billing.charge_id
    {
        "name": "DISPUTES",
        "match_from": ("Complaint", "disputed_charge_id"),
        "match_to": ("ChargingRecord", "canonical_id"),
    },
    # sla_credits.related_incident_id → incident.disruption_id
    {
        "name": "CREDITS_FOR",
        "match_from": ("Adjustment", "related_incident_id"),
        "match_to": ("ServiceProblem", "canonical_id"),
    },
    # payment_failures.invoice_id → billing.invoice_id
    {
        "name": "PAYMENT_FOR",
        "match_from": ("Payment", "invoice_id"),
        "match_to": ("Invoice", "canonical_id"),
    },
    # pm_counters.site_id → network.affected_site (same site)
    {
        "name": "OBSERVED_AT_SAME_SITE",
        "match_from": ("PMCounter", "site_id"),
        "match_to": ("Alarm", "affected_site"),
    },
]


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class IngestionStats:
    domain: str
    rows_received: int = 0
    rows_written: int = 0
    rows_rejected: int = 0
    nodes_created: int = 0
    relationships_created: int = 0
    chunks_embedded: int = 0
    rejected_details: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CSVIngestionService:
    """Deterministic CSV-to-Neo4j ingestion pipeline.

    Implements Sections 4, 9, 11 of the Ingestion Pipeline Spec:
    - No LLM involvement in entity extraction
    - Column-to-property mapping written once per domain
    - Values passed as Cypher parameters (injection-safe)
    - MERGE for idempotent writes
    """

    def __init__(
        self,
        neo4j_uri: str,
        neo4j_user: str,
        neo4j_password: str,
        neo4j_database: str = "neo4j",
        vector_service: VectorService | None = None,
    ):
        self._uri = neo4j_uri
        self._user = neo4j_user
        self._password = neo4j_password
        self._database = neo4j_database
        self._driver = None
        self._vector_service = vector_service

    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self._uri, auth=(self._user, self._password), connection_timeout=5
            )
        return self._driver

    def close(self):
        if self._driver:
            self._driver.close()
            self._driver = None

    # -----------------------------------------------------------------------
    # Step 2: Neo4j Schema Setup (constraints + indexes)
    # -----------------------------------------------------------------------
    def setup_schema(self) -> list[str]:
        """Create UNIQUE constraints and indexes for all domain node labels."""
        labels = ["Customer", "BillingAccount"]
        for config in DOMAIN_CONFIGS.values():
            labels.append(config["node_label"])
            for extra_label in (config.get("additional_nodes") or {}):
                labels.append(extra_label)

        statements = []
        for label in sorted(set(labels)):
            statements.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.canonical_id IS UNIQUE"
            )
        # Additional indexes for common query patterns
        statements.extend([
            "CREATE INDEX IF NOT EXISTS FOR (n:Customer) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:BillingAccount) ON (n.account_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Alarm) ON (n.affected_site)",
            "CREATE INDEX IF NOT EXISTS FOR (n:PMCounter) ON (n.site_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Invoice) ON (n.status)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Complaint) ON (n.status)",
            "CREATE INDEX IF NOT EXISTS FOR (n:ServiceProblem) ON (n.severity)",
        ])

        executed = []
        with self.driver.session(database=self._database) as session:
            for stmt in statements:
                try:
                    session.run(stmt)
                    executed.append(stmt)
                except Exception as exc:
                    executed.append(f"SKIPPED: {stmt} — {exc}")
        return executed

    # -----------------------------------------------------------------------
    # Step 4: Validate Row (Section 4.1 & 15)
    # -----------------------------------------------------------------------
    @staticmethod
    def _validate_row(row: dict[str, str], config: dict[str, Any], row_number: int) -> ValidationResult:
        errors = []

        # Check required columns
        for col in config.get("required_columns", []):
            if not (row.get(col) or "").strip():
                errors.append(f"Row {row_number}: required column '{col}' is empty")

        # Check enum validations
        for col, allowed in config.get("enum_validations", {}).items():
            val = (row.get(col) or "").strip().upper()
            if val and val not in allowed:
                errors.append(f"Row {row_number}: '{col}' value '{val}' not in {allowed}")

        # Check date columns are parseable
        for col in config.get("date_columns", []):
            val = (row.get(col) or "").strip()
            if val:
                try:
                    # Accept ISO 8601 formats
                    if "T" in val:
                        datetime.fromisoformat(val.replace("Z", "+00:00"))
                    else:
                        datetime.strptime(val, "%Y-%m-%d")
                except (ValueError, TypeError):
                    errors.append(f"Row {row_number}: '{col}' value '{val}' is not a valid date")

        # Check numeric columns
        for col in ("amount", "charge_amount", "invoice_amount", "payment_amount", "value", "threshold",
                     "kpi_value", "threshold_value", "duration_minutes"):
            val = (row.get(col) or "").strip()
            if val:
                try:
                    float(val)
                except (ValueError, TypeError):
                    errors.append(f"Row {row_number}: '{col}' value '{val}' is not numeric")

        return ValidationResult(valid=len(errors) == 0, errors=errors)

    # -----------------------------------------------------------------------
    # Step 5: Ingest a single domain CSV
    # -----------------------------------------------------------------------
    def ingest_domain(self, domain: str, csv_content: str | bytes) -> IngestionStats:
        """Ingest a CSV for a specific domain into Neo4j."""
        if domain not in DOMAIN_CONFIGS:
            raise ValueError(f"Unknown domain '{domain}'. Valid domains: {list(DOMAIN_CONFIGS.keys())}")

        config = DOMAIN_CONFIGS[domain]
        stats = IngestionStats(domain=domain)

        # Parse CSV
        if isinstance(csv_content, bytes):
            csv_content = csv_content.decode("utf-8-sig")

        reader = csv.DictReader(io.StringIO(csv_content))
        rows = list(reader)
        stats.rows_received = len(rows)

        if not rows:
            stats.warnings.append(f"No data rows found in {domain} CSV")
            return stats

        # Validate headers
        expected = set(config.get("required_columns", []))
        actual = set(rows[0].keys())
        missing = expected - actual
        if missing:
            raise ValueError(f"Missing required columns for domain '{domain}': {missing}")

        # Process each row
        embed_batch: list[dict[str, Any]] = []
        with self.driver.session(database=self._database) as session:
            for row_num, row in enumerate(rows, start=2):
                validation = self._validate_row(row, config, row_num)
                if not validation.valid:
                    stats.rows_rejected += 1
                    stats.rejected_details.append({
                        "row": row_num,
                        "errors": validation.errors,
                    })
                    continue

                try:
                    node_count, rel_count, props = self._write_row(session, domain, config, row)
                    stats.rows_written += 1
                    stats.nodes_created += node_count
                    stats.relationships_created += rel_count

                    # Collect row for batch embedding (Step 5: Embed)
                    if self._vector_service is not None:
                        embed_batch.append({
                            "domain": domain,
                            "canonical_id": row[config["id_column"]].strip(),
                            "customer_id": row["customer_id"].strip(),
                            "account_id": row["account_id"].strip(),
                            "node_label": config["node_label"],
                            "props": props,
                        })
                except Exception as exc:
                    stats.rows_rejected += 1
                    stats.rejected_details.append({
                        "row": row_num,
                        "errors": [str(exc)],
                    })

        # Step 5: Batch embed all successfully written rows
        if self._vector_service is not None and embed_batch:
            try:
                stats.chunks_embedded = self._vector_service.embed_batch(embed_batch)
            except Exception as exc:
                stats.warnings.append(f"Vector embedding failed: {exc}")

        return stats

    # -----------------------------------------------------------------------
    # Step 5b: Write a single row to Neo4j (Section 11 Cypher patterns)
    # -----------------------------------------------------------------------
    def _write_row(self, session: Any, domain: str, config: dict[str, Any], row: dict[str, str]) -> tuple[int, int, dict[str, Any]]:
        """Write one CSV row as Neo4j nodes + relationships. Returns (nodes_created, rels_created, props)."""
        customer_id = row["customer_id"].strip()
        account_id = row["account_id"].strip()
        node_label = config["node_label"]
        id_col = config["id_column"]
        canonical_id = row[id_col].strip()

        # Build node properties from the property map
        props = {}
        for csv_col, neo4j_prop in config["property_map"].items():
            val = (row.get(csv_col) or "").strip()
            if val:
                # Convert numeric values
                if neo4j_prop in ("amount", "value", "threshold_value", "kpi_value", "duration_minutes"):
                    try:
                        props[neo4j_prop] = float(val)
                    except ValueError:
                        props[neo4j_prop] = val
                else:
                    props[neo4j_prop] = val

        props["source_system"] = (row.get("source_system") or f"{domain}_csv").strip()
        props["source_domain"] = domain

        nodes_created = 0
        rels_created = 0

        # 1. MERGE Customer + BillingAccount
        session.run(
            """
            MERGE (c:Customer {canonical_id: $customer_id})
            SET c.customer_id = $customer_id, c.name = $customer_id
            MERGE (ba:BillingAccount {canonical_id: $account_id})
            SET ba.account_id = $account_id, ba.name = $account_id
            MERGE (c)-[:HAS_ACCOUNT]->(ba)
            """,
            customer_id=customer_id,
            account_id=account_id,
        )
        nodes_created += 2
        rels_created += 1

        # 2. MERGE primary domain node
        set_clause = ", ".join(f"n.{k} = ${k}" for k in props if k != "canonical_id")
        session.run(
            f"""
            MERGE (n:{node_label} {{canonical_id: $canonical_id}})
            SET {set_clause}
            """,
            canonical_id=canonical_id,
            **{k: v for k, v in props.items() if k != "canonical_id"},
        )
        nodes_created += 1

        # 3. Link primary node to BillingAccount
        rel_type = f"HAS_{node_label.upper()}"
        if node_label == "Invoice":
            rel_type = "HAS_INVOICE"
        elif node_label == "Alarm":
            rel_type = "HAS_ALARM"
        elif node_label == "Complaint":
            rel_type = "HAS_COMPLAINT"
        elif node_label == "ServiceProblem":
            rel_type = "HAS_DISRUPTION"
        elif node_label == "PMCounter":
            rel_type = "HAS_PM_COUNTER"
        elif node_label == "KPIObservation":
            rel_type = "HAS_KPI_OBSERVATION"
        elif node_label == "LogEvent":
            rel_type = "HAS_LOG"
        elif node_label == "Adjustment":
            rel_type = "HAS_ADJUSTMENT"
        elif node_label == "Payment":
            rel_type = "HAS_PAYMENT"

        session.run(
            f"""
            MATCH (ba:BillingAccount {{canonical_id: $account_id}})
            MATCH (n:{node_label} {{canonical_id: $canonical_id}})
            MERGE (ba)-[:{rel_type}]->(n)
            """,
            account_id=account_id,
            canonical_id=canonical_id,
        )
        rels_created += 1

        # 4. Create additional nodes (e.g., ChargingRecord from billing, Dunning from payment_failures)
        for extra_label, extra_config in (config.get("additional_nodes") or {}).items():
            extra_id_col = extra_config["id_column"]
            extra_id = (row.get(extra_id_col) or "").strip()
            if not extra_id:
                continue

            extra_props = {}
            for csv_col, neo4j_prop in extra_config["property_map"].items():
                val = (row.get(csv_col) or "").strip()
                if val:
                    if neo4j_prop in ("amount",):
                        try:
                            extra_props[neo4j_prop] = float(val)
                        except ValueError:
                            extra_props[neo4j_prop] = val
                    else:
                        extra_props[neo4j_prop] = val

            extra_props["source_system"] = props.get("source_system", f"{domain}_csv")
            extra_props["source_domain"] = domain

            extra_set = ", ".join(f"n.{k} = ${k}" for k in extra_props if k != "canonical_id")
            session.run(
                f"""
                MERGE (n:{extra_label} {{canonical_id: $canonical_id}})
                SET {extra_set}
                """,
                canonical_id=extra_id,
                **{k: v for k, v in extra_props.items() if k != "canonical_id"},
            )
            nodes_created += 1

            # Link additional node to primary node
            if extra_label == "ChargingRecord":
                session.run(
                    """
                    MATCH (inv:Invoice {canonical_id: $invoice_id})
                    MATCH (cr:ChargingRecord {canonical_id: $charge_id})
                    MERGE (inv)-[:CONTAINS]->(cr)
                    """,
                    invoice_id=canonical_id,
                    charge_id=extra_id,
                )
            elif extra_label == "Dunning":
                session.run(
                    """
                    MATCH (p:Payment {canonical_id: $payment_id})
                    MATCH (d:Dunning {canonical_id: $dunning_id})
                    MERGE (p)-[:HAS_DUNNING]->(d)
                    """,
                    payment_id=canonical_id,
                    dunning_id=extra_id,
                )
            rels_created += 1

        return nodes_created, rels_created, props

    # -----------------------------------------------------------------------
    # Step 7: Create Cross-Domain Relationships
    # -----------------------------------------------------------------------
    def create_cross_domain_relationships(self) -> dict[str, int]:
        """Create FK-based relationships across domains after all CSVs are loaded."""
        results = {}
        with self.driver.session(database=self._database) as session:
            # 1. Complaint → DISPUTES → ChargingRecord (via charge_id)
            result = session.run("""
                MATCH (comp:Complaint) WHERE comp.disputed_charge_id IS NOT NULL
                MATCH (cr:ChargingRecord {canonical_id: comp.disputed_charge_id})
                MERGE (comp)-[:DISPUTES]->(cr)
                RETURN count(*) AS count
            """)
            results["DISPUTES"] = result.single()["count"]

            # 2. Adjustment → CREDITS_FOR → ServiceProblem (via related_incident_id)
            result = session.run("""
                MATCH (adj:Adjustment) WHERE adj.related_incident_id IS NOT NULL
                MATCH (sp:ServiceProblem {canonical_id: adj.related_incident_id})
                MERGE (adj)-[:CREDITS_FOR]->(sp)
                RETURN count(*) AS count
            """)
            results["CREDITS_FOR"] = result.single()["count"]

            # 3. Payment → PAYMENT_FOR → Invoice (via invoice_id)
            result = session.run("""
                MATCH (p:Payment) WHERE p.invoice_id IS NOT NULL
                MATCH (inv:Invoice {canonical_id: p.invoice_id})
                MERGE (p)-[:PAYMENT_FOR]->(inv)
                RETURN count(*) AS count
            """)
            results["PAYMENT_FOR"] = result.single()["count"]

            # 4. PMCounter ←→ Alarm at same site
            result = session.run("""
                MATCH (pm:PMCounter) WHERE pm.site_id IS NOT NULL
                MATCH (a:Alarm {affected_site: pm.site_id})
                MERGE (pm)-[:OBSERVED_AT_SAME_SITE]->(a)
                RETURN count(*) AS count
            """)
            results["OBSERVED_AT_SAME_SITE"] = result.single()["count"]

            # 5. Temporal correlation: events from same customer within 24h
            # Link LogEvent → ServiceProblem for same customer
            result = session.run("""
                MATCH (ba:BillingAccount)-[:HAS_LOG]->(log:LogEvent)
                MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
                MERGE (log)-[:EVIDENCES]->(sp)
                RETURN count(*) AS count
            """)
            results["EVIDENCES"] = result.single()["count"]

            # 6. Link KPIObservation to Alarm for same customer
            result = session.run("""
                MATCH (ba:BillingAccount)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
                MATCH (ba)-[:HAS_ALARM]->(a:Alarm)
                MERGE (kpi)-[:CORRELATED_WITH]->(a)
                RETURN count(*) AS count
            """)
            results["CORRELATED_WITH"] = result.single()["count"]

        return results

    # -----------------------------------------------------------------------
    # Batch Ingest: Load all CSVs from a directory
    # -----------------------------------------------------------------------
    def ingest_directory(self, directory: str | Path) -> dict[str, IngestionStats]:
        """Ingest all recognized CSV files from a directory."""
        directory = Path(directory)
        results = {}

        # Map filenames to domains
        file_domain_map = {
            "network.csv": "network",
            "billing.csv": "billing",
            "complaints.csv": "complaints",
            "incident.csv": "incident",
            "pm_counters.csv": "pm_counters",
            "api.csv": "api",
            "logs.csv": "logs",
            "sla_credits.csv": "sla_credits",
            "payment_failures.csv": "payment_failures",
        }

        for filename, domain in file_domain_map.items():
            filepath = directory / filename
            if filepath.exists():
                csv_content = filepath.read_text(encoding="utf-8-sig")
                results[domain] = self.ingest_domain(domain, csv_content)

        return results

    # -----------------------------------------------------------------------
    # Verification Queries
    # -----------------------------------------------------------------------
    def verify_graph(self) -> dict[str, Any]:
        """Run verification queries to confirm the graph is loaded correctly."""
        with self.driver.session(database=self._database) as session:
            # Node counts per label
            result = session.run("""
                MATCH (n)
                WITH labels(n) AS lbls
                UNWIND lbls AS label
                RETURN label, count(*) AS count
                ORDER BY count DESC
            """)
            node_counts = {r["label"]: r["count"] for r in result}

            # Relationship counts per type
            result = session.run("""
                MATCH ()-[r]->()
                RETURN type(r) AS type, count(*) AS count
                ORDER BY count DESC
            """)
            rel_counts = {r["type"]: r["count"] for r in result}

            # Cross-domain link verification
            result = session.run("""
                OPTIONAL MATCH (comp:Complaint)-[:DISPUTES]->(cr:ChargingRecord)
                WITH count(comp) AS disputes
                OPTIONAL MATCH (adj:Adjustment)-[:CREDITS_FOR]->(sp:ServiceProblem)
                WITH disputes, count(adj) AS credits
                OPTIONAL MATCH (p:Payment)-[:PAYMENT_FOR]->(inv:Invoice)
                WITH disputes, credits, count(p) AS payments
                OPTIONAL MATCH (pm:PMCounter)-[:OBSERVED_AT_SAME_SITE]->(a:Alarm)
                RETURN disputes, credits, payments, count(pm) AS site_correlations
            """)
            cross_domain = dict(result.single())

            # Customer count
            result = session.run("MATCH (c:Customer) RETURN count(c) AS count")
            customer_count = result.single()["count"]

            return {
                "node_counts": node_counts,
                "relationship_counts": rel_counts,
                "cross_domain_links": cross_domain,
                "total_customers": customer_count,
            }

    # -----------------------------------------------------------------------
    # Customer queries
    # -----------------------------------------------------------------------
    def list_customers(self) -> list[dict[str, Any]]:
        """List all customers with their data profile (which domains they appear in)."""
        with self.driver.session(database=self._database) as session:
            result = session.run("""
                MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba:BillingAccount)
                OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)
                OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)
                OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)
                OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
                OPTIONAL MATCH (ba)-[:HAS_PM_COUNTER]->(pm:PMCounter)
                OPTIONAL MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
                OPTIONAL MATCH (ba)-[:HAS_LOG]->(log:LogEvent)
                OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
                OPTIONAL MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)
                WITH c.canonical_id AS customer_id,
                     ba.canonical_id AS account_id,
                     count(DISTINCT alarm) AS alarms,
                     count(DISTINCT inv) AS invoices,
                     count(DISTINCT comp) AS complaints,
                     count(DISTINCT sp) AS disruptions,
                     count(DISTINCT pm) AS pm_counters,
                     count(DISTINCT kpi) AS kpi_observations,
                     count(DISTINCT log) AS logs,
                     count(DISTINCT adj) AS adjustments,
                     count(DISTINCT pay) AS payments
                WITH customer_id, account_id,
                     alarms, invoices, complaints, disruptions,
                     pm_counters, kpi_observations, logs, adjustments, payments,
                     alarms + invoices + complaints + disruptions + pm_counters +
                     kpi_observations + logs + adjustments + payments AS total_events,
                     CASE
                       WHEN alarms > 0 AND pm_counters > 0 THEN 'NETWORK_RCA'
                       WHEN alarms > 0 THEN 'NETWORK_IMPACT'
                       WHEN payments > 0 THEN 'PAYMENT_DUNNING'
                       WHEN disruptions > 0 AND adjustments > 0 THEN 'SERVICE_DISRUPTION'
                       WHEN logs > 0 THEN 'SYSTEM_ERROR'
                       WHEN complaints > 0 THEN 'COMPLAINT'
                       ELSE 'BILLING_ONLY'
                     END AS customer_type
                RETURN customer_id, account_id, customer_type, total_events,
                       alarms, invoices, complaints, disruptions,
                       pm_counters, kpi_observations, logs, adjustments, payments
                ORDER BY total_events DESC, customer_id
            """)
            return [dict(r) for r in result]

    def customer_360(self, customer_id: str) -> dict[str, Any]:
        """Get full 360-degree view of a customer's graph."""
        with self.driver.session(database=self._database) as session:
            result = session.run("""
                MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
                OPTIONAL MATCH (ba)-[r]->(n)
                WITH c, ba, collect(DISTINCT n) AS related_nodes, collect(DISTINCT r) AS rels
                UNWIND related_nodes AS node
                OPTIONAL MATCH (node)-[r2]->(linked)
                WHERE NOT linked:BillingAccount AND NOT linked:Customer
                RETURN c, ba,
                       related_nodes + collect(DISTINCT linked) AS all_nodes,
                       rels + collect(DISTINCT r2) AS all_rels
            """, customer_id=customer_id)
            record = result.single()
            if not record:
                return {"customer_id": customer_id, "found": False}

            return {
                "customer_id": customer_id,
                "found": True,
                "account_id": record["ba"].get("canonical_id"),
                "node_count": len(record["all_nodes"]),
                "relationship_count": len(record["all_rels"]),
            }

    def clear_graph(self) -> dict[str, int]:
        """Delete all nodes and relationships from the graph database."""
        with self.driver.session(database=self._database) as session:
            # Count before deletion
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

            # Delete in batches to avoid memory issues on large graphs
            deleted_rels = 0
            while True:
                result = session.run(
                    "MATCH ()-[r]->() WITH r LIMIT 10000 DELETE r RETURN count(r) AS c"
                )
                batch = result.single()["c"]
                deleted_rels += batch
                if batch == 0:
                    break

            deleted_nodes = 0
            while True:
                result = session.run(
                    "MATCH (n) WITH n LIMIT 10000 DETACH DELETE n RETURN count(n) AS c"
                )
                batch = result.single()["c"]
                deleted_nodes += batch
                if batch == 0:
                    break

            # Drop all constraints and indexes
            dropped_constraints = 0
            for record in session.run("SHOW CONSTRAINTS"):
                session.run(f"DROP CONSTRAINT {record['name']}")
                dropped_constraints += 1

            dropped_indexes = 0
            for record in session.run("SHOW INDEXES"):
                if record["type"] != "LOOKUP":
                    try:
                        session.run(f"DROP INDEX {record['name']}")
                        dropped_indexes += 1
                    except Exception:
                        pass

            return {
                "deleted_nodes": deleted_nodes,
                "deleted_relationships": deleted_rels,
                "dropped_constraints": dropped_constraints,
                "dropped_indexes": dropped_indexes,
                "previous_node_count": node_count,
                "previous_relationship_count": rel_count,
            }
