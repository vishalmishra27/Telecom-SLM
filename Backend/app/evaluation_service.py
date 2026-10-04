import re
import uuid
from dataclasses import dataclass

from .models import AnswerEvaluation, ManualEvaluationResponse
from .qa_service import QAService


# Telecom domain terminology for relevance scoring
TELECOM_TERMS = {
    # Network
    "alarm", "outage", "cell", "site", "rca", "root cause", "network fault",
    "severity", "critical", "major", "minor", "degraded", "failure",
    # KPI
    "kpi", "threshold", "breach", "packet loss", "latency", "jitter",
    "throughput", "error rate", "availability", "performance",
    # Billing
    "invoice", "charge", "credit", "adjustment", "billing cycle",
    "dunning", "payment", "overdue", "past due", "amount",
    # Service
    "sla", "service level", "disruption", "maintenance", "downtime",
    "restoration", "mttr", "mtbf", "incident",
    # Infrastructure
    "mediation", "provisioning", "queue", "system error", "log",
    "trace", "infrastructure", "node", "element",
    # Complaint
    "complaint", "dispute", "escalation", "resolution", "customer impact",
    # RCA specific
    "root cause", "impact analysis", "recommendation", "corrective action",
    "preventive", "downstream", "upstream", "cascade", "correlation",
}

# Patterns for entity IDs in our KG
ID_PATTERNS = [
    r"[A-Z]{2,4}-GEN-[A-F0-9]{4,8}",
    r"CUST-\d+",
    r"SITE-[A-Z]{2,3}-[A-Z]{3}-\d+",
    r"ACC-GEN-[A-F0-9]{4,8}",
]


@dataclass
class CandidateAnswer:
    name: str
    text: str


