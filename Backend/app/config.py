from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    app_name: str = "Telecom RCA Intelligence"
    app_env: str = "development"
    frontend_origins: str = "*"
    neo4j_uri: str = "neo4j://localhost:7687"
    neo4j_username: str = "neo4j"
    neo4j_password: str = "replace_me"
    neo4j_database: str = "neo4j"
    answer_llm_provider: str = "openai"
    openai_api_key: str = "replace_me"
    openai_model: str = "gpt-5-mini"
    openai_temperature: float = 0.2
    openai_enabled: bool = True
    claude_api_key: str = "replace_me"
    claude_model: str = "claude-opus-5"
    claude_enabled: bool = True
    ollama_url: str = "http://localhost:11434/v1"
    ollama_model: str = "llama3.1"
    lmstudio_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = "tslam-4b"
    lmstudio_enabled: bool = False
    groq_api_key: str = "replace_me"
    groq_model: str = "llama-3.1-70b-versatile"
    groq_enabled: bool = False
    huggingface_api_key: str = "replace_me"
    huggingface_model: str = "openai/gpt-oss-120b"
    huggingface_provider: str = "novita"
    huggingface_enabled: bool = False
    allow_demo_fallback: bool = True
    conversation_memory_enabled: bool = True
    conversation_memory_dir: Path = PROJECT_ROOT / ".chroma_conversations"
    vector_store_dir: Path = PROJECT_ROOT / ".vector_store"
    vector_embedding_enabled: bool = True
    vector_search_top_k: int = 10
    rca_data_dir: Path = PROJECT_ROOT / "RCA2"

    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env" if (BACKEND_ROOT / ".env").exists() else None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def origins(self) -> list[str]:
        origins = [item.strip() for item in self.frontend_origins.split(",") if item.strip()]
        return ["*"] if "*" in origins else origins

    @property
    def cors_allow_credentials(self) -> bool:
        return "*" not in self.origins

    @property
    def neo4j_configured(self) -> bool:
        return bool(self.neo4j_password and self.neo4j_password != "replace_me")

    @property
    def openai_configured(self) -> bool:
        return self.openai_enabled and bool(self.openai_api_key and self.openai_api_key != "replace_me")

    @property
    def claude_configured(self) -> bool:
        return self.claude_enabled and bool(self.claude_api_key and self.claude_api_key != "replace_me")

    @property
    def llm_provider(self) -> str:
        provider = (self.answer_llm_provider or "").strip().lower()
        return provider if provider in {"openai", "ollama", "kg"} else "kg"

    @property
    def ollama_configured(self) -> bool:
        return self.llm_provider == "ollama" and bool(self.ollama_url and self.ollama_model)

    @property
    def lmstudio_configured(self) -> bool:
        return self.lmstudio_enabled and bool(self.lmstudio_url and self.lmstudio_model)

    @property
    def groq_configured(self) -> bool:
        return self.groq_enabled and bool(self.groq_api_key and self.groq_api_key != "replace_me")

    @property
    def huggingface_configured(self) -> bool:
        return self.huggingface_enabled and bool(self.huggingface_api_key and self.huggingface_api_key != "replace_me")

    @property
    def answer_llm_configured(self) -> bool:
        if self.llm_provider == "openai":
            return self.openai_configured
        if self.llm_provider == "ollama":
            return self.ollama_configured
        return False


@lru_cache
def get_settings() -> Settings:
    return Settings()
