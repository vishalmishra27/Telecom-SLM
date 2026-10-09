"""
RCA Retrieval Service — Causal Ontology Traversals

Routing by ID prefix:
  NF-  → NetworkFailure → CAUSED_CHARGE → Chain B + AT_SITE + PM/API CORROBORATES
  PF-  → PaymentFailure → FAILED_ON → Invoice, Charge ON_INVOICE → Chain B
  SD-  → Incident → CAUSED_CHARGE → Chain B + SlaCredit COMPENSATES + API CORROBORATES
  LOG- → LogEvent → CAUSED_CHARGE → Chain B + EMITTED_BY Service
  KPI- → ApiKpiBreach → root: CAUSED_CHARGE→B; evidence: CORROBORATES→root→B
  PM-  → PmCounter → CORROBORATES → NF → B + MEASURED_AT Site
  DSP- → Dispute → DISPUTES → Charge → Chain B + root + evidence
  ADJ- → SlaCredit → COMPENSATES → Incident → Chain B
  CUST-→ Customer ← Account ← Invoice ← Charge, then root per charge

Chain B = Charge → ON_INVOICE → Invoice → BILLED_TO → Account → OWNED_BY → Customer
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import GraphDatabase

# Billing chain suffix used in most traversals
_CHAIN_B = """
    (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(acct:Account)-[:OWNED_BY]->(cust:Customer)
"""

_PREFIX_LABEL = {
    "NF-": "NetworkFailure",
    "PF-": "PaymentFailure",
    "SD-": "Incident",
    "LOG-": "LogEvent",
    "KPI-": "ApiKpiBreach",
    "PM-": "PmCounter",
    "DSP-": "Dispute",
    "ADJ-": "SlaCredit",
    "CUST-": "Customer",
    "ACC-": "Account",
    "INV-": "Invoice",
    "CHG-": "Charge",
    "SITE-": "Site",
}


class RCARetrievalService:
    """Executes causal-chain RCA traversals against the new ontology."""

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

    def _run(self, query: str, **params) -> list[dict]:
        with self.driver.session(database=self._database) as s:
            return [dict(r) for r in s.run(query, **params)]

    def _single(self, query: str, **params) -> dict | None:
        rows = self._run(query, **params)
        return rows[0] if rows else None

    # ===================================================================
    # ID-routed single-entity RCA
    # ===================================================================
    def rca_by_id(self, entity_id: str) -> dict[str, Any]:
        """Route to the right traversal based on ID prefix."""
        eid = entity_id.strip().upper()
        if eid.startswith("NF-"):
            return self._rca_nf(eid)
        if eid.startswith("PF-"):
            return self._rca_pf(eid)
        if eid.startswith("SD-"):
            return self._rca_sd(eid)
        if eid.startswith("LOG-"):
            return self._rca_log(eid)
        if eid.startswith("KPI-"):
            return self._rca_kpi(eid)
        if eid.startswith("PM-"):
            return self._rca_pm(eid)
        if eid.startswith("DSP-"):
            return self._rca_dsp(eid)
        if eid.startswith("ADJ-"):
            return self._rca_adj(eid)
        if eid.startswith("CUST-"):
            return self.rca_full_chain(eid)
        if eid.startswith("CHG-"):
            return self._rca_chg(eid)
        if eid.startswith("SITE-"):
            return self.site_impact(eid)
        return {"error": f"Unknown ID prefix: {eid}", "entity_id": eid}

    # -------------------------------------------------------------------
    # NF- : NetworkFailure → charges → billing chain + site + evidence
    # -------------------------------------------------------------------
    def _rca_nf(self, nf_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (nf:NetworkFailure {id: $id})-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (nf)-[:AT_SITE]->(site:Site)
            OPTIONAL MATCH (pm:PmCounter)-[corr:CORROBORATES]->(nf)
            OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(nf)
            OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)
            RETURN nf, ch, inv, acct, cust, site,
                   collect(DISTINCT {pm: pm, breach_ratio: corr.breach_ratio,
                           plausible: corr.plausible}) AS pm_evidence,
                   collect(DISTINCT api) AS api_evidence,
                   collect(DISTINCT d) AS disputes
        """, id=nf_id)
        return self._pack_nf(rows, nf_id)

    def _pack_nf(self, rows, nf_id):
        if not rows:
            return {"entity_id": nf_id, "found": False}
        r = rows[0]
        nf = _props(r["nf"])
        charges = []
        disputes = []
        pm_ev = []
        api_ev = []
        seen_ch = set()
        for row in rows:
            ch = _props(row["ch"])
            if ch["id"] not in seen_ch:
                charges.append(ch)
                seen_ch.add(ch["id"])
            for d in (row.get("disputes") or []):
                if d:
                    disputes.append(_props(d))
            for p in (row.get("pm_evidence") or []):
                pm = p.get("pm")
                if pm:
                    pm_ev.append({**_props(pm), "breach_ratio": p.get("breach_ratio"), "plausible": p.get("plausible")})
            for a in (row.get("api_evidence") or []):
                if a:
                    api_ev.append(_props(a))

        confidence = "High" if any(p.get("plausible") for p in pm_ev) else ("Medium" if pm_ev or api_ev else "Unconfirmed")
        return {
            "entity_id": nf_id, "found": True, "type": "NetworkFailure",
            "root": nf,
            "site": _props(rows[0].get("site")) if rows[0].get("site") else None,
            "charges": _dedup(charges),
            "invoice": _props(rows[0].get("inv")),
            "account": _props(rows[0].get("acct")),
            "customer": _props(rows[0].get("cust")),
            "pm_evidence": _dedup(pm_ev),
            "api_evidence": _dedup(api_ev),
            "disputes": _dedup(disputes),
            "confidence": confidence,
        }

    # -------------------------------------------------------------------
    # PF- : PaymentFailure → FAILED_ON → Invoice, Charge ON_INVOICE
    # -------------------------------------------------------------------
    def _rca_pf(self, pf_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (pf:PaymentFailure {id: $id})-[:FAILED_ON]->(inv:Invoice)
            MATCH (inv)-[:BILLED_TO]->(acct:Account)-[:OWNED_BY]->(cust:Customer)
            OPTIONAL MATCH (ch:Charge)-[:ON_INVOICE]->(inv)
            OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(pf)
            RETURN pf, inv, acct, cust,
                   collect(DISTINCT ch) AS charges,
                   collect(DISTINCT api) AS api_evidence
        """, id=pf_id)
        if not rows:
            return {"entity_id": pf_id, "found": False}
        r = rows[0]
        return {
            "entity_id": pf_id, "found": True, "type": "PaymentFailure",
            "root": _props(r["pf"]),
            "invoice": _props(r["inv"]),
            "account": _props(r["acct"]),
            "customer": _props(r["cust"]),
            "charges": [_props(c) for c in (r.get("charges") or []) if c],
            "api_evidence": [_props(a) for a in (r.get("api_evidence") or []) if a],
            "confidence": "Medium" if r.get("api_evidence") else "Unconfirmed",
        }

    # -------------------------------------------------------------------
    # SD- : Incident → CAUSED_CHARGE → Chain B + SlaCredit + API
    # -------------------------------------------------------------------
    def _rca_sd(self, sd_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (inc:Incident {id: $id})-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc)
            OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(inc)
            RETURN inc, ch, inv, acct, cust,
                   collect(DISTINCT sc) AS sla_credits,
                   collect(DISTINCT api) AS api_evidence
        """, id=sd_id)
        if not rows:
            return {"entity_id": sd_id, "found": False}
        r = rows[0]
        return {
            "entity_id": sd_id, "found": True, "type": "Incident",
            "root": _props(r["inc"]),
            "charges": _dedup([_props(row["ch"]) for row in rows]),
            "invoice": _props(r["inv"]),
            "account": _props(r["acct"]),
            "customer": _props(r["cust"]),
            "sla_credits": [_props(s) for s in (r.get("sla_credits") or []) if s],
            "api_evidence": [_props(a) for a in (r.get("api_evidence") or []) if a],
            "confidence": "Medium",
        }

    # -------------------------------------------------------------------
    # LOG- : LogEvent → CAUSED_CHARGE → Chain B + EMITTED_BY Service
    # -------------------------------------------------------------------
    def _rca_log(self, log_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (l:LogEvent {id: $id})-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (l)-[:EMITTED_BY]->(svc:Service)
            RETURN l, ch, inv, acct, cust, svc
        """, id=log_id)
        if not rows:
            return {"entity_id": log_id, "found": False}
        r = rows[0]
        return {
            "entity_id": log_id, "found": True, "type": "LogEvent",
            "root": _props(r["l"]),
            "service": _props(r.get("svc")) if r.get("svc") else None,
            "charges": _dedup([_props(row["ch"]) for row in rows]),
            "invoice": _props(r["inv"]),
            "account": _props(r["acct"]),
            "customer": _props(r["cust"]),
            "confidence": "Medium",
        }

    # -------------------------------------------------------------------
    # KPI- : ApiKpiBreach — root → CAUSED_CHARGE; evidence → CORROBORATES → root → B
    # -------------------------------------------------------------------
    def _rca_kpi(self, kpi_id: str) -> dict[str, Any]:
        # Try as root first
        rows = self._run("""
            MATCH (k:ApiKpiBreach {id: $id})-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH """ + _CHAIN_B + """
            RETURN k, ch, inv, acct, cust, 'root' AS role
        """, id=kpi_id)
        if rows:
            r = rows[0]
            return {
                "entity_id": kpi_id, "found": True, "type": "ApiKpiBreach", "role": "root",
                "root": _props(r["k"]),
                "charges": _dedup([_props(row["ch"]) for row in rows]),
                "invoice": _props(r["inv"]),
                "account": _props(r["acct"]),
                "customer": _props(r["cust"]),
                "confidence": "Medium",
            }
        # Try as evidence
        rows = self._run("""
            MATCH (k:ApiKpiBreach {id: $id})-[:CORROBORATES]->(root)
            OPTIONAL MATCH (root)-[:CAUSED_CHARGE]->(ch:Charge)
            OPTIONAL MATCH """ + _CHAIN_B + """
            RETURN k, root, labels(root) AS root_labels, ch, inv, acct, cust, 'evidence' AS role
        """, id=kpi_id)
        if rows:
            r = rows[0]
            return {
                "entity_id": kpi_id, "found": True, "type": "ApiKpiBreach", "role": "evidence",
                "evidence_node": _props(r["k"]),
                "corroborates_root": _props(r["root"]),
                "root_type": r["root_labels"][0] if r.get("root_labels") else "Unknown",
                "charges": _dedup([_props(row["ch"]) for row in rows if row.get("ch")]),
                "invoice": _props(r.get("inv")) if r.get("inv") else None,
                "account": _props(r.get("acct")) if r.get("acct") else None,
                "customer": _props(r.get("cust")) if r.get("cust") else None,
                "confidence": "Medium",
            }
        return {"entity_id": kpi_id, "found": False}

    # -------------------------------------------------------------------
    # PM- : PmCounter → CORROBORATES → NF → Chain B + MEASURED_AT Site
    # -------------------------------------------------------------------
    def _rca_pm(self, pm_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (pm:PmCounter {id: $id})-[corr:CORROBORATES]->(nf:NetworkFailure)
            OPTIONAL MATCH (nf)-[:CAUSED_CHARGE]->(ch:Charge)
            OPTIONAL MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (pm)-[:MEASURED_AT]->(site:Site)
            RETURN pm, nf, corr.breach_ratio AS breach_ratio, corr.plausible AS plausible,
                   ch, inv, acct, cust, site
        """, id=pm_id)
        if not rows:
            return {"entity_id": pm_id, "found": False}
        r = rows[0]
        return {
            "entity_id": pm_id, "found": True, "type": "PmCounter",
            "evidence_node": _props(r["pm"]),
            "corroborates_root": _props(r["nf"]),
            "breach_ratio": r.get("breach_ratio"),
            "plausible": r.get("plausible"),
            "site": _props(r.get("site")) if r.get("site") else None,
            "charges": _dedup([_props(row["ch"]) for row in rows if row.get("ch")]),
            "invoice": _props(r.get("inv")) if r.get("inv") else None,
            "account": _props(r.get("acct")) if r.get("acct") else None,
            "customer": _props(r.get("cust")) if r.get("cust") else None,
            "confidence": "High" if r.get("plausible") else "Medium",
        }

    # -------------------------------------------------------------------
    # DSP- : Dispute → DISPUTES → Charge → Chain B + find root for charge
    # -------------------------------------------------------------------
    def _rca_dsp(self, dsp_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (d:Dispute {id: $id})-[:DISPUTES]->(ch:Charge)
            MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv)
            RETURN d, ch, inv, acct, cust,
                   collect(DISTINCT {root: root, labels: labels(root)}) AS roots,
                   collect(DISTINCT pf) AS pf_roots
        """, id=dsp_id)
        if not rows:
            return {"entity_id": dsp_id, "found": False}
        r = rows[0]
        roots = []
        for rt in (r.get("roots") or []):
            if rt.get("root"):
                roots.append({**_props(rt["root"]), "type": rt["labels"][0] if rt.get("labels") else "Unknown"})
        for pf in (r.get("pf_roots") or []):
            if pf:
                roots.append({**_props(pf), "type": "PaymentFailure"})
        return {
            "entity_id": dsp_id, "found": True, "type": "Dispute",
            "dispute": _props(r["d"]),
            "charge": _props(r["ch"]),
            "roots": _dedup(roots),
            "invoice": _props(r["inv"]),
            "account": _props(r["acct"]),
            "customer": _props(r["cust"]),
            "confidence": "High" if roots else "Unconfirmed",
        }

    # -------------------------------------------------------------------
    # ADJ- : SlaCredit → COMPENSATES → Incident → CAUSED_CHARGE → Chain B
    # -------------------------------------------------------------------
    def _rca_adj(self, adj_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (sc:SlaCredit {id: $id})-[:COMPENSATES]->(inc:Incident)
            OPTIONAL MATCH (inc)-[:CAUSED_CHARGE]->(ch:Charge)
            OPTIONAL MATCH """ + _CHAIN_B + """
            RETURN sc, inc, ch, inv, acct, cust
        """, id=adj_id)
        if not rows:
            return {"entity_id": adj_id, "found": False}
        r = rows[0]
        return {
            "entity_id": adj_id, "found": True, "type": "SlaCredit",
            "sla_credit": _props(r["sc"]),
            "incident": _props(r["inc"]),
            "charges": _dedup([_props(row["ch"]) for row in rows if row.get("ch")]),
            "invoice": _props(r.get("inv")) if r.get("inv") else None,
            "account": _props(r.get("acct")) if r.get("acct") else None,
            "customer": _props(r.get("cust")) if r.get("cust") else None,
            "confidence": "Medium",
        }

    # -------------------------------------------------------------------
    # CHG- : Charge → Chain B + find root
    # -------------------------------------------------------------------
    def _rca_chg(self, chg_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (ch:Charge {id: $id})
            MATCH """ + _CHAIN_B + """
            OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)
            RETURN ch, inv, acct, cust,
                   collect(DISTINCT {root: root, labels: labels(root)}) AS roots,
                   collect(DISTINCT d) AS disputes
        """, id=chg_id)
        if not rows:
            return {"entity_id": chg_id, "found": False}
        r = rows[0]
        roots = []
        for rt in (r.get("roots") or []):
            if rt.get("root"):
                roots.append({**_props(rt["root"]), "type": rt["labels"][0] if rt.get("labels") else "Unknown"})
        return {
            "entity_id": chg_id, "found": True, "type": "Charge",
            "charge": _props(r["ch"]),
            "roots": roots,
            "invoice": _props(r["inv"]),
            "account": _props(r["acct"]),
            "customer": _props(r["cust"]),
            "disputes": [_props(d) for d in (r.get("disputes") or []) if d],
            "confidence": "High" if roots else "Unconfirmed",
        }

    # ===================================================================
    # Customer-level full chain (CUST-)
    # ===================================================================
    def rca_full_chain(self, customer_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})
            MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)
            OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv)
            OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)
            OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (pm:PmCounter)-[corr:CORROBORATES]->(nf:NetworkFailure)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(root)
            RETURN c, a, ch, inv, root, labels(root) AS root_labels,
                   pf, d, sc, inc,
                   pm, corr.breach_ratio AS breach_ratio, corr.plausible AS plausible,
                   api, nf
        """, cid=customer_id)
        if not rows:
            return {"customer_id": customer_id, "found": False}

        charges = []
        roots = []
        disputes = []
        sla_credits = []
        pm_evidence = []
        api_evidence = []
        pf_list = []
        seen = {"ch": set(), "root": set(), "d": set(), "sc": set(), "pm": set(), "api": set(), "pf": set()}
        # Build traversal paths: entity_id → path string with real IDs
        traversals: dict[str, str] = {}

        for r in rows:
            cust = _props(r["c"]) if r.get("c") else {}
            acct = _props(r["a"]) if r.get("a") else {}
            inv = _props(r["inv"]) if r.get("inv") else {}
            ch = _props(r["ch"])
            cid = cust.get("id", customer_id)
            aid = acct.get("id", "?")
            iid = inv.get("id", "?")
            chid = ch.get("id", "?")
            base_path = f"{cid} → {aid} → {iid} → {chid}"

            if chid not in seen["ch"]:
                charges.append(ch)
                seen["ch"].add(chid)
                traversals[chid] = f"{cid} → {aid} → {iid} → {chid}"

            if r.get("root"):
                rt = _props(r["root"])
                rt["type"] = r["root_labels"][0] if r.get("root_labels") else "Unknown"
                if rt["id"] not in seen["root"]:
                    roots.append(rt)
                    seen["root"].add(rt["id"])
                    traversals[rt["id"]] = f"{base_path} ← {rt['id']}"

            if r.get("d"):
                d = _props(r["d"])
                if d["id"] not in seen["d"]:
                    disputes.append(d)
                    seen["d"].add(d["id"])
                    traversals[d["id"]] = f"{base_path} ← {d['id']}"

            if r.get("sc"):
                sc = _props(r["sc"])
                inc_node = _props(r["inc"]) if r.get("inc") else {}
                if sc["id"] not in seen["sc"]:
                    sla_credits.append(sc)
                    seen["sc"].add(sc["id"])
                    inc_id = inc_node.get("id", "?")
                    traversals[sc["id"]] = f"{base_path} ← {inc_id} ← {sc['id']}"

            if r.get("pm"):
                pm = {**_props(r["pm"]), "breach_ratio": r.get("breach_ratio"), "plausible": r.get("plausible")}
                nf_node = _props(r["nf"]) if r.get("nf") else {}
                if pm["id"] not in seen["pm"]:
                    pm_evidence.append(pm)
                    seen["pm"].add(pm["id"])
                    nf_id = nf_node.get("id", "?")
                    traversals[pm["id"]] = f"{base_path} ← {nf_id} ← {pm['id']}"

            if r.get("api"):
                api = _props(r["api"])
                root_node = _props(r["root"]) if r.get("root") else {}
                if api["id"] not in seen["api"]:
                    api_evidence.append(api)
                    seen["api"].add(api["id"])
                    root_id = root_node.get("id", "?")
                    traversals[api["id"]] = f"{base_path} ← {root_id} ← {api['id']}"

            if r.get("pf"):
                pf = _props(r["pf"])
                if pf["id"] not in seen["pf"]:
                    pf_list.append(pf)
                    seen["pf"].add(pf["id"])
                    traversals[pf["id"]] = f"{cid} → {aid} → {iid} ← {pf['id']}"

        has_plausible = any(p.get("plausible") for p in pm_evidence)
        confidence = "High" if has_plausible else ("Medium" if pm_evidence or api_evidence else "Unconfirmed")
        return {
            "customer_id": customer_id, "found": True,
            "charges": charges,
            "roots": roots,
            "payment_failures": pf_list,
            "disputes": disputes,
            "sla_credits": sla_credits,
            "pm_evidence": pm_evidence,
            "api_evidence": api_evidence,
            "traversals": traversals,
            "confidence": confidence,
        }

    # ===================================================================
    # Site impact
    # ===================================================================
    def site_impact(self, site_id: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (nf:NetworkFailure)-[:AT_SITE]->(s:Site {id: $sid})
            OPTIONAL MATCH (nf)-[:CAUSED_CHARGE]->(ch:Charge)
            OPTIONAL MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)
            OPTIONAL MATCH (pm:PmCounter)-[corr:CORROBORATES]->(nf)
            RETURN nf, ch, inv,
                   collect(DISTINCT {pm_id: pm.id, breach_ratio: corr.breach_ratio,
                           plausible: corr.plausible}) AS pm_evidence
        """, sid=site_id)
        if not rows:
            return {"site_id": site_id, "found": False}

        failures = []
        total_exposure = 0.0
        past_due = 0
        pm_backed = 0
        seen_nf = set()
        for r in rows:
            nf = _props(r["nf"])
            if nf["id"] not in seen_nf:
                failures.append(nf)
                seen_nf.add(nf["id"])
            if r.get("ch"):
                total_exposure += r["ch"].get("amount", 0) or 0
            if r.get("inv") and (r["inv"].get("status") or "").upper() == "PAST_DUE":
                past_due += 1
            for pm in (r.get("pm_evidence") or []):
                if pm.get("plausible"):
                    pm_backed += 1

        return {
            "site_id": site_id, "found": True,
            "failure_count": len(failures),
            "failures": failures,
            "total_exposure": round(total_exposure, 2),
            "past_due_invoices": past_due,
            "pm_backed_count": pm_backed,
        }

    # ===================================================================
    # SLA leakage — incidents with CAUSED_CHARGE but no COMPENSATES
    # ===================================================================
    def sla_leakage(self) -> list[dict[str, Any]]:
        rows = self._run("""
            MATCH (inc:Incident)-[:CAUSED_CHARGE]->(ch:Charge)
            WHERE NOT ()-[:COMPENSATES]->(inc)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            RETURN inc.id AS incident_id, inc.disruption_reason AS reason,
                   inc.severity AS severity, ch.id AS charge_id,
                   ch.amount AS charge_amount, inv.status AS invoice_status,
                   c.id AS customer_id
        """)
        return rows

    # ===================================================================
    # Service name lookup — LogEvent EMITTED_BY Service → charges → disputes
    # ===================================================================
    def service_impact(self, service_name: str) -> dict[str, Any]:
        rows = self._run("""
            MATCH (l:LogEvent)-[:EMITTED_BY]->(s:Service {id: $sn})
            OPTIONAL MATCH (l)-[:CAUSED_CHARGE]->(ch:Charge)
            OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)
            RETURN s, count(DISTINCT l) AS log_count,
                   count(DISTINCT ch) AS charge_count,
                   count(DISTINCT d) AS dispute_count
        """, sn=service_name)
        if not rows:
            return {"service_name": service_name, "found": False}
        r = rows[0]
        return {
            "service_name": service_name, "found": True,
            "log_count": r["log_count"],
            "charge_count": r["charge_count"],
            "dispute_count": r["dispute_count"],
        }

    # ===================================================================
    # Unresolved issues (open disputes + past-due invoices)
    # ===================================================================
    def unresolved_issues(self) -> dict[str, Any]:
        disputes = self._run("""
            MATCH (d:Dispute)-[:DISPUTES]->(ch:Charge)
            WHERE d.status IN ['OPEN', 'IN_REVIEW', 'ESCALATED']
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            RETURN d.id AS dispute_id, d.reason AS reason, d.status AS status,
                   ch.id AS charge_id, ch.amount AS charge_amount,
                   inv.id AS invoice_id, inv.status AS invoice_status,
                   c.id AS customer_id
            ORDER BY ch.amount DESC LIMIT 50
        """)
        past_due = self._run("""
            MATCH (inv:Invoice {status: 'PAST_DUE'})-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            RETURN inv.id AS invoice_id, inv.amount AS amount,
                   inv.due_date AS due_date, c.id AS customer_id
            ORDER BY inv.amount DESC LIMIT 50
        """)
        return {
            "open_disputes": disputes,
            "past_due_invoices": past_due,
            "total_open_disputes": len(disputes),
            "total_past_due": len(past_due),
        }

    # ===================================================================
    # Cross-customer aggregate queries (for "all customers" mode)
    # ===================================================================
    def all_network_failures(self, limit: int = 50) -> list[dict]:
        return self._run("""
            MATCH (nf:NetworkFailure)-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            OPTIONAL MATCH (nf)-[:AT_SITE]->(s:Site)
            RETURN nf.id AS failure_id, nf.failure_type AS failure_type,
                   nf.severity AS severity, nf.affected_service AS service,
                   s.id AS site_id, ch.amount AS charge_amount,
                   inv.status AS invoice_status, c.id AS customer_id
            ORDER BY ch.amount DESC LIMIT $limit
        """, limit=limit)

    def all_payment_failures(self, limit: int = 50) -> list[dict]:
        return self._run("""
            MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv:Invoice)
            MATCH (inv)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            RETURN pf.id AS pf_id, pf.failure_type AS failure_type,
                   pf.failure_reason AS reason, pf.payment_amount AS amount,
                   inv.id AS invoice_id, inv.status AS invoice_status,
                   c.id AS customer_id
            ORDER BY pf.payment_amount DESC LIMIT $limit
        """, limit=limit)

    def all_incidents(self, limit: int = 50) -> list[dict]:
        return self._run("""
            MATCH (inc:Incident)-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc)
            RETURN inc.id AS incident_id, inc.disruption_reason AS reason,
                   inc.severity AS severity, ch.amount AS charge_amount,
                   inv.status AS invoice_status, c.id AS customer_id,
                   sc.id AS sla_credit_id
            ORDER BY ch.amount DESC LIMIT $limit
        """, limit=limit)

    def all_disputes(self, limit: int = 50) -> list[dict]:
        return self._run("""
            MATCH (d:Dispute)-[:DISPUTES]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            RETURN d.id AS dispute_id, d.reason AS reason, d.status AS status,
                   ch.id AS charge_id, ch.amount AS amount, c.id AS customer_id
            ORDER BY ch.amount DESC LIMIT $limit
        """, limit=limit)

    def all_log_errors(self, limit: int = 50) -> list[dict]:
        return self._run("""
            MATCH (l:LogEvent)-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            OPTIONAL MATCH (l)-[:EMITTED_BY]->(svc:Service)
            RETURN l.id AS log_id, l.log_level AS level, l.message AS message,
                   svc.id AS service, ch.amount AS charge_amount, c.id AS customer_id
            ORDER BY ch.amount DESC LIMIT $limit
        """, limit=limit)

    # ===================================================================
    # Graph payloads for visualization
    # ===================================================================
    def overview_graph(self, limit: int = 30) -> dict[str, Any]:
        """Sampled overview of the causal graph."""
        rows = self._run("""
            MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)
            WITH root, ch, inv, a, c, rand() AS r
            ORDER BY r LIMIT $limit
            RETURN root, labels(root) AS root_labels, ch, inv, a, c
        """, limit=limit)

        nodes = {}
        rels = []
        for r in rows:
            for key, lbl_override in [("root", r["root_labels"][0] if r.get("root_labels") else "RootCause"),
                                       ("ch", "Charge"), ("inv", "Invoice"),
                                       ("a", "Account"), ("c", "Customer")]:
                n = r.get(key)
                if n:
                    props = _props(n)
                    nid = props.get("id", "")
                    if nid and nid not in nodes:
                        nodes[nid] = {
                            "id": nid, "name": nid,
                            "ontology_class": lbl_override,
                            "properties": props,
                        }

        # Build rels from the known chain
        for r in rows:
            root_id = _props(r.get("root")).get("id", "")
            ch_id = _props(r.get("ch")).get("id", "")
            inv_id = _props(r.get("inv")).get("id", "")
            acc_id = _props(r.get("a")).get("id", "")
            cust_id = _props(r.get("c")).get("id", "")
            if root_id and ch_id:
                rels.append({"id": f"r-{root_id}-{ch_id}", "source": root_id, "target": ch_id, "type": "CAUSED_CHARGE"})
            if ch_id and inv_id:
                rels.append({"id": f"r-{ch_id}-{inv_id}", "source": ch_id, "target": inv_id, "type": "ON_INVOICE"})
            if inv_id and acc_id:
                rels.append({"id": f"r-{inv_id}-{acc_id}", "source": inv_id, "target": acc_id, "type": "BILLED_TO"})
            if acc_id and cust_id:
                rels.append({"id": f"r-{acc_id}-{cust_id}", "source": acc_id, "target": cust_id, "type": "OWNED_BY"})

        # Dedup rels
        seen_rels = set()
        unique_rels = []
        for rel in rels:
            if rel["id"] not in seen_rels:
                unique_rels.append(rel)
                seen_rels.add(rel["id"])

        return {
            "nodes": list(nodes.values()),
            "relationships": unique_rels,
            "source": "neo4j",
            "total_nodes": len(nodes),
            "total_relationships": len(unique_rels),
        }

    def customer_graph_payload(self, customer_id: str) -> dict[str, Any]:
        """Full causal graph for a single customer."""
        rows = self._run("""
            MATCH (a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})
            MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)
            OPTIONAL MATCH (root)-[:CAUSED_CHARGE]->(ch) WHERE root:RootCause
            OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv)
            OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)
            OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (pm:PmCounter)-[:CORROBORATES]->(nf:NetworkFailure)-[:CAUSED_CHARGE]->(ch)
            OPTIONAL MATCH (pm)-[:MEASURED_AT]->(site:Site)
            OPTIONAL MATCH (nf2:NetworkFailure)-[:AT_SITE]->(site2:Site) WHERE nf2.id IN [root.id]
            OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(root)
            OPTIONAL MATCH (le:LogEvent)-[:EMITTED_BY]->(svc:Service) WHERE le.id IN [root.id]
            RETURN c, a, ch, inv, root, labels(root) AS root_labels,
                   pf, d, sc, inc, pm, site, nf2, site2, api, le, svc
        """, cid=customer_id)

        nodes = {}
        rels = []

        def add_node(obj, label):
            if not obj:
                return
            props = _props(obj)
            nid = props.get("id", "")
            if nid and nid not in nodes:
                nodes[nid] = {"id": nid, "name": nid, "ontology_class": label, "properties": props}

        def add_rel(src, tgt, rtype):
            if src and tgt:
                sid = _props(src).get("id", "") if not isinstance(src, str) else src
                tid = _props(tgt).get("id", "") if not isinstance(tgt, str) else tgt
                if sid and tid:
                    rels.append({"id": f"r-{sid}-{tid}-{rtype}", "source": sid, "target": tid, "type": rtype})

        for r in rows:
            add_node(r.get("c"), "Customer")
            add_node(r.get("a"), "Account")
            add_node(r.get("ch"), "Charge")
            add_node(r.get("inv"), "Invoice")
            root_label = r["root_labels"][0] if r.get("root_labels") else "RootCause"
            add_node(r.get("root"), root_label)
            add_node(r.get("pf"), "PaymentFailure")
            add_node(r.get("d"), "Dispute")
            add_node(r.get("sc"), "SlaCredit")
            add_node(r.get("inc"), "Incident")
            add_node(r.get("pm"), "PmCounter")
            add_node(r.get("site"), "Site")
            add_node(r.get("site2"), "Site")
            add_node(r.get("api"), "ApiKpiBreach")
            add_node(r.get("svc"), "Service")

            add_rel(r.get("a"), r.get("c"), "OWNED_BY")
            add_rel(r.get("inv"), r.get("a"), "BILLED_TO")
            add_rel(r.get("ch"), r.get("inv"), "ON_INVOICE")
            add_rel(r.get("root"), r.get("ch"), "CAUSED_CHARGE")
            add_rel(r.get("pf"), r.get("inv"), "FAILED_ON")
            add_rel(r.get("d"), r.get("ch"), "DISPUTES")
            add_rel(r.get("sc"), r.get("inc"), "COMPENSATES")
            if r.get("pm") and r.get("nf2"):
                add_rel(r.get("pm"), r["nf2"], "CORROBORATES")
            add_rel(r.get("pm"), r.get("site"), "MEASURED_AT")
            add_rel(r.get("nf2"), r.get("site2"), "AT_SITE")
            add_rel(r.get("api"), r.get("root"), "CORROBORATES")
            if r.get("le") and r.get("svc"):
                add_rel(r["le"], r["svc"], "EMITTED_BY")

        # Dedup rels
        seen_rels = set()
        unique_rels = []
        for rel in rels:
            if rel["id"] not in seen_rels:
                unique_rels.append(rel)
                seen_rels.add(rel["id"])

        return {
            "nodes": list(nodes.values()),
            "relationships": unique_rels,
            "source": "neo4j",
            "total_nodes": len(nodes),
            "total_relationships": len(unique_rels),
        }

    # Aliases for backward compatibility
    def customer_impact(self, customer_id: str) -> dict[str, Any]:
        return self.rca_full_chain(customer_id)

    def dunning_chain(self, customer_id: str) -> list[dict]:
        chain = self.rca_full_chain(customer_id)
        return chain.get("payment_failures", [])

    def sla_credit_chain(self, customer_id: str) -> list[dict]:
        chain = self.rca_full_chain(customer_id)
        return chain.get("sla_credits", [])

    def system_error_chain(self, customer_id: str) -> list[dict]:
        return self._run("""
            MATCH (l:LogEvent)-[:CAUSED_CHARGE]->(ch:Charge)
            MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})
            OPTIONAL MATCH (l)-[:EMITTED_BY]->(svc:Service)
            RETURN l.id AS log_id, l.log_level AS level, l.message AS message,
                   svc.id AS service, ch.id AS charge_id, ch.amount AS charge_amount
        """, cid=customer_id)

    def kpi_breach_to_incident(self, customer_id: str) -> list[dict]:
        return self._run("""
            MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})
            MATCH (k:ApiKpiBreach)-[:CAUSED_CHARGE]->(ch)
            RETURN k.id AS kpi_id, k.kpi_name AS kpi_name,
                   k.kpi_value AS kpi_value, k.threshold_value AS threshold,
                   ch.id AS charge_id, ch.amount AS charge_amount
        """, cid=customer_id)

    def intent_graph_payload(self, customer_id: str, intent: str) -> dict[str, Any]:
        return self.customer_graph_payload(customer_id)


def _props(node) -> dict[str, Any]:
    """Safely extract properties from a Neo4j node or return empty dict."""
    if node is None:
        return {}
    if hasattr(node, "items"):
        return dict(node)
    return {}


def _dedup(items: list[dict]) -> list[dict]:
    """Deduplicate list of dicts by 'id' key."""
    seen = set()
    out = []
    for item in items:
        iid = item.get("id")
        if iid and iid not in seen:
            out.append(item)
            seen.add(iid)
        elif not iid:
            out.append(item)
    return out
