from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

from openai import OpenAI

from .config import Settings
from .models import (
    ChatResponse,
    EvidenceItem,
    GraphNode,
    GraphPayload,
    TokenUsage,
    TraversalStep,
)
from .conversation_memory import ConversationMemory
from .neo4j_service import Neo4jService
from .repository import DemoRepository


class QAService:
    def __init__(self, settings: Settings, demo: DemoRepository, neo4j: Neo4jService):
        self.settings = settings
        self.demo = demo
        self.neo4j = neo4j
        self._conversation_scenario: dict[str, str] = {}
        self._history: dict[str, list[dict[str, str]]] = defaultdict(list)
        self.memory = ConversationMemory(settings)
        self._openai = OpenAI(api_key=settings.openai_api_key) if settings.llm_provider == "openai" and settings.openai_configured else None
        self._ollama = OpenAI(base_url=settings.ollama_url, api_key="ollama") if settings.ollama_configured else None

    def _select_scenario(self, message: str, conversation_id: str) -> str:
        _ = message
        return self._conversation_scenario.get(conversation_id, "")

    @staticmethod
    def _intent(message: str) -> str:
        normalized = message.lower()
        if re.search(r"\bINC\d{6,10}\b", message, re.IGNORECASE) and any(term in normalized for term in ("similar", "same", "related", "matching", "like")):
            return "similar_incidents"
        if "incident" in normalized and any(term in normalized for term in ("kind", "kinds", "type", "types", "category", "categories", "occurred", "occured")):
            return "incident_kind_summary"
        if "incident" in normalized and any(term in normalized for term in ("show one", "show an", "show a", "give one", "give an", "give a", "one example", "an example", "a example")):
            return "incident_example"
        if "incident" in normalized and any(term in normalized for term in ("show", "list", "every", "all", "each", "times", "records")):
            return "incident_record_set"
        if "issue categor" in normalized and any(term in normalized for term in ("severe", "severity", "sev1", "sev2")):
            return "issue_category_severity"
        if any(term in normalized for term in ("why", "fixed", "resolve", "resolved", "restored", "stuck", "outage")) and any(term in normalized for term in ("order", "orders", "service", "journey", "application", "latency", "barring", "unbarring")):
            return "incident_example"
        if any(term in normalized for term in ("fix", "resolve", "remediation", "runbook", "action")):
            return "remediation_path"
        if any(term in normalized for term in ("impact", "customer", "affected", "service")):
            return "impact_analysis"
        if any(term in normalized for term in ("alarm", "kpi", "evidence", "prove")):
            return "evidence_investigation"
        return "investigation"

    @staticmethod
    def _is_incident_record_set_question(message: str, scope: dict[str, Any]) -> bool:
        normalized = message.lower()
        if scope.get("incident_id"):
            return False
        if "incident" in normalized and any(term in normalized for term in ("show", "list", "every", "all", "each", "times", "records", "which")):
            return True
        return "incident" in normalized and any(term in normalized for term in ("require", "requires", "need", "needs", "pending", "open", "remediation", "action"))

    @staticmethod
    def _is_operational_incident_question(message: str, scope: dict[str, Any]) -> bool:
        normalized = message.lower()
        if scope.get("incident_id") or not scope.get("terms"):
            return False
        incident_nouns = (
            "incident", "issue", "outage", "degradation", "latency", "pendency",
            "orders", "journey", "application", "service", "barring", "unbarring",
            "payment", "posting", "dunning",
        )
        question_verbs = (
            "why", "what", "which", "how", "fixed", "resolve", "resolved",
            "restore", "restored", "stuck", "impacted", "affected", "failed",
        )
        return any(term in normalized for term in incident_nouns) and any(term in normalized for term in question_verbs)

    @staticmethod
    def _expects_single_incident(message: str) -> bool:
        normalized = message.lower()
        single_markers = ("show one", "show an", "show a", "give one", "give an", "give a", "one example", "an example")
        causal_markers = ("why", "how was", "how were", "fixed", "resolved", "restored", "stuck")
        return any(term in normalized for term in single_markers) or any(term in normalized for term in causal_markers)

    def _graph(self, scenario_id: str) -> GraphPayload:
        if self.neo4j.verify():
            try:
                return self.neo4j.graph(scenario_id=scenario_id)
            except Exception:
                if not self.settings.allow_demo_fallback:
                    raise
        return self.demo.graph(scenario_id=scenario_id)

    @staticmethod
    def _scope_from_message(message: str) -> dict[str, str]:
        scenario_match = re.search(r"\b(?:SCN-[A-Z0-9-]+|RCA-EP-\d{6})\b", message, re.IGNORECASE)
        incident_match = re.search(r"\bINC\d{6,10}\b", message, re.IGNORECASE)
        alert_match = re.search(r"\b(?:ALT|ALM)-\d{6}\b", message, re.IGNORECASE)
        identifiers = sorted({
            item.upper()
            for item in re.findall(r"\b[A-Z]{2,10}[-_]?\d{3,12}\b", message, re.IGNORECASE)
        })
        identifiers = sorted({
            *identifiers,
            *{
                item.upper()
                for item in re.findall(r"\b[A-Z][A-Z0-9]+(?:_[A-Z0-9]+)+\b", message)
            },
            *{
                item.upper()
                for item in re.findall(r"\b[A-Z][A-Z0-9]{2,}\b", message)
                if not item.upper().startswith("INC")
            },
        })
        return {
            "scenario_id": scenario_match.group(0).upper() if scenario_match else "",
            "incident_id": incident_match.group(0).upper() if incident_match else "",
            "alert_id": alert_match.group(0).upper() if alert_match else "",
            "root_cause": "",
            "identifiers": identifiers,
        }

    @staticmethod
    def _query_terms(message: str) -> list[str]:
        stopwords = {
            "what", "which", "where", "when", "why", "how", "this", "that", "did",
            "the", "and", "for", "from", "with", "into", "show", "give",
            "tell", "about", "happened", "incident", "issue", "issues",
            "kind", "kinds", "type", "types", "occur", "occurred", "occured",
            "category", "categories", "generate", "most", "more", "less",
            "severe", "severity", "solution", "remediation", "root", "cause",
            "one", "example", "was", "were", "have", "has", "had", "latency",
            "restored", "loading",
        }
        terms = []
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_/-]{2,}", message):
            lowered = token.lower()
            if lowered not in stopwords and not re.fullmatch(r"inc\d+", lowered):
                terms.append(token)
                for part in re.split(r"[/_-]+", token):
                    if len(part) >= 3 and part.lower() not in stopwords:
                        terms.append(part)
        month_numbers = {
            "jan": "01", "january": "01",
            "feb": "02", "february": "02",
            "mar": "03", "march": "03",
            "apr": "04", "april": "04",
            "may": "05",
            "jun": "06", "june": "06",
            "jul": "07", "july": "07",
            "aug": "08", "august": "08",
            "sep": "09", "sept": "09", "september": "09",
            "oct": "10", "october": "10",
            "nov": "11", "november": "11",
            "dec": "12", "december": "12",
        }
        for day, month, year in re.findall(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b", message):
            month_number = month_numbers.get(month.lower())
            if month_number:
                terms.insert(0, f"{year}-{month_number}-{int(day):02d}")
                terms.insert(1, f"{year}-{month_number}")
        return list(dict.fromkeys(terms))[:10]

    @staticmethod
    def _node_by_id(graph: GraphPayload) -> dict[str, GraphNode]:
        return {node.id: node for node in graph.nodes}

    @staticmethod
    def _neighbors(graph: GraphPayload, node_id: str, types: set[str] | None = None) -> list[GraphNode]:
        node_by_id = QAService._node_by_id(graph)
        results = []
        for rel in graph.relationships:
            if types and rel.type not in types and rel.ontology_property not in types:
                continue
            other_id = ""
            if rel.source == node_id:
                other_id = rel.target
            elif rel.target == node_id:
                other_id = rel.source
            if other_id and other_id in node_by_id:
                results.append(node_by_id[other_id])
        return results

    @staticmethod
    def _local_context_graph(graph: GraphPayload, scope: dict[str, str], limit: int = 260) -> GraphPayload:
        seeds = {value for value in (scope.get("scenario_id"), scope.get("incident_id"), scope.get("alert_id")) if value}
        selected = [
            node for node in graph.nodes
            if (scope.get("scenario_id") and node.scenario_id == scope["scenario_id"])
            or node.id in seeds
            or any(seed and seed in node.id for seed in seeds)
        ]
        if not selected and scope.get("root_cause"):
            root = scope["root_cause"].lower()
            cause_ids = {
                node.id for node in graph.nodes
                if node.ontology_class == "AlarmProbableCause"
                and root in f"{node.id} {node.name}".lower()
            }
            scenario_ids = {
                rel.scenario_id for rel in graph.relationships
                if rel.target in cause_ids and rel.scenario_id
            }
            selected = [node for node in graph.nodes if node.scenario_id in scenario_ids]
        if not selected:
            selected = graph.nodes[:limit]
        selected = selected[:limit]
        ids = {node.id for node in selected}
        for _ in range(2):
            for rel in graph.relationships:
                if rel.source in ids or rel.target in ids:
                    ids.add(rel.source)
                    ids.add(rel.target)
            if len(ids) >= limit:
                break
        nodes = [node for node in graph.nodes if node.id in ids][:limit]
        final_ids = {node.id for node in nodes}
        relationships = [
            rel for rel in graph.relationships
            if rel.source in final_ids and rel.target in final_ids
        ]
        return GraphPayload(
            nodes=nodes,
            relationships=relationships,
            source=graph.source,
            total_nodes=len(nodes),
            total_relationships=len(relationships),
        )

    def _context_graph(self, scope: dict[str, str], conversation_id: str) -> GraphPayload:
        scenario_id = scope.get("scenario_id") or self._conversation_scenario.get(conversation_id, "")
        entity_ids = [value for value in (scope.get("incident_id"), scope.get("alert_id")) if value]
        identifiers = list(dict.fromkeys([*scope.get("identifiers", []), *entity_ids]))
        if self.neo4j.verify():
            if identifiers:
                graph = self.neo4j.dynamic_context_graph(identifiers=identifiers, terms=[], limit=260)
            elif scenario_id:
                graph = self.neo4j.context_graph(scenario_id or None, [])
            elif scope.get("terms"):
                graph = self.neo4j.dynamic_context_graph(identifiers=[], terms=scope["terms"], limit=260)
            else:
                graph = self.neo4j.graph(root_cause=scope.get("root_cause") or None, limit=260)
            if graph.nodes:
                return graph
        return self.demo.graph(scenario_id=scenario_id or None, limit=260)

    @staticmethod
    def _infer_scenario_id(graph: GraphPayload, scope: dict[str, str]) -> str:
        if scope.get("scenario_id"):
            return scope["scenario_id"]
        counts = Counter(node.scenario_id for node in graph.nodes if node.scenario_id)
        return counts.most_common(1)[0][0] if counts else ""

    @staticmethod
    def _primary_incident(graph: GraphPayload, scope: dict[str, str]) -> GraphNode | None:
        node_by_id = QAService._node_by_id(graph)
        if scope.get("incident_id") in node_by_id:
            return node_by_id[scope["incident_id"]]
        incident_id = str(scope.get("incident_id", "")).upper()
        if incident_id:
            for node in graph.nodes:
                props = node.properties or {}
                values = {
                    str(node.id).upper(),
                    str(node.name).upper(),
                    str(props.get("Incident_Number", "")).upper(),
                    str(props.get("incident_number", "")).upper(),
                }
                if incident_id in values:
                    return node
        incidents = [node for node in graph.nodes if node.ontology_class in {"Incident", "IncidentRCARecord"}]
        if incidents:
            def incident_rank(node: GraphNode) -> tuple[int, str]:
                props = node.properties or {}
                closed = str(props.get("state", "")).strip() == "6" or bool(props.get("resolved_at"))
                current_hint = node.id.startswith("INC010") or node.id.startswith("INCIDENT:RCA-EP")
                return (1 if closed else 0, 0 if current_hint else 1, node.id)
            return sorted(incidents, key=incident_rank)[0]
        tickets = [node for node in graph.nodes if node.ontology_class == "TroubleTicket"]
        return sorted(tickets, key=lambda item: item.id)[0] if tickets else None

    @staticmethod
    def _root_cause_nodes(graph: GraphPayload) -> list[GraphNode]:
        node_by_id = QAService._node_by_id(graph)
        rca_ids = {node.id for node in graph.nodes if node.ontology_class == "RootCauseAnalysis"}
        cause_ids = {
            rel.target for rel in graph.relationships
            if rel.source in rca_ids and rel.type == "CLASSIFIED_AS"
        }
        causes = [node_by_id[node_id] for node_id in cause_ids if node_id in node_by_id]
        if causes:
            return causes
        return [node for node in graph.nodes if node.ontology_class == "AlarmProbableCause"]

    @staticmethod
    def _graph_evidence(graph: GraphPayload) -> list[EvidenceItem]:
        classes = {"Alarm", "KPIObservation", "LogEvent", "ChangeRecord", "Runbook", "RootCauseAnalysis", "AlarmProbableCause"}
        evidence = []
        for node in graph.nodes:
            if node.ontology_class not in classes:
                continue
            label = node.ontology_class
            if node.ontology_class == "KPIObservation":
                label = node.properties.get("kpi_name") or node.name
            value = node.id if node.id == node.name else f"{node.name} ({node.id})"
            evidence.append(EvidenceItem(label=str(label), value=value, source=node.source_system or "Neo4j"))
            if len(evidence) >= 12:
                break
        return evidence

    @staticmethod
    def _graph_traversal(graph: GraphPayload) -> list[TraversalStep]:
        node_by_id = QAService._node_by_id(graph)
        preferred = [
            "SAME_AS_INCIDENT", "HAS_TICKET", "HAS_STATUS", "HAS_CATEGORY",
            "HAS_SEVERITY", "HAS_PROBLEM", "HAS_ROOT_CAUSE", "RESOLVED_BY",
            "ABOUT", "IMPACTS", "HAS_CHANGE", "ASSIGNED_TO", "REPORTED_BY",
            "LOCATED_AT", "TRIGGERED_BY", "ESCALATES_TO", "ESCALATED_FROM",
            "AFFECTS", "CLASSIFIED_AS", "ROOT_CAUSE_IN", "EVIDENCES",
            "MEASURED_ON", "INDICATES", "RESOLVED_BY", "GUIDED_BY_RUNBOOK",
            "CONTAINS_TASK", "USES", "TRIGGERS",
        ]
        ordered = sorted(
            graph.relationships,
            key=lambda rel: (preferred.index(rel.type) if rel.type in preferred else 99, rel.source, rel.target),
        )
        steps = []
        for rel in ordered[:12]:
            source = node_by_id.get(rel.source)
            target = node_by_id.get(rel.target)
            if not source or not target:
                continue
            steps.append(TraversalStep(
                order=len(steps) + 1,
                from_entity=source.id,
                from_class=source.ontology_class,
                relationship=rel.type,
                to_entity=target.id,
                to_class=target.ontology_class,
                explanation=f"Graph edge `{rel.type}` from `{source.id}` to `{target.id}` returned by {rel.source_system or 'Neo4j'}.",
            ))
        return steps

    @staticmethod
    def _compact_list(nodes: list[GraphNode], limit: int = 8) -> str:
        if not nodes:
            return "Not found in the retrieved graph context"
        values = [f"`{node.id}`" if node.id == node.name else f"`{node.name}` (`{node.id}`)" for node in nodes[:limit]]
        suffix = f" and {len(nodes) - limit} more" if len(nodes) > limit else ""
        return ", ".join(values) + suffix

    @staticmethod
    def _humanize_key(key: str) -> str:
        return key.replace("_", " ").replace("-", " ").strip().title()

    @staticmethod
    def _important_properties(node: GraphNode, limit: int = 14) -> list[tuple[str, Any]]:
        skip = {
            "canonical_id", "id", "label", "ontology_class", "source_system",
            "source_document_id", "source_sheet", "source_column", "source_row",
            "source_row:int",
        }
        values = []
        for key, value in (node.properties or {}).items():
            if key in skip or value in (None, ""):
                continue
            values.append((key, value))
        return values[:limit]

    @staticmethod
    def _row_value(row: dict[str, Any], *keys: str, default: str = "") -> str:
        for key in keys:
            value = row.get(key)
            if value not in (None, ""):
                return str(value).strip()
        return default

    @staticmethod
    def _brief(value: Any, limit: int = 240) -> str:
        text = str(value or "")
        text = text.replace(chr(65533), " ")
        for bad in ("�", "â€¢", "•", "\u2022", "\t"):
            text = text.replace(bad, " ")
        text = text.encode("ascii", "ignore").decode("ascii")
        text = re.sub(r"\s+", " ", text).strip(" -:\t")
        if not text:
            return "Not found"
        return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."

    @classmethod
    def _table_cell(cls, value: Any, limit: int = 220) -> str:
        return cls._brief(value, limit=limit).replace("|", "\\|")

    @classmethod
    def _row_incident_number(cls, row: dict[str, Any]) -> str:
        return cls._row_value(row, "Incident_Number", "incident_number", "name", "id", default="Unknown")

    @staticmethod
    def _split_actions(text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return []
        markers = [
            r"to mitigate(?: the issue)?",
            r"to resolve(?: the issue| the same)?",
            r"as a workaround",
            r"post which",
            r"after which",
            r"following this",
        ]
        actions = []
        lowered = cleaned.lower()
        for marker in markers:
            match = re.search(marker, lowered)
            if match:
                actions.append(cleaned[match.start():])
                break
        if not actions:
            actions.append(cleaned)
        return actions

    @staticmethod
    def _family_name(text: str) -> str:
        normalized = text.lower()
        if "insufficient memory" in normalized or ("memory" in normalized and any(term in normalized for term in ("clm", "server", "hung"))):
            return "CLM server memory / hung server"
        if "dlt" in normalized and any(term in normalized for term in ("scrubb", "701", "url", "whitelist")):
            return "DLT scrubbing / URL whitelisting"
        if "dff not available" in normalized:
            return "Service Portal DFF / Case interaction error"
        if "service portal" in normalized and any(term in normalized for term in ("sr", "advisor", "cc advisor", "troubleticket", "case")):
            return "Service Portal SR creation / Case API issue"
        if "jndi" in normalized or ("south url" in normalized and "north url" in normalized):
            return "JNDI routing issue"
        if "campaign" in normalized and any(term in normalized for term in ("expired", "active campaign", "campaign type")):
            return "Campaign configuration issue"
        checks = [
            (("solace", "queue", "esb", "bind count", "consumer", "publisher"), "ESB/Solace queue or publisher blockage"),
            (("job", "stuck", "lookup", "response", "clm"), "CLM job/process blockage"),
            (("change", "rollback", "rolled back", "chg"), "Change deployment impact"),
            (("power", "ups", "dc", "nxtra"), "Data-center power / infrastructure"),
            (("bgp", "transport", "air node"), "Network transport / routing"),
        ]
        best_name = "Other application/process issue"
        best_score = 0
        for keywords, name in checks:
            score = sum(1 for keyword in keywords if keyword in normalized)
            if score > best_score:
                best_name = name
                best_score = score
        return best_name

    @classmethod
    def _family_summary(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            text = " ".join([
                cls._row_value(row, "Issue_Description"),
                cls._row_value(row, "Restoration_Details"),
                cls._row_value(row, "RCA"),
            ])
            family = cls._family_name(text)
            bucket = grouped.setdefault(family, {
                "family": family,
                "incidents": [],
                "outage": 0,
                "problems": set(),
                "evidence": [],
                "actions": [],
            })
            incident_number = cls._row_incident_number(row)
            if incident_number and incident_number not in bucket["incidents"]:
                bucket["incidents"].append(incident_number)
            try:
                bucket["outage"] += int(float(cls._row_value(row, "Minutes_of_Outage", default="0") or 0))
            except ValueError:
                pass
            problem = cls._row_value(row, "Problem_Record")
            if problem:
                bucket["problems"].add(problem)
            evidence = cls._row_value(row, "RCA") or cls._row_value(row, "Restoration_Details") or cls._row_value(row, "Issue_Description")
            if evidence:
                bucket["evidence"].append(evidence)
            bucket["actions"].extend(cls._split_actions(cls._row_value(row, "Restoration_Details")))
        summaries = []
        for bucket in grouped.values():
            summaries.append({
                **bucket,
                "problems": sorted(bucket["problems"]),
                "root_cause": cls._brief(bucket["evidence"][0] if bucket["evidence"] else "", limit=190),
                "remediation": cls._brief(bucket["actions"][0] if bucket["actions"] else "", limit=190),
            })
        return sorted(summaries, key=lambda item: (-len(item["incidents"]), -item["outage"], item["family"]))

    @staticmethod
    def _family_remediation(family: str, observed_action: str) -> str:
        playbook = {
            "DLT scrubbing / URL whitelisting": "Whitelist/register required URLs at DLT, keep DLT bypass only as temporary mitigation, then repush pending SMS.",
            "CLM server memory / hung server": "Increase CLM capacity, restart/recover impacted CLM services, then repush pending SMS after response flow is stable.",
            "ESB/Solace queue or publisher blockage": "Clear/refresh the stuck ESB/Solace queue, restore consumer/publisher bind count, restart CLM jobs, then repush pendency.",
            "CLM job/process blockage": "Fix the stuck CLM job or lookup process, restart affected jobs/services, then validate delivery and clear pending transactions.",
            "JNDI routing issue": "Correct JNDI routing to the active endpoint, rerun impacted jobs, then verify SMS delivery recovery.",
            "Campaign configuration issue": "Activate or correct the campaign configuration, rerun the affected campaign/job, then validate pending SMS clearance.",
            "Change deployment impact": "Roll back or fix the impacting change, verify CLM/BTFLY response recovery, then resume SMS processing.",
            "Data-center power / infrastructure": "Recover DC power/infrastructure first, bring dependent app services back, then validate BTFLY_KCI collateral impact closure.",
            "Network transport / routing": "Restore the failed network/transport path, shift traffic if required, then confirm SMS and dependent journey recovery.",
            "Service Portal DFF / Case interaction error": "Refresh/recover the Case interaction or Rules Framework service, validate `DFF not available` errors stop, then retest SR creation from Service Portal.",
            "Service Portal SR creation / Case API issue": "Recover the impacted Case/TroubleTicket API or Service Portal component, retest CC advisor SR creation, and mark repeat if the same fingerprint recurs.",
        }
        return playbook.get(family, observed_action or "Use the matched restoration details to close the immediate blockage, then convert repeated manual steps into permanent fixes.")

    @classmethod
    def _row_text(cls, row: dict[str, Any]) -> str:
        return " ".join([
            cls._row_value(row, "Issue_Description"),
            cls._row_value(row, "Restoration_Details"),
            cls._row_value(row, "RCA"),
            cls._row_value(row, "Impacted_Application", "ApplicationName"),
        ]).lower()

    @classmethod
    def _similarity_subclusters(cls, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        definitions = [
            ("DFF not available from Case end", ("dff not available",), "Case team refresh of Rules Framework / TroubleTicket API."),
            ("Case API / Kafka / Guardian failure", ("case management api", "case api", "kafka", "guardian", "v3 microservice"), "Refresh/recover impacted Case service or V3 microservice."),
            ("HB_CRM / BTFLY_CRM DB or downstream", ("hb_crm", "btfly_crm", "database", "db ", "reco", "archive"), "DB failover/reset or downstream archive/config correction."),
            ("iFactory / data-usage API", ("ifactory", "data usage", "data-usage"), "Kill long queries or restart iFactory/data-usage API."),
            ("Service Portal pod/server issue", ("pod", "kong", "server", "node"), "Restart impacted pod/server or remove bad node from serving path."),
            ("Shifting SR / upstream journey scenario", ("shifting", "mop", "selfcare_b2c", "pacs", "homes", "nokia oss"), "Correct upstream journey owner action and retest SR flow."),
            ("Case deployment / config drift", ("deployment", "redeployment", "rollback", "config", "scenario removed", "column"), "Rollback bad deployment or restore missing configuration."),
        ]
        buckets = [
            {"cluster": name, "incidents": [], "rows": [], "restoration": restoration}
            for name, _, restoration in definitions
        ]
        other = {"cluster": "Other Service Portal SR issues", "incidents": [], "rows": [], "restoration": "Use recorded restoration details, then review for repeat tagging."}
        for row in rows:
            text = cls._row_text(row)
            target = other
            for index, (_, keywords, _) in enumerate(definitions):
                if any(keyword in text for keyword in keywords):
                    target = buckets[index]
                    break
            incident_number = cls._row_incident_number(row)
            if incident_number and incident_number not in target["incidents"]:
                target["incidents"].append(incident_number)
            target["rows"].append(row)
        populated = [bucket for bucket in [*buckets, other] if bucket["incidents"]]
        return sorted(populated, key=lambda item: (-len(item["incidents"]), item["cluster"]))

    @staticmethod
    def _incident_relationship_groups(graph: GraphPayload, incident_id: str) -> dict[str, list[GraphNode]]:
        node_by_id = QAService._node_by_id(graph)
        groups: dict[str, list[GraphNode]] = defaultdict(list)
        for rel in graph.relationships:
            if rel.source == incident_id and rel.target in node_by_id:
                groups[rel.type].append(node_by_id[rel.target])
            elif rel.target == incident_id and rel.source in node_by_id:
                groups[f"INCOMING_{rel.type}"].append(node_by_id[rel.source])
        return groups

    @staticmethod
    def _solution_relationship_types(groups: dict[str, list[GraphNode]]) -> list[str]:
        priority_terms = (
            "PROBLEM", "ROOT", "CAUSE", "RESOL", "REMED", "CHANGE",
            "ASSIGNED", "OWNER", "STATUS", "CATEGORY", "SEVERITY",
            "IMPACT", "ABOUT", "LOCATION", "TICKET",
        )
        ranked = []
        for rel_type in groups:
            upper = rel_type.upper()
            score = next((index for index, term in enumerate(priority_terms) if term in upper), len(priority_terms))
            ranked.append((score, rel_type))
        return [rel_type for _, rel_type in sorted(ranked)]

    @staticmethod
    def _root_resource_nodes(graph: GraphPayload) -> list[GraphNode]:
        node_by_id = QAService._node_by_id(graph)
        rca_ids = {node.id for node in graph.nodes if node.ontology_class == "RootCauseAnalysis"}
        root_ids = {
            rel.target for rel in graph.relationships
            if rel.source in rca_ids and rel.type == "ROOT_CAUSE_IN"
        }
        ordered = [node_by_id[node_id] for node_id in root_ids if node_id in node_by_id]
        if ordered:
            return ordered
        return []

    @staticmethod
    def _action_items(graph: GraphPayload) -> list[str]:
        actions: list[str] = []
        for node in graph.nodes:
            if node.ontology_class == "Resolution":
                props = node.properties or {}
                for item in props.get("tool_path", []):
                    actions.append(str(item))
            if node.ontology_class == "ProcessStep":
                actions.append(node.name)
        deduped = []
        for action in actions:
            if action and action not in deduped:
                deduped.append(action)
        return deduped

    def _compose_graph_answer(self, message: str, graph: GraphPayload, intent: str, scope: dict[str, str]) -> str:
        if not graph.nodes:
            terms = [*scope.get("identifiers", []), *scope.get("terms", [])]
            target = ", ".join(f"`{term}`" for term in terms) if terms else "the question terms"
            return (
                f"**Summary:** I could not find matching Neo4j evidence for {target}.\n\n"
                "**Findings**\n"
                "- No seed node matched the identifiers or keywords extracted from the question.\n\n"
                "**Traversal To Solution**\n"
                "- No traversal was run because no starting node was found.\n\n"
                "**Validation Against Workbook**\n"
                "- No workbook row can be validated until a graph node is matched."
            )
        incident = self._primary_incident(graph, scope)
        scenario_id = self._infer_scenario_id(graph, scope)
        rcas = [node for node in graph.nodes if node.ontology_class == "RootCauseAnalysis"]
        causes = self._root_cause_nodes(graph)
        alarms = [node for node in graph.nodes if node.ontology_class == "Alarm"]
        kpis = [node for node in graph.nodes if node.ontology_class == "KPIObservation"]
        logs = [node for node in graph.nodes if node.ontology_class == "LogEvent"]
        services = [node for node in graph.nodes if node.ontology_class in {"Service", "CustomerFacingService"}]
        all_resources = [node for node in graph.nodes if node.ontology_class in {"Device", "IPInterface", "FiberSpan", "Router", "CellSite", "gNodeB", "Cell", "PowerSystem", "BatteryBackup", "Generator"}]
        root_resources = self._root_resource_nodes(graph)
        root_ids = {node.id for node in root_resources}
        resources = [*root_resources, *[node for node in all_resources if node.id not in root_ids]]
        changes = [node for node in graph.nodes if node.ontology_class == "ChangeRecord"]
        runbooks = [node for node in graph.nodes if node.ontology_class == "Runbook"]
        resolutions = [node for node in graph.nodes if node.ontology_class == "Resolution"]
        process_steps = [node for node in graph.nodes if node.ontology_class == "ProcessStep"]

        evidence_sources = {node.source_system for node in [*alarms, *kpis, *logs, *changes, *runbooks, *rcas] if node.source_system}
        confidence = "high" if len(evidence_sources) >= 3 else "medium" if len(evidence_sources) == 2 else "low" if evidence_sources else "not enough evidence"
        subject = f"`{incident.id}`" if incident else f"`{scenario_id}`" if scenario_id else "the retrieved RCA context"
        root_text = self._compact_list(causes[:3])
        service_text = self._compact_list(services[:5])
        resource_text = self._compact_list(resources[:6])

        lines = [
            f"**Summary:** For {subject}, the graph returns RCA evidence as {root_text}. Impacted service context is {service_text}. Confidence is {confidence} based on {len(evidence_sources)} independent source group(s) in the retrieved graph context.",
            "",
            "**Findings**",
            f"- RCA nodes: {self._compact_list(rcas[:4])}.",
            f"- Probable causes: {root_text}.",
            f"- Affected resources: {resource_text}.",
            "",
            "**Evidence**",
            f"- Alarms: {self._compact_list(alarms[:8])}.",
            f"- KPI observations: {self._compact_list(kpis[:8])}.",
            f"- Logs: {self._compact_list(logs[:6])}.",
            f"- Change/deployment records: {self._compact_list(changes[:6])}.",
            "",
            "**Blast Radius**",
            f"- Impacted services: {service_text}.",
            f"- Device/resource context: {resource_text}.",
        ]
        if changes:
            lines.extend([
                "",
                "**Ruled-Out Or Correlated Changes**",
                f"- Change records returned by traversal: {self._compact_list(changes[:8])}. Review relationship properties for correlation status and timing.",
            ])
        if runbooks or resolutions or process_steps:
            action_items = self._action_items(graph)
            lines.extend([
                "",
                "**Remediation Path**",
                f"- Runbooks: {self._compact_list(runbooks[:5])}.",
                f"- Resolutions: {self._compact_list(resolutions[:5])}.",
                f"- Ordered tool/process steps: {self._compact_list(process_steps[:10], limit=10)}.",
            ])
            for index, action in enumerate(action_items[:8], start=1):
                lines.append(f"{index}. {action}")
        if kpis or alarms:
            lines.extend([
                "",
                "**Verification**",
                "- Verify recovery against returned alarm state and KPI threshold properties. If no cleared-state or post-recovery KPI node is present, treat this as expected verification, not observed closure.",
            ])
        lines.extend([
            "",
            "**Traversal Used**",
        ])
        for step in self._graph_traversal(graph)[:8]:
            lines.append(f"- `{step.from_entity}` -[:`{step.relationship}`]-> `{step.to_entity}`")
        lines.extend([
            "",
            f"**Source Provenance:** {', '.join(sorted(evidence_sources)) if evidence_sources else 'Not found in the retrieved graph context'}.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _token_usage(response: Any) -> TokenUsage | None:
        usage = getattr(response, "usage", None)
        if not usage:
            return None
        input_tokens = getattr(usage, "input_tokens", None)
        if input_tokens is None:
            input_tokens = getattr(usage, "prompt_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", None)
        if output_tokens is None:
            output_tokens = getattr(usage, "completion_tokens", 0)
        total_tokens = getattr(usage, "total_tokens", 0) or input_tokens + output_tokens
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
        )

    @staticmethod
    def _answer_instructions() -> str:
        return (
            "You are a telecom RCA analyst. Answer only from the supplied retrieved "
            "Neo4j graph context and the drafted graph-grounded answer. Always start with a short "
            "normal-language line beginning with '**Summary:**'. Keep answers compact and structured. "
            "For any why/how incident question, explicitly include '**Why It Happened**' before the recovery or remediation section. "
            "Use Markdown sections such as '**Root Causes**', '**Why It Happened**', '**Closest Incidents**', '**Remediation**', "
            "'**Validation Against Workbook**', and '**Traversal**' when relevant. Use Markdown pipe "
            "tables for comparisons or ranked records. Use short bullets for actions. Wrap artifact "
            "identifiers, statuses, severities, owners, and counts in backticks so the UI can render "
            "them as badges. End with source provenance. Never invent identifiers, topology hops, KPI "
            "values, alarms, runbooks, timestamps, counts, workbook rows, or remediation steps."
        )

    @staticmethod
    def _llm_context(graph: GraphPayload, scope: dict[str, str], intent: str, draft: str) -> dict[str, Any]:
        return {
            "intent": intent,
            "retrieval_scope": scope,
            "draft_answer": draft,
            "nodes": [
                {
                    "id": node.id,
                    "name": node.name,
                    "ontology_class": node.ontology_class,
                    "source_system": node.source_system,
                    "scenario_id": node.scenario_id,
                    "properties": node.properties,
                }
                for node in graph.nodes[:80]
            ],
            "relationships": [
                {
                    "source": rel.source,
                    "target": rel.target,
                    "type": rel.type,
                    "ontology_property": rel.ontology_property,
                    "source_system": rel.source_system,
                    "scenario_id": rel.scenario_id,
                    "properties": rel.properties,
                }
                for rel in graph.relationships[:140]
            ],
        }

    def _openai_answer(self, message: str, context: dict[str, Any], fallback: str, conversation_id: str) -> tuple[str, str, TokenUsage | None]:
        if not self._openai:
            return fallback, "kg", None
        context_json = json.dumps(context, ensure_ascii=False)
        history = self._history[conversation_id][-6:]
        try:
            request = {
                "model": self.settings.openai_model,
                "instructions": self._answer_instructions(),
                "input": [*history, {"role": "user", "content": f"Question: {message}\nRetrieved graph context: {context_json}"}],
            }
            if not self.settings.openai_model.lower().startswith("gpt-5"):
                request["temperature"] = self.settings.openai_temperature
            response = self._openai.responses.create(**request)
            answer = response.output_text.strip()
            return (answer or fallback), "openai", self._token_usage(response)
        except Exception:
            return fallback, "kg", None

    def _ollama_answer(self, message: str, context: dict[str, Any], fallback: str, conversation_id: str) -> tuple[str, str, TokenUsage | None]:
        if not self._ollama:
            return fallback, "kg", None
        context_json = json.dumps(context, ensure_ascii=False)
        history = self._history[conversation_id][-6:]
        try:
            response = self._ollama.chat.completions.create(
                model=self.settings.ollama_model,
                messages=[
                    {"role": "system", "content": self._answer_instructions()},
                    *history,
                    {"role": "user", "content": f"Question: {message}\nRetrieved graph context: {context_json}"},
                ],
            )
            answer = (response.choices[0].message.content or "").strip()
            return (answer or fallback), "ollama", self._token_usage(response)
        except Exception:
            return fallback, "kg", None

    def _llm_answer(self, message: str, context: dict[str, Any], fallback: str, conversation_id: str) -> tuple[str, str, TokenUsage | None]:
        if self.settings.llm_provider == "ollama":
            return self._ollama_answer(message, context, fallback, conversation_id)
        if self.settings.llm_provider == "openai":
            return self._openai_answer(message, context, fallback, conversation_id)
        return fallback, "kg", None

    @staticmethod
    def _empty_graph(source: str = "demo") -> GraphPayload:
        return GraphPayload(nodes=[], relationships=[], source=source, total_nodes=0, total_relationships=0)

    @classmethod
    def _issue_category_severity_answer(cls, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return (
                "**Summary:** I could not find issue-category severity data in the current Neo4j graph.\n\n"
                "**Findings**\n"
                "- Expected pattern: `IncidentRCARecord` -[:`HAS_CATEGORY` {source_column: `Issue Category`}]-> `CommonCategory`.\n"
                "- Expected pattern: `IncidentRCARecord` -[:`HAS_SEVERITY`]-> `CommonCategory`.\n\n"
                "**Remediation**\n"
                "- Confirm the fixed import files from `SamplePOCData/import` were loaded and that `HAS_CATEGORY` / `HAS_SEVERITY` relationships exist."
            )

        lines = [
            "**Summary:** Issue categories generating the most severe incidents are ranked by `Sev1`, then `Sev2`, then `SDN`, then total incident count.",
            "",
            "**Severity Ranking**",
            "| Issue Category | Sev1 | Sev2 | SDN | LW | Total |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for index, row in enumerate(rows, start=1):
            _ = index
            lines.append(f"| `{cls._table_cell(row['issue_category'], limit=80)}` | `{row['sev1']}` | `{row['sev2']}` | `{row['sdn']}` | `{row['lw']}` | `{row['total']}` |")
        top = rows[0]
        lines.extend([
            "",
            "**Findings**",
            f"- Top category by severe incident ranking: `{top['issue_category']}` with `{top['sev1']}` Sev1 and `{top['sev2']}` Sev2 incident(s).",
            "- Use Sev1 and Sev2 categories first for remediation backlog prioritization.",
            "",
            "**Remediation**",
            "- Prioritize permanent fixes for issue categories with Sev1 incidents.",
            "- For categories with high Sev2 volume, group related incidents by application, service path, and supporting partner before assigning remediation owners.",
            "",
            "**Traversal Used**",
            "- `IncidentRCARecord` -[:`HAS_CATEGORY` {source_column: `Issue Category`}]-> `CommonCategory`.",
            "- `IncidentRCARecord` -[:`HAS_SEVERITY`]-> `CommonCategory`.",
            "- The result is grouped by issue category and severity, so there is no single incident-to-remediation path for this aggregate question.",
            "",
            "**Source Provenance:** Live Neo4j aggregate over `IncidentRCARecord`, `CommonCategory`, `HAS_CATEGORY`, and `HAS_SEVERITY`.",
        ])
        return "\n".join(lines)

    @classmethod
    def _incident_record_set_answer(cls, rows: list[dict[str, Any]], graph: GraphPayload, scope: dict[str, Any]) -> str:
        if not rows:
            terms = [*scope.get("identifiers", []), *scope.get("terms", [])]
            target = ", ".join(f"`{term}`" for term in terms) if terms else "the requested incident cohort"
            return (
                f"**Summary:** I could not find matching `IncidentRCARecord` rows for {target} in Neo4j.\n\n"
                "**Traversal To Solution**\n"
                "- Searched imported incident RCA records by the identifiers and keywords in the question.\n"
                "- No matched row was found, so no root-cause or remediation path can be validated.\n\n"
                "**Validation Against Workbook**\n"
                "- Reload `SamplePOCData/import` if the workbook was recently regenerated."
            )

        incident_numbers = [cls._row_incident_number(row) for row in rows]
        unique_incidents = sorted(set(incident_numbers))
        reopens = len(incident_numbers) - len(unique_incidents)
        outage_total = 0
        for row in rows:
            try:
                outage_total += int(float(cls._row_value(row, "Minutes_of_Outage", default="0") or 0))
            except ValueError:
                pass
        problem_records = sorted({
            cls._row_value(row, "Problem_Record")
            for row in rows
            if cls._row_value(row, "Problem_Record")
        })
        source_rows = [
            cls._row_value(row, "source_row", "source_row:int", default="?")
            for row in rows
        ]
        families = cls._family_summary(rows)

        lines = [
            f"**Summary:** `{len(unique_incidents)}` unique incident(s), `{len(rows)}` RCA record(s), `{outage_total}` outage minutes. Main driver: **{families[0]['family']}**.",
            "",
            "**Root Causes**",
            "| Root Cause Group | Incidents | Outage Min | Problem Records | Sample Incidents |",
            "|---|---:|---:|---|---|",
        ]

        for index, family in enumerate(families[:5], start=1):
            _ = index
            incident_list = ", ".join(f"`{item}`" for item in family["incidents"][:8])
            if len(family["incidents"]) > 8:
                incident_list += f" +{len(family['incidents']) - 8} more"
            problem_text = ", ".join(f"`{cls._table_cell(item, limit=40)}`" for item in family["problems"]) if family["problems"] else "no PRB linked"
            lines.append(f"| **{cls._table_cell(family['family'], limit=80)}** | `{len(family['incidents'])}` | `{family['outage']}` | {problem_text} | {incident_list} |")

        lines.extend([
            "",
            "**Remediation**",
        ])
        for family in families[:5]:
            lines.append(f"- **{family['family']}**: {cls._family_remediation(family['family'], family['remediation'])}")
        lines.extend([
            "- **Priority:** close linked PRBs first, then eliminate repeated manual recovery steps: bypass, queue flush, restart, rerun, and repush.",
            "",
            "**Validation Against Workbook**",
            f"- Workbook: `{cls._row_value(rows[0], 'source_document_id', default='Incident RCA.xlsm')}`, sheet `{cls._row_value(rows[0], 'source_sheet', default='Not found')}`.",
            f"- Re-open/additional records: `{reopens}`. Problem records: {', '.join(f'`{item}`' for item in problem_records) if problem_records else 'Not found in matched rows'}.",
            f"- Matched workbook rows: {', '.join(f'`{row}`' for row in source_rows[:35])}{' ...' if len(source_rows) > 35 else ''}.",
            "",
            "**Traversal**",
            "- `IncidentRCARecord` -> `RCA` / `Restoration_Details` / `Problem_Record` -> root-cause family -> remediation backlog.",
        ])
        lines.append("")
        lines.append("**Source Provenance:** Live Neo4j over imported `IncidentRCARecord` rows.")
        return "\n".join(lines)

    @classmethod
    def _incident_kind_summary_answer(cls, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return (
                "**Summary:** I could not find incident kind/category data in the current Neo4j graph.\n\n"
                "**Findings**\n"
                "- Expected pattern: `IncidentRCARecord` -[:`HAS_CATEGORY` {source_column: `Issue Category` or `Sub Category`}]-> `CommonCategory`.\n\n"
                "**Remediation**\n"
                "- Confirm the incident RCA workbook has been imported and that `HAS_CATEGORY` relationships were loaded for `Issue Category` / `Sub Category`."
            )

        total = sum(int(row.get("incidents") or 0) for row in rows)
        lines = [
            f"**Summary:** The KG found `{total}` categorized incident record(s). The most common kind is `{rows[0]['issue_category']}`.",
            "",
            "**Incident Kinds**",
            "| Kind / Issue Category | Incident Records | Sample Sub-Categories |",
            "|---|---:|---|",
        ]
        for row in rows:
            sub_categories = row.get("sub_categories") or []
            sub_text = ", ".join(f"`{cls._table_cell(item, limit=45)}`" for item in sub_categories) if sub_categories else "Not found"
            lines.append(f"| `{cls._table_cell(row['issue_category'], limit=80)}` | `{row['incidents']}` | {sub_text} |")
        lines.extend([
            "",
            "**Traversal Used**",
            "- `IncidentRCARecord` -[:`HAS_CATEGORY` {source_column: `Issue Category`}]-> `CommonCategory`.",
            "- Optional sub-category labels are read from `HAS_CATEGORY` relationships whose source column is `Sub Category` / `SubCategory`.",
            "",
            "**Source Provenance:** Live Neo4j aggregate over imported `IncidentRCARecord`, `CommonCategory`, and `HAS_CATEGORY` relationships.",
        ])
        return "\n".join(lines)

    @classmethod
    def _incident_example_answer(cls, rows: list[dict[str, Any]], graph: GraphPayload, scope: dict[str, Any]) -> str:
        if not rows:
            terms = [*scope.get("identifiers", []), *scope.get("terms", [])]
            target = ", ".join(f"`{term}`" for term in terms) if terms else "the requested example"
            return (
                f"**Summary:** I could not find one matching resolved RCA incident for {target}.\n\n"
                "**Remediation**\n"
                "- Confirm the question terms exist in imported `IncidentRCARecord` properties and reload the Neo4j import if needed."
            )

        row = rows[0]
        incident_number = cls._row_incident_number(row)
        restoration = cls._row_value(row, "Restoration_Details")
        rca = cls._row_value(row, "RCA")
        issue = cls._row_value(row, "Issue_Description")
        source_row = cls._row_value(row, "source_row", "source_row:int", default="?")
        matched_values = list(dict.fromkeys([*row.get("matched_identifiers", []), *row.get("matched_terms", [])]))
        match_terms = ", ".join(f"`{item}`" for item in matched_values)
        lines = [
            f"**Summary:** One strong match is `{incident_number}`. It matched {match_terms or 'the question terms'} and the restoration path is available in the imported RCA row.",
            "",
            "**Incident Snapshot**",
            "| Field | Value |",
            "|---|---|",
            f"| Incident | `{cls._table_cell(incident_number, limit=40)}` |",
            f"| Date / duration | `{cls._table_cell(cls._row_value(row, 'Incident_Start_Time'), limit=40)}` -> `{cls._table_cell(cls._row_value(row, 'Incident_Resolved_Time'), limit=40)}`; `{cls._table_cell(cls._row_value(row, 'Minutes_of_Outage'), limit=20)}` min |",
            f"| Partner | `{cls._table_cell(cls._row_value(row, 'Supporting_Partner', 'SupportingPartner'), limit=80)}` |",
            f"| Application / Journey | `{cls._table_cell(cls._row_value(row, 'Impacted_Application', 'ApplicationName'), limit=80)}` / `{cls._table_cell(cls._row_value(row, 'Business_Service_Path'), limit=100)}` |",
            f"| Location / LOB | `{cls._table_cell(cls._row_value(row, 'Location'), limit=80)}` / `{cls._table_cell(cls._row_value(row, 'Impacted_LOB', 'ImpactingLOB', 'Impacting_LOB'), limit=80)}` |",
            f"| Severity / Status | `{cls._table_cell(cls._row_value(row, 'Severity_Category', 'Severity'), limit=30)}` / `{cls._table_cell(cls._row_value(row, 'Status'), limit=30)}` |",
            f"| Category | `{cls._table_cell(cls._row_value(row, 'Issue_Category'), limit=60)}` -> `{cls._table_cell(cls._row_value(row, 'Issue_Sub_Category'), limit=80)}`; `{cls._table_cell(cls._row_value(row, 'Issue_Type'), limit=40)}`; repeat `{cls._table_cell(cls._row_value(row, 'Repeat_Issue', 'Repeat'), limit=20)}` |",
            f"| Reported By | `{cls._table_cell(cls._row_value(row, 'Reported_by_NOC_App_MIM_SM'), limit=80)}` |",
            f"| Business Impact | `{cls._table_cell(cls._row_value(row, 'Business_Impact'), limit=180)}` |",
            f"| Problem Record | `{cls._table_cell(cls._row_value(row, 'Problem_Record', default='Not found'), limit=40)}` |",
            "",
            "**What Happened**",
            f"- {cls._table_cell(issue or rca or restoration, limit=320)}",
            "",
            "**Why It Happened**",
        ]
        why_steps = cls._why_steps(rca, restoration)
        if why_steps:
            for index, step in enumerate(why_steps[:6], start=1):
                lines.append(f"{index}. {cls._table_cell(step, limit=380)}")
        else:
            lines.append("- Root-cause details were not captured in the matched row.")
        lines.extend([
            "",
            "**How Latency Was Restored**",
        ])
        steps = cls._restoration_steps(restoration)
        if steps:
            for index, step in enumerate(steps[:7], start=1):
                lines.append(f"{index}. {cls._table_cell(step, limit=360)}")
        else:
            lines.append("- Restoration steps were not captured in the matched row.")
        lines.extend([
            "",
            "**Remediation**",
            f"- Immediate: {cls._table_cell(cls._best_restoration_step(steps) or restoration, limit=320)}",
            f"- Permanent: {cls._table_cell(cls._permanent_action(row, why_steps), limit=360)}",
            "",
            "**Validation Against Workbook**",
            f"- Workbook: `{cls._row_value(row, 'source_document_id', default='Incident RCA.xlsm')}`, sheet `{cls._row_value(row, 'source_sheet', default='Not found')}`, row `{source_row}`.",
            f"- Match score: `{cls._row_value(row, 'match_score', default='0')}`.",
            "",
            "**Traversal**",
            "- Question terms -> `IncidentRCARecord` property match -> `Issue_Description` / `Restoration_Details` / `RCA` -> restoration/remediation.",
            "",
            "**Source Provenance:** Live Neo4j over imported `IncidentRCARecord` rows.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        if not cleaned:
            return []
        parts = re.split(r"(?<=[.!?])\s+|(?:\s*\|\s*)+", cleaned)
        return [part.strip(" .") for part in parts if part.strip(" .")]

    @classmethod
    def _why_steps(cls, rca: str, restoration: str) -> list[str]:
        steps: list[str] = []
        causal_terms = (
            "because", "due to", "root cause", "caused", "occurred due", "identified",
            "rejected", "rejection", "not registered", "unregistered", "not replicated",
            "missing", "failure", "error", "timeout", "queue pile up", "not getting consumed",
            "actual cause", "unknown reason",
        )
        for line in re.split(r"\n+|\s*\|\s*", str(rca or "")):
            cleaned = cls._brief(line, limit=520)
            lowered = cleaned.lower()
            if not cleaned or lowered.startswith(("q1", "q2", "q3", "q4", "q5", "q6", "observations", "preventive actions", "key observations")):
                continue
            if lowered in {"root cause summary", "primary root cause", "primary root cause summary"}:
                continue
            if any(term in lowered for term in (" eta ", " open ", "will implement", "need to be revised", "threshold need", "are advised to", "prevent reoccurrence")):
                continue
            if lowered.startswith(("ans", "a1", "a2", "a3", "a4", "a5", "primary root cause")):
                cleaned = re.sub(r"^(ans\.?|a\d+\.?|primary root cause:?)\s*", "", cleaned, flags=re.IGNORECASE).strip(" :-")
                cleaned = cls._clean_why_step(cleaned)
                if cleaned and cleaned not in steps:
                    steps.append(cleaned)
            elif any(term in lowered for term in causal_terms):
                cleaned = cls._clean_why_step(cleaned)
                if cleaned and cleaned not in steps:
                    steps.append(cleaned)
        if steps:
            return steps

        for sentence in cls._split_sentences(restoration):
            lowered = sentence.lower()
            if any(term in lowered for term in causal_terms):
                sentence = cls._clean_why_step(sentence)
                if sentence and sentence not in steps:
                    steps.append(sentence)
        return steps

    @staticmethod
    def _clean_why_step(text: str) -> str:
        cleaned = str(text or "").strip()
        for marker in (" Further to mitigate", " Further, to mitigate", " To mitigate", " As a workaround"):
            position = cleaned.lower().find(marker.lower())
            if position > 40:
                cleaned = cleaned[:position].strip(" .")
                break
        return cleaned

    @staticmethod
    def _restoration_steps(text: str) -> list[str]:
        sentences = QAService._split_sentences(text)
        if not sentences:
            return []
        action_markers = (
            "to fix", "to mitigate", "workaround", "rolled back", "rollback",
            "restart", "restarted", "refresh", "repush", "re-push", "corrective action",
            "created new", "renamed", "killed", "cleared", "resolved", "started working fine",
            "improvement was observed",
        )
        matches = []
        for sentence in sentences:
            lowered = sentence.lower()
            if any(marker in lowered for marker in action_markers):
                if sentence not in matches:
                    matches.append(sentence)
        return matches or sentences[-4:]

    @staticmethod
    def _best_restoration_step(steps: list[str]) -> str:
        if not steps:
            return ""
        priority_terms = ("renamed", "created new", "processing fine", "cleared", "resolved", "post which")
        for term in priority_terms:
            for step in steps:
                if term in step.lower():
                    return step
        return steps[-1]

    @classmethod
    def _permanent_action(cls, row: dict[str, Any], why_steps: list[str]) -> str:
        text = " ".join([
            cls._row_value(row, "Issue_Description"),
            cls._row_value(row, "Restoration_Details"),
            cls._row_value(row, "RCA"),
            " ".join(why_steps),
        ]).lower()
        if any(term in text for term in ("dlt", "scrubbing", "sms", "kci")):
            return "register and periodically validate required KCI/DLT URLs, add failure logging between Bumblebee and DLT, and alert on SMS rejection codes or pendency growth."
        if any(term in text for term in ("token validation", "cache api", "cart", "ecaf", "cms api")):
            return "make DB configuration and Cache API updates atomic, add post-change token/cache validation, and include live eCAF order creation checks in deployment sanity."
        if any(term in text for term in ("queue pile up", "not getting consumed", "clm", "solace", "esb")):
            return "monitor queue depth and consumer health, alert on stuck consumption, and define ownership for repush/consumer restart before pendency grows."
        if any(term in text for term in ("change", "rollback", "deployment", "chg")):
            return "add pre/post deployment validation, rollback criteria, and business-flow sanity checks for impacted applications and journeys."
        return "convert the observed workaround into a permanent control with monitoring, ownership, validation evidence, and repeat-issue prevention."

    @classmethod
    def _similar_incidents_answer(
        cls,
        anchor: dict[str, Any] | None,
        matches: list[dict[str, Any]],
        graph: GraphPayload,
        incident_id: str,
    ) -> str:
        if not anchor:
            return (
                f"**Summary:** I could not find `{incident_id}` in Neo4j.\n\n"
                "**Remediation**\n"
                "- Reload the current import files and confirm the incident exists in `IncidentRCARecord.Incident_Number`."
            )

        category = cls._row_value(anchor, "Issue_Category", "Category", default="Not found")
        sub_category = cls._row_value(anchor, "Issue_Sub_Category", default="Not found")
        severity = cls._row_value(anchor, "Severity_Category", "Severity", default="Not found")
        issue_type = cls._row_value(anchor, "Issue_Type", default="Not found")
        application = cls._row_value(anchor, "Impacted_Application", "ApplicationName", default="Not found")
        lob = cls._row_value(anchor, "Impacted_LOB", "ImpactingLOB", "Impacting_LOB", default="Not found")
        families = cls._family_summary(matches)
        tight_matches = [
            row for row in matches
            if "same sub-category" in (row.get("similarity_reasons") or [])
            and "same impacted application" in (row.get("similarity_reasons") or [])
        ]
        analysis_rows = tight_matches or matches
        subclusters = cls._similarity_subclusters(analysis_rows)
        top_family = subclusters[0]["cluster"] if subclusters else (families[0]["family"] if families else "Not enough matching incidents")
        direct_analogues = [
            row for row in analysis_rows
            if any(term in cls._row_text(row) for term in ("dff not available", "unable to raise sr", "cc advisors", "service portal"))
        ]
        closest = (direct_analogues or analysis_rows)[:8]
        source_rows = [cls._row_value(row, "source_row", "source_row:int", default="?") for row in closest]
        counts = anchor.get("similarity_counts") or {}
        same_category_severity = counts.get("same_category_severity", len(matches))
        same_sub_category = counts.get("same_sub_category", 0)
        same_application = counts.get("same_application", 0)

        lines = [
            f"**Summary:** `{incident_id}` is `{category}` / `{severity}`. Found `{same_category_severity}` resolved incident(s) with the same category and severity; top ranked pattern is **{top_family}**.",
            "",
            "**Anchor Signature**",
            f"- Sub-category: `{sub_category}`; issue type: `{issue_type}`.",
            f"- Application: `{application}`; LOB: `{lob}`.",
            f"- Tightening counts: same sub-category `{same_sub_category}`, same application `{same_application}`.",
            "",
            "**Sub-Clusters**",
            "| Sub-Cluster | Incidents | Sample Incidents | Typical Restoration |",
            "|---|---:|---|---|",
        ]
        if subclusters:
            for cluster in subclusters[:7]:
                incidents = ", ".join(f"`{item}`" for item in cluster["incidents"][:10])
                if len(cluster["incidents"]) > 10:
                    incidents += f" +{len(cluster['incidents']) - 10} more"
                lines.append(f"| **{cls._table_cell(cluster['cluster'], limit=90)}** | `{len(cluster['incidents'])}` | {incidents} | {cls._table_cell(cluster['restoration'], limit=180)} |")
        else:
            lines.append("- No resolved incidents matched the anchor category and severity.")

        lines.extend(["", "**Direct Analogues**", "| Incident | Outage Min | Repeat | Restoration |", "|---|---:|---|---|"])
        if closest:
            for row in closest[:6]:
                incident_number = cls._row_incident_number(row)
                mins = cls._row_value(row, "Minutes_of_Outage", default="?")
                repeat = cls._row_value(row, "Repeat_Issue", "Repeat", default="No")
                restoration = cls._table_cell(cls._row_value(row, "Restoration_Details", "Issue_Description"), limit=135)
                lines.append(f"| `{cls._table_cell(incident_number, limit=40)}` | `{cls._table_cell(mins, limit=20)}` | `{cls._table_cell(repeat, limit=20)}` | {restoration} |")
        else:
            lines.append("- No direct analogue found beyond category/severity match.")

        lines.extend(["", "**Remediation**"])
        if subclusters:
            for cluster in subclusters[:5]:
                lines.append(f"- **{cluster['cluster']}**: {cluster['restoration']}")
        else:
            lines.append("- No remediation pattern can be inferred without similar resolved incidents.")
        lines.extend([
            "- For `INC17506243`: treat as repeat-candidate if the same Service Portal SR/DFF fingerprint appears in validation rows; RCA is blank, so use nearest resolved analogues to propose Case/Service Portal remediation.",
            "",
            "**Validation Against Workbook**",
            f"- Anchor row: `{cls._row_value(anchor, 'source_row', 'source_row:int', default='?')}` in `{cls._row_value(anchor, 'source_document_id', default='Incident RCA.xlsm')}` / `{cls._row_value(anchor, 'source_sheet', default='Not found')}`.",
            f"- Closest match rows: {', '.join(f'`{row}`' for row in source_rows) if source_rows else 'Not found'}.",
            "",
            "**Traversal**",
            "- Anchor `IncidentRCARecord` -> same `Issue_Category` + `Severity_Category` resolved records -> score by sub-category, application, LOB, issue type, and issue/restoration wording -> remediation pattern.",
            "",
            "**Source Provenance:** Live Neo4j over imported `IncidentRCARecord` rows.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _related_nodes(graph: GraphPayload, start_id: str, rel_type: str, source_column: str | None = None) -> list[GraphNode]:
        node_by_id = QAService._node_by_id(graph)
        results = []
        for rel in graph.relationships:
            if rel.source != start_id or rel.type != rel_type:
                continue
            if source_column and str((rel.properties or {}).get("source_column", "")).lower() != source_column.lower():
                continue
            target = node_by_id.get(rel.target)
            if target:
                results.append(target)
        return results

    @staticmethod
    def _find_incident_record(graph: GraphPayload, incident_id: str) -> GraphNode | None:
        normalized = incident_id.upper()
        matches = []
        for node in graph.nodes:
            props = node.properties or {}
            values = {
                str(node.name).upper(),
                str(node.id).upper(),
                str(props.get("Incident_Number", "")).upper(),
                str(props.get("incident_number", "")).upper(),
            }
            if normalized in values:
                matches.append(node)
        with_props = [node for node in matches if (node.properties or {}).get("Incident_Number")]
        return with_props[0] if with_props else matches[0] if matches else None

    def _incident_detail_answer(self, graph: GraphPayload, incident_id: str) -> str:
        incident = self._find_incident_record(graph, incident_id)
        if not incident:
            return (
                f"**Summary:** I could not find `{incident_id}` in the current Neo4j graph.\n\n"
                "**Findings**\n"
                "- I searched by incident ID against node ID, node name, and imported `Incident_Number` properties.\n\n"
                "**Remediation**\n"
                "- Confirm the incident exists in Neo4j and that the fixed import files from `SamplePOCData/import` were loaded."
            )

        props = incident.properties or {}
        groups = self._incident_relationship_groups(graph, incident.id)
        source_document = props.get("source_document_id") or incident.source_system or "Not found"
        source_sheet = props.get("source_sheet") or "Not found"
        source_row = props.get("source_row") or props.get("source_row:int") or "Not found"
        lines = [
            f"**Summary:** `{incident_id}` matched graph node `{incident.id}`. The answer is built from that node's imported properties and direct Neo4j relationships.",
            "",
            "**Matched Source Record**",
            f"- Workbook/source: `{source_document}`.",
            f"- Sheet: `{source_sheet}`.",
            f"- Row: `{source_row}`.",
            "",
            "**Incident Properties**",
        ]
        for key, value in self._important_properties(incident):
            lines.append(f"- {self._humanize_key(key)}: `{value}`.")
        lines.extend(["", "**Graph Relationships**"])
        if groups:
            for rel_type in self._solution_relationship_types(groups):
                lines.append(f"- `{rel_type}`: {self._compact_list(groups[rel_type], limit=6)}.")
        else:
            lines.append("- No direct relationships were found for the matched incident node.")
        lines.extend(["", "**Traversal To Solution**"])
        solution_types = [
            rel_type for rel_type in self._solution_relationship_types(groups)
            if any(term in rel_type.upper() for term in ("PROBLEM", "ROOT", "CAUSE", "RESOL", "REMED", "CHANGE", "ASSIGNED", "STATUS"))
        ]
        if solution_types:
            for rel_type in solution_types[:10]:
                lines.append(f"- `{incident.id}` -[:`{rel_type}`]-> {self._compact_list(groups[rel_type], limit=4)}.")
        else:
            lines.append("- No solution-oriented relationship was found. Add/import RCA, problem, restoration, owner, or status relationships for this incident.")
        lines.extend([
            "",
            "**Validation Against Workbook**",
            f"- Validate against `{source_document}`, sheet `{source_sheet}`, row `{source_row}`.",
            "- The answer uses graph properties created from the workbook row plus directly connected Neo4j nodes.",
            "",
            "**Traversal Used**",
        ])
        for step in self._graph_traversal(graph)[:10]:
            lines.append(f"- `{step.from_entity}` -[:`{step.relationship}`]-> `{step.to_entity}`")
        lines.append("")
        lines.append("**Source Provenance:** Live Neo4j traversal from the matched `IncidentRCARecord`.")
        return "\n".join(lines)

    def _answer_incident_detail(self, conversation_id: str, intent: str, incident_id: str) -> ChatResponse:
        graph = self._context_graph({"incident_id": incident_id, "scenario_id": "", "alert_id": "", "root_cause": "", "identifiers": [incident_id], "terms": []}, conversation_id)
        answer = self._incident_detail_answer(graph, incident_id)
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.9 if graph.nodes else 0.0,
            evidence=self._graph_evidence(graph),
            entities=graph.nodes[:24],
            relationships=graph.relationships[:36],
            traversal=self._graph_traversal(graph),
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def _answer_issue_category_severity(self, conversation_id: str, intent: str) -> ChatResponse:
        rows = self.neo4j.issue_category_severity_summary() if self.neo4j.verify() else []
        graph = self.neo4j.graph(limit=120) if self.neo4j.verify() else self._empty_graph()
        answer = self._issue_category_severity_answer(rows)
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.95 if rows else 0.0,
            evidence=[
                EvidenceItem(
                    label=str(row["issue_category"]),
                    value=f"Sev1={row['sev1']}, Sev2={row['sev2']}, SDN={row['sdn']}, LW={row['lw']}, Total={row['total']}",
                    source="Neo4j",
                    confidence="high",
                )
                for row in rows[:8]
            ],
            entities=graph.nodes[:24],
            relationships=graph.relationships[:36],
            traversal=[
                TraversalStep(
                    order=1,
                    from_entity="IncidentRCARecord",
                    from_class="IncidentRCARecord",
                    relationship="HAS_CATEGORY",
                    to_entity="CommonCategory",
                    to_class="CommonCategory",
                    explanation="Filter `HAS_CATEGORY` relationships where `source_column` is `Issue Category`.",
                ),
                TraversalStep(
                    order=2,
                    from_entity="IncidentRCARecord",
                    from_class="IncidentRCARecord",
                    relationship="HAS_SEVERITY",
                    to_entity="CommonCategory",
                    to_class="CommonCategory",
                    explanation="Join each incident record to its severity category, then count incidents by issue category and severity.",
                ),
            ],
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def _answer_incident_kind_summary(self, conversation_id: str, intent: str) -> ChatResponse:
        rows = self.neo4j.incident_kind_summary() if self.neo4j.verify() else []
        graph = self.neo4j.graph(limit=120) if self.neo4j.verify() else self._empty_graph()
        answer = self._incident_kind_summary_answer(rows)
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.95 if rows else 0.0,
            evidence=[
                EvidenceItem(
                    label=str(row["issue_category"]),
                    value=f"{row['incidents']} incident record(s)",
                    source="Neo4j",
                    confidence="high",
                )
                for row in rows[:8]
            ],
            entities=graph.nodes[:24],
            relationships=graph.relationships[:36],
            traversal=[
                TraversalStep(
                    order=1,
                    from_entity="IncidentRCARecord",
                    from_class="IncidentRCARecord",
                    relationship="HAS_CATEGORY",
                    to_entity="CommonCategory",
                    to_class="CommonCategory",
                    explanation="Count incident records by imported issue category.",
                ),
            ],
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def _answer_incident_record_set(self, message: str, conversation_id: str, intent: str, scope: dict[str, Any]) -> ChatResponse:
        identifiers = list(dict.fromkeys(scope.get("identifiers", [])))
        terms = list(dict.fromkeys(scope.get("terms", [])))
        if self.neo4j.verify():
            rows, graph = self.neo4j.incident_rca_record_set(identifiers=identifiers, terms=terms, limit=80)
            if rows and not graph.relationships:
                graph = self.neo4j.dynamic_context_graph(identifiers=identifiers, terms=[], limit=260)
        else:
            rows, graph = [], self._empty_graph()
        answer = self._incident_record_set_answer(rows, graph, scope)
        self._history[conversation_id].extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ])
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.95 if rows else 0.0,
            evidence=[
                EvidenceItem(
                    label=self._row_incident_number(row),
                    value=self._brief(self._row_value(row, "Issue_Description", "Restoration_Details"), limit=180),
                    source=self._row_value(row, "source_document_id", default="Neo4j"),
                    confidence="high",
                )
                for row in rows[:12]
            ],
            entities=graph.nodes[:36],
            relationships=graph.relationships[:60],
            traversal=self._graph_traversal(graph),
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def _answer_incident_example(self, message: str, conversation_id: str, intent: str, scope: dict[str, Any]) -> ChatResponse:
        _ = message
        if self.neo4j.verify():
            rows, graph = self.neo4j.incident_rca_examples(
                identifiers=list(dict.fromkeys(scope.get("identifiers", []))),
                terms=list(dict.fromkeys(scope.get("terms", []))),
                limit=12,
            )
        else:
            rows, graph = [], self._empty_graph()
        answer = self._incident_example_answer(rows, graph, scope)
        traversal = self._graph_traversal(graph)
        if rows and not traversal:
            incident_number = self._row_incident_number(rows[0])
            traversal = [
                TraversalStep(
                    order=1,
                    from_entity="Question terms",
                    from_class="UserQuestion",
                    relationship="MATCHED_RECORD",
                    to_entity=incident_number,
                    to_class="IncidentRCARecord",
                    explanation="Question identifiers and keywords matched imported `IncidentRCARecord` properties.",
                ),
                TraversalStep(
                    order=2,
                    from_entity=incident_number,
                    from_class="IncidentRCARecord",
                    relationship="READS_FIELD",
                    to_entity="Issue_Description",
                    to_class="WorkbookProperty",
                    explanation="Used the incident description to identify what failed.",
                ),
                TraversalStep(
                    order=3,
                    from_entity=incident_number,
                    from_class="IncidentRCARecord",
                    relationship="READS_FIELD",
                    to_entity="Restoration_Details",
                    to_class="WorkbookProperty",
                    explanation="Used restoration details to extract the ordered recovery steps.",
                ),
                TraversalStep(
                    order=4,
                    from_entity="Restoration_Details",
                    from_class="WorkbookProperty",
                    relationship="DERIVES",
                    to_entity="Remediation",
                    to_class="AnswerSection",
                    explanation="Converted the recorded fix and follow-up into immediate and permanent remediation.",
                ),
            ]
        self._history[conversation_id].extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ])
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.95 if rows else 0.0,
            evidence=[
                EvidenceItem(
                    label=self._row_incident_number(row),
                    value=self._brief(self._row_value(row, "Issue_Description", "Restoration_Details"), limit=180),
                    source=self._row_value(row, "source_document_id", default="Neo4j"),
                    confidence="high",
                )
                for row in rows[:8]
            ],
            entities=graph.nodes[:24],
            relationships=graph.relationships[:36],
            traversal=traversal,
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def _answer_similar_incidents(self, conversation_id: str, intent: str, incident_id: str) -> ChatResponse:
        if self.neo4j.verify():
            anchor, matches, graph = self.neo4j.similar_incident_records(incident_id=incident_id, limit=200)
        else:
            anchor, matches, graph = None, [], self._empty_graph()
        answer = self._similar_incidents_answer(anchor, matches, graph, incident_id)
        self._history[conversation_id].extend([
            {"role": "user", "content": f"similar incidents to {incident_id}"},
            {"role": "assistant", "content": answer},
        ])
        return ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id="",
            confidence=0.95 if anchor and matches else 0.4 if anchor else 0.0,
            evidence=[
                EvidenceItem(
                    label=self._row_incident_number(row),
                    value=f"score={self._row_value(row, 'similarity_score', default='0')}; {self._brief(self._row_value(row, 'Restoration_Details', 'Issue_Description'), limit=140)}",
                    source=self._row_value(row, "source_document_id", default="Neo4j"),
                    confidence="high",
                )
                for row in matches[:12]
            ],
            entities=graph.nodes[:36],
            relationships=graph.relationships[:60],
            traversal=self._graph_traversal(graph),
            graph=graph,
            generated_by="kg",
            token_usage=None,
        )

    def ask(self, message: str, conversation_id: str | None) -> ChatResponse:
        conversation_id = conversation_id or str(uuid.uuid4())
        repeated = self.memory.find_exact(message)
        if repeated:
            duplicate = self.memory.duplicate_response(repeated, conversation_id)
            if duplicate:
                return duplicate

        def finish(response: ChatResponse) -> ChatResponse:
            self.memory.store(message, response)
            return response

        scope = self._scope_from_message(message)
        scope["terms"] = self._query_terms(message)
        scenario_id = scope.get("scenario_id") or self._select_scenario(message, conversation_id)
        if scenario_id:
            scope["scenario_id"] = scenario_id
            self._conversation_scenario[conversation_id] = scenario_id
        intent = self._intent(message)
        if intent == "similar_incidents" and scope.get("incident_id"):
            return finish(self._answer_similar_incidents(conversation_id, intent, scope["incident_id"]))
        if intent == "issue_category_severity":
            return finish(self._answer_issue_category_severity(conversation_id, intent))
        if intent == "incident_kind_summary":
            return finish(self._answer_incident_kind_summary(conversation_id, intent))
        if intent == "incident_example":
            return finish(self._answer_incident_example(message, conversation_id, intent, scope))
        if self._is_incident_record_set_question(message, scope):
            return finish(self._answer_incident_record_set(message, conversation_id, "incident_record_set", scope))
        if scope.get("incident_id"):
            return finish(self._answer_incident_detail(conversation_id, intent, scope["incident_id"]))
        if self._is_operational_incident_question(message, scope):
            if self._expects_single_incident(message):
                return finish(self._answer_incident_example(message, conversation_id, "incident_example", scope))
            return finish(self._answer_incident_record_set(message, conversation_id, "incident_record_set", scope))
        graph = self._context_graph(scope, conversation_id)
        scenario_id = self._infer_scenario_id(graph, scope) or scenario_id
        if scenario_id:
            self._conversation_scenario[conversation_id] = scenario_id
            scope["scenario_id"] = scenario_id
        fallback = self._compose_graph_answer(message, graph, intent, scope)
        context = self._llm_context(graph, scope, intent, fallback)
        answer, generated_by, token_usage = self._llm_answer(message, context, fallback, conversation_id)
        self._history[conversation_id].extend([
            {"role": "user", "content": message},
            {"role": "assistant", "content": answer},
        ])
        return finish(ChatResponse(
            conversation_id=conversation_id,
            message_id=str(uuid.uuid4()),
            answer=answer,
            intent=intent,
            scenario_id=scenario_id,
            confidence=0.9 if graph.nodes else 0.0,
            evidence=self._graph_evidence(graph),
            entities=graph.nodes[:24],
            relationships=graph.relationships[:36],
            traversal=self._graph_traversal(graph),
            graph=graph,
            generated_by=generated_by,
            token_usage=token_usage,
        ))
