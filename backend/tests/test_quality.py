"""The quality scorer: what the gate structurally cannot ask.

The gate answers "is this solvable?" by executing things. These metrics answer
"is this any good?", which needs the prose the gate never reads.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from freetcoder.generate.quality import Scorecard, score_question
from freetcoder.models import GeneratedQuestion

FIXTURES = Path(__file__).parent / "fixtures" / "llm"


def load_raw(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def good_question() -> dict[str, Any]:
    """A question that satisfies everything the scorer looks for."""
    d = copy.deepcopy(load_raw("two_sum_good"))
    d["title"] = "Steady Tide Windows"
    d["statement_md"] = (
        "Given an integer array `nums` and an integer `target`, return the two "
        "indices whose values add to `target`.\n\n"
        "Return them in ascending order. When no pair exists, return an empty "
        "list.\n\n"
        "Example 1 - `nums = [2, 7, 11, 15]`, `target = 9` returns `[0, 1]`, "
        "because 2 + 7 is 9."
    )
    d["visible_tests"] = [
        {"args": {"nums": [2, 7, 11, 15], "target": 9}, "expected": [0, 1]},
    ]
    return d


class TestProseMetrics:
    def test_a_complete_statement_scores_complete(self) -> None:
        report = score_question(GeneratedQuestion.model_validate(good_question()))
        assert report.names_all_parameters
        assert report.shows_examples
        assert report.addresses_edge_cases
        assert report.bounds_every_parameter
        assert report.prose_complete

    def test_a_parameter_the_prose_omits_is_caught(self) -> None:
        d = good_question()
        # Every mention, including inside the worked example -- naming it once
        # anywhere is enough, which is the point of the check.
        d["statement_md"] = (
            "Given an integer array `nums` and a goal value, return the two "
            "indices whose values add to the goal, in ascending order. When no "
            "pair exists, return an empty list.\n\n"
            "Example 1 - `nums = [2, 7, 11, 15]` with goal 9 returns `[0, 1]`."
        )
        report = score_question(GeneratedQuestion.model_validate(d))
        assert not report.names_all_parameters
        assert not report.prose_complete

    def test_examples_only_in_the_data_do_not_count(self) -> None:
        """The candidate reads prose; visible_tests is not the statement."""
        d = good_question()
        d["statement_md"] = (
            "Given an integer array `nums` and an integer `target`, return the "
            "two indices whose values add to `target`, in ascending order."
        )
        report = score_question(GeneratedQuestion.model_validate(d))
        assert not report.shows_examples

    def test_missing_bounds_are_caught(self) -> None:
        d = good_question()
        d["constraints"] = [{"name": "nums", "min_length": 2, "max_length": 10}]
        report = score_question(GeneratedQuestion.model_validate(d))
        assert not report.bounds_every_parameter


class TestRecallDetection:
    def test_the_reported_question_is_flagged(self) -> None:
        """LeetCode 974's example, verbatim, is what DeepSeek produced."""
        d = load_raw("recalled_no_statement")
        d["constraints_md"] = "- `1 <= k <= 100`"
        report = score_question(GeneratedQuestion.model_validate(d))
        assert report.recalled_example == "subarray sums divisible by k"
        assert report.looks_recalled

    def test_a_known_title_is_flagged(self) -> None:
        d = good_question()
        d["title"] = "Valid Parentheses"
        assert score_question(GeneratedQuestion.model_validate(d)).looks_recalled

    def test_an_invented_question_is_not_flagged(self) -> None:
        """The check must not punish every array question."""
        d = good_question()
        d["visible_tests"] = [
            {"args": {"nums": [3, 8, 1], "target": 11}, "expected": [0, 1]},
        ]
        d["statement_md"] = d["statement_md"].replace(
            "`nums = [2, 7, 11, 15]`, `target = 9` returns `[0, 1]`, "
            "because 2 + 7 is 9",
            "`nums = [3, 8, 1]`, `target = 11` returns `[0, 1]`",
        )
        report = score_question(GeneratedQuestion.model_validate(d))
        assert not report.looks_recalled