class ManualEvaluationService:
    def __init__(self, qa_service: QAService):
        self.qa_service = qa_service

    @staticmethod
    def _normalize(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "")).strip().lower()

    @staticmethod
    def _token_estimate(text: str) -> int:
        tokens = re.findall(r"\w+|[^\w\s]", str(text or ""))
        return max(1, round(len(tokens) * 0.75)) if tokens else 0

    @staticmethod
    def _sentences(text: str) -> list[str]:
        cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
        return [item.strip(" -:\t") for item in re.split(r"(?<=[.!?])\s+|\n+", cleaned) if item.strip(" -:\t")]

    @staticmethod
    def _facts_from_source(source_answer: str) -> list[str]:
        facts: list[str] = []
        for line in str(source_answer or "").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("|---") or stripped.startswith("**Source Provenance"):
                continue
            # Table rows (legacy format)
            if stripped.startswith("|") and stripped.endswith("|"):
                cells = [cell.strip(" `*") for cell in stripped.strip("|").split("|")]
                if len(cells) >= 2 and cells[0].lower() != "field":
                    facts.append(f"{cells[0]}: {cells[1]}")
                continue
            # Bullet points with entity IDs (KG template format: - `ID` — **key**: val, ...)
            if stripped.startswith("- `") and "—" in stripped:
                # Extract the whole line as a fact (strip markdown)
                clean = re.sub(r"[`*_]", "", stripped).lstrip("- ").strip()
                facts.append(clean)
                continue
            # Regular bullet points and numbered lists
            if stripped.startswith(("-", "1.", "2.", "3.", "4.", "5.", "6.", "7.")):
                facts.append(re.sub(r"^\d+\.\s+|^-\s+", "", stripped).strip())
            elif stripped.startswith("**Summary:**"):
                facts.append(stripped.replace("**Summary:**", "").strip())
            # Key-value lines (KG format: - **Key**: Value)
            elif stripped.startswith("- **") and "**:" in stripped:
                clean = re.sub(r"[`*_]", "", stripped).lstrip("- ").strip()
                facts.append(clean)
        deduped = []
        seen = set()
        for fact in facts:
            compact = re.sub(r"[`*_]", "", fact).strip()
            key = compact.lower()
            if compact and key not in seen:
                seen.add(key)
                deduped.append(compact)
        return deduped[:30]

    @staticmethod
    def _keywords(text: str) -> set[str]:
        stopwords = {
            "the", "and", "for", "with", "from", "that", "this", "was", "were",
            "into", "after", "which", "then", "over", "under", "source", "live",
            "neo4j", "incident", "record", "records", "answer", "summary",
        }
        return {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_/.-]{3,}", str(text or ""))
            if token.lower() not in stopwords
        }

    @staticmethod
    def _find_all_ids(text: str) -> set[str]:
        """Find all entity IDs in text — both KG patterns and legacy ITIL patterns."""
        ids = set()
        for pat in ID_PATTERNS:
            ids.update(m.group(0).upper() for m in re.finditer(pat, text, re.IGNORECASE))
        # Also match legacy ITIL patterns
        ids.update(m.group(0).upper() for m in re.finditer(r"\b(?:INC|PRB|CHG|CRQ)\d+\b", text, re.IGNORECASE))
        return ids

    @classmethod
    def _fact_supported(cls, fact: str, answer: str) -> bool:
        normalized_answer = cls._normalize(answer)
        fact_text = re.sub(r"[`*_]", "", fact)
        ids = cls._find_all_ids(fact_text)
        if ids and any(item.lower() in normalized_answer for item in ids):
            return True
        keywords = cls._keywords(fact_text)
        if not keywords:
            return False
        hits = sum(1 for keyword in keywords if keyword in normalized_answer)
        return hits >= max(2, min(3, round(len(keywords) * 0.30)))

    @classmethod
    def _unsupported_claims(cls, answer: str, source_facts: list[str]) -> list[str]:
        source_keywords = set()
        source_ids = set()
        for fact in source_facts:
            source_keywords.update(cls._keywords(fact))
            source_ids.update(item.lower() for item in cls._find_all_ids(fact))
        unsupported = []
        for sentence in cls._sentences(answer):
            ids = {item.lower() for item in cls._find_all_ids(sentence)}
            if ids and not ids.issubset(source_ids):
                unsupported.append(sentence)
                continue
            keywords = cls._keywords(sentence)
            if len(keywords) >= 5:
                overlap = len(keywords & source_keywords) / max(1, len(keywords))
                if overlap < 0.25:
                    unsupported.append(sentence)
            if len(unsupported) >= 5:
                break
        return unsupported

    @staticmethod
    def _extract_ids(text: str) -> list[str]:
        """Extract all entity IDs cited in the answer."""
        ids = set()
        for pat in ID_PATTERNS:
            for m in re.finditer(pat, text, re.IGNORECASE):
                ids.add(m.group(0).upper())
        return sorted(ids)

    def _evaluate_one(self, candidate: CandidateAnswer, source_facts: list[str], real_ids: list[str]) -> AnswerEvaluation:
        if not candidate.text.strip():
            return AnswerEvaluation(
                name=candidate.name,
                score=0,
                token_estimate=0,
                source_fact_overlap=0,
                supported_facts=[],
                missing_facts=source_facts[:10],
                unsupported_claims=[],
                outside_content_ratio=1.0,
                verdict="No answer pasted.",
            )
        supported = [fact for fact in source_facts if self._fact_supported(fact, candidate.text)]
        missing = [fact for fact in source_facts if fact not in supported]
        unsupported = self._unsupported_claims(candidate.text, source_facts)
        coverage = len(supported) / max(1, len(source_facts))
        outside_ratio = min(1.0, len(unsupported) / max(1, len(self._sentences(candidate.text))))
        token_estimate = self._token_estimate(candidate.text)
        concise_bonus = 1.0 if token_estimate <= 900 else max(0.75, 900 / token_estimate)

        # Telecom-specific metrics
        cited_ids = self._extract_ids(candidate.text)
        real_ids_set = {rid.upper() for rid in real_ids}
        valid_ids = [cid for cid in cited_ids if cid in real_ids_set]
        invalid_ids = [cid for cid in cited_ids if cid not in real_ids_set]
        entity_id_accuracy = round(len(valid_ids) / max(1, len(cited_ids)) * 100) if cited_ids else 0

        # Grounding = combination of fact coverage + no hallucinated IDs
        grounding = round(coverage * 70 + (1 - outside_ratio) * 30)

        # Overall score: Grounding + Entity ID accuracy + conciseness
        score = round(
            grounding * 0.45 +
            entity_id_accuracy * 0.35 +
            (1 - outside_ratio) * 10 +
            concise_bonus * 10
        )

        verdict = "Strong" if score >= 80 else "Good" if score >= 65 else "Weak" if score >= 45 else "Poor"
        return AnswerEvaluation(
            name=candidate.name,
            score=max(0, min(100, score)),
            token_estimate=token_estimate,
            source_fact_overlap=len(supported),
            supported_facts=supported[:12],
            missing_facts=missing[:10],
            unsupported_claims=unsupported[:5],
            outside_content_ratio=round(outside_ratio, 2),
            verdict=verdict,
            grounding_score=grounding,
            entity_id_accuracy=entity_id_accuracy,
            cited_ids=valid_ids[:20],
            invalid_ids=invalid_ids[:10],
        )

    def evaluate(self, question: str, kg_answer: str, claude_answer: str, chatgpt_answer: str) -> ManualEvaluationResponse:
        source_response = self.qa_service.ask(question, f"eval-{uuid.uuid4()}")
        source_facts = self._facts_from_source(source_response.answer)
        # Extract real entity IDs from the KG source answer for accuracy checking
        real_ids = self._extract_ids(source_response.answer + "\n" + kg_answer)
        candidates = [
            CandidateAnswer("Our KG", kg_answer),
            CandidateAnswer("Claude", claude_answer),
            CandidateAnswer("ChatGPT", chatgpt_answer),
        ]
        evaluations = [self._evaluate_one(candidate, source_facts, real_ids) for candidate in candidates]
        winner = max(evaluations, key=lambda item: item.score).name if evaluations else ""
        return ManualEvaluationResponse(
            question=question,
            winner=winner,
            real_facts=source_facts,
            source_answer=source_response.answer,
            evaluations=evaluations,
        )
