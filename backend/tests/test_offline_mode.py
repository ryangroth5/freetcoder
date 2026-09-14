"""Offline mode has to work on the strategy the product actually runs.

`FREETCODER_FAKE_LLM=1` is the no-key demo the README leads with, and every
Playwright test runs on it. It queued a JSON dict against a path that lints,
type-checks and imports its reply, so the demo generated nothing and the
browser suite could not cover the shipped path at all.
"""

from __future__ import annotations

import pytest

from freetcoder.formats import resolve
from freetcoder.llm import FakeLLM, build_client
from freetcoder.service import obtain_question
from freetcoder.settings import get_settings
from freetcoder.storage import Storage


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch):
    def configure(strategy: str) -> None:
        monkeypatch.setenv("FREETCODER_FAKE_LLM", "1")
        monkeypatch.setenv("FREETCODER_GENERATION_STRATEGY", strategy)
        get_settings.cache_clear()

    yield configure
    get_settings.cache_clear()


class TestTheRecordedQuestionMatchesTheStrategy:
    async def test_the_module_path_produces_a_question_with_no_key(
        self, offline
    ) -> None:
        offline("module")
        store = Storage(None)
        await store.connect()
        try:
            got = await obtain_question(
                store, build_client(), resolve("leetcode"), 0, max_attempts=1
            )
        finally:
            await store.close()

        assert got is not None, "the no-key demo generated nothing"
        q = got[1].question
        assert q.title == "Two Sum"
        assert q.signatures[0].function_name == "two_sum"
        assert got[1].hidden_tests, "the gate must have computed real cases"

    async def test_every_offered_language_is_translated(self, offline) -> None:
        """`leetcode` offers three. A recorded module alone would die at the
        translation stage, which is where the first attempt at this failed."""
        offline("module")
        store = Storage(None)
        await store.connect()
        try:
            got = await obtain_question(
                store, build_client(), resolve("leetcode"), 0, max_attempts=1
            )
        finally:
            await store.close()

        assert got is not None
        langs = {s.language.value for s in got[1].question.signatures}
        assert langs == {"python", "javascript", "typescript"}

    async def test_the_monolithic_path_still_works(self, offline) -> None:
        offline("monolithic")
        store = Storage(None)
        await store.connect()
        try:
            got = await obtain_question(
                store, build_client(), resolve("leetcode"), 0, max_attempts=1
            )
        finally:
            await store.close()

        assert got is not None
        assert got[1].question.title == "Two Sum"


class TestAFakeThatAnswersTheRequestIsNotExhausted:
    """`service._can_generate` reads `exhausted` to mean "no LLM configured".

    A request-answering fake has an empty queue by construction, so without
    this every offline session skipped generation entirely and fell through to
    an empty cache -- a failure that looked exactly like having no key.
    """

    def test_a_request_answering_fake_is_never_exhausted(self) -> None:
        fake = FakeLLM(text_for=lambda system, user: "anything")
        assert not fake.exhausted

    def test_an_empty_queue_still_means_unconfigured(self) -> None:
        assert FakeLLM().exhausted

    def test_a_cycling_queue_is_never_exhausted(self) -> None:
        assert not FakeLLM([{"a": 1}], cycle=True).exhausted
