"""
CSV Ingestion Service — Causal Ontology Graph

Architecture:
  Root causes (NetworkFailure, PaymentFailure, Incident, LogEvent, root ApiKpiBreach)
  → CAUSED_CHARGE / FAILED_ON → Charge / Invoice
  → ON_INVOICE → Invoice → BILLED_TO → Account → OWNED_BY → Customer

  Evidence (PmCounter, SlaCredit, Dispute, evidence ApiKpiBreach)
  → CORROBORATES / COMPENSATES / DISPUTES → root or charge

  NO direct event→Customer/Account edges. customer_id is a property only.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from neo4j import GraphDatabase

try:
    from .vector_service import VectorService
except ImportError:
    VectorService = None

# ---------------------------------------------------------------------------
# Regex patterns for extracting IDs from free-text columns
# ---------------------------------------------------------------------------
_CHG_RE = re.compile(r"CHG-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_NF_RE = re.compile(r"NF-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_PF_RE = re.compile(r"PF-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_SD_RE = re.compile(r"SD-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)
_INV_RE = re.compile(r"INV-GEN-[0-9A-Fa-f]{4,8}", re.IGNORECASE)

# plausible KPI→failure-type map for CORROBORATES
_PLAUSIBLE_MAP = {
    "CELL_OUTAGE": {"rrc_setup_success_rate", "call_drop_rate"},
    "CONGESTION": {"throughput_mbps", "rrc_setup_success_rate"},
    "SIGNAL_DEGRADATION": {"handover_success_rate", "call_drop_rate"},
    "ROAMING_PARTNER_OUTAGE": set(),
}


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
    """Deterministic CSV-to-Neo4j causal ontology pipeline."""

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
    # Schema Setup — unique constraints + indexes for causal ontology
    # -----------------------------------------------------------------------
    def setup_schema(self) -> list[str]:
        labels = [
            "Customer", "Account", "Invoice", "Charge",
            "NetworkFailure", "PaymentFailure", "Incident",
            "LogEvent", "ApiKpiBreach", "PmCounter",
            "Dispute", "SlaCredit", "Site", "Service",
            "RemediationAction",
        ]
        statements = []
        for label in labels:
            statements.append(
                f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:{label}) REQUIRE n.id IS UNIQUE"
            )
        # Extra indexes for traversal performance
        statements.extend([
            "CREATE INDEX IF NOT EXISTS FOR (n:Charge) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Invoice) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:NetworkFailure) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:Incident) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:LogEvent) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:ApiKpiBreach) ON (n.customer_id)",
            "CREATE INDEX IF NOT EXISTS FOR (n:PmCounter) ON (n.site_id)",
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
    # Main entry point — ingest a single domain CSV
    # -----------------------------------------------------------------------
    def ingest_domain(self, domain: str, csv_content: str | bytes) -> IngestionStats:
        dispatch = {
            "billing": self._ingest_billing,
            "network": self._ingest_network,
            "payment_failures": self._ingest_payment_failures,
            "incident": self._ingest_incident,
            "logs": self._ingest_logs,
            "api": self._ingest_api,
            "pm_counters": self._ingest_pm_counters,
            "complaints": self._ingest_complaints,
            "sla_credits": self._ingest_sla_credits,
        }
        if domain not in dispatch:
            raise ValueError(f"Unknown domain '{domain}'. Valid: {list(dispatch.keys())}")

        if isinstance(csv_content, bytes):
            csv_content = csv_content.decode("utf-8-sig")

        rows = list(csv.DictReader(io.StringIO(csv_content)))
        stats = IngestionStats(domain=domain, rows_received=len(rows))
        if not rows:
            stats.warnings.append(f"No data rows in {domain} CSV")
            return stats

        with self.driver.session(database=self._database) as session:
            dispatch[domain](session, rows, stats)

        return stats

    # ===================================================================
    # BILLING — creates Customer, Account, Invoice, Charge + billing chain
    # ===================================================================
    def _ingest_billing(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                cid = r["customer_id"].strip()
                aid = r["account_id"].strip()
                inv_id = r["invoice_id"].strip()
                chg_id = r["charge_id"].strip()
                if not all([cid, aid, inv_id, chg_id]):
                    stats.rows_rejected += 1
                    stats.rejected_details.append({"row": i, "errors": ["missing required ID"]})
                    continue

                # MERGE Customer
                session.run(
                    "MERGE (c:Customer {id: $id}) SET c.customer_id = $id",
                    id=cid,
                )
                # MERGE Account → OWNED_BY → Customer
                session.run(
                    """MERGE (a:Account {id: $aid}) SET a.account_id = $aid, a.customer_id = $cid
                    WITH a
                    MATCH (c:Customer {id: $cid})
                    MERGE (a)-[:OWNED_BY]->(c)""",
                    aid=aid, cid=cid,
                )
                # MERGE Invoice → BILLED_TO → Account
                session.run(
                    """MERGE (inv:Invoice {id: $iid})
                    SET inv.billing_period = $bp, inv.amount = $amt,
                        inv.currency = $cur, inv.status = $st, inv.due_date = $dd,
                        inv.customer_id = $cid, inv.account_id = $aid
                    WITH inv
                    MATCH (a:Account {id: $aid})
                    MERGE (inv)-[:BILLED_TO]->(a)""",
                    iid=inv_id, bp=r.get("billing_period", ""),
                    amt=_float(r.get("invoice_amount")),
                    cur=r.get("currency", "USD"), st=r.get("invoice_status", ""),
                    dd=r.get("due_date", ""), cid=cid, aid=aid,
                )
                # MERGE Charge → ON_INVOICE → Invoice
                session.run(
                    """MERGE (ch:Charge {id: $chid})
                    SET ch.category = $cat, ch.description = $desc,
                        ch.amount = $amt, ch.customer_id = $cid,
                        ch.account_id = $aid, ch.invoice_id = $iid
                    WITH ch
                    MATCH (inv:Invoice {id: $iid})
                    MERGE (ch)-[:ON_INVOICE]->(inv)""",
                    chid=chg_id, cat=r.get("charge_category", ""),
                    desc=r.get("charge_description", ""),
                    amt=_float(r.get("charge_amount")),
                    cid=cid, aid=aid, iid=inv_id,
                )
                stats.nodes_created += 4
                stats.relationships_created += 3
                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # NETWORK — NetworkFailure:RootCause → CAUSED_CHARGE → Charge, AT_SITE
    # ===================================================================
    def _ingest_network(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                nf_id = r["failure_id"].strip()
                cid = r["customer_id"].strip()
                site = r.get("affected_site", "").strip()
                desc = r.get("impact_description", "")
                if not nf_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (nf:NetworkFailure:RootCause {id: $nfid})
                    SET nf.failure_type = $ft, nf.severity = $sev,
                        nf.affected_service = $svc, nf.affected_site = $site,
                        nf.event_date = $ed, nf.duration_minutes = $dur,
                        nf.impact_description = $desc, nf.customer_id = $cid""",
                    nfid=nf_id, ft=r.get("failure_type", ""),
                    sev=r.get("severity", ""), svc=r.get("affected_service", ""),
                    site=site, ed=r.get("event_date", ""),
                    dur=_float(r.get("duration_minutes")), desc=desc, cid=cid,
                )
                stats.nodes_created += 1

                # AT_SITE → Site
                if site:
                    session.run(
                        """MERGE (s:Site {id: $sid}) SET s.site_id = $sid
                        WITH s MATCH (nf:NetworkFailure {id: $nfid})
                        MERGE (nf)-[:AT_SITE]->(s)""",
                        sid=site, nfid=nf_id,
                    )
                    stats.nodes_created += 1
                    stats.relationships_created += 1

                # CAUSED_CHARGE → Charge (regex from impact_description)
                for chg_id in _CHG_RE.findall(desc):
                    chg_id = chg_id.upper()
                    session.run(
                        """MATCH (nf:NetworkFailure {id: $nfid})
                        MERGE (ch:Charge {id: $chid})
                        MERGE (nf)-[:CAUSED_CHARGE]->(ch)""",
                        nfid=nf_id, chid=chg_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # PAYMENT_FAILURES — PaymentFailure:RootCause → FAILED_ON → Invoice
    # ===================================================================
    def _ingest_payment_failures(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                pf_id = r["payment_failure_id"].strip()
                inv_id = r.get("invoice_id", "").strip()
                cid = r["customer_id"].strip()
                if not pf_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (pf:PaymentFailure:RootCause {id: $pfid})
                    SET pf.failure_type = $ft, pf.failure_reason = $fr,
                        pf.failure_date = $fd, pf.card_last_four = $clf,
                        pf.issuer_response = $ir, pf.payment_amount = $amt,
                        pf.payment_date = $pd, pf.payment_status = $ps,
                        pf.payment_id = $pid, pf.customer_id = $cid,
                        pf.invoice_id = $iid""",
                    pfid=pf_id, ft=r.get("failure_type", ""),
                    fr=r.get("failure_reason", ""),
                    fd=r.get("failure_date", ""), clf=r.get("card_last_four", ""),
                    ir=r.get("issuer_response", ""),
                    amt=_float(r.get("payment_amount")),
                    pd=r.get("payment_date", ""), ps=r.get("payment_status", ""),
                    pid=r.get("payment_id", ""), cid=cid, iid=inv_id,
                )
                stats.nodes_created += 1

                # FAILED_ON → Invoice
                if inv_id:
                    session.run(
                        """MATCH (pf:PaymentFailure {id: $pfid})
                        MERGE (inv:Invoice {id: $iid})
                        MERGE (pf)-[:FAILED_ON]->(inv)""",
                        pfid=pf_id, iid=inv_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # INCIDENT — Incident:RootCause → CAUSED_CHARGE → Charge
    # ===================================================================
    def _ingest_incident(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                sd_id = r["disruption_id"].strip()
                cid = r["customer_id"].strip()
                desc = r.get("impact_on_charges", "")
                if not sd_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (inc:Incident:RootCause {id: $sdid})
                    SET inc.service_type = $st, inc.disruption_reason = $dr,
                        inc.severity = $sev, inc.event_date = $ed,
                        inc.resolution_date = $rd, inc.impact_on_charges = $desc,
                        inc.customer_id = $cid""",
                    sdid=sd_id, st=r.get("service_type", ""),
                    dr=r.get("disruption_reason", ""),
                    sev=r.get("severity", ""), ed=r.get("event_date", ""),
                    rd=r.get("resolution_date", ""), desc=desc, cid=cid,
                )
                stats.nodes_created += 1

                for chg_id in _CHG_RE.findall(desc):
                    chg_id = chg_id.upper()
                    session.run(
                        """MATCH (inc:Incident {id: $sdid})
                        MERGE (ch:Charge {id: $chid})
                        MERGE (inc)-[:CAUSED_CHARGE]->(ch)""",
                        sdid=sd_id, chid=chg_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # LOGS — LogEvent:RootCause → CAUSED_CHARGE → Charge, EMITTED_BY → Service
    # ===================================================================
    def _ingest_logs(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                log_id = r["log_id"].strip()
                cid = r["customer_id"].strip()
                svc_name = r.get("service_name", "").strip()
                desc = r.get("impact_description", "")
                if not log_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (l:LogEvent:RootCause {id: $lid})
                    SET l.log_source = $ls, l.service_name = $sn,
                        l.log_level = $ll, l.message = $msg,
                        l.trace_id = $tid, l.host = $host,
                        l.event_time = $et, l.impact_description = $desc,
                        l.customer_id = $cid""",
                    lid=log_id, ls=r.get("log_source", ""),
                    sn=svc_name, ll=r.get("log_level", ""),
                    msg=r.get("message", ""), tid=r.get("trace_id", ""),
                    host=r.get("host_or_pod", ""), et=r.get("event_time", ""),
                    desc=desc, cid=cid,
                )
                stats.nodes_created += 1

                # EMITTED_BY → Service
                if svc_name:
                    session.run(
                        """MERGE (s:Service {id: $sn}) SET s.service_name = $sn
                        WITH s MATCH (l:LogEvent {id: $lid})
                        MERGE (l)-[:EMITTED_BY]->(s)""",
                        sn=svc_name, lid=log_id,
                    )
                    stats.nodes_created += 1
                    stats.relationships_created += 1

                for chg_id in _CHG_RE.findall(desc):
                    chg_id = chg_id.upper()
                    session.run(
                        """MATCH (l:LogEvent {id: $lid})
                        MERGE (ch:Charge {id: $chid})
                        MERGE (l)-[:CAUSED_CHARGE]->(ch)""",
                        lid=log_id, chid=chg_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # API (KPI breaches) — dual role: root (has CHG) or evidence (has NF/PF/SD)
    # ===================================================================
    def _ingest_api(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                kpi_id = r["kpi_observation_id"].strip()
                cid = r["customer_id"].strip()
                desc = r.get("impact_description", "")
                if not kpi_id:
                    stats.rows_rejected += 1
                    continue

                chg_ids = [m.upper() for m in _CHG_RE.findall(desc)]
                nf_ids = [m.upper() for m in _NF_RE.findall(desc)]
                pf_ids = [m.upper() for m in _PF_RE.findall(desc)]
                sd_ids = [m.upper() for m in _SD_RE.findall(desc)]
                ref_ids = nf_ids + pf_ids + sd_ids

                # Determine role: if has CHG → root; if has NF/PF/SD → evidence; never both
                is_root = len(chg_ids) > 0
                is_evidence = len(ref_ids) > 0

                if is_root:
                    extra_label = ":RootCause"
                elif is_evidence:
                    extra_label = ":Evidence"
                else:
                    extra_label = ""

                session.run(
                    f"""MERGE (k:ApiKpiBreach{extra_label} {{id: $kid}})
                    SET k.kpi_name = $kn, k.kpi_value = $kv,
                        k.threshold_value = $tv, k.unit = $unit,
                        k.severity = $sev, k.measurement_window = $mw,
                        k.observed_at = $oa, k.impact_description = $desc,
                        k.customer_id = $cid, k.is_root = $is_root""",
                    kid=kpi_id, kn=r.get("kpi_name", ""),
                    kv=_float(r.get("kpi_value")),
                    tv=_float(r.get("threshold_value")),
                    unit=r.get("unit", ""), sev=r.get("severity", ""),
                    mw=r.get("measurement_window", ""),
                    oa=r.get("observed_at", ""), desc=desc,
                    cid=cid, is_root=is_root,
                )
                stats.nodes_created += 1

                if is_root:
                    for chg_id in chg_ids:
                        session.run(
                            """MATCH (k:ApiKpiBreach {id: $kid})
                            MERGE (ch:Charge {id: $chid})
                            MERGE (k)-[:CAUSED_CHARGE]->(ch)""",
                            kid=kpi_id, chid=chg_id,
                        )
                        stats.relationships_created += 1
                elif is_evidence:
                    for ref_id in ref_ids:
                        # Determine target label
                        if ref_id.startswith("NF-"):
                            target = "NetworkFailure"
                        elif ref_id.startswith("PF-"):
                            target = "PaymentFailure"
                        else:
                            target = "Incident"
                        session.run(
                            f"""MATCH (k:ApiKpiBreach {{id: $kid}})
                            MERGE (t:{target} {{id: $tid}})
                            MERGE (k)-[:CORROBORATES]->(t)""",
                            kid=kpi_id, tid=ref_id,
                        )
                        stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # PM_COUNTERS — PmCounter:Evidence → CORROBORATES → NetworkFailure + MEASURED_AT → Site
    # ===================================================================
    def _ingest_pm_counters(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                pm_id = r["counter_id"].strip()
                cid = r["customer_id"].strip()
                site_id = r.get("site_id", "").strip()
                kpi_name = r.get("kpi_name", "").strip()
                desc = r.get("impact_description", "")
                value = _float(r.get("value"))
                threshold = _float(r.get("threshold"))
                if not pm_id:
                    stats.rows_rejected += 1
                    continue

                # breach_ratio
                if value is not None and threshold is not None and threshold > 0:
                    if kpi_name in ("call_drop_rate",):
                        breach_ratio = round(value / threshold, 2)
                    else:
                        breach_ratio = round(threshold / value, 2) if value > 0 else 0.0
                else:
                    breach_ratio = None

                session.run(
                    """MERGE (pm:PmCounter:Evidence {id: $pmid})
                    SET pm.kpi_name = $kn, pm.value = $val,
                        pm.unit = $unit, pm.threshold = $thr,
                        pm.severity = $sev, pm.observed_at = $oa,
                        pm.impact_description = $desc, pm.customer_id = $cid,
                        pm.site_id = $site, pm.breach_ratio = $br""",
                    pmid=pm_id, kn=kpi_name, val=value,
                    unit=r.get("unit", ""), thr=threshold,
                    sev=r.get("severity", ""), oa=r.get("observed_at", ""),
                    desc=desc, cid=cid, site=site_id, br=breach_ratio,
                )
                stats.nodes_created += 1

                # MEASURED_AT → Site
                if site_id:
                    session.run(
                        """MERGE (s:Site {id: $sid}) SET s.site_id = $sid
                        WITH s MATCH (pm:PmCounter {id: $pmid})
                        MERGE (pm)-[:MEASURED_AT]->(s)""",
                        sid=site_id, pmid=pm_id,
                    )
                    stats.relationships_created += 1

                # CORROBORATES → NetworkFailure (regex)
                for nf_id in _NF_RE.findall(desc):
                    nf_id = nf_id.upper()
                    # Determine plausibility
                    # We need the failure_type — look it up from the NF node
                    plausible = None
                    try:
                        nf_rec = session.run(
                            "MATCH (nf:NetworkFailure {id: $nfid}) RETURN nf.failure_type AS ft",
                            nfid=nf_id,
                        ).single()
                        if nf_rec:
                            ft = (nf_rec["ft"] or "").upper()
                            plausible_kpis = _PLAUSIBLE_MAP.get(ft, set())
                            plausible = kpi_name in plausible_kpis
                    except Exception:
                        pass

                    lag_min = None
                    # TODO: compute lag from timestamps if needed

                    props = {"breach_ratio": breach_ratio}
                    if plausible is not None:
                        props["plausible"] = plausible
                    if lag_min is not None:
                        props["lag_min"] = lag_min

                    set_clause = ", ".join(f"r.{k} = ${k}" for k in props if props[k] is not None)
                    session.run(
                        f"""MATCH (pm:PmCounter {{id: $pmid}})
                        MERGE (nf:NetworkFailure {{id: $nfid}})
                        MERGE (pm)-[r:CORROBORATES]->(nf)
                        SET {set_clause}""" if set_clause else
                        """MATCH (pm:PmCounter {id: $pmid})
                        MERGE (nf:NetworkFailure {id: $nfid})
                        MERGE (pm)-[:CORROBORATES]->(nf)""",
                        pmid=pm_id, nfid=nf_id, **{k: v for k, v in props.items() if v is not None},
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # COMPLAINTS — Dispute:Evidence → DISPUTES → Charge
    # ===================================================================
    def _ingest_complaints(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                dsp_id = r["dispute_id"].strip()
                chg_id = r.get("charge_id", "").strip()
                cid = r["customer_id"].strip()
                if not dsp_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (d:Dispute:Evidence {id: $did})
                    SET d.reason = $reason, d.status = $st,
                        d.raised_date = $rd, d.resolution_details = $res,
                        d.customer_id = $cid, d.charge_id = $chid""",
                    did=dsp_id, reason=r.get("reason", ""),
                    st=r.get("status", ""), rd=r.get("raised_date", ""),
                    res=r.get("resolution_details", ""),
                    cid=cid, chid=chg_id,
                )
                stats.nodes_created += 1

                if chg_id:
                    session.run(
                        """MATCH (d:Dispute {id: $did})
                        MERGE (ch:Charge {id: $chid})
                        MERGE (d)-[:DISPUTES]->(ch)""",
                        did=dsp_id, chid=chg_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # SLA_CREDITS — SlaCredit:Evidence → COMPENSATES → Incident
    # ===================================================================
    def _ingest_sla_credits(self, session, rows: list[dict], stats: IngestionStats):
        for i, r in enumerate(rows, 2):
            try:
                adj_id = r["adjustment_id"].strip()
                inc_id = r.get("related_incident_id", "").strip()
                cid = r["customer_id"].strip()
                if not adj_id:
                    stats.rows_rejected += 1
                    continue

                session.run(
                    """MERGE (sc:SlaCredit:Evidence {id: $scid})
                    SET sc.adjustment_type = $at, sc.amount = $amt,
                        sc.currency = $cur, sc.reason = $reason,
                        sc.related_incident_id = $iid, sc.issued_date = $idt,
                        sc.status = $st, sc.customer_id = $cid""",
                    scid=adj_id, at=r.get("adjustment_type", ""),
                    amt=_float(r.get("amount")),
                    cur=r.get("currency", "USD"), reason=r.get("reason", ""),
                    iid=inc_id, idt=r.get("issued_date", ""),
                    st=r.get("status", ""), cid=cid,
                )
                stats.nodes_created += 1

                if inc_id:
                    session.run(
                        """MATCH (sc:SlaCredit {id: $scid})
                        MERGE (inc:Incident {id: $iid})
                        MERGE (sc)-[:COMPENSATES]->(inc)""",
                        scid=adj_id, iid=inc_id,
                    )
                    stats.relationships_created += 1

                stats.rows_written += 1
            except Exception as exc:
                stats.rows_rejected += 1
                stats.rejected_details.append({"row": i, "errors": [str(exc)]})

    # ===================================================================
    # Cross-domain relationships (no longer needed — edges built inline)
    # Kept as no-op for API compatibility
    # ===================================================================
    def create_cross_domain_relationships(self) -> dict[str, int]:
        """Edges are created during ingestion. Also creates RemediationAction nodes
        for resolved issues and sets up Neo4j vector indexes."""
        self._create_remediation_actions()
        self._setup_vector_indexes()
        return self._count_edge_types()

    def _create_remediation_actions(self):
        """Create RemediationAction nodes for resolved issues and link via REMEDIATED_BY.

        Spec: RemediationAction nodes represent resolved/remediated issues.
        Looks for issues with resolution notes or resolved status.
        """
        with self.driver.session(database=self._database) as session:
            # Create remediations for resolved disputes
            session.run("""
                MATCH (d:Dispute)
                WHERE d.status IN ['resolved', 'RESOLVED', 'Resolved', 'closed', 'CLOSED']
                  AND d.resolution_notes IS NOT NULL
                  AND NOT (d)-[:REMEDIATED_BY]->()
                WITH d, 'REM-' + replace(d.id, 'DSP-GEN-', '') AS rem_id
                MERGE (r:RemediationAction {id: rem_id})
                SET r.action_type = 'dispute_resolution',
                    r.description = d.resolution_notes,
                    r.status = 'completed',
                    r.resolved_at = coalesce(d.raised_date, ''),
                    r.assigned_to = 'auto'
                MERGE (d)-[:REMEDIATED_BY]->(r)
            """)
            # Create remediations for incidents with SLA credits
            session.run("""
                MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)
                WHERE NOT (inc)-[:REMEDIATED_BY]->()
                WITH inc, sc, 'REM-' + replace(inc.id, 'SD-GEN-', '') AS rem_id
                MERGE (r:RemediationAction {id: rem_id})
                SET r.action_type = 'sla_credit_applied',
                    r.description = 'SLA credit ' + sc.id + ' applied for amount ' + coalesce(toString(sc.credit_amount), '?'),
                    r.status = 'completed',
                    r.assigned_to = 'auto'
                MERGE (inc)-[:REMEDIATED_BY]->(r)
            """)
            print("[Remediation] Created RemediationAction nodes for resolved issues")

    def _setup_vector_indexes(self):
        """Create Neo4j-native vector indexes per entity label (Spec Section 11.2).

        7 indexes, one per correlating entity type, each on a 384-dimension
        embedding property. Idempotent — checks SHOW INDEXES first.
        """
        vector_labels = [
            "NetworkFailure", "PaymentFailure", "Incident",
            "LogEvent", "ApiKpiBreach", "Dispute", "Charge",
        ]
        with self.driver.session(database=self._database) as session:
            # Check existing indexes
            try:
                existing = {r["name"] for r in session.run("SHOW INDEXES")}
            except Exception:
                existing = set()

            for label in vector_labels:
                idx_name = f"vector_idx_{label.lower()}"
                if idx_name in existing:
                    continue
                try:
                    session.run(f"""
                        CALL db.index.vector.createNodeIndex(
                            '{idx_name}', '{label}', 'embedding', 384, 'cosine'
                        )
                    """)
                    print(f"[Vector] Created index {idx_name}")
                except Exception as exc:
                    # Neo4j version may not support vector indexes
                    print(f"[Vector] Could not create {idx_name}: {exc}")

    def _count_edge_types(self) -> dict[str, int]:
        with self.driver.session(database=self._database) as session:
            result = session.run(
                "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC"
            )
            return {rec["t"]: rec["c"] for rec in result}

    # ===================================================================
    # Batch ingest from directory
    # ===================================================================
    def ingest_directory(self, directory: str | Path) -> dict[str, IngestionStats]:
        directory = Path(directory)
        results = {}
        # Ingest billing first (creates Customer/Account/Invoice/Charge)
        order = [
            "billing", "network", "payment_failures", "incident",
            "logs", "api", "pm_counters", "complaints", "sla_credits",
        ]
        file_map = {
            "billing": "billing.csv", "network": "network.csv",
            "payment_failures": "payment_failures.csv", "incident": "incident.csv",
            "logs": "logs.csv", "api": "api.csv", "pm_counters": "pm_counters.csv",
            "complaints": "complaints.csv", "sla_credits": "sla_credits.csv",
        }
        for domain in order:
            filepath = directory / file_map[domain]
            if filepath.exists():
                csv_content = filepath.read_text(encoding="utf-8-sig")
                results[domain] = self.ingest_domain(domain, csv_content)
        return results

    # ===================================================================
    # Verification
    # ===================================================================
    def verify_graph(self) -> dict[str, Any]:
        with self.driver.session(database=self._database) as session:
            result = session.run("""
                MATCH (n) WITH labels(n) AS lbls
                UNWIND lbls AS label
                RETURN label, count(*) AS count ORDER BY count DESC
            """)
            node_counts = {r["label"]: r["count"] for r in result}

            rel_counts = self._count_edge_types()

            # Verify every Charge has exactly 1 root
            orphan_charges = session.run("""
                MATCH (ch:Charge)
                WHERE NOT ()-[:CAUSED_CHARGE]->(ch)
                RETURN count(ch) AS c
            """).single()["c"]

            # Verify 0 event→Customer edges
            event_to_cust = session.run("""
                MATCH (n)-[r]->(c:Customer)
                WHERE NOT n:Account
                RETURN count(r) AS c
            """).single()["c"]

            result = session.run("MATCH (c:Customer) RETURN count(c) AS count")
            customer_count = result.single()["count"]

            return {
                "node_counts": node_counts,
                "relationship_counts": rel_counts,
                "cross_domain_links": {
                    "orphan_charges_without_root": orphan_charges,
                    "event_to_customer_edges": event_to_cust,
                },
                "total_customers": customer_count,
            }

    # ===================================================================
    # Customer listing (traversal-based, no star schema)
    # ===================================================================
    def list_customers(self) -> list[dict[str, Any]]:
        with self.driver.session(database=self._database) as session:
            result = session.run("""
                MATCH (a:Account)-[:OWNED_BY]->(c:Customer)
                OPTIONAL MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)
                WITH c, a,
                     collect(DISTINCT inv) AS invs,
                     collect(DISTINCT ch) AS chs
                // Count roots per charge
                OPTIONAL MATCH (nf:NetworkFailure)-[:CAUSED_CHARGE]->(ch2:Charge)-[:ON_INVOICE]->(:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (inc:Incident)-[:CAUSED_CHARGE]->(ch3:Charge)-[:ON_INVOICE]->(:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (le:LogEvent)-[:CAUSED_CHARGE]->(ch4:Charge)-[:ON_INVOICE]->(:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv2:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch5:Charge)-[:ON_INVOICE]->(:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc2:Incident)-[:CAUSED_CHARGE]->(:Charge)-[:ON_INVOICE]->(:Invoice)-[:BILLED_TO]->(a)
                WITH c.id AS customer_id, a.id AS account_id,
                     size(invs) AS invoices, size(chs) AS charges,
                     count(DISTINCT nf) AS alarms,
                     count(DISTINCT inc) AS disruptions,
                     count(DISTINCT le) AS logs,
                     count(DISTINCT pf) AS payments,
                     count(DISTINCT d) AS complaints,
                     count(DISTINCT sc) AS adjustments
                WITH customer_id, account_id, invoices, charges,
                     alarms, disruptions, logs, payments, complaints, adjustments,
                     alarms + disruptions + logs + payments + complaints + adjustments + invoices AS total_events,
                     CASE
                       WHEN alarms > 0 THEN 'NETWORK_RCA'
                       WHEN disruptions > 0 THEN 'SERVICE_DISRUPTION'
                       WHEN payments > 0 THEN 'PAYMENT_DUNNING'
                       WHEN logs > 0 THEN 'SYSTEM_ERROR'
                       WHEN complaints > 0 THEN 'COMPLAINT'
                       ELSE 'BILLING_ONLY'
                     END AS customer_type
                RETURN customer_id, account_id, customer_type, total_events,
                       alarms, invoices, complaints, disruptions,
                       0 AS pm_counters, 0 AS kpi_observations,
                       logs, adjustments, payments
                ORDER BY total_events DESC, customer_id
            """)
            return [dict(r) for r in result]

    def customer_360(self, customer_id: str) -> dict[str, Any]:
        with self.driver.session(database=self._database) as session:
            result = session.run("""
                MATCH (a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})
                OPTIONAL MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)
                OPTIONAL MATCH (root)-[:CAUSED_CHARGE]->(ch)
                RETURN c.id AS customer_id, a.id AS account_id,
                       count(DISTINCT inv) AS invoices,
                       count(DISTINCT ch) AS charges,
                       count(DISTINCT root) AS roots
            """, cid=customer_id)
            record = result.single()
            if not record:
                return {"customer_id": customer_id, "found": False}
            return {
                "customer_id": customer_id,
                "found": True,
                "account_id": record["account_id"],
                "invoices": record["invoices"],
                "charges": record["charges"],
                "roots": record["roots"],
            }

    def clear_graph(self) -> dict[str, int]:
        with self.driver.session(database=self._database) as session:
            node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rel_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]

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


def _float(val) -> float | None:
    if val is None:
        return None
    val = str(val).strip()
    if not val:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None
