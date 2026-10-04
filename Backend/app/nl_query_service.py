"""
Natural Language Query Service — Multi-Model Grounded Narration Layer

Implements the Ingestion Pipeline Spec:
- Section 8: LLM Models Used (Claude, OpenAI, deterministic template)
- Section 8.1: Fallback chain — TRY 1 → TRY 2 → deterministic template
- Section 9.3: LLM is narration-only, not extraction
- Section 10.3: Retrieval queries feed context to LLM
- Section 12.2: Response with citations, confidence, model_used, response_time_ms
- Section 13: Error handling — retry once, then fallback

The LLM turns retrieved graph evidence into readable sentences.
It cannot add facts that weren't retrieved — this is the grounding guarantee.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

import anthropic
from openai import OpenAI

from .rca_retrieval_service import RCARetrievalService


# Intent classification — maps natural language to the right retrieval query
INTENT_PATTERNS = [
    # Network RCA
    (r"(network|alarm|outage|cell|site|failure|cell.?outage)", "network_rca"),
    (r"(kpi|threshold|breach|degraded|performance|counter)", "kpi_breach"),
    # Payment / Dunning
    (r"(payment|dunning|fail|expired|card|declined|past.?due)", "dunning_chain"),
    # Service Disruption
    (r"(disruption|sla|credit|maintenance|outage|service.?problem)", "sla_credit"),
    # System Error
    (r"(log|system|error|mediation|queue|infrastructure|trace)", "system_error"),
    # Complaints
    (r"(complaint|dispute|resolution|resolved|open|escalat)", "complaint"),
    # Financial impact
    (r"(charge|bill|invoice|financial|impact|cost|amount|total)", "financial_impact"),
    # Full 360
    (r"(360|everything|full|all|complete|overview|summary)", "full_chain"),
    # Unresolved
    (r"(unresolved|pending|open|stuck|outstanding)", "unresolved"),
]


SYSTEM_PROMPT = """You are a Telecom RCA (Root Cause Analysis) assistant for EzInsights.
You answer questions about telecom network incidents, billing issues, and service disruptions
using ONLY the evidence retrieved from the Knowledge Graph.

