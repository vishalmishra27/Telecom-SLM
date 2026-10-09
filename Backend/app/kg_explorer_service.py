"""
KG Explorer Service — Interactive Multi-Hop Graph Exploration

Spec Section 10 (Shape 6): Four operations of increasing traversal depth.

Endpoints:
- search: 0 hops — text/category match
- neighborhood: 1 hop — one node + all directly connected
- subgraph: 1 hop per entity, merged — "View in KG Explorer" from RCA
- category-subgraph: 1-4 hops — deep, user-adjustable category filter
"""

from __future__ import annotations

import re
from typing import Any

from neo4j import GraphDatabase

from . import schema_registry


def _props(obj) -> dict:
    """Safely convert a Neo4j Node/Relationship to a plain dict."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    try:
        return dict(obj)
    except Exception:
        return {}


class KGExplorerService:
    """Interactive graph exploration — search, drill down, category filter."""

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

    # -------------------------------------------------------------------
    # search: 0 hops — text/category match
    # -------------------------------------------------------------------
    def search(
        self,
        query: str = "",
        categories: list[str] | None = None,
        customer_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Search nodes by text (id/description match) and/or category.

        If categories are given, only match nodes with those labels.
        If a text query is given, filter by id CONTAINS or description CONTAINS.
        """
        # Resolve categories to labels
        labels = []
        if categories:
            for cat in categories:
                group_labels = schema_registry.CATEGORY_GROUPS.get(cat, [])
                labels.extend(group_labels)
        if not labels:
            labels = schema_registry.all_labels()

        # Build dynamic UNION query across labels
        parts = []
        params: dict[str, Any] = {"query_text": query.upper() if query else "", "limit": limit}
        if customer_id:
            params["customer_id"] = customer_id

        for label in set(labels):
            where_clauses = []
            if query:
                where_clauses.append("(toUpper(n.id) CONTAINS $query_text OR toUpper(coalesce(n.description, n.impact_description, n.failure_type, n.dispute_reason, '')) CONTAINS $query_text)")
            if customer_id:
                where_clauses.append("n.customer_id = $customer_id")
            where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
            parts.append(
                f"MATCH (n:{label}) {where} "
                f"RETURN n AS node, '{label}' AS label LIMIT $limit"
            )

        if not parts:
            return {"nodes": [], "edges": [], "total": 0}

        full_query = "\nUNION ALL\n".join(parts)
        rows = self._run(full_query, **params)

        nodes = []
        seen = set()
        for r in rows:
            props = _props(r["node"])
            nid = props.get("id", "")
            if nid and nid not in seen:
                seen.add(nid)
                nodes.append({
                    "key": f"{r['label']}:{nid}",
                    "label": r["label"],
                    "id": nid,
                    "display": props.get("failure_type") or props.get("dispute_reason") or props.get("disruption_type") or nid,
                    "props": props,
                })

        return {"nodes": nodes, "edges": [], "total": len(nodes)}

    # -------------------------------------------------------------------
    # neighborhood: 1 hop — one node + directly connected
    # -------------------------------------------------------------------
    def neighborhood(self, node_id: str, limit: int = 50) -> dict[str, Any]:
        """Return one node plus every directly-connected node and relationship."""
        rows = self._run("""
            MATCH (n {id: $node_id})-[r]-(m)
            RETURN n, r, m, labels(n) AS n_labels, labels(m) AS m_labels, type(r) AS rel_type
            LIMIT $limit
        """, node_id=node_id, limit=limit)

        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        for row in rows:
            n_props = _props(row["n"])
            m_props = _props(row["m"])
            n_id = n_props.get("id", "")
            m_id = m_props.get("id", "")
            n_label = row["n_labels"][0] if row.get("n_labels") else "Entity"
            m_label = row["m_labels"][0] if row.get("m_labels") else "Entity"

            if n_id and n_id not in nodes:
                nodes[n_id] = {
                    "key": f"{n_label}:{n_id}", "label": n_label,
                    "id": n_id, "display": n_id, "props": n_props,
                }
            if m_id and m_id not in nodes:
                nodes[m_id] = {
                    "key": f"{m_label}:{m_id}", "label": m_label,
                    "id": m_id, "display": m_id, "props": m_props,
                }
            if n_id and m_id:
                edges.append({
                    "from": f"{n_label}:{n_id}",
                    "to": f"{m_label}:{m_id}",
                    "rel_type": row.get("rel_type", ""),
                })

        return {"nodes": list(nodes.values()), "edges": edges, "total": len(nodes)}

    # -------------------------------------------------------------------
    # subgraph: 1 hop per entity, merged
    # -------------------------------------------------------------------
    def subgraph(self, entity_ids: list[str], limit: int = 100) -> dict[str, Any]:
        """Merged 1-hop neighborhoods for a list of entity IDs.

        Powers "View in KG Explorer" from an RCA result.
        """
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        for eid in entity_ids:
            result = self.neighborhood(eid, limit=30)
            for node in result.get("nodes", []):
                nid = node.get("id", "")
                if nid and nid not in nodes:
                    nodes[nid] = node
            edges.extend(result.get("edges", []))

        # Deduplicate edges
        edge_keys = set()
        deduped_edges = []
        for e in edges:
            key = f"{e['from']}-{e['rel_type']}-{e['to']}"
            if key not in edge_keys:
                edge_keys.add(key)
                deduped_edges.append(e)

        return {
            "nodes": list(nodes.values())[:limit],
            "edges": deduped_edges,
            "total": len(nodes),
            "truncated": len(nodes) > limit,
        }

    # -------------------------------------------------------------------
    # category-subgraph: 1-4 hops, user-adjustable (Section 10.1)
    # -------------------------------------------------------------------
    def category_subgraph(
        self,
        categories: list[str],
        customer_id: str | None = None,
        depth: int = 1,
        limit_per_root: int = 15,
        max_roots: int = 40,
    ) -> dict[str, Any]:
        """Deep, user-adjustable multi-hop category filter.

        Phase 1: Root discovery — find nodes matching the selected categories.
        Phase 2: Hop-depth expansion — BFS around each root, user-controlled depth.
        Hard total-node ceiling: 300.
        """
        MAX_TOTAL_NODES = 300
        depth = max(1, min(4, depth))  # Clamp 1-4

        # Resolve categories to labels
        labels = []
        for cat in categories:
            group_labels = schema_registry.CATEGORY_GROUPS.get(cat, [])
            labels.extend(group_labels)
        if not labels:
            return {"nodes": [], "edges": [], "root_count": 0, "truncated": False}
        labels = list(set(labels))

        # Phase 1: Root discovery
        roots: list[dict] = []
        if customer_id:
            # Customer-scoped: variable-length path from Customer to matching nodes
            # Max 6 hops (Section 10.1)
            for label in labels:
                rows = self._run(f"""
                    MATCH p = (c:Customer {{id: $customer_id}})-[*1..6]-(n:{label})
                    RETURN DISTINCT n AS node, '{label}' AS label
                    LIMIT $max_roots
                """, customer_id=customer_id, max_roots=max_roots // len(labels))
                for r in rows:
                    props = _props(r["node"])
                    nid = props.get("id", "")
                    if nid:
                        roots.append({"id": nid, "label": r["label"], "props": props})
        else:
            # Un-scoped: spread root budget evenly across labels
            per_label = max(1, max_roots // len(labels))
            for label in labels:
                rows = self._run(f"""
                    MATCH (n:{label})
                    RETURN n AS node, '{label}' AS label
                    LIMIT $per_label
                """, per_label=per_label)
                for r in rows:
                    props = _props(r["node"])
                    nid = props.get("id", "")
                    if nid:
                        roots.append({"id": nid, "label": r["label"], "props": props})

        if not roots:
            return {"nodes": [], "edges": [], "root_count": 0, "truncated": False}

        # Phase 2: Hop-depth expansion (BFS using neighborhood)
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        # Add root nodes first
        for root in roots:
            nodes[root["id"]] = {
                "key": f"{root['label']}:{root['id']}",
                "label": root["label"],
                "id": root["id"],
                "display": root["id"],
                "props": root["props"],
            }

        # BFS expansion: each hop uses the neighborhood operation
        frontier = [r["id"] for r in roots]
        visited = set(frontier)

        for hop in range(depth):
            if len(nodes) >= MAX_TOTAL_NODES:
                break
            next_frontier = []
            for nid in frontier:
                if len(nodes) >= MAX_TOTAL_NODES:
                    break
                result = self.neighborhood(nid, limit=limit_per_root)
                for node in result.get("nodes", []):
                    node_id = node.get("id", "")
                    if node_id and node_id not in nodes:
                        nodes[node_id] = node
                        if node_id not in visited:
                            visited.add(node_id)
                            next_frontier.append(node_id)
                edges.extend(result.get("edges", []))
            frontier = next_frontier

        # Deduplicate edges
        edge_keys = set()
        deduped = []
        for e in edges:
            key = f"{e['from']}-{e['rel_type']}-{e['to']}"
            if key not in edge_keys:
                edge_keys.add(key)
                deduped.append(e)

        truncated = len(nodes) >= MAX_TOTAL_NODES

        return {
            "nodes": list(nodes.values())[:MAX_TOTAL_NODES],
            "edges": deduped,
            "root_count": len(roots),
            "truncated": truncated,
        }

    # -------------------------------------------------------------------
    # filter-categories: list available category groups
    # -------------------------------------------------------------------
    @staticmethod
    def filter_categories() -> list[dict[str, Any]]:
        """Return the curated category list for the UI."""
        result = []
        for cat_key, labels in schema_registry.CATEGORY_GROUPS.items():
            display = cat_key.replace("_", " ").title()
            result.append({
                "id": cat_key,
                "display_name": display,
                "labels": labels,
                "count": len(labels),
            })
        return result
