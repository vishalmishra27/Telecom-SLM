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
3. If the evidence is insufficient, say so explicitly — do not guess.
4. Use a professional, concise tone appropriate for a telecom NOC operator.
5. Keep answers short and focused. Do NOT output markdown tables. Use plain text with bullet points.
6. Structure: brief Summary, then Key Findings (bullet points with IDs), then Recommendation if applicable.
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
        # HuggingFace Inference API (GPT-OSS-120B, OpenAI-compatible)
        huggingface_api_key: str = "",
        huggingface_model: str = "openai/gpt-oss-120b",
        huggingface_provider: str = "novita",
        huggingface_enabled: bool = False,
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

        # HuggingFace Inference API (OpenAI-compatible, routed via provider)
        _HF_PROVIDER_URLS = {
            "novita": "https://router.huggingface.co/novita/v3/openai",
            "together": "https://router.huggingface.co/together/v1",
            "fireworks-ai": "https://router.huggingface.co/fireworks-ai/inference/v1",
            "cerebras": "https://router.huggingface.co/cerebras/v1",
            "nscale": "https://router.huggingface.co/nscale/v1",
        }
        self._hf_model = huggingface_model
        self._hf_provider = huggingface_provider
        self._hf = None
        if huggingface_enabled and huggingface_api_key and huggingface_api_key != "replace_me":
            base_url = _HF_PROVIDER_URLS.get(huggingface_provider, _HF_PROVIDER_URLS["novita"])
            self._hf = OpenAI(base_url=base_url, api_key=huggingface_api_key)
            print(f"[HuggingFace] Configured: model={huggingface_model}, provider={huggingface_provider}")

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
        if self._hf:
            models.append({
                "id": "huggingface",
                "display_name": f"GPT-OSS-120B ({self._hf_provider})",
                "model_id": self._hf_model,
                "available": True,
            })
        models.append({
            "id": "deterministic",
            "display_name": "KG Traversal",
            "model_id": "graph-direct",
            "available": True,
        })
        return models

    def narrate(self, prompt: str, model: str | None = None) -> str:
        """Simple prompt-in → text-out call for RCA narration.

        Uses the preferred model or falls through the configured chain.
        Returns the raw LLM response text, or empty string on failure.
        Has a 30s timeout to avoid blocking the RCA pipeline.
        """
        import concurrent.futures

        def _call():
            system = (
                "You are a Telecom RCA expert. Produce clear, structured, evidence-grounded "
                "root cause analysis reports. Only cite entity IDs present in the evidence. "
                "Do not invent facts."
            )
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ]

            # Build ordered list of clients to try
            clients = []
            if model == "huggingface" or (model is None and self._hf):
                clients.append(("huggingface", self._hf, self._hf_model))
            if model == "groq" or (model is None and self._groq):
                clients.append(("groq", self._groq, self._groq_model))
            if model == "openai" or (model is None and self._openai):
                clients.append(("openai", self._openai, self._openai_model))
            if model == "lmstudio" or (model is None and self._lmstudio):
                clients.append(("lmstudio", self._lmstudio, self._lmstudio_model))

            # Claude uses a different API
            if model == "claude" and self._claude:
                resp = self._claude.messages.create(
                    model=self._claude_model,
                    max_tokens=1500,
                    temperature=0.3,
                    system=system,
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text

            for name, client, model_id in clients:
                if not client:
                    continue
                try:
                    resp = client.chat.completions.create(
                        model=model_id,
                        temperature=0.3,
                        messages=messages,
                        max_tokens=1500,
                    )
                    return resp.choices[0].message.content
                except Exception:
                    continue

            return ""

        # Run with timeout so a slow LLM doesn't block the pipeline
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(_call)
                return future.result(timeout=30)
        except (concurrent.futures.TimeoutError, Exception):
            return ""

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

        # Map model names to their generators
        _MODEL_MAP = {
            "lmstudio": ("lmstudio", self._lmstudio, self._generate_lmstudio),
            "groq": ("groq", self._groq, self._generate_groq),
            "huggingface": ("huggingface", self._hf, self._generate_huggingface),
            "claude": ("claude", self._claude, self._generate_claude),
            "openai": ("openai", self._openai, self._generate_openai),
        }

        # If user explicitly selected a model, ONLY try that model — no silent fallback
        if preferred_model and preferred_model in _MODEL_MAP:
            model_name, client, generate_fn = _MODEL_MAP[preferred_model]
            if not client:
                error_msg = f"**{model_name}** is not configured. Check your .env file for the API key and enabled flag."
                return error_msg, model_name, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, [], "low"
            last_error = None
            for retry in range(2):
                try:
                    answer, token_usage = generate_fn(question, customer_id, intent, evidence, conversation_history=conversation_history)
                    citations = self._parse_citations(answer, evidence)
                    confidence = "high" if citations else "medium"
                    return answer, model_name, token_usage, citations, confidence
                except Exception as exc:
                    last_error = exc
                    if retry == 0:
                        continue
            # Model explicitly selected but failed — return the error, don't fall back
            error_msg = f"**{model_name}** failed after 2 attempts: {last_error}\n\nSelect a different model or use KG Traversal."
            return error_msg, model_name, {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}, [], "low"

        # No preference (Auto) — try fallback chain
        attempts = []
        if self._claude:
            attempts.append(("claude", self._generate_claude))
        if self._openai:
            attempts.append(("openai", self._generate_openai))
        if self._groq:
            attempts.append(("groq", self._generate_groq))
        if self._hf:
            attempts.append(("huggingface", self._generate_huggingface))
        if self._lmstudio:
            attempts.append(("lmstudio", self._generate_lmstudio))

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

Based on the evidence above, answer the customer's question concisely. Follow the rules in your system prompt.
Do NOT use markdown tables. Use bullet points with entity IDs."""

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
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for i, msg in enumerate(conversation_history):
                if i == 0 and msg["role"] == "user":
                    messages.append({"role": "user", "content": f"Data:\n{template_text}\n\n{msg['content']}"})
                else:
                    messages.append(msg)
            messages.append({"role": "user", "content": question})
        else:
            prompt = (
                f"Customer: {customer_id}\n\n"
                f"KG Evidence:\n{template_text}\n\n"
                f"Question: {question}\n\n"
                f"Answer concisely with: Summary, Key Findings (bullet points with IDs), Recommendation. "
                f"No markdown tables. Cite entity IDs."
            )
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

        response = self._lmstudio.chat.completions.create(
            model=self._lmstudio_model,
            temperature=0.23,
            messages=messages,
            max_tokens=1500,
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
            messages = [{"role": "system", "content": "You are a telecom network analyst. Use the data provided to answer questions concisely. Cite specific IDs. No markdown tables — use bullet points."}]
            for i, msg in enumerate(conversation_history):
                if i == 0 and msg["role"] == "user":
                    messages.append({"role": "user", "content": f"Data:\n{template_text}\n\n{msg['content']}"})
                else:
                    messages.append(msg)
            messages.append({"role": "user", "content": question})
        else:
            prompt = (
                f"Customer: {customer_id}\n\n"
                f"KG Evidence:\n{template_text}\n\n"
                f"Question: {question}\n\n"
                f"Answer concisely with: Summary, Key Findings (bullet points with IDs), Recommendation. "
                f"No markdown tables. Cite entity IDs."
            )
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

        response = self._groq.chat.completions.create(
            model=self._groq_model,
            temperature=0.3,
            messages=messages,
            max_tokens=1500,
        )
        answer = response.choices[0].message.content
        usage_obj = response.usage
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "total_tokens": getattr(usage_obj, "total_tokens", 0) or 0,
        }
        return answer, usage

    def _generate_huggingface(
        self, question: str, customer_id: str, intent: str, evidence: dict[str, Any],
        conversation_history: list[dict] | None = None, **kwargs
    ) -> tuple[str, dict[str, int]]:
        """Generate answer using HuggingFace Inference API (GPT-OSS-120B, OpenAI-compatible)."""
        template_text = self._fallback_answer(customer_id, intent, evidence)
        template_text = template_text.replace(
            "\n---\n_Generated directly from Knowledge Graph traversal._", ""
        ).replace(
            "\n---\n_Generated by deterministic template — LLM narration unavailable._", ""
        ).strip()

        if conversation_history:
            messages = [{"role": "system", "content": SYSTEM_PROMPT}]
            for i, msg in enumerate(conversation_history):
                if i == 0 and msg["role"] == "user":
                    messages.append({"role": "user", "content": f"Data:\n{template_text}\n\n{msg['content']}"})
                else:
                    messages.append(msg)
            messages.append({"role": "user", "content": question})
        else:
            prompt = (
                f"Customer: {customer_id}\n\n"
                f"KG Evidence:\n{template_text}\n\n"
                f"Question: {question}\n\n"
                f"Answer concisely with: Summary, Key Findings (bullet points with IDs), Recommendation. "
                f"No markdown tables. Cite entity IDs."
            )
            messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]

        response = self._hf.chat.completions.create(
            model=self._hf_model,
            temperature=0.3,
            messages=messages,
            max_tokens=1500,
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
            return {"network_failures": self.retrieval.all_network_failures()}
        elif intent == "kpi_breach":
            return {"network_failures": self.retrieval.all_network_failures()}
        elif intent == "dunning_chain":
            return {"payment_failures": self.retrieval.all_payment_failures()}
        elif intent == "sla_credit":
            return {"incidents": self.retrieval.all_incidents()}
        elif intent == "system_error":
            return {"log_errors": self.retrieval.all_log_errors()}
        elif intent == "complaint":
            return {"disputes": self.retrieval.all_disputes()}
        elif intent == "financial_impact":
            return {"network_failures": self.retrieval.all_network_failures(limit=20),
                    "payment_failures": self.retrieval.all_payment_failures(limit=20)}
        elif intent == "unresolved":
            return self.retrieval.unresolved_issues()
        elif intent == "full_chain":
            return {
                "network_failures": self.retrieval.all_network_failures(limit=20),
                "payment_failures": self.retrieval.all_payment_failures(limit=20),
                "incidents": self.retrieval.all_incidents(limit=20),
                "disputes": self.retrieval.all_disputes(limit=20),
                "log_errors": self.retrieval.all_log_errors(limit=20),
            }
        else:
            return {"network_failures": self.retrieval.all_network_failures(limit=20)}

    def _retrieve_evidence(self, intent: str, customer_id: str, question: str) -> dict[str, Any]:
        # Check if the question mentions a specific entity ID — if so, route by prefix
        entity_ids = self._extract_entity_ids(question)
        for eid in entity_ids:
            if not eid.startswith("CUST-"):
                return self.retrieval.rca_by_id(eid)

        # Otherwise use the customer-level full chain
        return self.retrieval.rca_full_chain(customer_id)

    # -----------------------------------------------------------------
    # Traversal metadata — Causal ontology paths
    # -----------------------------------------------------------------
    _INTENT_CYPHER: dict[str, dict] = {
        "full_chain": {
            "description": "Full Causal Chain — Root Causes → Charges → Invoice → Account → Customer",
            "cypher": (
                "MATCH (a:Account)-[:OWNED_BY]->(c:Customer {id: $cid})\n"
                "MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a)\n"
                "OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)\n"
                "OPTIONAL MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv)\n"
                "OPTIONAL MATCH (d:Dispute)-[:DISPUTES]->(ch)\n"
                "OPTIONAL MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)\n"
                "OPTIONAL MATCH (pm:PmCounter)-[:CORROBORATES]->(nf:NetworkFailure)"
            ),
            "steps": [
                {"order": 1, "from_class": "RootCause", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "Root cause event created the billing charge"},
                {"order": 2, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charge appears on an invoice"},
                {"order": 3, "from_class": "Invoice", "rel": "BILLED_TO", "to_class": "Account", "explanation": "Invoice billed to customer account"},
                {"order": 4, "from_class": "Account", "rel": "OWNED_BY", "to_class": "Customer", "explanation": "Account owned by the customer"},
                {"order": 5, "from_class": "PmCounter", "rel": "CORROBORATES", "to_class": "NetworkFailure", "explanation": "PM counter evidence corroborates the failure"},
                {"order": 6, "from_class": "Dispute", "rel": "DISPUTES", "to_class": "Charge", "explanation": "Customer disputes the charge"},
                {"order": 7, "from_class": "SlaCredit", "rel": "COMPENSATES", "to_class": "Incident", "explanation": "SLA credit compensates the incident"},
            ],
        },
        "network_rca": {
            "description": "Network RCA — NetworkFailure → CAUSED_CHARGE → Chain B + PM/API evidence",
            "cypher": (
                "MATCH (nf:NetworkFailure)-[:CAUSED_CHARGE]->(ch:Charge)\n"
                "MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)\n"
                "OPTIONAL MATCH (pm:PmCounter)-[:CORROBORATES]->(nf)\n"
                "OPTIONAL MATCH (api:ApiKpiBreach)-[:CORROBORATES]->(nf)"
            ),
            "steps": [
                {"order": 1, "from_class": "NetworkFailure", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "Network failure caused a billing charge"},
                {"order": 2, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charge on invoice"},
                {"order": 3, "from_class": "PmCounter", "rel": "CORROBORATES", "to_class": "NetworkFailure", "explanation": "PM counter corroborates the failure"},
                {"order": 4, "from_class": "ApiKpiBreach", "rel": "CORROBORATES", "to_class": "NetworkFailure", "explanation": "API KPI breach corroborates the failure"},
            ],
        },
        "dunning_chain": {
            "description": "Payment Failure → FAILED_ON → Invoice → Account → Customer",
            "cypher": (
                "MATCH (pf:PaymentFailure)-[:FAILED_ON]->(inv:Invoice)\n"
                "MATCH (inv)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)"
            ),
            "steps": [
                {"order": 1, "from_class": "PaymentFailure", "rel": "FAILED_ON", "to_class": "Invoice", "explanation": "Payment failed on an invoice"},
                {"order": 2, "from_class": "Invoice", "rel": "BILLED_TO", "to_class": "Account", "explanation": "Invoice billed to account"},
                {"order": 3, "from_class": "Account", "rel": "OWNED_BY", "to_class": "Customer", "explanation": "Account owned by customer"},
            ],
        },
        "sla_credit": {
            "description": "SLA Credit → COMPENSATES → Incident → CAUSED_CHARGE → Chain B",
            "cypher": (
                "MATCH (sc:SlaCredit)-[:COMPENSATES]->(inc:Incident)\n"
                "MATCH (inc)-[:CAUSED_CHARGE]->(ch:Charge)\n"
                "MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)"
            ),
            "steps": [
                {"order": 1, "from_class": "SlaCredit", "rel": "COMPENSATES", "to_class": "Incident", "explanation": "SLA credit compensates an incident"},
                {"order": 2, "from_class": "Incident", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "Incident caused a billing charge"},
                {"order": 3, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charge on invoice"},
            ],
        },
        "system_error": {
            "description": "LogEvent → CAUSED_CHARGE → Charge → Chain B + EMITTED_BY Service",
            "cypher": (
                "MATCH (l:LogEvent)-[:CAUSED_CHARGE]->(ch:Charge)\n"
                "MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)\n"
                "OPTIONAL MATCH (l)-[:EMITTED_BY]->(svc:Service)"
            ),
            "steps": [
                {"order": 1, "from_class": "LogEvent", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "System error caused a billing charge"},
                {"order": 2, "from_class": "LogEvent", "rel": "EMITTED_BY", "to_class": "Service", "explanation": "Log emitted by service"},
                {"order": 3, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charge on invoice"},
            ],
        },
        "complaint": {
            "description": "Dispute → DISPUTES → Charge → Chain B + find root cause",
            "cypher": (
                "MATCH (d:Dispute)-[:DISPUTES]->(ch:Charge)\n"
                "MATCH (ch)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)\n"
                "OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)"
            ),
            "steps": [
                {"order": 1, "from_class": "Dispute", "rel": "DISPUTES", "to_class": "Charge", "explanation": "Customer disputes a charge"},
                {"order": 2, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charge on invoice"},
                {"order": 3, "from_class": "RootCause", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "Root cause that created the disputed charge"},
            ],
        },
        "financial_impact": {
            "description": "Charge → ON_INVOICE → Invoice → BILLED_TO → Account + root causes",
            "cypher": (
                "MATCH (ch:Charge)-[:ON_INVOICE]->(inv:Invoice)-[:BILLED_TO]->(a:Account)-[:OWNED_BY]->(c:Customer)\n"
                "OPTIONAL MATCH (root:RootCause)-[:CAUSED_CHARGE]->(ch)"
            ),
            "steps": [
                {"order": 1, "from_class": "Charge", "rel": "ON_INVOICE", "to_class": "Invoice", "explanation": "Charges grouped into invoices"},
                {"order": 2, "from_class": "Invoice", "rel": "BILLED_TO", "to_class": "Account", "explanation": "Invoice billed to account"},
                {"order": 3, "from_class": "RootCause", "rel": "CAUSED_CHARGE", "to_class": "Charge", "explanation": "Root causes behind the charges"},
            ],
        },
    }

    def _build_traversal(self, intent: str, customer_id: str, evidence: dict[str, Any]) -> dict[str, Any]:
        """Build traversal metadata showing how the KG was traversed."""
        meta = self._INTENT_CYPHER.get(intent, self._INTENT_CYPHER["full_chain"])

        steps_with_counts = []
        for step in meta["steps"]:
            s = dict(step)
            cls_lower = step["to_class"].lower()
            count_map = {
                "charge": len(evidence.get("charges", [])),
                "invoice": 1 if evidence.get("invoice") else 0,
                "account": 1 if evidence.get("account") else 0,
                "customer": 1 if evidence.get("customer") else 0,
                "networkfailure": len(evidence.get("roots", evidence.get("network_failures", []))),
                "incident": len(evidence.get("roots", evidence.get("incidents", []))),
                "pmcounter": len(evidence.get("pm_evidence", [])),
                "apikpibreach": len(evidence.get("api_evidence", [])),
                "dispute": len(evidence.get("disputes", [])),
                "slacredit": len(evidence.get("sla_credits", [])),
                "service": 1 if evidence.get("service") else 0,
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
