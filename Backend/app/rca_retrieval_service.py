"""
RCA Retrieval Service — Pre-built Cypher Queries

Implements Ontology Spec Section 4 (7 query patterns) adapted for the
account-centric graph shape used by the CSV ingestion pipeline.

These are deterministic, parameterized queries — no LLM involved in
graph retrieval. The LLM is only used for narration (Section 9.3).
"""

from __future__ import annotations

from typing import Any

from neo4j import GraphDatabase


class RCARetrievalService:
    """Executes the 7 pre-built RCA retrieval query patterns against Neo4j."""

    def __init__(self, neo4j_uri: str, neo4j_user: str, neo4j_password: str, neo4j_database: str = "neo4j"):
        self._uri = neo4j_uri
        self._user = neo4j_user
        self._password = neo4j_password
        self._database = neo4j_database
        self._driver = None

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

    # -------------------------------------------------------------------
    # Q1: Full RCA Chain from Customer (adapted from Ontology Spec 4.1)
    #
    # Traversal: Customer → Alarms → KPI breaches → ServiceProblems
    #            → Billing impact → Complaints → SLA Credits
    # -------------------------------------------------------------------
    def rca_full_chain(self, customer_id: str) -> dict[str, Any]:
        """Full RCA chain for a customer: alarm → KPI → incident → billing → complaint → credit."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)

        OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)
        OPTIONAL MATCH (ba)-[:HAS_PM_COUNTER]->(pm:PMCounter)
        OPTIONAL MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
        OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
        OPTIONAL MATCH (ba)-[:HAS_LOG]->(log:LogEvent)
        OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)
        OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)
        OPTIONAL MATCH (comp)-[:DISPUTES]->(disputed_cr:ChargingRecord)
        OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
        OPTIONAL MATCH (adj)-[:CREDITS_FOR]->(credited_sp:ServiceProblem)
        OPTIONAL MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)
        OPTIONAL MATCH (pay)-[:PAYMENT_FOR]->(paid_inv:Invoice)
        OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)

        RETURN c.canonical_id AS customer_id,
               ba.canonical_id AS account_id,
               collect(DISTINCT {
                 id: alarm.canonical_id, type: alarm.alarm_type,
                 severity: alarm.severity, site: alarm.affected_site,
                 service: alarm.affected_service, raised_at: alarm.raised_at,
                 duration: alarm.duration_minutes, description: alarm.description
               }) AS alarms,
               collect(DISTINCT {
                 id: pm.canonical_id, kpi: pm.counter_name,
                 value: pm.value, threshold: pm.threshold_value,
                 site: pm.site_id, severity: pm.severity
               }) AS pm_counters,
               collect(DISTINCT {
                 id: kpi.canonical_id, kpi: kpi.kpi_name,
                 value: kpi.kpi_value, threshold: kpi.threshold_value,
                 severity: kpi.severity, unit: kpi.unit
               }) AS kpi_observations,
               collect(DISTINCT {
                 id: sp.canonical_id, service: sp.service_type,
                 reason: sp.description, severity: sp.severity,
                 opened: sp.opened_at, resolved: sp.resolved_at,
                 impact: sp.impact_description
               }) AS service_problems,
               collect(DISTINCT {
                 id: log.canonical_id, source: log.log_source,
                 service: log.service_name, level: log.level,
                 message: log.message, trace: log.trace_id
               }) AS log_events,
               collect(DISTINCT {
                 invoice_id: inv.canonical_id, amount: inv.amount,
                 status: inv.status, period: inv.billing_period,
                 charge_id: cr.canonical_id, charge_category: cr.charge_category,
                 charge_amount: cr.amount, charge_desc: cr.description
               }) AS billing,
               collect(DISTINCT {
                 id: comp.canonical_id, reason: comp.complaint_type,
                 status: comp.status, opened: comp.opened_at,
                 resolution: comp.resolution_text,
                 disputed_charge: disputed_cr.canonical_id
               }) AS complaints,
               collect(DISTINCT {
                 id: adj.canonical_id, type: adj.adjustment_type,
                 amount: adj.amount, reason: adj.reason,
                 incident: credited_sp.canonical_id
               }) AS adjustments,
               collect(DISTINCT {
                 id: pay.canonical_id, amount: pay.amount,
                 status: pay.status, invoice: paid_inv.canonical_id,
                 dunning_id: dun.canonical_id, failure_type: dun.failure_type,
                 failure_reason: dun.reason
               }) AS payments
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query, customer_id=customer_id).single()
        if not record:
            return {"customer_id": customer_id, "found": False}

        # Clean up null entries from OPTIONAL MATCHes
        def clean_list(items: list) -> list:
            return [item for item in items if item.get("id")]

        return {
            "customer_id": record["customer_id"],
            "account_id": record["account_id"],
            "found": True,
            "alarms": clean_list(record["alarms"]),
            "pm_counters": clean_list(record["pm_counters"]),
            "kpi_observations": clean_list(record["kpi_observations"]),
            "service_problems": clean_list(record["service_problems"]),
            "log_events": clean_list(record["log_events"]),
            "billing": [b for b in record["billing"] if b.get("invoice_id")],
            "complaints": clean_list(record["complaints"]),
            "adjustments": clean_list(record["adjustments"]),
            "payments": clean_list(record["payments"]),
        }

    # -------------------------------------------------------------------
    # Q2: KPI Breach → Incident Path (Ontology Spec 4.4)
    # -------------------------------------------------------------------
    def kpi_breach_to_incident(self, customer_id: str) -> list[dict[str, Any]]:
        """Trace KPI threshold breaches to correlated incidents for a customer."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
        MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
        WHERE toFloat(kpi.kpi_value) > toFloat(kpi.threshold_value)
        OPTIONAL MATCH (kpi)-[:CORRELATED_WITH]->(alarm:Alarm)
        RETURN kpi.canonical_id AS kpi_id,
               kpi.kpi_name AS kpi_name,
               kpi.kpi_value AS kpi_value,
               kpi.threshold_value AS threshold,
               kpi.severity AS severity,
               kpi.unit AS unit,
               alarm.canonical_id AS alarm_id,
               alarm.alarm_type AS alarm_type,
               alarm.affected_site AS site
        ORDER BY kpi.severity DESC
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, customer_id=customer_id)]

    # -------------------------------------------------------------------
    # Q3: Customer Impact Scope (Ontology Spec 4.5)
    # -------------------------------------------------------------------
    def customer_impact(self, customer_id: str) -> dict[str, Any]:
        """Financial and service impact summary for a customer."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)

        OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)
        WITH c, ba, sum(toFloat(coalesce(cr.amount, '0'))) AS total_charges

        OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
        WITH c, ba, total_charges, sum(toFloat(coalesce(adj.amount, '0'))) AS total_credits

        OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)
        WITH c, ba, total_charges, total_credits,
             count(comp) AS total_complaints,
             count(CASE WHEN comp.status = 'OPEN' THEN 1 END) AS open_complaints

        OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)
        WITH c, ba, total_charges, total_credits, total_complaints, open_complaints,
             count(alarm) AS total_alarms

        OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
        WITH c, ba, total_charges, total_credits, total_complaints, open_complaints,
             total_alarms, count(sp) AS total_disruptions

        OPTIONAL MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)
        OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)
        RETURN c.canonical_id AS customer_id,
               total_charges,
               total_credits,
               total_charges - total_credits AS net_charges,
               total_complaints,
               open_complaints,
               total_alarms,
               total_disruptions,
               count(DISTINCT pay) AS total_payments,
               count(DISTINCT dun) AS failed_payments
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query, customer_id=customer_id).single()
        return dict(record) if record else {"customer_id": customer_id, "found": False}

    # -------------------------------------------------------------------
    # Q4: Site Impact Analysis (adapted from Ontology Spec 4.2 blast radius)
    # -------------------------------------------------------------------
    def site_impact(self, site_id: str) -> dict[str, Any]:
        """All KPI breaches, alarms, and affected customers at a specific site."""
        query = """
        OPTIONAL MATCH (alarm:Alarm {affected_site: $site_id})
        WITH collect(DISTINCT {
          id: alarm.canonical_id, type: alarm.alarm_type,
          severity: alarm.severity, service: alarm.affected_service,
          raised_at: alarm.raised_at, duration: alarm.duration_minutes
        }) AS alarms

        OPTIONAL MATCH (pm:PMCounter {site_id: $site_id})
        WITH alarms, collect(DISTINCT {
          id: pm.canonical_id, kpi: pm.counter_name,
          value: pm.value, threshold: pm.threshold_value,
          severity: pm.severity
        }) AS pm_counters

        OPTIONAL MATCH (ba:BillingAccount)-[:HAS_ALARM]->(a:Alarm {affected_site: $site_id})
        MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        WITH alarms, pm_counters,
             collect(DISTINCT c.canonical_id) AS affected_customers

        RETURN $site_id AS site_id,
               alarms, pm_counters, affected_customers,
               size(alarms) AS alarm_count,
               size(pm_counters) AS kpi_count,
               size(affected_customers) AS customer_count
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query, site_id=site_id).single()

        if not record:
            return {"site_id": site_id, "found": False}

        def clean_list(items):
            return [i for i in items if i.get("id")]

        return {
            "site_id": site_id,
            "found": True,
            "alarms": clean_list(record["alarms"]),
            "pm_counters": clean_list(record["pm_counters"]),
            "affected_customers": record["affected_customers"],
            "alarm_count": record["alarm_count"],
            "kpi_count": record["kpi_count"],
            "customer_count": record["customer_count"],
        }

    # -------------------------------------------------------------------
    # Q5: Dunning Chain (Payment failure → Invoice → Complaint)
    # -------------------------------------------------------------------
    def dunning_chain(self, customer_id: str) -> list[dict[str, Any]]:
        """Trace payment failure → overdue invoice → complaint chain."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
        MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)
        OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)
        OPTIONAL MATCH (pay)-[:PAYMENT_FOR]->(inv:Invoice)
        RETURN pay.canonical_id AS payment_id,
               pay.amount AS payment_amount,
               pay.status AS payment_status,
               dun.canonical_id AS dunning_id,
               dun.failure_type AS failure_type,
               dun.reason AS failure_reason,
               inv.canonical_id AS invoice_id,
               inv.amount AS invoice_amount,
               inv.status AS invoice_status
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, customer_id=customer_id)]

    # -------------------------------------------------------------------
    # Q6: SLA Credit Verification
    # -------------------------------------------------------------------
    def sla_credit_chain(self, customer_id: str) -> list[dict[str, Any]]:
        """Trace SLA credit → incident → billing charges during outage."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
        MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
        OPTIONAL MATCH (adj)-[:CREDITS_FOR]->(sp:ServiceProblem)
        RETURN adj.canonical_id AS adjustment_id,
               adj.adjustment_type AS type,
               adj.amount AS credit_amount,
               adj.reason AS reason,
               sp.canonical_id AS incident_id,
               sp.service_type AS service_type,
               sp.description AS incident_reason,
               sp.severity AS severity,
               sp.opened_at AS incident_start,
               sp.resolved_at AS incident_end
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, customer_id=customer_id)]

    # -------------------------------------------------------------------
    # Q7: System Error Chain (Log → Incident → Billing)
    # -------------------------------------------------------------------
    def system_error_chain(self, customer_id: str) -> list[dict[str, Any]]:
        """Trace system log errors → service disruptions → billing impact."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
        MATCH (ba)-[:HAS_LOG]->(log:LogEvent)
        OPTIONAL MATCH (log)-[:EVIDENCES]->(sp:ServiceProblem)
        OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)-[:CREDITS_FOR]->(sp)
        RETURN log.canonical_id AS log_id,
               log.service_name AS service,
               log.level AS level,
               log.message AS message,
               log.trace_id AS trace_id,
               sp.canonical_id AS incident_id,
               sp.description AS incident_reason,
               sp.severity AS incident_severity,
               adj.canonical_id AS credit_id,
               adj.amount AS credit_amount
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, customer_id=customer_id)]

    # -------------------------------------------------------------------
    # Aggregate Queries
    # -------------------------------------------------------------------
    def unresolved_issues(self) -> dict[str, Any]:
        """Cross-domain view: open complaints + past-due invoices + failed payments."""
        query = """
        OPTIONAL MATCH (comp:Complaint)
        WHERE comp.status IN ['OPEN', 'IN_REVIEW']
        WITH collect(DISTINCT {
          id: comp.canonical_id, reason: comp.complaint_type,
          status: comp.status, opened: comp.opened_at
        }) AS open_complaints

        OPTIONAL MATCH (inv:Invoice)
        WHERE inv.status = 'PAST_DUE'
        WITH open_complaints, collect(DISTINCT {
          id: inv.canonical_id, amount: inv.amount,
          period: inv.billing_period, due_date: inv.due_date
        }) AS past_due_invoices

        OPTIONAL MATCH (dun:Dunning)
        WITH open_complaints, past_due_invoices, collect(DISTINCT {
          id: dun.canonical_id, type: dun.failure_type, reason: dun.reason
        }) AS failed_payments

        RETURN open_complaints, past_due_invoices, failed_payments,
               size(open_complaints) AS open_complaint_count,
               size(past_due_invoices) AS past_due_count,
               size(failed_payments) AS failed_payment_count
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query).single()

        def clean(items):
            return [i for i in items if i.get("id")]

        return {
            "open_complaints": clean(record["open_complaints"]),
            "past_due_invoices": [i for i in record["past_due_invoices"] if i.get("id")],
            "failed_payments": clean(record["failed_payments"]),
            "open_complaint_count": record["open_complaint_count"],
            "past_due_count": record["past_due_count"],
            "failed_payment_count": record["failed_payment_count"],
        }

    # -------------------------------------------------------------------
    # Cross-customer aggregate queries (no customer_id required)
    # -------------------------------------------------------------------
    def all_alarms(self, limit: int = 50) -> list[dict[str, Any]]:
        """All alarms across all customers (network issues)."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_ALARM]->(alarm:Alarm)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN alarm.canonical_id AS alarm_id,
               alarm.alarm_type AS type,
               alarm.severity AS severity,
               alarm.affected_site AS site,
               alarm.affected_service AS service,
               alarm.raised_at AS raised_at,
               alarm.duration_minutes AS duration,
               alarm.description AS description,
               c.canonical_id AS customer_id
        ORDER BY alarm.severity DESC, alarm.raised_at DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_kpi_breaches(self, limit: int = 50) -> list[dict[str, Any]]:
        """All KPI threshold breaches across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
        WHERE toFloat(kpi.kpi_value) > toFloat(kpi.threshold_value)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN kpi.canonical_id AS kpi_id,
               kpi.kpi_name AS kpi_name,
               kpi.kpi_value AS value,
               kpi.threshold_value AS threshold,
               kpi.severity AS severity,
               kpi.unit AS unit,
               c.canonical_id AS customer_id
        ORDER BY kpi.severity DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_complaints(self, limit: int = 50) -> list[dict[str, Any]]:
        """All complaints across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_COMPLAINT]->(comp:Complaint)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN comp.canonical_id AS complaint_id,
               comp.complaint_type AS type,
               comp.status AS status,
               comp.opened_at AS opened,
               comp.resolution_text AS resolution,
               c.canonical_id AS customer_id
        ORDER BY comp.opened_at DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_billing(self, limit: int = 50) -> list[dict[str, Any]]:
        """All invoices/charges across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_INVOICE]->(inv:Invoice)
        OPTIONAL MATCH (inv)-[:CONTAINS]->(cr:ChargingRecord)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN inv.canonical_id AS invoice_id,
               inv.amount AS amount,
               inv.status AS status,
               inv.billing_period AS period,
               cr.canonical_id AS charge_id,
               cr.charge_category AS charge_category,
               cr.amount AS charge_amount,
               c.canonical_id AS customer_id
        ORDER BY inv.status, inv.amount DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_dunning(self, limit: int = 50) -> list[dict[str, Any]]:
        """All payment failures / dunning events across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_PAYMENT]->(pay:Payment)
        OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN pay.canonical_id AS payment_id,
               pay.amount AS amount,
               pay.status AS status,
               dun.canonical_id AS dunning_id,
               dun.failure_type AS failure_type,
               dun.reason AS failure_reason,
               c.canonical_id AS customer_id
        ORDER BY pay.status, pay.amount DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_service_disruptions(self, limit: int = 50) -> list[dict[str, Any]]:
        """All service disruptions / SLA events across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
        OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)-[:CREDITS_FOR]->(sp)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN sp.canonical_id AS disruption_id,
               sp.service_type AS service,
               sp.severity AS severity,
               sp.description AS reason,
               sp.opened_at AS opened,
               sp.resolved_at AS resolved,
               adj.canonical_id AS credit_id,
               adj.amount AS credit_amount,
               c.canonical_id AS customer_id
        ORDER BY sp.severity DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_system_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        """All system error / log events across all customers."""
        query = """
        MATCH (ba:BillingAccount)-[:HAS_LOG]->(log:LogEvent)
        OPTIONAL MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba)
        RETURN log.canonical_id AS log_id,
               log.log_source AS source,
               log.service_name AS service,
               log.level AS level,
               log.message AS message,
               log.trace_id AS trace_id,
               c.canonical_id AS customer_id
        ORDER BY log.level DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def all_customers_summary(self, limit: int = 50) -> list[dict[str, Any]]:
        """Summary of all customers with event counts."""
        query = """
        MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba:BillingAccount)
        OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)
        OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)
        OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)
        OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
        RETURN c.canonical_id AS customer_id,
               c.customer_type AS customer_type,
               count(DISTINCT alarm) AS alarm_count,
               count(DISTINCT comp) AS complaint_count,
               count(DISTINCT inv) AS invoice_count,
               count(DISTINCT sp) AS disruption_count
        ORDER BY alarm_count + complaint_count + invoice_count + disruption_count DESC
        LIMIT $limit
        """
        with self.driver.session(database=self._database) as session:
            return [dict(r) for r in session.run(query, limit=limit)]

    def overview_graph(self, limit: int = 30) -> dict[str, Any]:
        """Return a sampled overview of the full KG: top customers + their connected entities.

        Picks customers with the most events so the overview is representative.
        Caps at ~300 nodes to keep the force-directed graph performant.
        """
        query = """
        MATCH (c:Customer)-[:HAS_ACCOUNT]->(ba:BillingAccount)
        OPTIONAL MATCH (ba)-[r]->(n)
        WITH c, ba, count(n) AS event_count, collect(DISTINCT n) AS related, collect(DISTINCT r) AS rels
        ORDER BY event_count DESC
        LIMIT $limit
        WITH collect(c) + collect(ba) AS cb_nodes,
             reduce(acc = [], items IN collect(related) | acc + items) AS all_related,
             reduce(acc = [], items IN collect(rels) | acc + items) AS all_rels
        WITH cb_nodes + all_related AS all_nodes, all_rels
        RETURN all_nodes AS nodes, all_rels AS relationships
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query, limit=limit).single()
        if not record:
            return {"nodes": [], "relationships": []}

        nodes = []
        seen_ids = set()
        for node in (record["nodes"] or []):
            nid = node.get("canonical_id")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                labels = list(node.labels) if hasattr(node, "labels") else []
                props = dict(node)
                nodes.append({
                    "id": nid,
                    "name": props.get("name", nid),
                    "labels": labels,
                    "properties": props,
                })

        relationships = []
        seen_rels = set()
        for rel in (record["relationships"] or []):
            rid = str(rel.element_id)
            if rid not in seen_rels:
                seen_rels.add(rid)
                src = rel.start_node.get("canonical_id")
                tgt = rel.end_node.get("canonical_id")
                if src in seen_ids and tgt in seen_ids:
                    relationships.append({
                        "id": rid,
                        "source": src,
                        "target": tgt,
                        "type": rel.type,
                    })

        return {"nodes": nodes, "relationships": relationships}

    def customer_graph_payload(self, customer_id: str) -> dict[str, Any]:
        """Return raw graph nodes and relationships for a customer (for visualization)."""
        query = """
        MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
        OPTIONAL MATCH path = (ba)-[*1..2]->(n)
        WITH c, ba, collect(DISTINCT n) AS related, collect(DISTINCT relationships(path)) AS rel_lists
        WITH [c, ba] + related AS all_nodes, rel_lists
        UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
        UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
        RETURN all_nodes AS nodes,
               [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """
        with self.driver.session(database=self._database) as session:
            record = session.run(query, customer_id=customer_id).single()
        if not record:
            return {"nodes": [], "relationships": []}

        nodes = []
        seen_ids = set()
        for node in (record["nodes"] or []):
            nid = node.get("canonical_id")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                labels = list(node.labels) if hasattr(node, "labels") else []
                props = dict(node)
                nodes.append({
                    "id": nid,
                    "name": props.get("name", nid),
                    "labels": labels,
                    "properties": props,
                })

        relationships = []
        for rel in (record["relationships"] or []):
            relationships.append({
                "id": str(rel.element_id),
                "source": rel.start_node.get("canonical_id"),
                "target": rel.end_node.get("canonical_id"),
                "type": rel.type,
            })

        return {"nodes": nodes, "relationships": relationships}

    # -------------------------------------------------------------------
    # Intent-specific graph traversal (for NL Query KG visualization)
    # -------------------------------------------------------------------

    _INTENT_GRAPH_QUERIES = {
        "network_rca": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_ALARM]->(alarm:Alarm)
            OPTIONAL MATCH p2 = (ba)-[:HAS_PM_COUNTER]->(pm:PMCounter)
            OPTIONAL MATCH p3 = (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
            WITH c, ba,
                 collect(DISTINCT alarm) + collect(DISTINCT pm) + collect(DISTINCT kpi) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) + collect(DISTINCT relationships(p3)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "kpi_breach": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            MATCH p1 = (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)
            OPTIONAL MATCH p2 = (kpi)-[:CORRELATED_WITH]->(alarm:Alarm)
            WITH c, ba,
                 collect(DISTINCT kpi) + collect(DISTINCT alarm) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "dunning_chain": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_PAYMENT]->(pay:Payment)
            OPTIONAL MATCH p2 = (pay)-[:HAS_DUNNING]->(dun:Dunning)
            OPTIONAL MATCH p3 = (pay)-[:PAYMENT_FOR]->(inv:Invoice)
            WITH c, ba,
                 collect(DISTINCT pay) + collect(DISTINCT dun) + collect(DISTINCT inv) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) + collect(DISTINCT relationships(p3)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "sla_credit": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
            OPTIONAL MATCH p2 = (adj)-[:CREDITS_FOR]->(sp:ServiceProblem)
            OPTIONAL MATCH p3 = (ba)-[:HAS_DISRUPTION]->(sp2:ServiceProblem)
            WITH c, ba,
                 collect(DISTINCT adj) + collect(DISTINCT sp) + collect(DISTINCT sp2) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) + collect(DISTINCT relationships(p3)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "system_error": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_LOG]->(log:LogEvent)
            OPTIONAL MATCH p2 = (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)
            WITH c, ba,
                 collect(DISTINCT log) + collect(DISTINCT sp) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "complaint": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_COMPLAINT]->(comp:Complaint)
            OPTIONAL MATCH p2 = (comp)-[:DISPUTES]->(cr:ChargingRecord)
            WITH c, ba,
                 collect(DISTINCT comp) + collect(DISTINCT cr) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
        "financial_impact": """
            MATCH (c:Customer {canonical_id: $customer_id})-[:HAS_ACCOUNT]->(ba:BillingAccount)
            OPTIONAL MATCH p1 = (ba)-[:HAS_INVOICE]->(inv:Invoice)
            OPTIONAL MATCH p2 = (inv)-[:CONTAINS]->(cr:ChargingRecord)
            OPTIONAL MATCH p3 = (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)
            WITH c, ba,
                 collect(DISTINCT inv) + collect(DISTINCT cr) + collect(DISTINCT adj) AS related,
                 collect(DISTINCT relationships(p1)) + collect(DISTINCT relationships(p2)) + collect(DISTINCT relationships(p3)) AS rel_lists
            WITH [c, ba] + [n IN related WHERE n IS NOT NULL] AS all_nodes, rel_lists
            UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
            UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
            RETURN all_nodes AS nodes,
                   [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """,
    }

    def intent_graph_payload(self, customer_id: str, intent: str) -> dict[str, Any]:
        """Return graph nodes/relationships scoped to a specific intent (not the full customer subgraph)."""
        query = self._INTENT_GRAPH_QUERIES.get(intent)
        if not query:
            return self.customer_graph_payload(customer_id)

        with self.driver.session(database=self._database) as session:
            record = session.run(query, customer_id=customer_id).single()
        if not record:
            return {"nodes": [], "relationships": []}

        nodes = []
        seen_ids = set()
        for node in (record["nodes"] or []):
            nid = node.get("canonical_id")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                labels = list(node.labels) if hasattr(node, "labels") else []
                props = dict(node)
                nodes.append({
                    "id": nid,
                    "name": props.get("name", nid),
                    "labels": labels,
                    "properties": props,
                })

        relationships = []
        for rel in (record["relationships"] or []):
            relationships.append({
                "id": str(rel.element_id),
                "source": rel.start_node.get("canonical_id"),
                "target": rel.end_node.get("canonical_id"),
                "type": rel.type,
            })

        return {"nodes": nodes, "relationships": relationships}
