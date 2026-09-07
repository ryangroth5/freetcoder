"""Statement sufficiency: can the question be solved from what you are given?

Every other check validates the question against itself. None of them read the
statement, so a question can be internally perfect and still underivable from
its own prose -- which is exactly what happened with "Most Frequent Word".
"""

from __future__ import annotations

import json

import pytest

from freetcoder.formats import resolve
from freetcoder.generate import generate_question
from freetcoder.generate.gate import validate_question
from freetcoder.generate.sufficiency import (
    CandidateSolution,
    build_solver_prompt,
    check_statement_sufficiency,
)
from freetcoder.llm import FakeLLM, LLMError
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import GateOutcome, GeneratedQuestion, Language

#: A reasonable reading of vague prose: split on whitespace, respect case.
NAIVE_SOLVER = CandidateSolution(
    code=(
        "from collections import Counter\n\n"
        "def most_frequent_word(message):\n"
        "    words = message.split()\n"
        "    if not words:\n"
        "        return ''\n"
        "    return Counter(words).most_common(1)[0][0]\n"
    ),
    assumptions="I assumed words are whitespace-separated and case matters.",
)

#: What the specified version implies: letters only, case folded, earliest tie.
CORRECT_SOLVER = CandidateSolution(
    code=(
        "import re\n"
        "from collections import Counter\n\n"
        "def most_frequent_word(message):\n"
        "    words = re.findall(r'[a-z]+', message.lower())\n"
        "    if not words:\n"
        "        return ''\n"
        "    counts = Counter(words)\n"
        "    best = max(counts.values())\n"
        "    for w in words:\n"
        "        if counts[w] == best:\n"
        "            return w\n"
        "    return ''\n"
    ),
)


def load(name: str) -> GeneratedQuestion:
    return GeneratedQuestion.model_validate_json(
        (FIXTURE_DIR / f"{name}.json").read_text()
    )


