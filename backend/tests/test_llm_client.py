"""The real client's failure handling. No network: the SDK is stubbed."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from freetcoder.llm import LLMError
from freetcoder.llm.client import OpenAICompatibleClient


class Ping(BaseModel):
    word: str


class _Choiceless:
    """What an OpenAI-compatible gateway returns when it fails at 200.

    OpenRouter answers with an error payload and no `choices` for rate limits
    and upstream provider failures alike. Indexing it blind produced a
    TypeError deep in the stack rather than something a caller could report.
    """

    choices = None
    error = {"message": "rate limited", "code": 429}


@pytest.fixture
def client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="http://example.invalid/v1", api_key="k", model="m",
        timeout_s=1, max_retries=0,
    )


class TestAProviderThatFailsAtStatus200:
    async def test_a_missing_choices_list_is_an_llm_error(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def create(**_: object) -> _Choiceless:
            return _Choiceless()

        monkeypatch.setattr(client._client.chat.completions, "create", create)
        with pytest.raises(LLMError, match="no completion"):
            await client.complete_json(system="s", user="u", schema=Ping)

    async def test_the_provider_error_is_reported(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without the detail there is nothing to act on."""
        async def create(**_: object) -> _Choiceless:
            return _Choiceless()

        monkeypatch.setattr(client._client.chat.completions, "create", create)
        with pytest.raises(LLMError, match="rate limited"):
            await client.complete_json(system="s", user="u", schema=Ping)
