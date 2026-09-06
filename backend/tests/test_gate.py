"""Solvability-gate tests.

Each fixture is a question broken in one specific way. The gate must accept
exactly the good one, and must say *why* it rejected the others -- a gate that
rejects everything is as useless as one that accepts everything.

All offline: fixtures are replayed, no API key, no network.
"""

from __future__ import annotations

import json

import pytest

from freetcoder.generate.gate import validate_question
from freetcoder.generate.harness import (
    RECORD_MARKER,
    decode_results,
    encode_cases,
    values_equal,
)
from freetcoder.llm.fake import FIXTURE_DIR
from freetcoder.models import GateOutcome, GeneratedQuestion, TestCase


def load(name: str) -> GeneratedQuestion:
    return GeneratedQuestion.model_validate_json((FIXTURE_DIR / f"{name}.json").read_text())


class TestAcceptsValidQuestion:
    def test_good_question_is_accepted(self) -> None:
        report = validate_question(load("two_sum_good"))
        assert report.outcome is GateOutcome.ACCEPTED, report.detail

    def test_hidden_cases_get_reference_computed_answers(self) -> None:
        report = validate_question(load("two_sum_good"))
        assert len(report.hidden_cases) >= 10
        # Expected values must be filled in by us, never left as the model's.
        assert all(c.expected is not None for c in report.hidden_cases)

    def test_hidden_answers_are_actually_correct(self) -> None:
        """Spot-check the oracle independently of the harness that produced it."""
        report = validate_question(load("two_sum_good"))
        for case in report.hidden_cases[:5]:
            nums = case.args["nums"]
            target = case.args["target"]
            i, j = case.expected  # type: ignore[misc]
            assert nums[i] + nums[j] == target


class TestRejectsBrokenQuestions:
    @pytest.mark.parametrize(
        ("fixture", "expected"),
        [
            ("two_sum_wrong_oracle", GateOutcome.VISIBLE_MISMATCH),
            ("two_sum_unsolvable", GateOutcome.REFERENCE_FAILED),
            ("two_sum_bad_generator", GateOutcome.NO_HIDDEN_CASES),
            ("two_sum_brute_disagrees", GateOutcome.BRUTE_FORCE_DISAGREES),
        ],
    )
    def test_rejects_with_the_right_reason(
        self, fixture: str, expected: GateOutcome
    ) -> None:
        report = validate_question(load(fixture))
        assert report.outcome is expected, f"got {report.outcome}: {report.detail}"
        assert report.detail, "a rejection must explain itself"

    def test_perf_target_must_be_enforced_by_the_tests(self) -> None:
        """Claiming O(n) while a quadratic solution passes everything is a lie."""
        report = validate_question(load("two_sum_perf_not_enforced"))
        assert report.outcome is GateOutcome.PERF_NOT_DISCRIMINATING

    def test_missing_language_signature_is_rejected(self) -> None:
        from freetcoder.models import Language

        report = validate_question(load("two_sum_good"), language=Language.GO)
        assert report.outcome is GateOutcome.SCHEMA_INVALID


class TestGateIsRobust:
    def test_hostile_generator_cannot_hang_the_gate(self) -> None:
        q = load("two_sum_good")
        q.hidden_generator_py = "while True: pass"
        assert validate_question(q).outcome is GateOutcome.GENERATOR_FAILED

    def test_generator_output_is_bounded(self) -> None:
        """A generator emitting millions of cases must not blow up the gate."""
        q = load("two_sum_good")
        q.hidden_generator_py = (
            "import json\n"
            "for _ in range(100000):\n"
            "    print(json.dumps({'args': {'nums': [1, 2], 'target': 3}}))\n"
        )
        report = validate_question(q)
        assert len(report.hidden_cases) <= 40

    def test_reference_that_loops_forever_is_caught(self) -> None:
        q = load("two_sum_good")
        q.signatures[0].reference_solution = (
            "def two_sum(nums, target):\n    while True: pass\n"
        )
        assert validate_question(q).outcome is GateOutcome.REFERENCE_FAILED


class TestHarnessPlumbing:
    def test_roundtrip(self) -> None:
        cases = [TestCase(args={"nums": [1, 2], "target": 3}, expected=[0, 1])]
        assert json.loads(encode_cases(cases).strip())["args"]["target"] == 3

    def test_decode_skips_truncated_trailing_line(self) -> None:
        # Records must carry the marker; see tests/test_harness.py for why.
        good = json.dumps({RECORD_MARKER: 1, "ok": True, "value": 1, "ms": 0.1})
        assert len(decode_results(f'{good}\n{{"ok": true, "val')) == 1

    @pytest.mark.parametrize(
        ("a", "b", "same"),
        [
            ([1, 2], [1, 2], True),
            ((1, 2), [1, 2], True),      # JSON has no tuples
            (1, 1.0, True),
            (True, 1, False),            # bool is not int here
            ([1, 2], [2, 1], False),
        ],
    )
    def test_value_comparison(self, a: object, b: object, same: bool) -> None:
        assert values_equal(a, b) is same