def fixture(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


def hidden_for(q: GeneratedQuestion):
    report = validate_question(q)
    assert report.accepted, report.detail
    return report.hidden_cases


class TestTheSolverSeesOnlyWhatTheCandidateSees:
    def test_the_prompt_carries_the_statement_and_examples(self) -> None:
        prompt = build_solver_prompt(load("most_frequent_word"), Language.PYTHON)
        assert "support inbox" in prompt
        assert "chased the cat" in prompt

    def test_it_carries_the_clarifications(self) -> None:
        prompt = build_solver_prompt(load("most_frequent_word"), Language.PYTHON)
        assert "Does capitalisation matter?" in prompt

    def test_it_never_carries_the_reference(self) -> None:
        """Solving with the answer in hand would prove nothing."""
        q = load("most_frequent_word")
        sig = q.signature_for(Language.PYTHON)
        assert sig is not None
        sig.reference_solution = "# SOLVER_MUST_NOT_SEE_THIS\n" + sig.reference_solution

        prompt = build_solver_prompt(q, Language.PYTHON)
        assert "SOLVER_MUST_NOT_SEE_THIS" not in prompt
        assert "re.findall" not in prompt

    def test_it_never_carries_hidden_cases(self) -> None:
        prompt = build_solver_prompt(load("most_frequent_word"), Language.PYTHON)
        assert "hidden" not in prompt.lower()


class TestDetectingAnUnderSpecifiedStatement:
    async def test_a_vague_statement_is_caught(self) -> None:
        """The prose never says punctuation splits words or that case folds,
        while the examples quietly rely on both."""
        q = load("most_frequent_word_vague")
        report = await check_statement_sufficiency(
            FakeLLM([NAIVE_SOLVER]), q, hidden_for(q)
        )
        assert report is not None
        assert report.outcome is GateOutcome.STATEMENT_INSUFFICIENT

    async def test_the_rejection_is_actionable(self) -> None:
        q = load("most_frequent_word_vague")
        report = await check_statement_sufficiency(
            FakeLLM([NAIVE_SOLVER]), q, hidden_for(q)
        )
        assert report is not None
        # It names the disagreement and what the attempt assumed.
        assert "intended solution gives" in report.detail
        assert "case matters" in report.detail
        # And it is worded as a suspicion: a weaker model is also possible.
        assert "may be under-specified" in report.detail

    async def test_a_specified_statement_passes(self) -> None:
        q = load("most_frequent_word")
        report = await check_statement_sufficiency(
            FakeLLM([CORRECT_SOLVER]), q, hidden_for(q)
        )
        assert report is None

    async def test_the_gate_alone_accepts_the_vague_version(self) -> None:
        """This is why the check exists: nothing else reads the statement."""
        assert validate_question(load("most_frequent_word_vague")).accepted


class TestItDoesNotBlameTheStatementUnfairly:
    async def test_a_provider_failure_is_inconclusive_not_failing(self) -> None:
        """A provider problem is not the question's fault."""
        q = load("most_frequent_word")
        report = await check_statement_sufficiency(
            FakeLLM([LLMError("provider down")]), q, hidden_for(q)
        )
        assert report is None

    async def test_a_solution_that_does_not_run_is_inconclusive(self) -> None:
        """That says more about the attempt than about the statement."""
        q = load("most_frequent_word")
        broken = CandidateSolution(code="def most_frequent_word(message):\n    syntax(")
        report = await check_statement_sufficiency(FakeLLM([broken]), q, hidden_for(q))
        assert report is None

    async def test_out_of_constraint_cases_are_excluded(self) -> None:
        """A failure on impossible input blames the statement for the
        generator's fault."""
        from freetcoder.models import ParamConstraint, TestCase

        q = load("most_frequent_word")
        q.constraints = [ParamConstraint(name="message", max_length=5)]
        impossible = [TestCase(args={"message": "far too long to be allowed"},
                               expected="wrong")]
        report = await check_statement_sufficiency(
            FakeLLM([CORRECT_SOLVER]), q, impossible
        )
        assert report is None

    async def test_no_hidden_cases_means_no_verdict(self) -> None:
        report = await check_statement_sufficiency(
            FakeLLM([CORRECT_SOLVER]), load("most_frequent_word"), []
        )
        assert report is None


class TestPipelineIntegration:
    async def test_a_vague_question_is_rejected_then_repaired(self) -> None:
        from freetcoder.generate.repair import QuestionPatch
        from freetcoder.models import Clarification

        client = FakeLLM([
            fixture("most_frequent_word_vague"),
            NAIVE_SOLVER,                       # solves it naively: mismatch
            QuestionPatch(                      # the repair says more
                target="statement",
                statement_md=(
                    "Given a `message`, return the word that appears most often. "
                    "Words are runs of letters; anything else separates them, and "
                    "capitalisation is ignored. Ties go to the earliest word.\n"
                ),
                clarifications=[
                    Clarification(question="Does capitalisation matter?",
                                  answer="No.", probe={"message": "Hello hello"},
                                  expect="hello"),
                ],
            ),
            CORRECT_SOLVER,                     # now derivable
        ])
        result = await generate_question(client, resolve("leetcode"))

        assert result.accepted, [a.detail for a in result.attempts]
        assert GateOutcome.STATEMENT_INSUFFICIENT in [
            a.outcome for a in result.attempts
        ]

    async def test_the_check_can_be_turned_off(self) -> None:
        client = FakeLLM([fixture("most_frequent_word_vague")])
        result = await generate_question(
            client, resolve("leetcode"), check_sufficiency=False
        )
        assert result.accepted
        assert not any(
            a.outcome is GateOutcome.STATEMENT_INSUFFICIENT for a in result.attempts
        )

    @pytest.mark.parametrize("flag", [True, False])
    async def test_a_good_question_passes_either_way(self, flag: bool) -> None:
        client = FakeLLM([fixture("most_frequent_word"), CORRECT_SOLVER])
        result = await generate_question(
            client, resolve("leetcode"), check_sufficiency=flag
        )
        assert result.accepted
