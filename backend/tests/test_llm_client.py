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


class TestRetriesStayConstrained:
    """A retry must not throw away the schema.

    The client used to ask for a strict schema once, then drop to plain JSON
    mode for every retry. Measured against a real provider, that turned a
    transient failure into a structural one: the retries failed on scattered
    unrelated fields -- a three-value enum, a plain string, nested signature
    fields -- which is an unguided model, not one that cannot write.
    """

    async def test_every_attempt_asks_for_the_schema(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[object] = []

        async def bad_then_good(**kwargs: object) -> object:
            seen.append(kwargs.get("response_format"))
            if len(seen) == 1:
                class _Bad:
                    choices = [type("C", (), {"message": type(
                        "M", (), {"content": '{"nope": 1}'})()})()]
                    usage = None
                return _Bad()

            class _Good:
                choices = [type("C", (), {"message": type(
                    "M", (), {"content": '{"word": "ok"}'})()})()]
                usage = None
            return _Good()

        monkeypatch.setattr(client._client.chat.completions, "create", bad_then_good)
        client_with_retries = client
        client_with_retries._max_retries = 2
        await client_with_retries.complete_json(system="s", user="u", schema=Ping)

        assert len(seen) == 2, "the first attempt should have been retried"
        kinds = [fmt.get("type") for fmt in seen]  # type: ignore[union-attr]
        assert kinds == ["json_schema", "json_schema"], kinds
