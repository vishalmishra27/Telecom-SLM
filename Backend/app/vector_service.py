"""
Vector Embedding Service — Chunking + all-MiniLM-L6-v2 Vectorisation

Implements the Embed step (Step 5) from the Ingestion Pipeline Spec Section 4:
- Converts CSV row properties into natural-language text chunks
- Embeds chunks using all-MiniLM-L6-v2 (384-dimensional sentence embeddings)
- Stores embeddings in ChromaDB for semantic similarity search
- Supports hybrid retrieval: graph traversal + vector similarity

Design:
- Each Neo4j node becomes one or more text chunks (depending on text length)
- Chunk metadata preserves: canonical_id, domain, node_label, customer_id, account_id
- Retrieval returns top-k semantically similar chunks for a natural language query
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any

import chromadb
from sentence_transformers import SentenceTransformer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Chunking Configuration
# ---------------------------------------------------------------------------
# Max characters per chunk. all-MiniLM-L6-v2 has a 256 word-piece token limit;
# ~600 chars keeps us comfortably under that while preserving context.
MAX_CHUNK_CHARS = 600
CHUNK_OVERLAP_CHARS = 100

# Model name — downloaded and cached on first use
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


# ---------------------------------------------------------------------------
# Text templates per domain — converts structured row → readable text chunk
# ---------------------------------------------------------------------------
DOMAIN_TEXT_TEMPLATES: dict[str, str] = {
    "network": (
        "Network Alarm {canonical_id}: {alarm_type} event with severity {severity} "
        "at site {affected_site} affecting service {affected_service}. "
        "Raised at {raised_at}, duration {duration_minutes} minutes. "
        "Description: {description}"
    ),
    "billing": (
        "Invoice {canonical_id}: billing period {billing_period}, amount {amount} {currency}, "
        "status {status}, due date {due_date}. Source system: {source_system}. "
        "Charge {charge_canonical_id}: category {charge_category}, "
        "amount {charge_amount}. {charge_description}"
    ),
    "complaints": (
        "Complaint {canonical_id}: type {complaint_type}, status {status}, "
        "disputed charge {disputed_charge_id}. Opened at {opened_at}. "
        "Resolution: {resolution_text}"
    ),
    "incident": (
        "Service Problem {canonical_id}: {service_type} disruption with severity {severity}. "
        "Opened {opened_at}, resolved {resolved_at}. "
        "Reason: {description}. Impact on charges: {impact_description}"
    ),
    "pm_counters": (
        "PM Counter {canonical_id} at site {site_id}: {counter_name} = {value} {unit} "
        "(threshold {threshold_value}), severity {severity}. "
        "Observed at {observed_at}. Impact: {description}"
    ),
    "api": (
        "KPI Observation {canonical_id}: {kpi_name} = {kpi_value} (threshold {threshold_value}) {unit}. "
        "Severity {severity}, measurement window {measurement_window}. "
        "Observed at {observed_at}. Source: {source_system}. Impact: {description}"
    ),
    "logs": (
        "Log Event {canonical_id}: [{level}] from {log_source}/{service_name} on host {host}. "
        "Time: {event_time}. Trace: {trace_id}. "
        "Message: {message}. Impact: {description}"
    ),
    "sla_credits": (
        "SLA Adjustment {canonical_id}: type {adjustment_type}, amount {amount} {currency}. "
        "Reason: {reason}. Related incident: {related_incident_id}. "
        "Issued at {issued_at}, status {status}"
    ),
    "payment_failures": (
        "Payment {canonical_id}: amount {amount}, date {payment_date}, status {status}. "
        "Card ending {card_last_four}. Issuer response: {issuer_response}. "
        "Invoice: {invoice_id}. "
        "Failure {dunning_canonical_id}: type {failure_type}, reason {failure_reason}, "
        "failed at {failed_at}"
    ),
}


def _chunk_text(text: str, max_chars: int = MAX_CHUNK_CHARS, overlap: int = CHUNK_OVERLAP_CHARS) -> list[str]:
    """Split text into overlapping chunks."""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        end = start + max_chars
        chunk = text[start:end]
        # Try to break at sentence boundary
        if end < len(text):
            last_period = chunk.rfind(". ")
            if last_period > max_chars // 2:
                end = start + last_period + 2
                chunk = text[start:end]
        chunks.append(chunk.strip())
        start = end - overlap
    return chunks


def _row_to_text(domain: str, props: dict[str, Any], customer_id: str, account_id: str) -> str:
    """Convert a row's properties to a natural-language text string."""
    template = DOMAIN_TEXT_TEMPLATES.get(domain, "")
    if not template:
        # Fallback: concatenate all properties
        parts = [f"Customer {customer_id}, Account {account_id}."]
        for k, v in props.items():
            if v:
                parts.append(f"{k}: {v}")
        return " ".join(parts)

    # Fill template with available values, use empty string for missing
    safe_props = {k: (v if v else "") for k, v in props.items()}
    safe_props["customer_id"] = customer_id
    safe_props["account_id"] = account_id
    try:
        text = template.format_map(SafeDict(safe_props))
    except Exception:
        # Fallback if template has keys not in props
        parts = [f"Customer {customer_id}, Account {account_id}."]
        for k, v in props.items():
            if v:
                parts.append(f"{k}: {v}")
        text = " ".join(parts)

    return f"Customer {customer_id}, Account {account_id}. {text}"


class SafeDict(dict):
    """Dict that returns empty string for missing keys in str.format_map()."""
    def __missing__(self, key: str) -> str:
        return ""