RULES:
1. ONLY use facts from the provided graph evidence. Never invent or assume facts.
2. Cite specific node IDs (e.g., NF-GEN-E4E3D5, INV-GEN-13D0FF) when referencing evidence.
3. Describe the traversal path you followed through the graph.
4. If the evidence is insufficient, say so explicitly — do not guess.
5. Use a professional, concise tone appropriate for a telecom NOC operator.
6. Structure your answer with: Summary, Evidence Chain, and Recommendation (if applicable).
"""


class NLQueryService:
    """Natural language query → graph retrieval → grounded narration with model picker."""

    def __init__(
        self,
        retrieval: RCARetrievalService,
        # OpenAI config
        openai_api_key: str = "",
        openai_model: str = "gpt-4o-mini",
        # Claude config
        claude_api_key: str = "",
        claude_model: str = "claude-sonnet-4-6",
        # LM Studio / local model config
        lmstudio_url: str = "",
        lmstudio_model: str = "",
        lmstudio_enabled: bool = False,
        # Groq config (free cloud API, OpenAI-compatible)
        groq_api_key: str = "",
        groq_model: str = "llama-3.1-70b-versatile",
        groq_enabled: bool = False,
        # Shared
        temperature: float = 0.2,
    ):
        self.retrieval = retrieval
        self._temperature = temperature

        # OpenAI client
        self._openai_model = openai_model
        self._openai = None
        if openai_api_key and openai_api_key != "replace_me":
            self._openai = OpenAI(api_key=openai_api_key)

        # Claude client
        self._claude_model = claude_model
        self._claude = None
        if claude_api_key and claude_api_key != "replace_me":
            self._claude = anthropic.Anthropic(api_key=claude_api_key)

        # LM Studio / local model client (OpenAI-compatible API)
        self._lmstudio_model = lmstudio_model
        self._lmstudio_url = lmstudio_url
        self._lmstudio = None
        if lmstudio_enabled and lmstudio_url:
            self._lmstudio = OpenAI(
                base_url=lmstudio_url,
                api_key="lm-studio",  # LM Studio doesn't need a real key
            )
            # Auto-detect loaded model name from LM Studio
            if lmstudio_model == "auto":
                self._lmstudio_model = self._detect_lmstudio_model()

        # Groq client (OpenAI-compatible API, free tier with Llama 70B)
        self._groq_model = groq_model
        self._groq = None
        if groq_enabled and groq_api_key and groq_api_key != "replace_me":
            self._groq = OpenAI(
                base_url="https://api.groq.com/openai/v1",
                api_key=groq_api_key,
            )

    def _detect_lmstudio_model(self) -> str:
        """Query LM Studio /v1/models to find the currently loaded model."""
        try:
            models = self._lmstudio.models.list()
            if models.data:
                model_id = models.data[0].id
                print(f"[LM Studio] Auto-detected model: {model_id}")
                return model_id
        except Exception as e:
            print(f"[LM Studio] Could not auto-detect model ({e}), using fallback")
        return "local-model"

    def available_models(self) -> list[dict[str, str]]:
        """Return list of models that are currently configured and available."""
        models = []
        if self._claude:
            models.append({
                "id": "claude",
                "display_name": "Claude (Anthropic)",
                "model_id": self._claude_model,
                "available": True,
            })
        if self._openai:
            models.append({
                "id": "openai",
                "display_name": "OpenAI GPT-4o",
                "model_id": self._openai_model,
                "available": True,
            })
        if self._lmstudio:
            models.append({
                "id": "lmstudio",
                "display_name": f"Local ({self._lmstudio_model})",
                "model_id": self._lmstudio_model,
                "available": True,
            })
        if self._groq:
            models.append({
                "id": "groq",
                "display_name": f"Groq ({self._groq_model})",
                "model_id": self._groq_model,
                "available": True,
            })
        models.append({
            "id": "deterministic",
            "display_name": "KG Traversal",
            "model_id": "graph-direct",
            "available": True,
        })
        return models

    def query(
        self,
        question: str,
        customer_id: str | None = None,
        model: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Process a natural language question and return a grounded answer.

        Args:
            question: The natural language question
            customer_id: Optional customer ID (auto-detected from question if not provided)
            model: Model to use — 'claude', 'openai', or None (uses fallback chain)
            conversation_history: Previous messages [{"role": "user"/"assistant", "content": "..."}]
        """
        start_time = time.time()

        intent = self._detect_intent(question)

        if not customer_id:
            customer_id = self._extract_customer_id(question)

        if not customer_id:
            # Cross-customer query — run aggregate queries instead
            evidence = self._retrieve_cross_customer_evidence(intent)
            if not evidence or all(
                (isinstance(v, list) and len(v) == 0) or (isinstance(v, dict) and not v)
                for v in evidence.values()
            ):
                return {
                    "answer": "No matching data found across customers. Try specifying a customer ID (e.g., CUST-4367) for detailed results.",
                    "intent": intent,
                    "customer_id": None,
                    "evidence": evidence or {},
                    "model_used": "none",
                    "grounded": False,
                    "confidence": "low",
                    "citations": [],
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                }
            # Filter by specific entity IDs if mentioned
            entity_ids = self._extract_entity_ids(question)
            if entity_ids:
                evidence = self._filter_evidence_by_ids(evidence, entity_ids)

            answer, model_used, token_usage, citations, confidence = self._generate_with_fallback(
                question, "All Customers", intent, evidence, preferred_model=model, conversation_history=conversation_history
            )

            # Build an overview graph for all-customer queries
            try:
                graph_payload = self.retrieval.overview_graph(limit=30)
            except Exception:
                graph_payload = {"nodes": [], "relationships": []}

            traversal = self._build_traversal(intent, "All Customers", evidence)

            return {
                "answer": answer,
                "intent": intent,
                "customer_id": None,
                "evidence": evidence,
                "graph": graph_payload,
                "traversal": traversal,
                "model_used": model_used,
                "grounded": True,
                "confidence": confidence,
                "citations": citations,
                "response_time_ms": int((time.time() - start_time) * 1000),
                "token_usage": token_usage,
            }

        evidence = self._retrieve_evidence(intent, customer_id, question)

        # If the user mentioned specific entity IDs, narrow evidence to just those records
        entity_ids = self._extract_entity_ids(question)
        if entity_ids:
            evidence = self._filter_evidence_by_ids(evidence, entity_ids)

        # Generate grounded answer using fallback chain (Spec Section 8.1)
        answer, model_used, token_usage, citations, confidence = self._generate_with_fallback(
            question, customer_id, intent, evidence, preferred_model=model, conversation_history=conversation_history
        )

        # Intent-scoped graph traversal — only return KG nodes relevant to the query
        graph_payload = self.retrieval.intent_graph_payload(customer_id, intent)

        # Build traversal metadata — Cypher query + path steps
        traversal = self._build_traversal(intent, customer_id, evidence)

        response_time_ms = int((time.time() - start_time) * 1000)

        return {
            "answer": answer,
            "intent": intent,
            "customer_id": customer_id,
            "evidence": evidence,
            "graph": graph_payload,
            "traversal": traversal,
            "model_used": model_used,
            "grounded": True,
            "confidence": confidence,
            "citations": citations,
            "response_time_ms": response_time_ms,
            "token_usage": token_usage,
        }

    def _generate_with_fallback(
        self,
        question: str,
        customer_id: str,
        intent: str,
        evidence: dict[str, Any],
        preferred_model: str | None = None,
        conversation_history: list[dict] | None = None,
    ) -> tuple[str, str, dict[str, int], list[dict], str]:
        """Fallback chain per Spec Section 8.1:
        TRY 1 → User's selected model
        TRY 2 → Configured fallback model
        LAST RESORT → Deterministic template
        """
        if preferred_model == "deterministic":
            answer = self._fallback_answer(customer_id, intent, evidence)
            citations = self._extract_citations_from_evidence(evidence)
            return answer, "deterministic", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, citations, "medium"

        attempts = []
        if preferred_model == "lmstudio" and self._lmstudio:
            attempts.append(("lmstudio", self._generate_lmstudio))
        elif preferred_model == "groq" and self._groq:
            attempts.append(("groq", self._generate_groq))
        elif preferred_model == "claude" and self._claude:
            attempts.append(("claude", self._generate_claude))
            if self._openai:
                attempts.append(("openai", self._generate_openai))
        elif preferred_model == "openai" and self._openai:
            attempts.append(("openai", self._generate_openai))
            if self._claude:
                attempts.append(("claude", self._generate_claude))
        else:
            # No preference — try claude first (default per spec), then openai, then groq, then local
            if self._claude:
                attempts.append(("claude", self._generate_claude))
            if self._openai:
                attempts.append(("openai", self._generate_openai))
            if self._groq:
                attempts.append(("groq", self._generate_groq))
            if self._lmstudio:
                attempts.append(("lmstudio", self._generate_lmstudio))

        # Try each model with one retry per Spec Section 13
        for model_name, generate_fn in attempts:
            for retry in range(2):
                try:
                    answer, token_usage = generate_fn(question, customer_id, intent, evidence, conversation_history=conversation_history)
                    citations = self._parse_citations(answer, evidence)
                    confidence = "high" if citations else "medium"
                    return answer, model_name, token_usage, citations, confidence
                except Exception:
                    if retry == 0:
                        continue
                    break

        # LAST RESORT — Deterministic template
        answer = self._fallback_answer(customer_id, intent, evidence)
        citations = self._extract_citations_from_evidence(evidence)
        return answer, "deterministic", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, citations, "medium"

    def _build_user_prompt(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any]
    ) -> str:
        evidence_text = json.dumps(evidence, indent=2, default=str)
        if len(evidence_text) > 12000:
            evidence_text = evidence_text[:12000] + "\n... (truncated)"
        return f"""Customer: {customer_id}
Question: {question}
Detected Intent: {intent}

Retrieved Graph Evidence:
{evidence_text}

Based on the evidence above, answer the customer's question. Follow the rules in your system prompt.
Include the specific traversal path through the knowledge graph nodes."""

    def _generate_claude(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any], **kwargs
    ) -> tuple[str, dict[str, int]]:
        """Generate answer using Claude (Anthropic)."""
        user_prompt = self._build_user_prompt(question, customer_id, intent, evidence)
        response = self._claude.messages.create(
            model=self._claude_model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_prompt}],
        )
        answer = response.content[0].text
        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
        }
        return answer, usage

    def _generate_openai(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any], **kwargs
    ) -> tuple[str, dict[str, int]]:
        """Generate answer using OpenAI GPT-4o."""
        user_prompt = self._build_user_prompt(question, customer_id, intent, evidence)
        response = self._openai.chat.completions.create(
            model=self._openai_model,
            temperature=self._temperature,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=1500,
        )
        answer = response.choices[0].message.content
        usage = {
            "input_tokens": response.usage.prompt_tokens,
            "output_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        }
        return answer, usage

    @staticmethod
    def _flatten_evidence_to_text(evidence: dict[str, Any], max_items: int = 10) -> str:
        """Convert JSON evidence into plain-text bullet points for local models.

        Small models (8B) struggle with raw JSON. This flattens each evidence
        category into readable lines like:
            ALARMS (3 records):
            - NF-GEN-E4E3D5: severity=critical, type=cell_outage, site=SITE-UK-LON-014, ...
        """
        sections = []
        for key, value in evidence.items():
            if isinstance(value, list) and value:
                items = value[:max_items]
                header = f"{key.upper().replace('_', ' ')} ({len(value)} records):"
                lines = []
                for item in items:
                    if isinstance(item, dict):
                        # Find the best ID field
                        eid = None
                        for id_field in ("id", "alarm_id", "kpi_id", "invoice_id", "charge_id",
                                         "payment_id", "complaint_id", "log_id", "incident_id",
                                         "disruption_id", "dunning_id", "error_id", "canonical_id"):
                            if item.get(id_field):
                                eid = str(item[id_field])
                                break
                        eid = eid or "?"
                        # Build key=value pairs, skip nulls and the id field itself
                        parts = []
                        for k, v in item.items():
                            if v is not None and k not in ("id", "description") and not k.endswith("_id"):
                                parts.append(f"{k}={v}")
                        detail = ", ".join(parts[:8])
                        line = f"  - {eid}: {detail}"
                        desc = item.get("description")
                        if desc:
                            line += f" | {desc[:100]}"
                        lines.append(line)
                sections.append(header + "\n" + "\n".join(lines))
                if len(value) > max_items:
                    sections[-1] += f"\n  ... and {len(value) - max_items} more"
            elif isinstance(value, dict) and value:
                header = f"{key.upper().replace('_', ' ')}:"
                lines = []
                for k, v in value.items():
                    if v is not None:
                        lines.append(f"  - {k}: {v}")
                sections.append(header + "\n" + "\n".join(lines))
        return "\n\n".join(sections)

    def _generate_lmstudio(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any],
        conversation_history: list[dict] | None = None, **kwargs
    ) -> tuple[str, dict[str, int]]:
        """Generate answer using LM Studio local model.

        Multi-turn strategy:
        - First message: full KG traversal data + question (sent as context)
        - Follow-ups: conversation history maintained so model has context
        """
        # Use the deterministic template text (graph traversal result) — not raw JSON
        template_text = self._fallback_answer(customer_id, intent, evidence)
        template_text = template_text.replace(
            "\n---\n_Generated directly from Knowledge Graph traversal._", ""
        ).replace(
            "\n---\n_Generated by deterministic template — LLM narration unavailable._", ""
        ).strip()

        if conversation_history:
            # Multi-turn: rebuild the full conversation with data in the first message
            messages = []
            for i, msg in enumerate(conversation_history):
                if i == 0 and msg["role"] == "user":
                    # First user message — prepend KG data context
                    messages.append({
                        "role": "user",
                        "content": f"Analyze the following telecom data:\n\n{template_text}\n\n{msg['content']}\n\nWhat is the root cause of these issues?\nWhat are your recommendations to resolve them?\nCite the specific IDs from the data in your answer."
                    })
                else:
                    messages.append(msg)
            # Add current question as new user message
            messages.append({"role": "user", "content": question})
        else:
            # First message — send full data + question
            prompt = (
                f"Analyze the following telecom data:\n\n"
                f"{template_text}\n\n"
                f"{question}\n\n"
                f"What is the root cause of these issues?\n"
                f"What are your recommendations to resolve them?\n"
                f"Cite the specific IDs from the data in your answer."
            )
            messages = [{"role": "user", "content": prompt}]

        response = self._lmstudio.chat.completions.create(
            model=self._lmstudio_model,
            temperature=0.23,
            messages=messages,
            max_tokens=4096,
        )
        answer = response.choices[0].message.content
        usage_obj = response.usage
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
        }
        return answer, usage

    def _generate_groq(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any],
        conversation_history: list[dict] | None = None, **kwargs
    ) -> tuple[str, dict[str, int]]:
        """Generate answer using Groq cloud API (Llama 70B, OpenAI-compatible)."""
        template_text = self._fallback_answer(customer_id, intent, evidence)
        template_text = template_text.replace(
            "\n---\n_Generated directly from Knowledge Graph traversal._", ""
        ).replace(
            "\n---\n_Generated by deterministic template — LLM narration unavailable._", ""
        ).strip()

        if conversation_history:
            messages = [{"role": "system", "content": "You are a telecom network analyst. Use the data provided in the conversation to answer questions. Cite specific IDs."}]
            for i, msg in enumerate(conversation_history):
                if i == 0 and msg["role"] == "user":
                    messages.append({"role": "user", "content": f"Data:\n{template_text}\n\n{msg['content']}"})
                else:
                    messages.append(msg)
            messages.append({"role": "user", "content": question})
        else:
            prompt = (
                f"You are a telecom network analyst. Analyze the following Knowledge Graph evidence "
                f"and provide a root cause analysis.\n\n"
                f"Customer: {customer_id}\n\n"
                f"Data:\n{template_text}\n\n"
                f"{question}\n\n"
                f"Provide:\n"
                f"1. Root cause of these issues\n"
                f"2. Recommendations to resolve them\n"
                f"Cite the specific IDs from the data in your answer."
            )
            messages = [{"role": "user", "content": prompt}]

        response = self._groq.chat.completions.create(
            model=self._groq_model,
            temperature=0.3,
            messages=messages,
            max_tokens=4096,
        )
        answer = response.choices[0].message.content
        usage_obj = response.usage
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
        }
        return answer, usage

    def _parse_citations(self, answer: str, evidence: dict[str, Any]) -> list[dict[str, str]]:
        """Extract citations from the LLM answer by matching node IDs found in evidence."""
        citations = []
        seen = set()
        evidence_ids = set()
        for value in evidence.values():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        for k in ("id", "invoice_id", "charge_id", "alarm_id"):
                            if item.get(k):
                                evidence_ids.add(str(item[k]))
            elif isinstance(value, dict):
                if value.get("customer_id"):
                    evidence_ids.add(str(value["customer_id"]))

        for eid in evidence_ids:
            if eid in answer and eid not in seen:
                seen.add(eid)
                for sentence in answer.split("."):
                    if eid in sentence:
                        citations.append({"evidence_id": eid, "claim": sentence.strip()[:200]})
                        break
        return citations

    def _extract_citations_from_evidence(self, evidence: dict[str, Any]) -> list[dict[str, str]]:
        """Build citations from raw evidence for deterministic template answers."""
        citations = []
        for key, value in evidence.items():
            if isinstance(value, list):
                for item in value[:5]:
                    if isinstance(item, dict):
                        eid = item.get("id") or item.get("invoice_id") or item.get("charge_id")
                        if eid:
                            citations.append({"evidence_id": str(eid), "claim": f"{key}: {json.dumps(item, default=str)[:150]}"})
            elif isinstance(value, dict) and value.get("customer_id"):
                citations.append({"evidence_id": value["customer_id"], "claim": f"Financial impact summary for {value['customer_id']}"})
        return citations

    def _detect_intent(self, question: str) -> str:
        normalized = question.lower()
        for pattern, intent in INTENT_PATTERNS:
            if re.search(pattern, normalized):
                return intent
        return "full_chain"

    @staticmethod
    def _extract_customer_id(question: str) -> str | None:
        match = re.search(r"CUST-\d+", question, re.IGNORECASE)
        return match.group(0).upper() if match else None

    @staticmethod
    def _extract_entity_ids(question: str) -> set[str]:
        """Extract specific entity IDs mentioned in the question (e.g., NF-GEN-E4E3D5, KPI-GEN-4A2D77)."""
        # Match common ID patterns: PREFIX-GEN-HEX, INV-GEN-HEX, etc.
        patterns = [
            r"[A-Z]{2,4}-GEN-[A-F0-9]{4,8}",       # NF-GEN-E4E3D5, KPI-GEN-4A2D77, etc.
            r"SD-GEN-[A-F0-9]{4,8}",                 # SD-GEN-71F6C7
            r"SITE-[A-Z]{2,3}-[A-Z]{3}-\d+",         # SITE-UK-LON-014
            r"PM-GEN-[A-F0-9]{4,8}",                 # PM-GEN-68F614
            r"LOG-GEN-[A-F0-9]{4,8}",                # LOG-GEN-...
            r"ADJ-GEN-[A-F0-9]{4,8}",                # ADJ-GEN-...
            r"CHG-GEN-[A-F0-9]{4,8}",                # CHG-GEN-...
            r"INV-GEN-[A-F0-9]{4,8}",                # INV-GEN-...
            r"PAY-GEN-[A-F0-9]{4,8}",                # PAY-GEN-...
            r"DUN-GEN-[A-F0-9]{4,8}",                # DUN-GEN-...
            r"CMP-GEN-[A-F0-9]{4,8}",                # CMP-GEN-...
        ]
        ids = set()
        for pat in patterns:
            for m in re.finditer(pat, question, re.IGNORECASE):
                ids.add(m.group(0).upper())
        return ids

    @staticmethod
    def _filter_evidence_by_ids(evidence: dict[str, Any], entity_ids: set[str]) -> dict[str, Any]:
        """Narrow evidence to only records that reference one of the specified entity IDs.

        When the user asks about specific entities (e.g. a single alarm), only return
        evidence records that mention those IDs.  Categories with zero matches are
        returned empty — we do NOT fall back to the full unfiltered list, because the
        user explicitly asked about particular items.
        """
        if not entity_ids:
            return evidence
        filtered = {}
        for key, value in evidence.items():
            if isinstance(value, list):
                matching = []
                for item in value:
                    if isinstance(item, dict):
                        item_vals = {str(v).upper() for v in item.values() if v is not None}
                        if entity_ids & item_vals:
                            matching.append(item)
                filtered[key] = matching
            else:
                filtered[key] = value
        return filtered

    def _retrieve_cross_customer_evidence(self, intent: str) -> dict[str, Any]:
        """Retrieve evidence across ALL customers (no customer_id filter)."""
        if intent == "network_rca":
            return {"alarms": self.retrieval.all_alarms()}
        elif intent == "kpi_breach":
            return {"kpi_breaches": self.retrieval.all_kpi_breaches()}
        elif intent == "dunning_chain":
            return {"payments": self.retrieval.all_dunning()}
        elif intent == "sla_credit":
            return {"service_disruptions": self.retrieval.all_service_disruptions()}
        elif intent == "system_error":
            return {"system_errors": self.retrieval.all_system_errors()}
        elif intent == "complaint":
            return {"complaints": self.retrieval.all_complaints()}
        elif intent == "financial_impact":
            return {"billing": self.retrieval.all_billing()}
        elif intent == "unresolved":
            return {"unresolved": self.retrieval.unresolved_issues()}
        elif intent == "full_chain":
            return {
                "alarms": self.retrieval.all_alarms(limit=20),
                "kpi_breaches": self.retrieval.all_kpi_breaches(limit=20),
                "complaints": self.retrieval.all_complaints(limit=20),
                "billing": self.retrieval.all_billing(limit=20),
                "payments": self.retrieval.all_dunning(limit=20),
                "service_disruptions": self.retrieval.all_service_disruptions(limit=20),
                "system_errors": self.retrieval.all_system_errors(limit=20),
            }
        else:
            return {"customers": self.retrieval.all_customers_summary()}

    def _retrieve_evidence(self, intent: str, customer_id: str, question: str) -> dict[str, Any]:
        if intent == "full_chain":
            return self.retrieval.rca_full_chain(customer_id)
        elif intent == "network_rca":
            chain = self.retrieval.rca_full_chain(customer_id)
            return {
                "alarms": chain.get("alarms", []),
                "pm_counters": chain.get("pm_counters", []),
                "kpi_observations": chain.get("kpi_observations", []),
            }
        elif intent == "kpi_breach":
            return {"kpi_breaches": self.retrieval.kpi_breach_to_incident(customer_id)}
        elif intent == "dunning_chain":
            return {"dunning_chain": self.retrieval.dunning_chain(customer_id)}
        elif intent == "sla_credit":
            return {"sla_credits": self.retrieval.sla_credit_chain(customer_id)}
        elif intent == "system_error":
            return {"system_errors": self.retrieval.system_error_chain(customer_id)}
        elif intent == "complaint":
            chain = self.retrieval.rca_full_chain(customer_id)
            return {"complaints": chain.get("complaints", [])}
        elif intent == "financial_impact":
            chain = self.retrieval.rca_full_chain(customer_id)
            result: dict[str, Any] = {"impact_summary": self.retrieval.customer_impact(customer_id)}
            # Include detail records so the model can cite specifics
            for detail_key in ("invoices", "charges", "payments", "complaints", "adjustments"):
                if chain.get(detail_key):
                    result[detail_key] = chain[detail_key]
            return result
        elif intent == "unresolved":
            return {"unresolved": self.retrieval.unresolved_issues()}
        else:
            return self.retrieval.rca_full_chain(customer_id)

    # -----------------------------------------------------------------
    # Traversal metadata — Cypher queries and step-by-step path per intent
    # -----------------------------------------------------------------
    _INTENT_CYPHER: dict[str, dict] = {
        "full_chain": {
            "description": "Full RCA Chain — Customer → Account → all connected evidence nodes",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)\n"
                "OPTIONAL MATCH (ba)-[:HAS_PM_COUNTER]->(pm:PMCounter)\n"
                "OPTIONAL MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)\n"
                "OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)\n"
                "OPTIONAL MATCH (ba)-[:HAS_LOG]->(log:LogEvent)\n"
                "OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)\n"
                "OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)\n"
                "OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)\n"
                "OPTIONAL MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)\n"
                "OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_ALARM", "to_class": "Alarm", "explanation": "Retrieve network alarms"},
                {"order": 3, "from_class": "BillingAccount", "rel": "HAS_PM_COUNTER", "to_class": "PMCounter", "explanation": "Retrieve performance counters"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_KPI_OBSERVATION", "to_class": "KPIObservation", "explanation": "Retrieve KPI threshold breaches"},
                {"order": 5, "from_class": "BillingAccount", "rel": "HAS_DISRUPTION", "to_class": "ServiceProblem", "explanation": "Retrieve service disruptions"},
                {"order": 6, "from_class": "BillingAccount", "rel": "HAS_LOG", "to_class": "LogEvent", "explanation": "Retrieve system log events"},
                {"order": 7, "from_class": "BillingAccount", "rel": "HAS_INVOICE → CONTAINS", "to_class": "Invoice → ChargingRecord", "explanation": "Retrieve billing chain"},
                {"order": 8, "from_class": "BillingAccount", "rel": "HAS_COMPLAINT", "to_class": "Complaint", "explanation": "Retrieve complaints/disputes"},
                {"order": 9, "from_class": "BillingAccount", "rel": "HAS_ADJUSTMENT", "to_class": "Adjustment", "explanation": "Retrieve SLA credits"},
                {"order": 10, "from_class": "BillingAccount", "rel": "HAS_PAYMENT → HAS_DUNNING", "to_class": "Payment → Dunning", "explanation": "Retrieve payments and dunning failures"},
            ],
        },
        "network_rca": {
            "description": "Network RCA — Customer → Account → Alarms, PM Counters, KPI Observations",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "OPTIONAL MATCH (ba)-[:HAS_ALARM]->(alarm:Alarm)\n"
                "OPTIONAL MATCH (ba)-[:HAS_PM_COUNTER]->(pm:PMCounter)\n"
                "OPTIONAL MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_ALARM", "to_class": "Alarm", "explanation": "Retrieve cell outage and network alarms"},
                {"order": 3, "from_class": "BillingAccount", "rel": "HAS_PM_COUNTER", "to_class": "PMCounter", "explanation": "Retrieve degraded performance counters"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_KPI_OBSERVATION", "to_class": "KPIObservation", "explanation": "Retrieve KPI threshold breaches"},
            ],
        },
        "kpi_breach": {
            "description": "KPI Breach → Incident — Customer → Account → KPI breaches → correlated Alarms",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "MATCH (ba)-[:HAS_KPI_OBSERVATION]->(kpi:KPIObservation)\n"
                "WHERE toFloat(kpi.kpi_value) > toFloat(kpi.threshold_value)\n"
                "OPTIONAL MATCH (kpi)-[:CORRELATED_WITH]->(alarm:Alarm)\n"
                "OPTIONAL MATCH (ba)-[:HAS_DISRUPTION]->(sp:ServiceProblem)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_KPI_OBSERVATION", "to_class": "KPIObservation", "explanation": "Find KPI values exceeding thresholds"},
                {"order": 3, "from_class": "KPIObservation", "rel": "CORRELATED_WITH", "to_class": "Alarm", "explanation": "Link breaches to correlated alarms"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_DISRUPTION", "to_class": "ServiceProblem", "explanation": "Find associated service disruptions"},
            ],
        },
        "dunning_chain": {
            "description": "Dunning Chain — Customer → Account → Payment → Dunning → Invoice → Complaint",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)\n"
                "OPTIONAL MATCH (pay)-[:HAS_DUNNING]->(dun:Dunning)\n"
                "OPTIONAL MATCH (pay)-[:PAYMENT_FOR]->(inv:Invoice)\n"
                "OPTIONAL MATCH (inv)-[:CONTAINS]->(cr:ChargingRecord)\n"
                "OPTIONAL MATCH (comp:Complaint)-[:DISPUTES]->(cr)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_PAYMENT", "to_class": "Payment", "explanation": "Retrieve payment records"},
                {"order": 3, "from_class": "Payment", "rel": "HAS_DUNNING", "to_class": "Dunning", "explanation": "Find dunning / payment failures"},
                {"order": 4, "from_class": "Payment", "rel": "PAYMENT_FOR", "to_class": "Invoice", "explanation": "Link payment to overdue invoice"},
                {"order": 5, "from_class": "Invoice", "rel": "CONTAINS", "to_class": "ChargingRecord", "explanation": "Get invoice line items"},
                {"order": 6, "from_class": "Complaint", "rel": "DISPUTES", "to_class": "ChargingRecord", "explanation": "Find complaint disputing a charge"},
            ],
        },
        "sla_credit": {
            "description": "SLA Credit — Customer → Account → Adjustment → ServiceProblem",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)\n"
                "OPTIONAL MATCH (adj)-[:CREDITS_FOR]->(sp:ServiceProblem)\n"
                "OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_ADJUSTMENT", "to_class": "Adjustment", "explanation": "Retrieve SLA credit adjustments"},
                {"order": 3, "from_class": "Adjustment", "rel": "CREDITS_FOR", "to_class": "ServiceProblem", "explanation": "Link credit to the incident it compensates"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_INVOICE → CONTAINS", "to_class": "Invoice → ChargingRecord", "explanation": "Get charges during outage period"},
            ],
        },
        "system_error": {
            "description": "System Error — Customer → Account → LogEvent → ServiceProblem → Billing",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "MATCH (ba)-[:HAS_LOG]->(log:LogEvent)\n"
                "OPTIONAL MATCH (log)-[:EVIDENCES]->(sp:ServiceProblem)\n"
                "OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)\n"
                "OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)-[:CREDITS_FOR]->(sp)\n"
                "OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_LOG", "to_class": "LogEvent", "explanation": "Retrieve system log events / errors"},
                {"order": 3, "from_class": "LogEvent", "rel": "EVIDENCES", "to_class": "ServiceProblem", "explanation": "Link log errors to service disruptions"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_INVOICE → CONTAINS", "to_class": "Invoice → ChargingRecord", "explanation": "Get billing impact"},
                {"order": 5, "from_class": "BillingAccount", "rel": "HAS_ADJUSTMENT → CREDITS_FOR", "to_class": "Adjustment → ServiceProblem", "explanation": "Find credits issued for the incident"},
            ],
        },
        "complaint": {
            "description": "Complaint — Customer → Account → Complaint → disputed ChargingRecord",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)\n"
                "OPTIONAL MATCH (comp)-[:DISPUTES]->(cr:ChargingRecord)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_COMPLAINT", "to_class": "Complaint", "explanation": "Retrieve complaints/disputes"},
                {"order": 3, "from_class": "Complaint", "rel": "DISPUTES", "to_class": "ChargingRecord", "explanation": "Link complaint to the disputed charge"},
            ],
        },
        "financial_impact": {
            "description": "Financial Impact — Customer → Account → Invoices, Charges, Credits",
            "cypher": (
                "MATCH (c:Customer {canonical_id: $cid})-[:HAS_ACCOUNT]->(ba:BillingAccount)\n"
                "OPTIONAL MATCH (ba)-[:HAS_INVOICE]->(inv:Invoice)-[:CONTAINS]->(cr:ChargingRecord)\n"
                "OPTIONAL MATCH (ba)-[:HAS_ADJUSTMENT]->(adj:Adjustment)\n"
                "OPTIONAL MATCH (ba)-[:HAS_COMPLAINT]->(comp:Complaint)\n"
                "OPTIONAL MATCH (ba)-[:HAS_PAYMENT]->(pay:Payment)"
            ),
            "steps": [
                {"order": 1, "from_class": "Customer", "rel": "HAS_ACCOUNT", "to_class": "BillingAccount", "explanation": "Anchor on the customer's billing account"},
                {"order": 2, "from_class": "BillingAccount", "rel": "HAS_INVOICE → CONTAINS", "to_class": "Invoice → ChargingRecord", "explanation": "Retrieve all invoices and charges"},
                {"order": 3, "from_class": "BillingAccount", "rel": "HAS_ADJUSTMENT", "to_class": "Adjustment", "explanation": "Retrieve credit adjustments"},
                {"order": 4, "from_class": "BillingAccount", "rel": "HAS_COMPLAINT", "to_class": "Complaint", "explanation": "Retrieve related complaints"},
                {"order": 5, "from_class": "BillingAccount", "rel": "HAS_PAYMENT", "to_class": "Payment", "explanation": "Retrieve payment records"},
            ],
        },
    }

    def _build_traversal(self, intent: str, customer_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        """Build traversal metadata showing how the KG was traversed."""
        meta = self._INTENT_CYPHER.get(intent, self._INTENT_CYPHER["full_chain"])

        # Count evidence nodes retrieved at each step
        steps_with_counts = []
        for step in meta["steps"]:
            s = dict(step)
            cls_lower = step["to_class"].split(" → ")[0].lower()
            count_map = {
                "billingaccount": 1,
                "alarm": len(evidence.get("alarms", [])),
                "pmcounter": len(evidence.get("pm_counters", [])),
                "kpiobservation": len(evidence.get("kpi_observations", evidence.get("kpi_breaches", []))),
                "serviceproblem": len(evidence.get("service_problems", [])),
                "logevent": len(evidence.get("log_events", evidence.get("system_errors", []))),
                "invoice": len(evidence.get("billing", [])),
                "chargingrecord": len(evidence.get("billing", [])),
                "complaint": len(evidence.get("complaints", [])),
                "adjustment": len(evidence.get("adjustments", evidence.get("sla_credits", []))),
                "payment": len(evidence.get("payments", evidence.get("dunning_chain", []))),
                "dunning": len(evidence.get("payments", evidence.get("dunning_chain", []))),
            }
            s["nodes_found"] = count_map.get(cls_lower, 0)
            steps_with_counts.append(s)

        return {
            "description": meta["description"],
            "cypher": meta["cypher"].replace("$cid", f"'{customer_id}'"),
            "steps": steps_with_counts,
            "total_steps": len(steps_with_counts),
            "intent": intent,
        }

    @staticmethod
    def _fallback_answer(customer_id: str, intent: str, evidence: dict[str, Any]) -> str:
        """Deterministic template narrator (Spec Section 8.1 — last resort).
        Fills evidence into fixed sentence structures — no LLM call at all."""
        is_all = customer_id == "All Customers"
        max_items = 20 if is_all else 5
        lines = [f"## RCA Report for {customer_id}\n"]
        lines.append(f"**Analysis Type:** {intent.replace('_', ' ').title()}\n")

        for key, value in evidence.items():
            if isinstance(value, list) and value:
                lines.append(f"### {key.replace('_', ' ').title()} ({len(value)} records)\n")
                # Group by customer when showing all-customer results
                if is_all:
                    by_cust: dict[str, list] = {}
                    for item in value:
                        if isinstance(item, dict):
                            cid = item.get("customer_id") or "Unknown"
                            by_cust.setdefault(cid, []).append(item)
                    for cid, items in list(by_cust.items())[:15]:
                        lines.append(f"\n**{cid}** ({len(items)} records)")
                        for item in items[:max_items]:
                            eid = item.get("id") or item.get("kpi_id") or item.get("alarm_id") or item.get("invoice_id") or item.get("charge_id") or item.get("payment_id") or item.get("complaint_id") or item.get("log_id") or item.get("incident_id") or item.get("disruption_id") or item.get("dunning_id") or "?"
                            parts = []
                            for k, v in item.items():
                                if v is not None and k not in ("id", "description", "customer_id"):
                                    parts.append(f"**{k.replace('_', ' ')}**: {v}")
                            lines.append(f"- `{eid}` — {', '.join(parts[:6])}")
                            if item.get("description"):
                                lines.append(f"  _{item['description']}_")
                        if len(items) > max_items:
                            lines.append(f"  _...and {len(items) - max_items} more_")
                else:
                    for item in value[:max_items]:
                        if isinstance(item, dict):
                            eid = item.get("id") or item.get("kpi_id") or item.get("alarm_id") or item.get("invoice_id") or item.get("charge_id") or item.get("payment_id") or item.get("complaint_id") or item.get("log_id") or item.get("incident_id") or item.get("disruption_id") or item.get("dunning_id") or "?"
                            parts = []
                            for k, v in item.items():
                                if v is not None and k not in ("id", "description"):
                                    parts.append(f"**{k.replace('_', ' ')}**: {v}")
                            lines.append(f"- `{eid}` — {', '.join(parts[:6])}")
                            if item.get("description"):
                                lines.append(f"  _{item['description']}_")
                    if len(value) > max_items:
                        lines.append(f"\n_...and {len(value) - max_items} more records_")
                lines.append("")
            elif isinstance(value, dict) and value:
                lines.append(f"### {key.replace('_', ' ').title()}\n")
                for k, v in value.items():
                    if v is not None:
                        lines.append(f"- **{k.replace('_', ' ').title()}**: {v}")
                lines.append("")

        lines.append("\n---\n_Generated directly from Knowledge Graph traversal._")
        return "\n".join(lines)
