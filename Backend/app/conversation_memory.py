import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .models import ChatResponse


class ConversationMemory:
    def __init__(self, settings: Settings):
        self.enabled = settings.conversation_memory_enabled
        self.path = Path(settings.conversation_memory_dir)
        self.collection = None
        self.fallback_path = self.path / "conversation_memory.jsonl"
        if not self.enabled:
            return
        self.path.mkdir(parents=True, exist_ok=True)
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(self.path))
            self.collection = client.get_or_create_collection(name="telecom_rca_conversations")
        except Exception:
            self.collection = None

    @staticmethod
    def normalize_question(question: str) -> str:
        text = str(question or "").lower()
        text = re.sub(r"[^a-z0-9]+", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _id_for(normalized_question: str) -> str:
        digest = hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()
        return f"question-{digest}"

    @staticmethod
    def _embedding(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [byte / 255 for byte in digest[:16]]

    def find_exact(self, question: str) -> dict[str, Any] | None:
        if not self.enabled:
            return None
        normalized = self.normalize_question(question)
        if not normalized:
            return None
        memory_id = self._id_for(normalized)
        if self.collection is not None:
            try:
                result = self.collection.get(ids=[memory_id], include=["metadatas"])
                metadatas = result.get("metadatas") or []
                if metadatas:
                    return dict(metadatas[0])
            except Exception:
                pass
        return self._find_exact_fallback(memory_id)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        records: list[dict[str, Any]] = []
        if self.collection is not None:
            try:
                result = self.collection.get(include=["metadatas"])
                ids = result.get("ids") or []
                metadatas = result.get("metadatas") or []
                for memory_id, metadata in zip(ids, metadatas):
                    records.append(self._history_item(memory_id, dict(metadata or {})))
            except Exception:
                records = []
        if not records:
            records = [
                self._history_item(memory_id, metadata)
                for memory_id, metadata in self._fallback_records().items()
            ]
        return sorted(records, key=lambda item: item.get("created_at", ""), reverse=True)[:limit]

    def response_by_id(self, memory_id: str) -> ChatResponse | None:
        metadata = None
        if self.collection is not None:
            try:
                result = self.collection.get(ids=[memory_id], include=["metadatas"])
                metadatas = result.get("metadatas") or []
                if metadatas:
                    metadata = dict(metadatas[0])
            except Exception:
                metadata = None
        if metadata is None:
            metadata = self._fallback_records().get(memory_id)
        if not metadata:
            return None
        try:
            response = ChatResponse.model_validate_json(str(metadata.get("response_json") or "{}"))
        except Exception:
            return None
        response.repeated_question = True
        response.repeated_of = str(metadata.get("original_question") or "")
        response.first_asked_at = str(metadata.get("created_at") or "")
        return response

    def store(self, question: str, response: ChatResponse) -> None:
        if not self.enabled:
            return
        normalized = self.normalize_question(question)
        if not normalized:
            return
        metadata = {
            "original_question": question,
            "normalized_question": normalized,
            "response_json": response.model_dump_json(),
            "intent": response.intent,
            "generated_by": response.generated_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        memory_id = self._id_for(normalized)
        if self.collection is not None:
            try:
                self.collection.upsert(
                    ids=[memory_id],
                    documents=[question],
                    metadatas=[metadata],
                    embeddings=[self._embedding(normalized)],
                )
                return
            except Exception:
                pass
        self._store_fallback(memory_id, metadata)

    def duplicate_response(self, memory: dict[str, Any], conversation_id: str) -> ChatResponse | None:
        try:
            response = ChatResponse.model_validate_json(str(memory.get("response_json") or "{}"))
        except Exception:
            return None
        created_at = str(memory.get("created_at") or "an earlier session")
        original_question = str(memory.get("original_question") or "this question")
        response.conversation_id = conversation_id
        response.message_id = str(uuid.uuid4())
        response.repeated_question = True
        response.repeated_of = original_question
        response.first_asked_at = created_at
        response.answer = (
            f"**Summary:** This question was already asked on `{created_at}`. "
            "Returning the previously grounded KG answer.\n\n"
            f"**Repeated Question**\n- Original question: `{original_question}`.\n\n"
            f"{response.answer}"
        )
        return response

    def _find_exact_fallback(self, memory_id: str) -> dict[str, Any] | None:
        return self._fallback_records().get(memory_id)

    def _fallback_records(self) -> dict[str, dict[str, Any]]:
        records: dict[str, dict[str, Any]] = {}
        if not self.fallback_path.exists():
            return records
        try:
            with self.fallback_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    item = json.loads(line)
                    if item.get("id"):
                        records[item["id"]] = dict(item.get("metadata") or {})
        except (OSError, json.JSONDecodeError):
            return {}
        return records

    @staticmethod
    def _history_item(memory_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": memory_id,
            "question": str(metadata.get("original_question") or ""),
            "intent": str(metadata.get("intent") or ""),
            "generated_by": str(metadata.get("generated_by") or ""),
            "created_at": str(metadata.get("created_at") or ""),
        }

    def _store_fallback(self, memory_id: str, metadata: dict[str, Any]) -> None:
        records: dict[str, dict[str, Any]] = {}
        if self.fallback_path.exists():
            try:
                with self.fallback_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        item = json.loads(line)
                        if item.get("id"):
                            records[item["id"]] = item
            except (OSError, json.JSONDecodeError):
                records = {}
        records[memory_id] = {"id": memory_id, "metadata": metadata}
        with self.fallback_path.open("w", encoding="utf-8") as handle:
            for item in records.values():
                handle.write(json.dumps(item, ensure_ascii=True) + "\n")
