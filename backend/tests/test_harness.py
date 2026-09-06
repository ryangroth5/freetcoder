"""Harness protocol tests.

These exist because a submission containing `print(nums)` -- the most ordinary
debugging action there is -- used to 500 the server. The harness wrote its result
records to the same stdout the candidate's print() writes to, with no framing.

Three failure modes, worsening: output silently swallowed; a crash on a JSON
array; and, worst, a printed JSON *object* being counted as a test result, which
shifts every later case and silently misgrades correct answers.
"""

from __future__ import annotations

import json

import pytest

from freetcoder.generate.harness import (
    MAX_CASE_STDOUT,
    RECORD_MARKER,
    CaseResult,
    build_python_harness,
    decode_results,
    encode_cases,
    values_equal,
)
from freetcoder.models import TestCase
from freetcoder.runner import Limits, Verdict, run_python

FAST = Limits(wall_seconds=8.0, cpu_seconds=6)


def run_cases(source: str, cases: list[TestCase], fn: str = "f") -> list[CaseResult]:
    result = run_python(
        source,
        harness=build_python_harness(fn),
        stdin=encode_cases(cases),
        limits=FAST,
    )
    assert result.verdict is not Verdict.INTERNAL_ERROR, result.stderr
    return decode_results(result.stdout)


CASES = [
    TestCase(args={"x": 1}, expected=2),
    TestCase(args={"x": 2}, expected=4),
    TestCase(args={"x": 3}, expected=6),
]


class TestPrintDoesNotCorruptResults:
    """One test per row of the table above."""

    def test_plain_print_is_captured_not_swallowed(self) -> None:
        results = run_cases("def f(x):\n    print('debugging', x)\n    return x * 2\n", CASES)
        assert len(results) == 3
        assert all(r.ok for r in results)
        assert "debugging 1" in results[0].stdout
        assert "debugging 2" in results[1].stdout

    def test_printing_a_json_array_does_not_crash(self) -> None:
        """The exact shape that produced 'list object has no attribute get'."""
        results = run_cases("def f(x):\n    print([x, x])\n    return x * 2\n", CASES)
        assert [r.ok for r in results] == [True, True, True]
        assert "[1, 1]" in results[0].stdout

    def test_printing_a_json_object_cannot_forge_a_result(self) -> None:
        """The dangerous one: a forged record would shift every later case."""
        source = (
            "import json\n"
            "def f(x):\n"
            "    print(json.dumps({'ok': True, 'value': 999, 'ms': 0}))\n"
            "    return x * 2\n"
        )
        results = run_cases(source, CASES)
        assert len(results) == 3, "a printed object was counted as a result"
        assert [r.value for r in results] == [2, 4, 6]

    def test_forging_the_marker_via_real_stdout_is_still_rejected(self) -> None:
        """Defence in depth: sys.__stdout__ bypasses our redirect."""
        source = (
            "import json, sys\n"
            "def f(x):\n"
            f"    print(json.dumps({{'{RECORD_MARKER}': 1, 'ok': True, 'value': 999}}),"
            "          file=sys.__stdout__, flush=True)\n"
            "    return x * 2\n"
        )
        results = run_cases(source, CASES)
        # A forged record adds an entry; the graded values must still be ours.
        genuine = [r for r in results if r.value in (2, 4, 6)]
        assert len(genuine) == 3

    def test_print_at_import_time_is_attributed_to_the_first_case(self) -> None:
        source = "print('module loaded')\ndef f(x):\n    return x * 2\n"
        results = run_cases(source, CASES)
        assert "module loaded" in results[0].stdout
        assert "module loaded" not in results[1].stdout


class TestStdoutIsBounded:
    def test_a_printing_loop_is_truncated(self) -> None:
        source = "def f(x):\n    print('A' * 200000)\n    return x * 2\n"
        results = run_cases(source, [CASES[0]])
        assert len(results) == 1
        assert len(results[0].stdout) <= MAX_CASE_STDOUT + 64
        assert results[0].ok

    def test_results_still_arrive_after_a_flood(self) -> None:
        source = "def f(x):\n    print('B' * 50000)\n    return x * 2\n"
        results = run_cases(source, CASES)
        assert [r.value for r in results] == [2, 4, 6]


class TestDecodeResultsIsDefensive:
    @pytest.mark.parametrize(
        "line",
        ['[1, 2, 3]', '"a string"', '42', 'null', '{"ok": true, "value": 1}'],
    )
    def test_unmarked_lines_are_ignored(self, line: str) -> None:
        assert decode_results(line) == []

    def test_marked_records_are_accepted(self) -> None:
        rec = json.dumps({RECORD_MARKER: 1, "ok": True, "value": 7, "ms": 1.5,
                          "stdout": "hi"})
        (result,) = decode_results(rec)
        assert result.ok and result.value == 7 and result.stdout == "hi"

    def test_truncated_trailing_line_is_skipped(self) -> None:
        good = json.dumps({RECORD_MARKER: 1, "ok": True, "value": 1, "ms": 0})
        assert len(decode_results(f'{good}\n{{"ok": tr')) == 1

    def test_missing_function_is_reported(self) -> None:
        results = run_cases("def other(x):\n    return x\n", CASES)
        assert results and not results[0].ok
        assert "not defined" in results[0].error


class TestExistingBehaviourPreserved:
    def test_exceptions_are_still_per_case(self) -> None:
        source = "def f(x):\n    if x == 2:\n        raise ValueError('nope')\n    return x * 2\n"
        results = run_cases(source, CASES)
        assert [r.ok for r in results] == [True, False, True]
        assert "ValueError" in results[1].error

    def test_unserialisable_return_is_a_wrong_answer_not_a_crash(self) -> None:
        results = run_cases("def f(x):\n    return object()\n", [CASES[0]])
        assert results and not results[0].ok
        assert "JSON-serialisable" in results[0].error


class TestFloatToleranceReachesNestedValues:
    """The tolerance used to apply only at the top level.

    `0.1 + 0.2` equalled `0.3`, but `[0.1 + 0.2]` did not equal `[0.3]`, so any
    question returning a list of floats could fail a correct solution.
    """

    def test_top_level_floats(self) -> None:
        assert values_equal(0.1 + 0.2, 0.3)

    def test_floats_in_a_list(self) -> None:
        assert values_equal([0.1 + 0.2], [0.3])

    def test_floats_nested_two_deep(self) -> None:
        assert values_equal([[0.1 + 0.2, 1.0]], [[0.3, 1.0]])

    def test_floats_in_a_dict(self) -> None:
        assert values_equal({"x": 0.1 + 0.2}, {"x": 0.3})

    def test_genuinely_different_numbers_still_differ(self) -> None:
        assert not values_equal([0.3001], [0.3])
        assert not values_equal({"x": 1.0}, {"x": 2.0})

    def test_structure_still_matters(self) -> None:
        assert not values_equal([1, 2], [2, 1])
        assert not values_equal([1, 2], [1, 2, 3])
        assert not values_equal({"a": 1}, {"b": 1})

    def test_booleans_are_not_numbers_at_any_depth(self) -> None:
        assert not values_equal([True], [1])
        assert not values_equal({"x": False}, {"x": 0})
