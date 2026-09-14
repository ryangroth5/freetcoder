"""LLM access: one protocol, a real client and an offline fake."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from ..settings import Settings, get_settings
from .base import LLMClient, LLMError
from .client import OpenAICompatibleClient
from .fake import FakeLLM, ToolCall

__all__ = [
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "LlmStatus",
    "OpenAICompatibleClient",
    "ToolCall",
    "build_client",
    "llm_status",
]


#: What the app is actually talking to, and why. `build_client` below makes
#: the same decision; keeping them adjacent is what stops the badge in the UI
#: from disagreeing with reality -- which is exactly what happened when the
#: only signals were `configured` and `has_key`, both true while every question
#: came from a fixture.
LlmStatus = Literal["live", "offline", "unconfigured"]


def llm_status(settings: Settings | None = None) -> tuple[LlmStatus, str]:
    """The effective provider state, and a sentence explaining it."""
    s = settings or get_settings()
    if s.fake_llm and not s.key_from_session:
        return "offline", (
            "FREETCODER_FAKE_LLM is set, so questions come from recorded "
            "fixtures. Entering a key below overrides it for this server."
        )
    if not s.configured:
        return "unconfigured", (
            "No API key. Put one in .env as FREETCODER_LLM_API_KEY, or enter "
            "one below to use this server until it restarts."
        )
    return "live", f"Using {s.llm_model} at {s.llm_base_url}."


def build_client(settings: Settings | None = None) -> LLMClient:
    """Construct the configured client.

    Falls back to an empty FakeLLM when no key is set, so the app boots and can
    show its setup screen instead of crashing on import.
    """
    s = settings or get_settings()
    # A key typed into the running app beats the flag: otherwise someone whose
    # container was started with FREETCODER_FAKE_LLM=1 has no way back to a
    # real provider from inside the UI. The flag still wins on its own, which
    # is what the browser suites rely on.
    if s.fake_llm and not s.key_from_session:
        import json

        from .fake import FIXTURE_DIR

        # Whichever artifact the active strategy actually asks for. The module
        # path calls `complete_text` and lints, type-checks and imports the
        # reply; handing it `str(dict)` produced a demo that silently generated
        # nothing and a browser suite that could not cover the shipped path.
        if s.generation_strategy == "module":
            return FakeLLM(
                text_for=_recorded_module(),
                chat_reply=_OFFLINE_CHAT,
            )
        recorded = json.loads((FIXTURE_DIR / "two_sum_good.json").read_text())
        # Cycles: offline mode must not run dry part-way through a session.
        # The canned chat reply keeps the tutor demonstrable without a key.
        return FakeLLM([recorded], cycle=True, chat_reply=_OFFLINE_CHAT)
    if not s.configured:
        return FakeLLM()
    return OpenAICompatibleClient(
        base_url=s.llm_base_url,
        api_key=s.llm_api_key,
        model=s.llm_model,
        timeout_s=s.llm_timeout_s,
        max_retries=s.llm_max_retries,
    )


_OFFLINE_CHAT = (
    "Offline mode is on, so this is a canned reply rather than a real tutor. "
    "Start with what you have already tried, then read the first failing case: "
    "the input and the expected answer usually point straight at the gap."
)


def _recorded_module() -> Callable[[str, str], str]:
    """Answer the module strategy's calls from recorded files.

    It makes several calls of different kinds per question -- the module, then
    one translation per extra language -- so a queue's position stops meaning
    anything. Match on the request instead.

    The sufficiency check is deliberately not answered: it asks through
    `complete_json`, finds nothing queued, and is skipped as inconclusive.
    Offline mode has no second model, and pretending otherwise would be a
    check that always passes.
    """
    from .fake import FIXTURE_DIR

    module_source = (FIXTURE_DIR / "two_sum_good.py").read_text()
    translations: dict[str, str] = json.loads(
        (FIXTURE_DIR / "two_sum_translations.json").read_text()
    )

    def reply(system: str, user: str) -> str:
        for language, text in translations.items():
            if f"into {language}" in user:
                return text
        return module_source

    return reply
