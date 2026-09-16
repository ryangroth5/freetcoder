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


class TestAProviderThatNeverAnswers:
    """The client's own `timeout` is not a deadline.

    httpx applies a bare float per *operation* -- connect, read, write, pool --
    so a provider that dribbles bytes keeps resetting the read timer and the
    request never expires. Measured against a live OpenRouter call: eleven
    minutes with a 300s timeout configured, the progress log frozen on the step
    that started it and no way for the candidate to tell it was stuck.
    """

    async def test_a_hanging_call_is_cut_off(self) -> None:
        import asyncio

        from freetcoder.llm import LLMError
        from freetcoder.llm.client import OpenAICompatibleClient

        client = OpenAICompatibleClient(
            base_url="http://unused", api_key="k", model="m",
            timeout_s=0.05, max_retries=1,
        )

        async def never(**kwargs):
            await asyncio.sleep(30)

        client._client.chat.completions.create = never  # type: ignore[method-assign]

        with pytest.raises(LLMError) as caught:
            await client.complete_text(system="s", user="u")
        assert "did not answer within" in str(caught.value)

    async def test_the_deadline_does_not_fire_on_a_normal_call(self) -> None:
        from types import SimpleNamespace

        from freetcoder.llm.client import OpenAICompatibleClient

        client = OpenAICompatibleClient(
            base_url="http://unused", api_key="k", model="m",
            timeout_s=5, max_retries=1,
        )

        async def answers(**kwargs):
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="hello"))],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
            )

        client._client.chat.completions.create = answers  # type: ignore[method-assign]
        assert await client.complete_text(system="s", user="u") == "hello"


class TestATimeoutIsNotRetried:
    """Measured: a 300s deadline fired three times inside one progress step --
    677 seconds of silence for a call that takes 103s when it works.

    A 429 or a 5xx says the provider was momentarily unable. A timeout says
    this request is too slow for this budget, and re-sending it unchanged is
    the least promising thing to do with another full deadline. The caller's
    own loop can vary the prompt; this one cannot.
    """

    def _client(self, **kw):
        from freetcoder.llm.client import OpenAICompatibleClient

        return OpenAICompatibleClient(
            base_url="http://unused", api_key="k", model="m", **kw
        )

    async def test_it_is_sent_once_not_three_times(self) -> None:
        import asyncio

        from freetcoder.llm import LLMError

        client = self._client(timeout_s=0.05, max_retries=3)
        calls = 0

        async def never(**kwargs):
            nonlocal calls
            calls += 1
            await asyncio.sleep(30)

        client._client.chat.completions.create = never  # type: ignore[method-assign]

        with pytest.raises(LLMError) as caught:
            await client.complete_text(system="s", user="u")
        assert calls == 1, f"the deadline was spent {calls} times over"
        assert "did not answer within" in str(caught.value)

    async def test_a_transient_failure_is_still_retried(self) -> None:
        """The loop was written for this case and keeps it: a provider that is
        momentarily unable deserves another go, unlike one that is simply too
        slow for the budget."""
        from types import SimpleNamespace

        client = self._client(timeout_s=5, max_retries=3)
        calls = 0

        async def flaky(**kwargs):
            nonlocal calls
            calls += 1
            if calls < 3:
                # Empty choices is the shape a 5xx or a pool error arrives in;
                # complete_text turns it into a retryable LLMError.
                return SimpleNamespace(choices=[], usage=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
                usage=None,
            )

        client._client.chat.completions.create = flaky  # type: ignore[method-assign]
        assert await client.complete_text(system="s", user="u") == "ok"
        assert calls == 3

    async def test_the_retry_hook_reports_each_re_send(self) -> None:
        from freetcoder.llm import LLMError

        client = self._client(timeout_s=5, max_retries=3)
        said: list[str] = []
        client.on_retry = said.append
        calls = 0

        async def always_empty(**kwargs):
            nonlocal calls
            calls += 1
            from types import SimpleNamespace

            return SimpleNamespace(choices=[], usage=None)

        client._client.chat.completions.create = always_empty  # type: ignore[method-assign]
        with pytest.raises(LLMError):
            await client.complete_text(system="s", user="u")
        assert calls == 3, "a retryable failure must still be retried"
        assert len(said) == 2, "reported between attempts, not after the last"
        assert "asking again (2 of 3)" in said[0]

    async def test_no_hook_is_safe(self) -> None:
        from freetcoder.llm import LLMError

        client = self._client(timeout_s=5, max_retries=2)

        async def always_empty(**kwargs):
            from types import SimpleNamespace

            return SimpleNamespace(choices=[], usage=None)

        client._client.chat.completions.create = always_empty  # type: ignore[method-assign]
        with pytest.raises(LLMError):
            await client.complete_text(system="s", user="u")


# --------------------------------------------------------------- streaming
def _chunk(content: str = "", reasoning: str = "", *, provider: str = "", usage=None):
    """A streamed chunk shaped like the OpenAI SDK's, extras included."""
    from types import SimpleNamespace

    delta = SimpleNamespace(
        content=content or None,
        model_extra={"reasoning": reasoning} if reasoning else {},
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta)] if (content or reasoning) else [],
        usage=usage,
        model_extra={"provider": provider} if provider else {},
    )


class _Stream:
    """An async stream of (delay_before, chunk) pairs."""

    def __init__(self, script):
        self._script = list(script)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        import asyncio

        if not self._script:
            raise StopAsyncIteration
        delay, chunk = self._script.pop(0)
        await asyncio.sleep(delay)
        return chunk

    async def close(self):
        self.closed = True


