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

        summary = Scorecard(variant="X", reports=[good, thin]).summary()
        assert summary["questions"] == 2
        assert summary["first_pass"] == 0.5
        assert summary["names_params"] == 0.5

    def test_an_empty_run_does_not_divide_by_zero(self) -> None:
        assert Scorecard(variant="X").summary()["questions"] == 0
