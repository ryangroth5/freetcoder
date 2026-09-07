"""LLM access: one protocol, a real client and an offline fake."""

from __future__ import annotations

from ..settings import Settings, get_settings
from .base import LLMClient, LLMError
from .client import OpenAICompatibleClient
from .fake import FakeLLM, ToolCall

__all__ = [
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "OpenAICompatibleClient",
    "ToolCall",
    "build_client",
]


def build_client(settings: Settings | None = None) -> LLMClient:
    """Construct the configured client.

    Falls back to an empty FakeLLM when no key is set, so the app boots and can
    show its setup screen instead of crashing on import.
    """
    s = settings or get_settings()
    if s.fake_llm:
        import json

        from .fake import FIXTURE_DIR

        payload = json.loads((FIXTURE_DIR / "two_sum_good.json").read_text())
        # Cycles: offline mode must not run dry part-way through a session.
        # The canned chat reply keeps the tutor demonstrable without a key.
        return FakeLLM(
            [payload],
            cycle=True,
            chat_reply=(
                "Offline mode is on, so this is a canned reply rather than a "
                "real tutor. Start with what you have already tried, then read "
                "the first failing case: the input and the expected answer "
                "usually point straight at the gap."
            ),
        )
    if not s.configured:
        return FakeLLM()
    return OpenAICompatibleClient(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        model=s.llm_model,
        timeout_s=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )
