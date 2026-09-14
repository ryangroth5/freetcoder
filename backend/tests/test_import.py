"""Importing a question from supplied text.

The property worth protecting: an imported question is validated exactly like a
generated one. An import path that skipped the gate would defeat the entire
premise -- "you pasted something approximate" must still yield a question that
provably runs.
"""

from __future__ import annotations

import inspect
import json

import pytest

from freetcoder.formats import resolve
from freetcoder.generate import build_user_prompt, generate_question
from freetcoder.generate.gate import validate_question
from freetcoder.llm import FakeLLM
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import Difficulty, GateOutcome, GeneratedQuestion, Language

ALL = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def load(name: str) -> GeneratedQuestion:
    return GeneratedQuestion.model_validate(fixture(name))


class TestResolvingAnImport:
    def test_supplied_text_marks_the_source(self) -> None:
        config = resolve("leetcode", import_text="count the dogs")
        assert config.generation.source == "imported"
        assert config.generation.import_text == "count the dogs"

    def test_without_text_the_source_stays_generated(self) -> None:
        assert resolve("leetcode").generation.source == "generated"

    def test_whitespace_only_text_is_not_an_import(self) -> None:
        assert resolve("leetcode", import_text="   \n ").generation.source == "generated"

    def test_import_is_distinct_from_freeform(self) -> None:
        """Freeform steers the topic; import supplies the problem."""
        config = resolve("leetcode", freeform="graphs only")
        assert config.generation.source == "generated"
        assert config.generation.freeform == "graphs only"


class TestPromptLayering:
    def test_the_style_still_applies(self) -> None:
        """An imported question is still a question of the chosen format."""
        prompt = build_user_prompt(
            resolve("codility", import_text="count the dogs"), Difficulty.EASY
        )
        assert "Codility-style" in prompt
        assert "Adapting a question the candidate supplied" in prompt

    def test_the_supplied_text_is_included_verbatim(self) -> None:
        prompt = build_user_prompt(
            resolve("leetcode", import_text="a kennel log of arrivals"),
            Difficulty.EASY,
        )
        assert "a kennel log of arrivals" in prompt

    def test_notes_are_requested(self) -> None:
        prompt = build_user_prompt(
            resolve("leetcode", import_text="x"), Difficulty.EASY
        )
        assert "import_notes" in prompt

    def test_a_plain_generation_never_mentions_importing(self) -> None:
        prompt = build_user_prompt(resolve("leetcode"), Difficulty.EASY)
        assert "Adapting a question" not in prompt


class TestTheGateTreatsImportsIdentically:
    def test_no_gate_check_branches_on_provenance(self) -> None:
        """The strongest guarantee available: the gate cannot know.

        If validation ever consulted `source` or `import_text`, an imported
        question could be held to a lower standard without anyone noticing.
        """
        from freetcoder.generate import gate

        source = inspect.getsource(gate)
        assert "import_text" not in source
        assert 'source == "imported"' not in source
        assert "generation.source" not in source

    def test_a_valid_import_is_accepted(self) -> None:
        report = validate_question(load("counting_dogs_imported"), languages=ALL)
        assert report.accepted, report.detail

    def test_a_broken_import_is_rejected_like_any_other(self) -> None:
        report = validate_question(load("counting_dogs_bad_generator"), languages=ALL)
        assert report.outcome is GateOutcome.NO_HIDDEN_CASES

    def test_import_notes_survive_validation(self) -> None:
        question = load("counting_dogs_imported")
        assert "I assumed exactly one valid pair" in question.import_notes


class TestGenerationFromImport:
    async def test_provenance_reaches_the_stored_question(self) -> None:
        client = FakeLLM([fixture("counting_dogs_imported")])
        result = await generate_question(
            client, resolve("leetcode", import_text="count the dogs in a kennel log")
        )
        assert result.accepted
        assert result.question is not None
        assert result.question.source == "imported"
        assert "kennel log" in result.question.import_text

    async def test_a_generated_question_records_no_import_text(self) -> None:
        client = FakeLLM([fixture("two_sum_good")])
        result = await generate_question(client, resolve("leetcode"))
        assert result.question is not None
        assert result.question.source == "generated"
        assert result.question.import_text == ""

    async def test_a_broken_import_is_repaired_not_regenerated(self) -> None:
        from freetcoder.generate.repair import QuestionPatch

        working = (
            "import json, random\nrandom.seed(4)\n"
            "for _ in range(12):\n"
            "    nums = [random.randint(0, 40) for _ in range(8)]\n"
            "    print(json.dumps({'args': {'nums': nums, "
            "'target': nums[0] + nums[1]}}))\n"
        )
        client = FakeLLM([
            fixture("counting_dogs_bad_generator"),
            QuestionPatch(target="generator", content=working),
        ])
        result = await generate_question(
            client, resolve("leetcode", import_text="count the dogs")
        )
        assert result.accepted
        assert any(a.repaired for a in result.attempts)
        assert result.question is not None
        assert result.question.source == "imported"


