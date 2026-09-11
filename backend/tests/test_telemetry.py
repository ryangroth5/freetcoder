"""Provider-call accounting.

Nothing measured latency or tokens before this, so every statement about
generation being slow rested on a stopwatch. These tests pin the properties the
bench relies on -- including that it costs nothing when nobody is collecting.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from freetcoder.llm import telemetry
from freetcoder.llm.client import OpenAICompatibleClient


class Ping(BaseModel):
    word: str


class _Usage:
    prompt_tokens = 120
    completion_tokens = 480


class _Message:
    content = '{"word": "ok"}'


class _Choice:
    message = _Message()


class _Response:
    choices = [_Choice()]
    usage = _Usage()


@pytest.fixture
def client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        base_url="http://example.invalid/v1", api_key="k", model="m-1",
        timeout_s=1, max_retries=1,
    )


async def _succeed(**_: object) -> _Response:
    return _Response()


class TestRecording:
    async def test_a_call_records_model_tokens_and_time(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client._client.chat.completions, "create", _succeed)
        with telemetry.collecting() as run:
            await client.complete_json(system="s", user="u", schema=Ping)

        assert len(run.calls) == 1
        call = run.calls[0]
        assert call.model == "m-1"
        assert call.prompt_tokens == 120
        assert call.completion_tokens == 480
        assert call.seconds >= 0
        assert call.ok

    async def test_stages_are_attributed(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Knowing a run was slow is useless; knowing which stage was is not."""
        monkeypatch.setattr(client._client.chat.completions, "create", _succeed)
        with telemetry.collecting() as run:
            with telemetry.stage("statement"):
                await client.complete_json(system="s", user="u", schema=Ping)
            with telemetry.stage("reference"):
                await client.complete_json(system="s", user="u", schema=Ping)


        by_stage = run.by_stage()
        assert set(by_stage) == {"statement", "reference"}
        assert by_stage["statement"]["calls"] == 1
        assert run.completion_tokens == 960

    async def test_a_failure_is_recorded_with_its_reason(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A strategy that fails fast and one that succeeds are not the same.

        RuntimeError rather than LLMError: the client only converts the failure
        kinds it knows how to retry, and lets anything else propagate untouched.
        Telemetry has to record the call regardless, or a run that died would
        look like a run that never happened.
        """
        async def boom(**_: object) -> _Response:
            raise RuntimeError("upstream is unwell")

        monkeypatch.setattr(client._client.chat.completions, "create", boom)
        with telemetry.collecting() as run, pytest.raises(RuntimeError):
            await client.complete_json(system="s", user="u", schema=Ping)

        assert run.calls, "a failed call must still be recorded"
        assert not run.calls[0].ok
        assert "upstream is unwell" in run.calls[0].error

    async def test_collecting_nothing_collects_nothing(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The serving path must not pay for the bench's instrumentation."""
        monkeypatch.setattr(client._client.chat.completions, "create", _succeed)
        await client.complete_json(system="s", user="u", schema=Ping)

        with telemetry.collecting() as run:
            pass
        assert run.calls == []

    async def test_collectors_do_not_leak_between_runs(
        self, client: OpenAICompatibleClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(client._client.chat.completions, "create", _succeed)
        with telemetry.collecting() as first:
            await client.complete_json(system="s", user="u", schema=Ping)
        with telemetry.collecting() as second:
            pass

        assert len(first.calls) == 1
        assert second.calls == []
