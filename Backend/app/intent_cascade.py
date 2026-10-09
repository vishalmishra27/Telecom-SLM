"""
Intent Detection — Five-Layer Cascade Over a Configurable Taxonomy

Spec Section 3: Five layers tried in order; first confident match wins.

Layer 0: Meta/capability — zero model dependency
Layer 1: Rule-based taxonomy — ordered keyword/phrase matching
Layer 2: Model-constrained classification — LLM picks from fixed list
Layer 2b: Investigative sub-routing — domain specialist dispatch
Layer 3: Open-domain chat fallback — model answers ungrounded
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Intent Taxonomy — configurable, domain-agnostic
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TaxonomyEntry:
    intent: str
    trigger_phrases: tuple[str, ...]
    investigative: bool = False
    shape: str = "single_entity"  # single_entity, aggregate, multi_hop, investigative


# The shipped billing/RCA taxonomy (Spec Section 3.3, Table 5)
TAXONOMY: list[TaxonomyEntry] = [
    # Most specific first — first match wins
    TaxonomyEntry(
        intent="issues_without_remediation",
        trigger_phrases=("remediat", "top issues", "outstanding issues", "unresolved", "no remediation", "unremediated"),
        shape="multi_hop",
    ),
    TaxonomyEntry(
        intent="revenue_leakage_check",
        trigger_phrases=("unbilled", "leakage", "not been billed", "missing charge", "revenue leak"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="root_cause_analysis",
        trigger_phrases=(
            "root cause", "why did this happen", "network issue", "card declined",
            "call dropped", "calls kept dropping", "service was down", "outage",
            "why was i charged", "charged during", "what caused", "overcharg",
            "billing issue", "billing problem",
        ),
        investigative=True,
        shape="investigative",
    ),
    TaxonomyEntry(
        intent="dispute_status",
        trigger_phrases=("disput", "complaint", "escalat"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="payment_status",
        trigger_phrases=("payment status", "did i pay", "payment", "dunning", "past due", "overdue"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="discount_inquiry",
        trigger_phrases=("discount", "promo", "loyalty"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="charge_breakdown",
        trigger_phrases=("breakdown", "itemiz", "categories", "what am i paying for"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="invoice_explanation",
        trigger_phrases=("why is my bill", "higher than", "explain my bill", "bill went up"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="invoice_summary",
        trigger_phrases=("invoice", "bill summary", "total amount", "how much", "my bill"),
        shape="single_entity",
    ),
    # RCA-focused intents (from current app — kept as extensions)
    TaxonomyEntry(
        intent="network_rca",
        trigger_phrases=("network", "alarm", "cell outage", "site failure", "signal"),
        investigative=True,
        shape="investigative",
    ),
    TaxonomyEntry(
        intent="kpi_breach",
        trigger_phrases=("kpi", "threshold", "breach", "degraded", "performance", "counter"),
        investigative=True,
        shape="investigative",
    ),
    TaxonomyEntry(
        intent="sla_credit",
        trigger_phrases=("sla", "credit", "maintenance", "service problem"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="system_error",
        trigger_phrases=("log", "system error", "mediation", "queue", "infrastructure"),
        investigative=True,
        shape="investigative",
    ),
    TaxonomyEntry(
        intent="financial_impact",
        trigger_phrases=("charg", "bill", "financial", "impact", "cost", "amount", "total"),
        shape="single_entity",
    ),
    TaxonomyEntry(
        intent="full_chain",
        trigger_phrases=("360", "everything", "full picture", "complete", "overview", "summary"),
        shape="single_entity",
    ),
]


# ---------------------------------------------------------------------------
# Layer 0: Meta/capability questions
# ---------------------------------------------------------------------------

_META_PHRASES = [
    "what can you do", "how does this work", "what is this", "tell me about yourself",
    "what are your capabilities", "help me", "how do i use this",
]
_META_WORDS = {"hi", "hello", "hey", "help", "thanks", "thank you", "bye", "goodbye"}


def _is_meta_question(question: str) -> str | None:
    """Check if question is a meta/capability/greeting question.

    Returns a reason_code or None.
    """
    lower = question.lower().strip()
    # Multi-word phrases — substring match
    for phrase in _META_PHRASES:
        if phrase in lower:
            return "GENERAL_CAPABILITIES"
    # Single short words — whole-word match only (prevents "higher" matching "hi")
    words = set(re.findall(r"\b\w+\b", lower))
    if words & _META_WORDS and len(words) <= 3:
        return "GENERAL_GREETING"
    return None


# ---------------------------------------------------------------------------
# Layer 1: Rule-based taxonomy match
# ---------------------------------------------------------------------------

_COMPILED_TAXONOMY: list[tuple[TaxonomyEntry, list[re.Pattern]]] = []


def _ensure_compiled():
    global _COMPILED_TAXONOMY
    if not _COMPILED_TAXONOMY:
        for entry in TAXONOMY:
            patterns = []
            for phrase in entry.trigger_phrases:
                # Multi-word phrases: literal substring match
                # Single words ending in a letter that could be a stem (e.g. "disput", "charg"):
                #   use \b prefix only (no trailing \b) so it matches "disputes", "charged", etc.
                # Single complete words: full word-boundary match
                if " " in phrase:
                    patterns.append(re.compile(re.escape(phrase), re.IGNORECASE))
                elif phrase[-1:].isalpha() and not phrase.endswith(("ed", "er", "es", "ly", "ing", "tion", "ment")):
                    # Likely a stem — match as prefix
                    patterns.append(re.compile(r"\b" + re.escape(phrase), re.IGNORECASE))
                else:
                    patterns.append(re.compile(r"\b" + re.escape(phrase) + r"\b", re.IGNORECASE))
            _COMPILED_TAXONOMY.append((entry, patterns))


def _rule_based_match(question: str) -> TaxonomyEntry | None:
    """Layer 1: Ordered keyword rules — first matching rule wins."""
    _ensure_compiled()
    for entry, patterns in _COMPILED_TAXONOMY:
        for pat in patterns:
            if pat.search(question):
                return entry
    return None


# ---------------------------------------------------------------------------
# Layer 2: Model-constrained classification
# ---------------------------------------------------------------------------

_CLASSIFICATION_PROMPT = """You are an intent classifier for this application's configured domain.
Read the question and pick EXACTLY ONE intent from this fixed list
that best matches it. If the question does not genuinely match ANY of
these intents — e.g. it's a general-knowledge question, small talk, or
otherwise unrelated to this domain — reply with exactly the word NONE
instead of forcing a match onto the closest-sounding one. Reply with
ONLY the intent string or NONE, nothing else — no punctuation, no
explanation.