class TestImportOverTheApi:
    """Uses the shared client fixture from conftest."""

    async def test_a_session_can_be_started_from_supplied_text(
        self, client, fake_llm: FakeLLM
    ) -> None:
        fake_llm.queue_next(fixture("counting_dogs_imported"))
        resp = await client.post("/api/sessions", json={
            "style": "leetcode",
            "import_text": "A kennel logs arrivals; find two entries summing to a target.",
        })
        assert resp.status_code == 200, resp.text

        sid = resp.json()["id"]
        question = (await client.get(f"/api/sessions/{sid}/questions/0")).json()
        assert "kennel" in question["statement_md"].lower()
        assert question["source"] == "imported"
        assert "I assumed" in question["import_notes"]

    async def test_a_generated_question_reports_no_import_notes(
        self, client
    ) -> None:
        resp = await client.post("/api/sessions", json={"style": "leetcode"})
        sid = resp.json()["id"]
        question = (await client.get(f"/api/sessions/{sid}/questions/0")).json()
        assert question["source"] == "generated"
        assert question["import_notes"] == ""

    async def test_an_oversized_paste_is_refused(self, client) -> None:
        resp = await client.post("/api/sessions", json={
            "style": "leetcode", "import_text": "x" * 20_000,
        })
        assert resp.status_code == 422


class TestPublishingAnImportIsDeliberate:
    """A shared library needs to tell original work from copied work."""

    @pytest.fixture(autouse=True)
    def _library(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from freetcoder.settings import get_settings

        monkeypatch.setenv("FREETCODER_LIBRARY_URL", "http://library:8090")
        get_settings.cache_clear()
        yield
        get_settings.cache_clear()

    async def test_publishing_an_import_is_refused_by_default(
        self, client, fake_llm: FakeLLM
    ) -> None:
        fake_llm.queue_next(fixture("counting_dogs_imported"))
        sid = (await client.post("/api/sessions", json={
            "style": "leetcode", "import_text": "count the dogs",
        })).json()["id"]

        resp = await client.post(f"/api/sessions/{sid}/questions/0/publish")
        assert resp.status_code == 409
        assert "may not be yours to share" in resp.json()["detail"]

    async def test_it_succeeds_when_explicitly_allowed(
        self, client, fake_llm: FakeLLM
    ) -> None:
        fake_llm.queue_next(fixture("counting_dogs_imported"))
        sid = (await client.post("/api/sessions", json={
            "style": "leetcode", "import_text": "count the dogs",
        })).json()["id"]

        resp = await client.post(
            f"/api/sessions/{sid}/questions/0/publish?allow_import=true"
        )
        # 200 if the library is up, 503 if not -- but never the provenance 409.
        assert resp.status_code != 409

    async def test_a_generated_question_publishes_without_a_prompt(
        self, client
    ) -> None:
        sid = (await client.post("/api/sessions",
                                 json={"style": "leetcode"})).json()["id"]
        resp = await client.post(f"/api/sessions/{sid}/questions/0/publish")
        assert resp.status_code != 409


class TestSuppliedProseReachesTheModulePath:
    """Pasting a problem no longer forces the strategy that measured zero.

    `import.md` is shared by both paths, so it must not name a field that only
    one of them has: the module path records judgement calls as clarifications
    with an executable probe, the monolithic path as free text.
    """

    def test_the_brief_carries_the_guidance_and_the_text(self) -> None:
        from freetcoder.formats import resolve
        from freetcoder.generate.staged import _brief
        from freetcoder.models import Difficulty

        brief = _brief(
            resolve("leetcode", import_text="a kennel log of arrivals"),
            Difficulty.EASY,
            "a dog kennel",
        )
        assert "a kennel log of arrivals" in brief
        assert "Adapting a question the candidate supplied" in brief

    def test_a_plain_generation_gets_none_of_it(self) -> None:
        from freetcoder.formats import resolve
        from freetcoder.generate.staged import _brief
        from freetcoder.models import Difficulty

        brief = _brief(resolve("leetcode"), Difficulty.EASY, "a dog kennel")
        assert "Adapting a question" not in brief

    def test_the_shared_prompt_names_no_strategy_specific_field(self) -> None:
        from freetcoder.generate.pipeline import _read_prompt

        assert "import_notes" not in _read_prompt("import"), (
            "the module path has no such field; it uses clarifications"
        )