def _stable_id(canonical_id: str, chunk_index: int) -> str:
    """Generate a deterministic ID for a chunk."""
    raw = f"{canonical_id}::chunk::{chunk_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class VectorService:
    """Manages embedding generation, storage, and retrieval using
    all-MiniLM-L6-v2 + ChromaDB."""

    def __init__(self, persist_dir: str | Path, collection_name: str = "telecom_rca_vectors"):
        self._persist_dir = Path(persist_dir)
        self._persist_dir.mkdir(parents=True, exist_ok=True)
        self._collection_name = collection_name
        self._model: SentenceTransformer | None = None
        self._client: chromadb.ClientAPI | None = None
        self._collection: chromadb.Collection | None = None

    @property
    def model(self) -> SentenceTransformer:
        if self._model is None:
            logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
            self._model = SentenceTransformer(EMBEDDING_MODEL)
        return self._model

    @property
    def collection(self) -> chromadb.Collection:
        if self._collection is None:
            self._client = chromadb.PersistentClient(path=str(self._persist_dir))
            self._collection = self._client.get_or_create_collection(
                name=self._collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    # ------------------------------------------------------------------
    # Embedding: called during ingestion (Step 5 of the pipeline)
    # ------------------------------------------------------------------

    def embed_row(
        self,
        domain: str,
        canonical_id: str,
        customer_id: str,
        account_id: str,
        node_label: str,
        props: dict[str, Any],
    ) -> int:
        """Chunk + embed + store a single ingested row. Returns number of chunks stored."""
        text = _row_to_text(domain, props, customer_id, account_id)
        chunks = _chunk_text(text)
        if not chunks:
            return 0

        ids = [_stable_id(canonical_id, i) for i in range(len(chunks))]
        embeddings = self.model.encode(chunks, show_progress_bar=False).tolist()
        metadatas = [
            {
                "canonical_id": canonical_id,
                "customer_id": customer_id,
                "account_id": account_id,
                "domain": domain,
                "node_label": node_label,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            for i in range(len(chunks))
        ]

        self.collection.upsert(
            ids=ids,
            documents=chunks,
            embeddings=embeddings,
            metadatas=metadatas,
        )
        return len(chunks)

    def embed_batch(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """Embed multiple rows at once (more efficient for batch ingestion).

        Each row dict must contain: domain, canonical_id, customer_id,
        account_id, node_label, props.
        """
        all_texts = []
        all_ids = []
        all_metadatas = []
        row_chunk_counts = []

        for row in rows:
            text = _row_to_text(
                row["domain"], row["props"], row["customer_id"], row["account_id"]
            )
            chunks = _chunk_text(text)
            row_chunk_counts.append(len(chunks))

            for i, chunk in enumerate(chunks):
                all_ids.append(_stable_id(row["canonical_id"], i))
                all_texts.append(chunk)
                all_metadatas.append({
                    "canonical_id": row["canonical_id"],
                    "customer_id": row["customer_id"],
                    "account_id": row["account_id"],
                    "domain": row["domain"],
                    "node_label": row["node_label"],
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                })

        if not all_texts:
            return 0

        # Batch encode all chunks at once — much faster than one at a time
        all_embeddings = self.model.encode(all_texts, show_progress_bar=False, batch_size=64).tolist()

        # ChromaDB upsert in batches of 5000 (API limit)
        batch_size = 5000
        for start in range(0, len(all_ids), batch_size):
            end = start + batch_size
            self.collection.upsert(
                ids=all_ids[start:end],
                documents=all_texts[start:end],
                embeddings=all_embeddings[start:end],
                metadatas=all_metadatas[start:end],
            )

        return len(all_texts)

    # ------------------------------------------------------------------
    # Retrieval: semantic similarity search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        customer_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic similarity search over embedded chunks.

        Returns top_k results with: text, score, metadata (canonical_id, domain, etc.)
        """
        query_embedding = self.model.encode([query], show_progress_bar=False).tolist()

        where_filter = None
        conditions = []
        if domain:
            conditions.append({"domain": domain})
        if customer_id:
            conditions.append({"customer_id": customer_id})

        if len(conditions) == 1:
            where_filter = conditions[0]
        elif len(conditions) > 1:
            where_filter = {"$and": conditions}

        results = self.collection.query(
            query_embeddings=query_embedding,
            n_results=top_k,
            where=where_filter,
            include=["documents", "metadatas", "distances"],
        )

        hits = []
        ids = results.get("ids", [[]])[0]
        docs = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        for i in range(len(ids)):
            hits.append({
                "id": ids[i],
                "text": docs[i],
                "score": round(1.0 - distances[i], 4),  # cosine distance → similarity
                "metadata": metadatas[i],
            })

        return hits

    # ------------------------------------------------------------------
    # Stats & maintenance
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return collection statistics."""
        count = self.collection.count()
        return {
            "collection": self._collection_name,
            "total_chunks": count,
            "embedding_model": EMBEDDING_MODEL,
            "embedding_dim": EMBEDDING_DIM,
            "persist_dir": str(self._persist_dir),
        }

    def delete_by_customer(self, customer_id: str) -> int:
        """Delete all chunks for a customer. Returns number deleted."""
        results = self.collection.get(
            where={"customer_id": customer_id},
            include=[],
        )
        ids = results.get("ids", [])
        if ids:
            self.collection.delete(ids=ids)
        return len(ids)

    def clear(self) -> int:
        """Delete all chunks from the collection. Returns count deleted."""
        count = self.collection.count()
        if count > 0:
            # Get all IDs and delete
            results = self.collection.get(include=[])
            ids = results.get("ids", [])
            if ids:
                self.collection.delete(ids=ids)
        return count
