from __future__ import annotations

import json
from typing import Any

from neo4j import GraphDatabase

from .config import Settings
from .models import GraphNode, GraphPayload, GraphRelationship

CORE_NODE_KEYS = {
    "canonical_id",
    "id",
    "name",
    "label",
    "ontology_class",
    "source_system",
    "scenario_id",
    "properties_json",
}

CORE_RELATIONSHIP_KEYS = {
    "ontology_property",
    "source_system",
    "scenario_id",
    "properties_json",
}


class Neo4jService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._driver = None

    @property
    def driver(self):
        if self._driver is None:
            self._driver = GraphDatabase.driver(
                self.settings.neo4j_uri,
                auth=(self.settings.neo4j_username, self.settings.neo4j_password),
                connection_timeout=3,
            )
        return self._driver

    def verify(self) -> bool:
        if not self.settings.neo4j_configured:
            return False
        try:
            self.driver.verify_connectivity()
            return True
        except Exception:
            return False

    @staticmethod
    def _properties(raw: Any) -> dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        try:
            return json.loads(raw or "{}")
        except (TypeError, json.JSONDecodeError):
            return {}

    @classmethod
    def _node_properties(cls, node: Any) -> dict[str, Any]:
        properties = cls._properties(node.get("properties_json"))
        for key, value in dict(node).items():
            if key not in CORE_NODE_KEYS and value not in (None, ""):
                properties[key] = value
        return properties

    @classmethod
    def _relationship_properties(cls, rel: Any) -> dict[str, Any]:
        properties = cls._properties(rel.get("properties_json"))
        for key, value in dict(rel).items():
            if key not in CORE_RELATIONSHIP_KEYS and value not in (None, ""):
                properties[key] = value
        return properties

    def _payload_from_records(self, raw_nodes: list[Any], raw_rels: list[Any]) -> GraphPayload:
        node_by_id = {}
        for node in raw_nodes:
            canonical_id = node.get("canonical_id")
            if not canonical_id or canonical_id in node_by_id:
                continue
            labels = list(node.labels) if hasattr(node, "labels") else []
            ontology_class = node.get("ontology_class") or (labels[0] if labels else "Entity")
            node_by_id[canonical_id] = GraphNode(
                id=canonical_id,
                name=node.get("name", canonical_id),
                ontology_class=ontology_class,
                source_system=node.get("source_system", ""),
                scenario_id=node.get("scenario_id", ""),
                properties=self._node_properties(node),
            )
        relationships = [GraphRelationship(
            id=str(rel.element_id),
            source=rel.start_node.get("canonical_id"),
            target=rel.end_node.get("canonical_id"),
            type=rel.type,
            ontology_property=rel.get("ontology_property", rel.type.lower()),
            source_system=rel.get("source_system", ""),
            scenario_id=rel.get("scenario_id", ""),
            properties=self._relationship_properties(rel),
        ) for rel in raw_rels if rel.start_node.get("canonical_id") in node_by_id and rel.end_node.get("canonical_id") in node_by_id]
        return GraphPayload(
            nodes=list(node_by_id.values()),
            relationships=relationships,
            source="neo4j",
            total_nodes=len(node_by_id),
            total_relationships=len(relationships),
        )

    def graph(
        self,
        scenario_id: str | None = None,
        root_cause: str | None = None,
        search: str | None = None,
        limit: int = 180,
    ) -> GraphPayload:
        query = """
        WITH $root_cause AS root_cause, $scenario_id AS scenario_id, $search AS search
        CALL (root_cause) {
          OPTIONAL MATCH (r:RootCauseAnalysis)-[:CLASSIFIED_AS]->(c:Entity)
          WHERE root_cause IS NOT NULL
            AND toLower(coalesce(c.name, '') + ' ' + coalesce(c.canonical_id, '')) CONTAINS toLower(root_cause)
          RETURN collect(DISTINCT r.scenario_id) AS root_cause_scenarios
        }
        MATCH (n)
        WHERE n.canonical_id IS NOT NULL
          AND (scenario_id IS NULL OR n.scenario_id = scenario_id)
          AND (
            search IS NULL
            OR toLower(
              coalesce(n.name, '') + ' ' +
              coalesce(n.canonical_id, '') + ' ' +
              coalesce(n.ontology_class, '') + ' ' +
              coalesce(n.Incident_Number, '') + ' ' +
              coalesce(n.Issue_Category, '') + ' ' +
              coalesce(n.Severity_Category, '') + ' ' +
              coalesce(n.RCA, '') + ' ' +
              coalesce(n.Restoration_Details, '')
            ) CONTAINS toLower(search)
          )
          AND (root_cause IS NULL OR n.scenario_id IN root_cause_scenarios)
        WITH n, scenario_id LIMIT $limit
        OPTIONAL MATCH (n)-[r]-(m)
        WHERE m.canonical_id IS NOT NULL
          AND (scenario_id IS NULL OR m.scenario_id = scenario_id)
        RETURN collect(DISTINCT n) + collect(DISTINCT m) AS nodes,
               collect(DISTINCT r) AS relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(
                query,
                scenario_id=scenario_id or None,
                root_cause=root_cause or None,
                search=search or None,
                limit=limit,
            ).single()
        raw_nodes = record["nodes"] if record else []
        raw_rels = record["relationships"] if record else []
        return self._payload_from_records(raw_nodes, raw_rels)

    def base_graph(self, limit: int = 40) -> GraphPayload:
        query = """
        MATCH (n:Entity)
        WITH coalesce(n.label, n.ontology_class, head(labels(n)), 'Entity') AS class_name,
             count(*) AS node_count
        ORDER BY node_count DESC, class_name
        LIMIT $limit
        WITH collect({class_name: class_name, node_count: node_count}) AS classes
        UNWIND classes AS class_info
        OPTIONAL MATCH (a:Entity)-[r]->(b:Entity)
        WHERE coalesce(a.label, a.ontology_class, head(labels(a)), 'Entity') = class_info.class_name
        WITH classes, class_info,
             coalesce(type(r), '') AS rel_type,
             coalesce(b.label, b.ontology_class, head(labels(b)), '') AS target_class,
             count(r) AS rel_count
        WHERE rel_type <> '' AND target_class <> ''
        WITH classes, collect({
          source: class_info.class_name,
          target: target_class,
          type: rel_type,
          count: rel_count
        }) AS rels
        RETURN classes, rels
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(query, limit=limit).single()
        if not record:
            return GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)

        class_names = {item["class_name"] for item in record["classes"]}
        nodes = [
            GraphNode(
                id=f"class::{item['class_name']}",
                name=f"{item['class_name']} ({item['node_count']})",
                ontology_class=item["class_name"],
                source_system="Neo4j class summary",
                properties={"node_count": item["node_count"], "is_base_class": True},
            )
            for item in record["classes"]
        ]
        relationships = []
        for index, rel in enumerate(record["rels"][: limit * 3], start=1):
            if rel["source"] not in class_names or rel["target"] not in class_names:
                continue
            relationships.append(GraphRelationship(
                id=f"base-rel-{index}",
                source=f"class::{rel['source']}",
                target=f"class::{rel['target']}",
                type=rel["type"],
                ontology_property=rel["type"].lower(),
                source_system="Neo4j class summary",
                properties={"relationship_count": rel["count"]},
            ))
        return GraphPayload(
            nodes=nodes,
            relationships=relationships,
            source="neo4j",
            total_nodes=len(nodes),
            total_relationships=len(relationships),
        )

    def expand_graph(self, node_id: str, depth: int = 1, limit: int = 80) -> GraphPayload:
        if node_id.startswith("class::"):
            class_name = node_id.removeprefix("class::")
            query = """
            MATCH (n:Entity)
            WHERE coalesce(n.label, n.ontology_class, head(labels(n)), 'Entity') = $class_name
            WITH n
            ORDER BY coalesce(n.source_row, 999999), n.canonical_id
            LIMIT $limit
            WITH collect(n) AS nodes
            UNWIND nodes AS n
            OPTIONAL MATCH (n)-[r]-(m:Entity)
            WHERE m IN nodes
            RETURN nodes, collect(DISTINCT r) AS relationships
            """
            with self.driver.session(database=self.settings.neo4j_database) as session:
                record = session.run(query, class_name=class_name, limit=limit).single()
            if not record:
                return GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)
            payload = self._payload_from_records(record["nodes"] or [], record["relationships"] or [])
            class_node = GraphNode(
                id=node_id,
                name=class_name,
                ontology_class=class_name,
                source_system="Neo4j class summary",
                properties={"is_base_class": True},
            )
            class_rels = [
                GraphRelationship(
                    id=f"{node_id}->{node.id}",
                    source=node_id,
                    target=node.id,
                    type="HAS_INSTANCE",
                    ontology_property="has_instance",
                    source_system="Neo4j class expansion",
                )
                for node in payload.nodes
            ]
            return GraphPayload(
                nodes=[class_node, *payload.nodes],
                relationships=[*class_rels, *payload.relationships],
                source="neo4j",
                total_nodes=len(payload.nodes) + 1,
                total_relationships=len(class_rels) + len(payload.relationships),
            )

        query = """
        MATCH (seed {canonical_id: $node_id})
        OPTIONAL MATCH path = (seed)-[*1..2]-(neighbor)
        WHERE neighbor.canonical_id IS NOT NULL
        WITH seed,
             [node IN collect(DISTINCT neighbor) WHERE node IS NOT NULL] AS neighbors,
             collect(DISTINCT relationships(path)) AS rel_lists
        WITH [seed] + neighbors AS found_nodes, rel_lists
        UNWIND found_nodes AS n
        WITH collect(DISTINCT n)[0..$limit] AS nodes, rel_lists
        UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
        UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
        RETURN nodes, [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """
        max_depth = max(1, min(depth, 2))
        query = query.replace("[*1..2]", f"[*1..{max_depth}]")
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(query, node_id=node_id, limit=limit).single()
        if not record:
            return GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)
        return self._payload_from_records(record["nodes"] or [], record["relationships"] or [])

    def scenarios(self, limit: int = 250) -> list[dict[str, Any]]:
        query = """
        MATCH (n:Entity)
        WHERE n.scenario_id IS NOT NULL AND n.scenario_id <> ''
        WITH n.scenario_id AS scenario_id, count(*) AS nodes
        OPTIONAL MATCH (:Entity {scenario_id: scenario_id})-[r]-(:Entity)
        WITH scenario_id, nodes, count(DISTINCT r) AS relationships
        OPTIONAL MATCH (i:Incident {scenario_id: scenario_id})
        WITH scenario_id, nodes, relationships, i
        ORDER BY CASE WHEN coalesce(i.properties_json, '') CONTAINS '"state": "6"' THEN 1 ELSE 0 END,
                 i.canonical_id
        WITH scenario_id, nodes, relationships, collect(i)[0] AS incident
        RETURN scenario_id,
               nodes,
               relationships,
               coalesce(incident.name, scenario_id) AS label
        ORDER BY scenario_id
        LIMIT $limit
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return [dict(record) for record in session.run(query, limit=limit)]

    def root_causes(self) -> list[dict[str, Any]]:
        query = """
        MATCH (:RootCauseAnalysis)-[:CLASSIFIED_AS]->(cause:Entity)
        WITH cause.canonical_id AS id, cause.name AS label, count(*) AS episodes
        RETURN id, label, episodes
        UNION
        MATCH (cause:AlarmProbableCause)
        WHERE cause.canonical_id STARTS WITH 'CAUSE:' OR cause.canonical_id STARTS WITH 'ROOT_CAUSE:'
        RETURN cause.canonical_id AS id, cause.name AS label, count(*) AS episodes
        ORDER BY episodes DESC, label
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return [dict(record) for record in session.run(query)]

    def issue_category_severity_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        query = """
        MATCH (incident:IncidentRCARecord)-[category_rel:HAS_CATEGORY]->(category:CommonCategory)
        WHERE toLower(coalesce(category_rel.source_column, '')) = 'issue category'
        MATCH (incident)-[:HAS_SEVERITY]->(severity:CommonCategory)
        WITH
          coalesce(category.name, category.canonical_id) AS issue_category,
          coalesce(severity.name, severity.canonical_id) AS severity,
          count(DISTINCT incident) AS incidents
        WITH
          issue_category,
          sum(CASE WHEN toLower(severity) IN ['sev1', 'severity 1', 'p1'] THEN incidents ELSE 0 END) AS sev1,
          sum(CASE WHEN toLower(severity) IN ['sev2', 'severity 2', 'p2'] THEN incidents ELSE 0 END) AS sev2,
          sum(CASE WHEN toLower(severity) = 'sdn' THEN incidents ELSE 0 END) AS sdn,
          sum(CASE WHEN toLower(severity) = 'lw' THEN incidents ELSE 0 END) AS lw,
          sum(incidents) AS total
        RETURN
          issue_category,
          sev1,
          sev2,
          sdn,
          lw,
          total,
          (sev1 * 1000 + sev2 * 100 + sdn * 10 + total) AS severity_score
        ORDER BY sev1 DESC, sev2 DESC, sdn DESC, total DESC, issue_category
        LIMIT $limit
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return [dict(record) for record in session.run(query, limit=limit)]

    def incident_kind_summary(self, limit: int = 20) -> list[dict[str, Any]]:
        query = """
        MATCH (incident:IncidentRCARecord)
        OPTIONAL MATCH (incident)-[issue_rel:HAS_CATEGORY]->(issue:CommonCategory)
        WHERE toLower(coalesce(issue_rel.source_column, '')) = 'issue category'
        WITH incident, coalesce(issue.name, issue.canonical_id, 'Other / uncategorized') AS issue_category
        OPTIONAL MATCH (incident)-[sub_rel:HAS_CATEGORY]->(sub:CommonCategory)
        WHERE toLower(coalesce(sub_rel.source_column, '')) IN ['sub category', 'subcategory']
        WITH
          issue_category,
          collect(DISTINCT coalesce(sub.name, sub.canonical_id)) AS sub_categories,
          count(DISTINCT incident) AS incidents
        RETURN
          issue_category,
          [item IN sub_categories WHERE item IS NOT NULL][0..8] AS sub_categories,
          incidents,
          issue_category = 'Other / uncategorized' AS uncategorized
        ORDER BY uncategorized ASC, incidents DESC, issue_category
        LIMIT $limit
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            return [dict(record) for record in session.run(query, limit=limit)]

    def incident_rca_record_set(
        self,
        identifiers: list[str],
        terms: list[str],
        limit: int = 80,
    ) -> tuple[list[dict[str, Any]], GraphPayload]:
        query = """
        WITH
          [item IN $identifiers WHERE item IS NOT NULL AND item <> ''] AS identifiers,
          [item IN $terms WHERE item IS NOT NULL AND item <> ''] AS terms
        MATCH (incident:IncidentRCARecord)
        WITH incident, identifiers, terms,
             toLower(reduce(text = '', key IN keys(incident) |
               text + ' ' + coalesce(toString(incident[key]), '')
             )) AS haystack
        WITH incident, identifiers, terms, haystack,
             [item IN identifiers WHERE haystack CONTAINS toLower(item)] AS matched_identifiers,
             [term IN terms WHERE haystack CONTAINS toLower(term)] AS matched_terms
        WHERE (size(identifiers) > 0 AND size(matched_identifiers) > 0)
           OR (size(identifiers) = 0 AND size(matched_terms) > 0)
        WITH incident,
             size(matched_identifiers) * 100 + size(matched_terms) * 10 AS score,
             matched_identifiers,
             matched_terms
        ORDER BY score DESC,
                 coalesce(incident.Incident_Start_Time, ''),
                 coalesce(incident.source_row, 999999),
                 incident.canonical_id
        LIMIT $limit
        WITH collect(incident) AS incidents
        UNWIND incidents AS incident
        OPTIONAL MATCH (incident)-[r]-(related:Entity)
        WITH incidents, collect(DISTINCT r) AS relationships
        RETURN incidents, relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(
                query,
                identifiers=identifiers,
                terms=terms,
                limit=limit,
            ).single()
        if not record:
            return [], GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)

        raw_incidents = record["incidents"] or []
        raw_rels = record["relationships"] or []
        related_nodes = []
        for rel in raw_rels:
            related_nodes.extend([rel.start_node, rel.end_node])
        payload = self._payload_from_records([*raw_incidents, *related_nodes], raw_rels)
        rows = []
        for node in raw_incidents:
            row = {
                "id": node.get("canonical_id"),
                "name": node.get("name", node.get("canonical_id")),
                "ontology_class": node.get("ontology_class", "IncidentRCARecord"),
            }
            row.update(self._node_properties(node))
            rows.append(row)
        return rows, payload

    def incident_rca_examples(
        self,
        identifiers: list[str],
        terms: list[str],
        limit: int = 12,
    ) -> tuple[list[dict[str, Any]], GraphPayload]:
        query = """
        WITH
          [item IN $identifiers WHERE item IS NOT NULL AND item <> ''] AS identifiers,
          [item IN $terms WHERE item IS NOT NULL AND item <> ''] AS terms
        MATCH (incident:IncidentRCARecord)
        WITH incident, identifiers, terms,
             toLower(reduce(text = '', key IN keys(incident) |
               text + ' ' + coalesce(toString(incident[key]), '')
             )) AS haystack,
             toLower(coalesce(incident.Impacted_Application, incident.ApplicationName, '')) AS app_text,
             toLower(coalesce(incident.Issue_Description, '') + ' ' + coalesce(incident.Restoration_Details, '') + ' ' + coalesce(incident.RCA, '')) AS evidence_text,
             toLower(coalesce(incident.Restoration_Details, '') + ' ' + coalesce(incident.RCA, '')) AS restoration_text
        WITH incident, identifiers, terms, haystack, app_text, evidence_text, restoration_text,
             [item IN identifiers WHERE haystack CONTAINS toLower(item)] AS matched_identifiers,
             [item IN identifiers WHERE app_text CONTAINS toLower(item)] AS app_identifiers,
             [term IN terms WHERE haystack CONTAINS toLower(term)] AS matched_terms,
             [term IN terms WHERE evidence_text CONTAINS toLower(term)] AS evidence_terms
        WHERE (size(identifiers) > 0 AND size(matched_identifiers) = size(identifiers))
           OR (size(identifiers) = 0 AND size(matched_terms) > 0)
        WITH incident,
             size(matched_identifiers) * 100
             + size(app_identifiers) * 80
             + size(matched_terms) * 20
             + size(evidence_terms) * 10
             + CASE WHEN toLower(coalesce(incident.Status, '')) = 'resolved' THEN 30 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'post which' THEN 20 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'resolved' THEN 20 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'cleared' THEN 20 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'processing fine' THEN 20 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'created new' THEN 15 ELSE 0 END
             + CASE WHEN restoration_text CONTAINS 'renamed' THEN 15 ELSE 0 END
             AS score,
             matched_identifiers,
             matched_terms
        ORDER BY score DESC,
                 coalesce(incident.Incident_Start_Time, '') DESC,
                 coalesce(incident.source_row, 999999),
                 incident.canonical_id
        LIMIT $limit
        WITH collect({node: incident, score: score, matched_identifiers: matched_identifiers, matched_terms: matched_terms}) AS matches
        WITH matches, [item IN matches | item.node] AS incidents
        UNWIND incidents AS incident
        OPTIONAL MATCH (incident)-[r]-(related:Entity)
        WITH matches, collect(DISTINCT r)[0..120] AS relationships
        RETURN matches, relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(
                query,
                identifiers=identifiers,
                terms=terms,
                limit=limit,
            ).single()
        if not record:
            return [], GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)

        raw_matches = record["matches"] or []
        raw_rels = record["relationships"] or []
        raw_nodes = [item["node"] for item in raw_matches]
        related_nodes = []
        for rel in raw_rels:
            related_nodes.extend([rel.start_node, rel.end_node])
        payload = self._payload_from_records([*raw_nodes, *related_nodes], raw_rels)
        rows = []
        for item in raw_matches:
            node = item["node"]
            row = {
                "id": node.get("canonical_id"),
                "name": node.get("name", node.get("canonical_id")),
                "ontology_class": node.get("ontology_class", "IncidentRCARecord"),
                "match_score": item["score"],
                "matched_identifiers": item["matched_identifiers"],
                "matched_terms": item["matched_terms"],
            }
            row.update(self._node_properties(node))
            rows.append(row)
        return rows, payload

    def similar_incident_records(
        self,
        incident_id: str,
        limit: int = 60,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]], GraphPayload]:
        query = """
        MATCH (anchor:IncidentRCARecord)
        WHERE toUpper(coalesce(anchor.Incident_Number, anchor.name, '')) = toUpper($incident_id)
        WITH anchor
        MATCH (candidate:IncidentRCARecord)
        WHERE candidate <> anchor
          AND toLower(coalesce(candidate.Status, '')) = 'resolved'
          AND coalesce(candidate.Issue_Category, '') = coalesce(anchor.Issue_Category, '')
          AND coalesce(candidate.Severity_Category, '') = coalesce(anchor.Severity_Category, '')
        WITH anchor, candidate,
             trim(coalesce(anchor.Issue_Sub_Category, '')) AS anchor_sub_category,
             trim(coalesce(candidate.Issue_Sub_Category, '')) AS candidate_sub_category,
             trim(coalesce(anchor.Impacted_Application, anchor.ApplicationName, '')) AS anchor_application,
             trim(coalesce(candidate.Impacted_Application, candidate.ApplicationName, '')) AS candidate_application,
             trim(coalesce(anchor.Impacted_LOB, anchor.ImpactingLOB, anchor.Impacting_LOB, '')) AS anchor_lob,
             trim(coalesce(candidate.Impacted_LOB, candidate.ImpactingLOB, candidate.Impacting_LOB, '')) AS candidate_lob,
             trim(coalesce(anchor.Issue_Type, '')) AS anchor_issue_type,
             trim(coalesce(candidate.Issue_Type, '')) AS candidate_issue_type,
             toLower(coalesce(anchor.Issue_Description, '') + ' ' + coalesce(anchor.Restoration_Details, '')) AS anchor_text,
             toLower(coalesce(candidate.Issue_Description, '') + ' ' + coalesce(candidate.Restoration_Details, '')) AS candidate_text
        WITH anchor, candidate,
             100
             + CASE WHEN anchor_sub_category <> '' AND candidate_sub_category = anchor_sub_category THEN 40 ELSE 0 END
             + CASE WHEN anchor_application <> '' AND toLower(candidate_application) CONTAINS toLower(anchor_application) THEN 30 ELSE 0 END
             + CASE WHEN anchor_lob <> '' AND candidate_lob = anchor_lob THEN 20 ELSE 0 END
             + CASE WHEN anchor_issue_type <> '' AND candidate_issue_type = anchor_issue_type THEN 15 ELSE 0 END
             + CASE WHEN anchor_text CONTAINS 'sr' AND candidate_text CONTAINS 'sr' THEN 10 ELSE 0 END
             + CASE WHEN anchor_text CONTAINS 'service portal' AND candidate_text CONTAINS 'service portal' THEN 10 ELSE 0 END
             + CASE WHEN anchor_text CONTAINS 'advisor' AND candidate_text CONTAINS 'advisor' THEN 10 ELSE 0 END
             + CASE WHEN anchor_text CONTAINS 'buffer' AND candidate_text CONTAINS 'buffer' THEN 8 ELSE 0 END
             AS score,
             [reason IN [
               CASE WHEN anchor_sub_category <> '' AND candidate_sub_category = anchor_sub_category THEN 'same sub-category' ELSE null END,
               CASE WHEN anchor_application <> '' AND toLower(candidate_application) CONTAINS toLower(anchor_application) THEN 'same impacted application' ELSE null END,
               CASE WHEN anchor_lob <> '' AND candidate_lob = anchor_lob THEN 'same impacted LOB' ELSE null END,
               CASE WHEN anchor_issue_type <> '' AND candidate_issue_type = anchor_issue_type THEN 'same issue type' ELSE null END,
               CASE WHEN anchor_text CONTAINS 'sr' AND candidate_text CONTAINS 'sr' THEN 'SR wording match' ELSE null END,
               CASE WHEN anchor_text CONTAINS 'service portal' AND candidate_text CONTAINS 'service portal' THEN 'Service Portal wording match' ELSE null END,
               CASE WHEN anchor_text CONTAINS 'advisor' AND candidate_text CONTAINS 'advisor' THEN 'advisor wording match' ELSE null END,
               CASE WHEN anchor_text CONTAINS 'buffer' AND candidate_text CONTAINS 'buffer' THEN 'buffering wording match' ELSE null END
             ] WHERE reason IS NOT NULL] AS reasons
        ORDER BY score DESC,
                 coalesce(candidate.Incident_Start_Time, ''),
                 coalesce(candidate.source_row, 999999),
                 candidate.canonical_id
        WITH anchor, collect({node: candidate, score: score, reasons: reasons})[0..$limit] AS matches
        WITH anchor, matches, [item IN matches | item.node] AS matched_nodes
        WITH anchor, matches, [anchor] + matched_nodes AS incidents
        UNWIND incidents AS incident
        OPTIONAL MATCH (incident)-[r]-(related:Entity)
        WITH anchor, matches, incidents, collect(DISTINCT r)[0..240] AS relationships
        RETURN anchor, matches, relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(query, incident_id=incident_id, limit=limit).single()
            count_record = session.run(
                """
                MATCH (anchor:IncidentRCARecord)
                WHERE toUpper(coalesce(anchor.Incident_Number, anchor.name, '')) = toUpper($incident_id)
                WITH anchor,
                     trim(coalesce(anchor.Issue_Sub_Category, '')) AS anchor_sub_category,
                     trim(coalesce(anchor.Impacted_Application, anchor.ApplicationName, '')) AS anchor_application,
                     trim(coalesce(anchor.Impacted_LOB, anchor.ImpactingLOB, anchor.Impacting_LOB, '')) AS anchor_lob,
                     trim(coalesce(anchor.Issue_Type, '')) AS anchor_issue_type,
                     toLower(coalesce(anchor.Issue_Description, '') + ' ' + coalesce(anchor.Restoration_Details, '')) AS anchor_text
                MATCH (candidate:IncidentRCARecord)
                WHERE candidate <> anchor
                  AND toLower(coalesce(candidate.Status, '')) = 'resolved'
                  AND coalesce(candidate.Issue_Category, '') = coalesce(anchor.Issue_Category, '')
                  AND coalesce(candidate.Severity_Category, '') = coalesce(anchor.Severity_Category, '')
                WITH anchor, anchor_sub_category, anchor_application, anchor_lob, anchor_issue_type, anchor_text, candidate,
                     trim(coalesce(candidate.Issue_Sub_Category, '')) AS candidate_sub_category,
                     trim(coalesce(candidate.Impacted_Application, candidate.ApplicationName, '')) AS candidate_application,
                     trim(coalesce(candidate.Impacted_LOB, candidate.ImpactingLOB, candidate.Impacting_LOB, '')) AS candidate_lob,
                     trim(coalesce(candidate.Issue_Type, '')) AS candidate_issue_type,
                     toLower(coalesce(candidate.Issue_Description, '') + ' ' + coalesce(candidate.Restoration_Details, '')) AS candidate_text
                RETURN
                  count(candidate) AS same_category_severity,
                  sum(CASE WHEN anchor_sub_category <> '' AND candidate_sub_category = anchor_sub_category THEN 1 ELSE 0 END) AS same_sub_category,
                  sum(CASE WHEN anchor_sub_category <> '' AND candidate_sub_category = anchor_sub_category
                             AND anchor_application <> '' AND toLower(candidate_application) CONTAINS toLower(anchor_application)
                           THEN 1 ELSE 0 END) AS same_application,
                  sum(CASE WHEN anchor_lob <> '' AND candidate_lob = anchor_lob THEN 1 ELSE 0 END) AS same_lob,
                  sum(CASE WHEN anchor_issue_type <> '' AND candidate_issue_type = anchor_issue_type THEN 1 ELSE 0 END) AS same_issue_type,
                  sum(CASE WHEN anchor_text CONTAINS 'service portal' AND candidate_text CONTAINS 'service portal'
                            AND anchor_text CONTAINS 'sr' AND candidate_text CONTAINS 'sr'
                           THEN 1 ELSE 0 END) AS closest_text
                """,
                incident_id=incident_id,
            ).single()
        if not record:
            return None, [], GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)

        raw_anchor = record["anchor"]
        raw_matches = record["matches"] or []
        raw_rels = record["relationships"] or []
        raw_nodes = [raw_anchor, *[item["node"] for item in raw_matches]]
        related_nodes = []
        for rel in raw_rels:
            related_nodes.extend([rel.start_node, rel.end_node])
        payload = self._payload_from_records([*raw_nodes, *related_nodes], raw_rels)

        anchor = {
            "id": raw_anchor.get("canonical_id"),
            "name": raw_anchor.get("name", raw_anchor.get("canonical_id")),
            "ontology_class": raw_anchor.get("ontology_class", "IncidentRCARecord"),
        }
        anchor.update(self._node_properties(raw_anchor))
        if count_record:
            anchor["similarity_counts"] = dict(count_record)
        matches = []
        for item in raw_matches:
            node = item["node"]
            row = {
                "id": node.get("canonical_id"),
                "name": node.get("name", node.get("canonical_id")),
                "ontology_class": node.get("ontology_class", "IncidentRCARecord"),
                "similarity_score": item["score"],
                "similarity_reasons": item["reasons"],
            }
            row.update(self._node_properties(node))
            matches.append(row)
        return anchor, matches, payload

    def dynamic_context_graph(
        self,
        identifiers: list[str],
        terms: list[str],
        limit: int = 260,
    ) -> GraphPayload:
        query = """
        WITH [item IN $identifiers WHERE item IS NOT NULL AND item <> ''] AS identifiers,
             [item IN $terms WHERE item IS NOT NULL AND item <> ''] AS terms
        MATCH (candidate)
        WHERE candidate.canonical_id IS NOT NULL
        WITH candidate, identifiers, terms,
             toLower(reduce(text = '', key IN keys(candidate) |
               text + ' ' + coalesce(toString(candidate[key]), '')
             )) AS haystack
        WITH candidate, identifiers, terms, haystack,
             reduce(score = 0, item IN identifiers |
               score + CASE WHEN haystack CONTAINS toLower(item) THEN 100 ELSE 0 END
             ) +
             reduce(score = 0, item IN terms |
               score + CASE WHEN haystack CONTAINS toLower(item) THEN 1 ELSE 0 END
             ) AS score
        WHERE score > 0
        WITH candidate, score
        ORDER BY score DESC, coalesce(candidate.source_row, 999999), candidate.canonical_id
        LIMIT 20
        WITH collect(candidate) AS seeds
        UNWIND seeds AS seed
        OPTIONAL MATCH path = (seed)-[*0..2]-(neighbor)
        WHERE neighbor.canonical_id IS NOT NULL
        WITH collect(DISTINCT seed) AS seed_nodes,
             [node IN collect(DISTINCT neighbor) WHERE node IS NOT NULL] AS neighbor_nodes,
             collect(DISTINCT relationships(path)) AS rel_lists
        WITH seed_nodes + neighbor_nodes AS found_nodes, rel_lists
        UNWIND found_nodes AS n
        WITH collect(DISTINCT n)[0..$limit] AS nodes, rel_lists
        UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
        UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
        RETURN nodes, [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(
                query,
                identifiers=identifiers,
                terms=terms,
                limit=limit,
            ).single()
        if not record:
            return GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)
        return self._payload_from_records(record["nodes"] or [], record["relationships"] or [])

    def context_graph(self, scenario_id: str | None, entity_ids: list[str], limit: int = 260) -> GraphPayload:
        query = """
        MATCH (seed)
        WHERE seed.canonical_id IS NOT NULL
          AND (($scenario_id IS NOT NULL AND seed.scenario_id = $scenario_id)
           OR seed.canonical_id IN $entity_ids
           OR toUpper(coalesce(seed.name, '')) IN $entity_ids
           OR toUpper(coalesce(seed.Incident_Number, '')) IN $entity_ids
           OR toUpper(coalesce(seed.Event_Alert_Number, '')) IN $entity_ids)
        WITH collect(DISTINCT seed)[0..25] AS seeds
        UNWIND seeds AS seed
        OPTIONAL MATCH path = (seed)-[*0..3]-(m)
        WHERE m.canonical_id IS NOT NULL
          AND ($scenario_id IS NULL
           OR m.scenario_id = $scenario_id
           OR m.canonical_id IN $entity_ids
           OR toUpper(coalesce(m.name, '')) IN $entity_ids
           OR toUpper(coalesce(m.Incident_Number, '')) IN $entity_ids
           OR toUpper(coalesce(m.Event_Alert_Number, '')) IN $entity_ids)
        WITH collect(DISTINCT seed) AS seed_nodes,
             [node IN collect(DISTINCT m) WHERE node IS NOT NULL] AS neighbor_nodes,
             collect(DISTINCT relationships(path)) AS rel_lists
        WITH seed_nodes + neighbor_nodes AS found_nodes, rel_lists
        UNWIND found_nodes AS n
        WITH collect(DISTINCT n)[0..$limit] AS nodes, rel_lists
        UNWIND CASE WHEN rel_lists = [] THEN [null] ELSE rel_lists END AS rel_list
        UNWIND CASE WHEN rel_list IS NULL OR rel_list = [] THEN [null] ELSE rel_list END AS r
        RETURN nodes, [rel IN collect(DISTINCT r) WHERE rel IS NOT NULL] AS relationships
        """
        with self.driver.session(database=self.settings.neo4j_database) as session:
            record = session.run(
                query,
                scenario_id=scenario_id or None,
                entity_ids=entity_ids,
                limit=limit,
            ).single()
        if not record:
            return GraphPayload(nodes=[], relationships=[], source="neo4j", total_nodes=0, total_relationships=0)
        raw_nodes = record["nodes"] or []
        raw_rels = record["relationships"] or []
        return self._payload_from_records(raw_nodes, raw_rels)