class TestExemplarCopying:
    """Few-shot exemplars risk being copied. Measure it rather than hope."""

    EXEMPLAR = (
        "A tide gauge writes a reading every minute. Given the readings levels "
        "and a tolerance drift, return the length of the longest run of "
        "consecutive readings whose highest and lowest values differ by no "
        "more than drift."
    )

    def test_copying_the_exemplar_scores_high(self) -> None:
        d = good_question()
        d["statement_md"] = self.EXEMPLAR + " Also `nums` and `target`."
        report = score_question(
            GeneratedQuestion.model_validate(d), exemplars=[self.EXEMPLAR]
        )
        assert report.exemplar_overlap > 0.8

    def test_an_unrelated_question_scores_low(self) -> None:
        report = score_question(
            GeneratedQuestion.model_validate(good_question()),
            exemplars=[self.EXEMPLAR],
        )
        assert report.exemplar_overlap < 0.3


class TestScorecard:
    def test_it_aggregates_rates(self) -> None:
        good = score_question(GeneratedQuestion.model_validate(good_question()))
        good.accepted_first_pass = True
        thin = copy.deepcopy(good)
        thin.names_all_parameters = False
        thin.accepted_first_pass = False

        card = Scorecard(variant="X", attempted=4, reports=[good, thin])
        summary = card.summary()
        assert summary["asked"] == 4
        assert summary["accepted"] == 2
        # Acceptance is over everything asked for; prose over what came back.
        assert summary["accept_rate"] == 0.5
        assert summary["names_params"] == 0.5

    def test_an_empty_run_does_not_divide_by_zero(self) -> None:
        summary = Scorecard(variant="X").summary()
        assert summary["accepted"] == 0
        assert summary["accept_rate"] == 0.0

    def test_prose_rates_do_not_flatter_a_variant_that_mostly_fails(self) -> None:
        """One immaculate question out of ten is not a good prompt."""
        good = score_question(GeneratedQuestion.model_validate(good_question()))
        card = Scorecard(variant="X", attempted=10, reports=[good],
                         failures=["reference_failed"] * 9)
        summary = card.summary()
        assert summary["prose_complete"] == 1.0   # of what came back
        assert summary["accept_rate"] == 0.1      # but only a tenth came back


class TestShowingExamplesIsAboutContentNotFormatting:
    """The old matcher required `json.dumps`'s exact rendering as a substring
    -- `[4, 6, 5, 9]`, spaces after the commas included.

    Measured on four well-formed statements, three scored as showing nothing:
    writing `[4,6,5,9]`, laying the example out in prose, or merely wrapping
    the line was enough to fail. It marked a bench question `thin` and sent us
    looking for a prompt problem that may never have existed. A metric that
    punishes formatting is not measuring prose.
    """

    def _question(self, statement: str):
        from freetcoder.models import (
            Difficulty,
            GeneratedQuestion,
            Language,
            Signature,
            TestCase,
        )

        return GeneratedQuestion(
            title="Steady Windows",
            difficulty=Difficulty.MEDIUM,
            statement_md=statement,
            constraints_md="- `0 <= drift <= 1000`",
            signatures=[Signature(
                language=Language.PYTHON, function_name="f",
                scaffold="def f(levels, drift):\n    pass\n",
                reference_solution="def f(levels, drift):\n    return 0\n",
            )],
            visible_tests=[
                TestCase(args={"levels": [4, 6, 5, 9], "drift": 2}, expected=3)
            ],
            hidden_generator_py="print()",
        )

    def _shows(self, statement: str) -> bool:
        from freetcoder.generate.quality import score_question

        return score_question(self._question(statement * 3)).shows_examples

    def test_the_canonical_rendering_still_counts(self) -> None:
        assert self._shows("Given levels = [4, 6, 5, 9] and drift = 2, return 3. ")

    def test_commas_without_spaces_count(self) -> None:
        assert self._shows("Given levels = [4,6,5,9] and drift = 2, return 3. ")

    def test_prose_without_brackets_counts(self) -> None:
        assert self._shows("The levels 4, 6, 5, 9 with drift 2 give 3. ")

    def test_a_wrapped_line_counts(self) -> None:
        assert self._shows("Given levels = [4, 6,\n5, 9] and drift = 2, return 3. ")

    def test_a_statement_showing_nothing_still_fails(self) -> None:
        assert not self._shows(
            "Return the length of the longest stable run of readings. "
        )

    def test_the_wrong_numbers_do_not_count(self) -> None:
        """A lone scalar matching some stray digit used to pass an example that
        showed nothing of the sort."""
        assert not self._shows("Given levels = [1, 2, 3] and drift = 9, return 1. ")

    def test_the_numbers_must_appear_in_order(self) -> None:
        assert not self._shows("The readings 9, 5, 6, 4 with drift 2 give 3. ")