class TestStreamingDetectsAStallEarly:
    """A non-streamed call gives no signal until it is finished, so a stuck
    model and a busy one looked identical for the full 300s. Streamed, a model
    that is working shows it within seconds."""

    def _client(self, **kw):
        from freetcoder.llm.client import OpenAICompatibleClient

        opts = {"timeout_s": 5.0, "max_retries": 1, "first_token_s": 0.2, "idle_s": 0.2}
        opts.update(kw)
        return OpenAICompatibleClient(base_url="http://unused", api_key="k", model="m", **opts)

    def _serve(self, client, *streams):
        queue = list(streams)
        sent: list[dict] = []

        async def create(**kwargs):
            sent.append(kwargs)
            return queue.pop(0)

        client._client.chat.completions.create = create  # type: ignore[method-assign]
        return sent

    async def test_no_first_token_is_a_stall_not_a_timeout(self) -> None:
        import time

        from freetcoder.llm import LLMStalled

        client = self._client()
        self._serve(client, _Stream([(3.0, _chunk("late"))]))
        started = time.monotonic()
        with pytest.raises(LLMStalled) as caught:
            await client.complete_text(system="s", user="u")
        assert time.monotonic() - started < 1.0, "should give up at the first-token window"
        assert "no tokens from m" in str(caught.value)

    async def test_a_steady_stream_runs_past_the_first_token_window(self) -> None:
        """Only silence is punished. A reply that keeps arriving may take as
        long as the overall deadline allows."""
        client = self._client()
        script = [(0.05, _chunk("tok ")) for _ in range(12)]  # ~0.6s total
        sent = self._serve(client, _Stream(script))
        assert await client.complete_text(system="s", user="u") == "tok " * 12
        assert sent[0]["stream"] is True

    async def test_silence_mid_reply_is_a_stall(self) -> None:
        from freetcoder.llm import LLMStalled

        client = self._client()
        self._serve(client, _Stream([(0.0, _chunk("start")), (3.0, _chunk("end"))]))
        with pytest.raises(LLMStalled) as caught:
            await client.complete_text(system="s", user="u")
        assert "went silent" in str(caught.value)

    async def test_reasoning_tokens_count_as_alive(self) -> None:
        """Thinking models emit reasoning long before any content."""
        client = self._client()
        script = [(0.1, _chunk(reasoning="hmm")) for _ in range(5)] + [(0.1, _chunk("done"))]
        self._serve(client, _Stream(script))
        assert await client.complete_text(system="s", user="u") == "done"

    async def test_the_stream_is_closed_when_abandoned(self) -> None:
        from freetcoder.llm import LLMStalled

        client = self._client()
        stream = _Stream([(0.0, _chunk("a")), (3.0, _chunk("b"))])
        self._serve(client, stream)
        with pytest.raises(LLMStalled):
            await client.complete_text(system="s", user="u")
        assert stream.closed

    async def test_the_record_says_what_happened(self) -> None:
        from freetcoder.llm import telemetry

        client = self._client()
        self._serve(client, _Stream([
            (0.05, _chunk("hel", provider="Groq")), (0.05, _chunk("lo")),
        ]))
        with telemetry.collecting() as calls:
            await client.complete_text(system="sys", user="ask")
        view = calls.calls[0].view()
        assert view["outcome"] == "ok" and view["served_by"] == "Groq"
        assert view["reply"] == "hello" and "ask" in view["prompt"]
        assert view["ttft_s"] is not None and view["tokens_streamed"] == 2
        assert view["in_flight"] is False


class TestTheFallbackModel:
    def _client(self, **kw):
        from freetcoder.llm.client import OpenAICompatibleClient

        opts = {"timeout_s": 5.0, "max_retries": 1, "first_token_s": 0.2,
                "idle_s": 0.2, "fallback_model": "backup"}
        opts.update(kw)
        return OpenAICompatibleClient(base_url="http://unused", api_key="k", model="main", **opts)

    async def test_a_stall_switches_to_the_fallback_once(self) -> None:
        from freetcoder.llm import telemetry

        client = self._client()
        said: list[str] = []
        client.on_retry = said.append
        streams = [_Stream([(3.0, _chunk("never"))]), _Stream([(0.0, _chunk("saved"))])]
        models: list[str] = []

        async def create(**kwargs):
            models.append(kwargs["model"])
            return streams.pop(0)

        client._client.chat.completions.create = create  # type: ignore[method-assign]
        with telemetry.collecting() as calls:
            assert await client.complete_text(system="s", user="u") == "saved"
        assert models == ["main", "backup"]
        assert any("switching to backup" in m and "no tokens from main" in m for m in said)
        assert [c.outcome for c in calls.calls] == ["stalled", "ok"]

    async def test_no_fallback_configured_means_no_switch(self) -> None:
        from freetcoder.llm import LLMStalled

        client = self._client(fallback_model="")
        count = 0

        async def create(**kwargs):
            nonlocal count
            count += 1
            return _Stream([(3.0, _chunk("never"))])

        client._client.chat.completions.create = create  # type: ignore[method-assign]
        with pytest.raises(LLMStalled):
            await client.complete_text(system="s", user="u")
        assert count == 1

    async def test_the_fallback_failing_too_surfaces_its_error(self) -> None:
        from freetcoder.llm import LLMStalled

        client = self._client()

        async def create(**kwargs):
            return _Stream([(3.0, _chunk("never"))])

        client._client.chat.completions.create = create  # type: ignore[method-assign]
        with pytest.raises(LLMStalled) as caught:
            await client.complete_text(system="s", user="u")
        assert "backup" in str(caught.value), "the second failure is the one reported"
