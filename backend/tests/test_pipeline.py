"""Generate-and-gate loop tests. Entirely offline via FakeLLM."""

from __future__ import annotations

import json

import pytest

from freetcoder.formats import resolve
from freetcoder.generate import build_user_prompt, generate_question
from freetcoder.llm import FakeLLM, LLMError
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import Difficulty, GateOutcome


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


class TestPromptAssembly:
    def test_style_template_is_included(self) -> None:
        p = build_user_prompt(resolve("codility"), Difficulty.MEDIUM)
        assert "Codility-style" in p

    def test_hint_instruction_follows_the_format(self) -> None:
        gca = build_user_prompt(resolve("codesignal_gca"), Difficulty.EASY)
        lc = build_user_prompt(resolve("leetcode"), Difficulty.EASY)
        assert "Do NOT provide `hint_md`" in gca
        assert "Provide a short `hint_md`" in lc

    def test_performance_formats_demand_a_complexity_target(self) -> None:
        p = build_user_prompt(resolve("codility", "performance"), Difficulty.HARD)
        assert "complexity_target" in p

    def test_freeform_is_framed_as_subordinate_to_the_format(self) -> None:
        """It must read as a refinement, not a licence to change the format."""
        p = build_user_prompt(
            resolve("codesignal_gca", freeform="only graph problems"), Difficulty.EASY
        )
        assert "only graph problems" in p
        assert "it does not change the format" in p

    def test_previous_titles_are_excluded(self) -> None:
        p = build_user_prompt(
            resolve("leetcode"), Difficulty.EASY, exclude_titles=["Two Sum"]
        )
        assert "Two Sum" in p and "pick something different" in p


class TestGenerationLoop:
    async def test_accepts_a_good_question_on_the_first_try(self) -> None:
        client = FakeLLM([fixture("two_sum_good")])
        result = await generate_question(client, resolve("leetcode"))
        assert result.accepted
        assert result.question is not None
        assert len(result.question.hidden_tests) >= 10

    async def test_retries_after_a_gate_rejection_and_recovers(self) -> None:
        client = FakeLLM([fixture("two_sum_wrong_oracle"), fixture("two_sum_good")])
        result = await generate_question(client, resolve("leetcode"), max_attempts=3)
        assert result.accepted
        assert [a.outcome for a in result.attempts] == [
            GateOutcome.VISIBLE_MISMATCH,
            GateOutcome.ACCEPTED,
        ]

    async def test_rejection_detail_is_fed_back_to_the_model(self) -> None:
        """A bare retry reproduces the same mistake; a specific complaint fixes it."""
        client = FakeLLM([fixture("two_sum_wrong_oracle"), fixture("two_sum_good")])
        await generate_question(client, resolve("leetcode"), max_attempts=3)
        second_prompt = client.calls[1][1]
        assert "was rejected" in second_prompt
        assert "visible_mismatch" in second_prompt

    async def test_gives_up_after_max_attempts(self) -> None:
        client = FakeLLM([fixture("two_sum_unsolvable")] * 3)
        result = await generate_question(client, resolve("leetcode"), max_attempts=3)
        assert not result.accepted
        assert len(result.attempts) == 3

    async def test_provider_failure_is_recorded_not_raised(self) -> None:
        client = FakeLLM([LLMError("502 from provider"), fixture("two_sum_good")])
        result = await generate_question(client, resolve("leetcode"), max_attempts=2)
        assert result.accepted
        assert result.attempts[0].outcome is GateOutcome.SCHEMA_INVALID

    async def test_reports_every_attempt_for_the_acceptance_rate(self) -> None:
        client = FakeLLM([fixture("two_sum_bad_generator"), fixture("two_sum_good")])
        result = await generate_question(client, resolve("leetcode"), max_attempts=2)
        assert [a.outcome for a in result.attempts] == [
            GateOutcome.NO_HIDDEN_CASES,
            GateOutcome.ACCEPTED,
        ]

    @pytest.mark.parametrize("style", ["leetcode", "codesignal_gca", "codility", "coderbyte"])
    async def test_every_style_can_drive_the_loop(self, style: str) -> None:
        client = FakeLLM([fixture("two_sum_good")])
        result = await generate_question(client, resolve(style))
        assert result.accepted


class TestFakeLLM:
    async def test_exhaustion_is_an_error_not_a_hang(self) -> None:
        from freetcoder.models import GeneratedQuestion

        client = FakeLLM([])
        with pytest.raises(LLMError):
            await client.complete_json(system="s", user="u", schema=GeneratedQuestion)

    def test_loads_named_fixtures(self) -> None:
        assert not FakeLLM.from_fixtures("two_sum_good").exhausted

    def test_missing_fixture_is_a_clear_error(self) -> None:
        with pytest.raises(FileNotFoundError):
            FakeLLM.from_fixtures("no_such_fixture")
