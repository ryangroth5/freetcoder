"""Runtime configuration, all overridable by environment variable."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FREETCODER_", extra="ignore")

    #: Any OpenAI-compatible endpoint: OpenRouter, Ollama, vLLM, LM Studio.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""
    llm_model: str = "anthropic/claude-sonnet-4.5"
    llm_timeout_s: float = 120.0
    llm_max_retries: int = 3

    #: Empty path means in-memory: the container has no persistent filesystem
    #: unless the user opts into a volume.
    db_path: str = ""
    static_dir: Path | None = None
    dev: bool = False

    #: How many times to regenerate before giving up on a question slot.
    generation_attempts: int = 4

    #: Patch-and-re-gate rounds per generated question, before regenerating.
    #: Bounded so a confused model cannot spend unlimited tokens or time.
    repair_rounds: int = 3

    #: How many times the model may execute code while repairing one question.
    tool_call_budget: int = 6

    #: The question library service. Empty disables publishing and browsing;
    #: unreachable degrades the same way. Practice never depends on it.
    library_url: str = ""

    #: Serve recorded fixture questions instead of calling a provider. Exists so
    #: the UI and the end-to-end tests can run with no API key and no network.
    #: Deliberately explicit -- an unset key must fail loudly, not silently
    #: fall back to canned questions.
    fake_llm: bool = False

    @property
    def configured(self) -> bool:
        """False until the user supplies a key on the setup screen."""
        return bool(self.llm_api_key and self.llm_base_url)


@lru_cache
def get_settings() -> Settings:
    return Settings()