Allowed intents: {allowed_intents}

Question: {question}

Intent (or NONE):"""


def _model_classification(question: str, model_fn: Callable[[str], str] | None) -> TaxonomyEntry | None:
    """Layer 2: Model picks one intent from fixed taxonomy list, or NONE.

    root_cause_analysis is excluded — Layer 1's investigative keywords catch it.
    """
    if model_fn is None:
        return None

    # Exclude investigative intents — Layer 1 handles those
    allowed = [e.intent for e in TAXONOMY if not e.investigative]
    prompt = _CLASSIFICATION_PROMPT.format(
        allowed_intents=", ".join(allowed),
        question=question,
    )

    try:
        raw = model_fn(prompt).strip()
    except Exception:
        return None

    # Parse defensively — first token, then scan for any allowed intent
    first_token = raw.split()[0] if raw.split() else ""
    for entry in TAXONOMY:
        if entry.intent == first_token and not entry.investigative:
            return entry
    # Scan whole reply for an allowed intent as standalone token
    for entry in TAXONOMY:
        if not entry.investigative and re.search(r"\b" + re.escape(entry.intent) + r"\b", raw):
            return entry
    return None


# ---------------------------------------------------------------------------
# Layer 2b: Investigative sub-routing — domain specialist dispatch
# ---------------------------------------------------------------------------

SPECIALIST_KEYWORDS: dict[str, list[str]] = {
    "network": ["network", "roam", "roaming", "signal", "drop", "outage", "cell", "tower", "congestion", "coverage", "latency"],
    "payment": ["payment", "card", "declined", "charged twice", "gateway", "autopay", "transaction", "refund"],
    "service": ["outage", "disruption", "maintenance", "down", "no service", "broadband", "fiber", "sla", "installation"],
    "performance": ["api", "latency", "slow", "performance", "mediation", "re-rate", "delay", "timeout", "throughput", "kpi"],
    "logs": ["log", "logs", "error", "exception", "crash", "kibana", "elk", "pod", "restart", "infra", "oom", "stack trace"],
}

# Compile as word-boundary patterns
_SPECIALIST_PATTERNS: dict[str, list[re.Pattern]] = {}


def _ensure_specialist_patterns():
    global _SPECIALIST_PATTERNS
    if not _SPECIALIST_PATTERNS:
        for specialist, keywords in SPECIALIST_KEYWORDS.items():
            _SPECIALIST_PATTERNS[specialist] = []
            for kw in keywords:
                if " " in kw:
                    _SPECIALIST_PATTERNS[specialist].append(re.compile(re.escape(kw), re.IGNORECASE))
                else:
                    _SPECIALIST_PATTERNS[specialist].append(re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE))


def dispatch_specialists(question: str) -> list[str]:
    """Layer 2b: Decide which domain specialists to dispatch for an investigative intent.

    Returns list of specialist names. If no keyword matches, dispatches ALL
    specialists (cast widest net rather than under-investigating).
    """
    _ensure_specialist_patterns()
    matched: list[str] = []
    for specialist, patterns in _SPECIALIST_PATTERNS.items():
        for pat in patterns:
            if pat.search(question):
                matched.append(specialist)
                break
    if not matched:
        # No keyword matched — dispatch everything (Section 3.5)
        matched = list(SPECIALIST_KEYWORDS.keys())
    return matched


# ---------------------------------------------------------------------------
# Period/year extraction
# ---------------------------------------------------------------------------

_PERIOD_EXACT = re.compile(r"\b(\d{4})-(\d{2})\b")
_PERIOD_SPELLED = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b"
    r"|\b(\d{4})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\b",
    re.IGNORECASE,
)
_PERIOD_YEAR = re.compile(r"\b(20\d{2})\b")

_MONTH_MAP = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}


def extract_period(question: str) -> tuple[str | None, bool]:
    """Extract billing period from question.

    Returns (period_str, is_full_year).
    - "2024-03" → ("2024-03", False)
    - "March 2024" → ("2024-03", False)
    - "2024" (bare year) → ("2024", True) → routes to aggregate shape
    """
    # Exact YYYY-MM
    m = _PERIOD_EXACT.search(question)
    if m:
        return f"{m.group(1)}-{m.group(2)}", False

    # Spelled month + year
    m = _PERIOD_SPELLED.search(question)
    if m:
        month_name = (m.group(1) or m.group(4)).lower()
        year = m.group(2) or m.group(3)
        return f"{year}-{_MONTH_MAP[month_name]}", False

    # Bare year
    m = _PERIOD_YEAR.search(question)
    if m:
        return m.group(1), True

    return None, False


# ---------------------------------------------------------------------------
# The cascade — main entry point
# ---------------------------------------------------------------------------

@dataclass
class IntentResult:
    """Result of the intent detection cascade."""
    intent: str
    layer: int                    # which layer resolved it (0-3)
    confidence: float = 0.85
    entry: TaxonomyEntry | None = None
    reason_codes: list[str] = field(default_factory=list)
    specialists: list[str] = field(default_factory=list)
    period: str | None = None
    is_full_year: bool = False
    is_investigative: bool = False
    is_meta: bool = False
    is_open_domain: bool = False  # Layer 3 — model's own knowledge, NOT grounded


def detect_intent(
    question: str,
    model_fn: Callable[[str], str] | None = None,
) -> IntentResult:
    """Run the five-layer intent cascade.

    Args:
        question: The user's natural language question.
        model_fn: Optional callable for Layer 2 (model-constrained classification).
                  Takes a prompt string, returns the model's response string.

    Returns:
        IntentResult with the resolved intent and metadata.
    """
    # Period extraction runs alongside (not instead of) intent matching
    period, is_full_year = extract_period(question)

    # Layer 0: Meta/capability
    meta_code = _is_meta_question(question)
    if meta_code:
        return IntentResult(
            intent="meta",
            layer=0,
            confidence=1.0,
            reason_codes=[meta_code],
            is_meta=True,
            period=period,
            is_full_year=is_full_year,
        )

    # Layer 1: Rule-based taxonomy match
    entry = _rule_based_match(question)
    if entry:
        specialists = dispatch_specialists(question) if entry.investigative else []
        return IntentResult(
            intent=entry.intent,
            layer=1,
            confidence=0.85,
            entry=entry,
            specialists=specialists,
            period=period,
            is_full_year=is_full_year,
            is_investigative=entry.investigative,
        )

    # Layer 2: Model-constrained classification
    entry = _model_classification(question, model_fn)
    if entry:
        return IntentResult(
            intent=entry.intent,
            layer=2,
            confidence=0.75,
            entry=entry,
            period=period,
            is_full_year=is_full_year,
            is_investigative=entry.investigative,
        )

    # Layer 3: Open-domain chat fallback
    return IntentResult(
        intent="open_domain",
        layer=3,
        confidence=0.5,
        reason_codes=["GENERAL_CHAT"],
        is_open_domain=True,
        period=period,
        is_full_year=is_full_year,
    )
