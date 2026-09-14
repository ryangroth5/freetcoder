"""The product's choice of generation strategy.

`service.obtain_question` is the only place the app asks for a question, so it
is the only place the strategy matters. Everything here runs offline.
"""

from __future__ import annotations

import pytest

from freetcoder.formats import resolve
from freetcoder.llm import FakeLLM
from freetcoder.models import Language
from freetcoder.service import obtain_question
from freetcoder.storage import Storage, cache_key
from tests.test_module_strategy import GOOD, python_only


@pytest.fixture
async def store():
    s = Storage(None)
    await s.connect()
    yield s
    await s.close()


class TestTheStrategySetting:
    def test_module_is_the_default(self) -> None:
        from freetcoder.settings import get_settings

        get_settings.cache_clear()
        import os

        os.environ.pop("FREETCODER_GENERATION_STRATEGY", None)
        try:
            assert get_settings().generation_strategy == "module"
        finally:
            get_settings.cache_clear()

    async def test_the_module_path_produces_a_gated_question(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("FREETCODER_GENERATION_STRATEGY", "module")
        from freetcoder.settings import get_settings

        get_settings.cache_clear()

        got = await obtain_question(
            store, FakeLLM([GOOD]), python_only(), 0, max_attempts=1
        )
        assert got is not None, "the module path produced nothing"
        qid, gated = got
        assert gated.hidden_tests, "the gate's computed cases must be carried"
        assert gated.question.title == "Steady Tide Windows"
        assert qid

    async def test_a_failed_module_falls_back_to_the_cache(
        self, store: Storage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fallback is what keeps practice working when the model cannot
        get anything past the gate."""
        monkeypatch.setenv("FREETCODER_GENERATION_STRATEGY", "module")
        from freetcoder.settings import get_settings

        get_settings.cache_clear()
        config = python_only()

        first = await obtain_question(
            store, FakeLLM([GOOD]), config, 0, max_attempts=1
        )
        assert first is not None

        # Nothing queued: every attempt fails, so the cached one must come back.
        again = await obtain_question(
            store, FakeLLM([]), config, 0, max_attempts=1
        )
        assert again is not None
        assert again[1].question.title == "Steady Tide Windows"


class TestTheCacheKeepsTheStrategiesApart:
    def test_the_key_names_the_strategy(self) -> None:
        config = resolve("leetcode")
        module = cache_key(config, "Medium", Language.PYTHON, "module")
        mono = cache_key(config, "Medium", Language.PYTHON, "monolithic")
        assert module != mono, (
            "a fallback that may return either strategy's question makes a "
            "quality change unobservable"
        )

    async def test_legacy_rows_are_dropped_on_migration(self) -> None:
        """Keys from before the strategy segment can never match again."""
        import json

        store = Storage(None)
        await store.connect()
        try:
            await store.db.execute(
                "INSERT INTO questions (id, cache_key, language, payload, created_at)"
                " VALUES ('old', 'leetcode||Medium|python|,|python', 'python', ?, 0)",
                (json.dumps({}),),
            )
            await store.db.commit()
            await store._drop_unkeyed_questions()
            cur = await store.db.execute("SELECT COUNT(*) AS n FROM questions")
            assert (await cur.fetchone())["n"] == 0
        finally:
            await store.close()

    async def _with(self, *, started_at: float, finished_at: float | None):
        """One legacy question, referenced by one session with these times."""
        import json

        store = Storage(None)
        await store.connect()
        await store.db.execute(
            "INSERT INTO questions (id, cache_key, language, payload, created_at)"
            " VALUES ('q1', 'leetcode||Medium|python|,|python', 'python', ?, 0)",
            (json.dumps({}),),
        )
        await store.db.execute(
            "INSERT INTO sessions (id, config, question_ids, started_at, finished_at)"
            " VALUES ('s1', '{}', ?, ?, ?)",
            (json.dumps(["q1"]), started_at, finished_at),
        )
        await store.db.commit()
        return store

    async def _remaining(self, store: Storage) -> int:
        await store._drop_unkeyed_questions()
        cur = await store.db.execute("SELECT COUNT(*) AS n FROM questions")
        return int((await cur.fetchone())["n"])

    async def test_a_question_someone_is_looking_at_is_kept(self) -> None:
        """Someone halfway through a run must not lose the problem in front of
        them because the key format changed."""
        import time

        store = await self._with(started_at=time.time() - 60, finished_at=None)
        try:
            assert await self._remaining(store) == 1
        finally:
            await store.close()

    async def test_an_abandoned_session_does_not_pin_it_forever(self) -> None:
        """The first version kept anything referenced by *any* session, which
        on a real database was every row: each cached question was created for
        some session, so the clause could never fire. Narrowing it to
        unfinished sessions was barely better -- 1532 of 1538 were unfinished,
        because people start runs and walk away."""
        import time

        store = await self._with(
            started_at=time.time() - 10 * 86_400, finished_at=None
        )
        try:
            assert await self._remaining(store) == 0
        finally:
            await store.close()

    async def test_a_finished_session_does_not_pin_it(self) -> None:
        import time

        store = await self._with(
            started_at=time.time() - 60, finished_at=time.time() - 30
        )
        try:
            assert await self._remaining(store) == 0
        finally:
            await store.close()
