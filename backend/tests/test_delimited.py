"""Delimited plain text instead of JSON.

Source code does not survive a JSON string. Asked for the same Python solution,
three of four models returned the body with no line breaks at all -- the whole
function on one line. Telling them not to did not help. This format removes the
escaping problem rather than detecting it.
"""

from __future__ import annotations

import pytest

from freetcoder.generate.delimited import (
    ParseError,
    parse_bounds,
    parse_cases,
    parse_question,
    parse_sections,
    parse_tests,
)

REPLY = """
=== TITLE ===
Steady Tide Windows

=== STATEMENT ===
Given the readings `levels` and a tolerance `drift`, return the length of the
longest run whose highest and lowest values differ by no more than `drift`.

Return 0 when `levels` is empty.

=== FUNCTION ===
steady_window(levels, drift)

=== SCAFFOLD ===
def steady_window(levels, drift):
    pass

=== SOLUTION ===
def steady_window(levels, drift):
    best = 0
    for i in range(len(levels)):
        lo = hi = levels[i]
        for j in range(i, len(levels)):
            lo = min(lo, levels[j])
            hi = max(hi, levels[j])
            if hi - lo <= drift:
                best = max(best, j - i + 1)
    return best

=== CASE ===
args: {"levels": [4, 6, 5, 9], "drift": 2}
expected: 3
why: the first three span 2

=== CASE ===
args: {"levels": [7], "drift": 0}
expected: 1

"""

TESTS_REPLY = """
=== GENERATOR ===
import json, random
random.seed(3)
for _ in range(12):
    print(json.dumps({"args": {"levels": [1, 2], "drift": 1}}))

=== BRUTE ===
none
"""

BOUNDS_REPLY = """
=== BOUNDS ===
- `0 <= levels.length <= 1000`
- `0 <= drift <= 1000`

=== BOUND ===
name: levels
min_length: 0
max_length: 1000
element_min: -500
element_max: 500

=== BOUND ===
name: drift
min: 0
max: 1000
"""


class TestCodeSurvivesIntact:
    """The whole point: indentation is syntax, and it must arrive unharmed."""

    def test_the_solution_keeps_its_line_breaks(self) -> None:
        draft = parse_question(REPLY)
        assert draft.reference_solution.count("\n") >= 8

    def test_the_solution_keeps_its_indentation(self) -> None:
        draft = parse_question(REPLY)
        assert "\n    best = 0" in draft.reference_solution
        assert "\n            if hi - lo <= drift:" in draft.reference_solution

    def test_it_actually_parses_as_python(self) -> None:
        import ast

        draft = parse_question(REPLY)
        ast.parse(draft.reference_solution)
        ast.parse(draft.scaffold)
        ast.parse(parse_tests(TESTS_REPLY)[0])


class TestTheParts:
    def test_the_signature_gives_the_parameters(self) -> None:
        draft = parse_question(REPLY)
        assert draft.function_name == "steady_window"
        assert draft.parameter_names == ["levels", "drift"]

    def test_typed_parameters_are_stripped_to_names(self) -> None:
        reply = REPLY.replace(
            "steady_window(levels, drift)",
            "steady_window(levels: list[int], drift: int = 0)",
        )
        assert parse_question(reply).parameter_names == ["levels", "drift"]

    def test_repeated_sections_accumulate(self) -> None:
        """Several CASE blocks must not overwrite each other."""
        draft = parse_question(REPLY)
        assert len(draft.visible_tests) == 2
        assert draft.visible_tests[0].expected == 3
        assert draft.visible_tests[1].args == {"levels": [7], "drift": 0}

    def test_the_reason_is_optional(self) -> None:
        draft = parse_question(REPLY)
        assert draft.visible_tests[0].explanation == "the first three span 2"
        assert draft.visible_tests[1].explanation is None

    def test_brute_none_means_none(self) -> None:
        assert parse_tests(TESTS_REPLY)[1] is None

    def test_a_real_brute_force_is_kept(self) -> None:
        reply = TESTS_REPLY.replace(
            "=== BRUTE ===\nnone", "=== BRUTE ===\ndef slow(x):\n    return x"
        )
        assert "def slow" in (parse_tests(reply)[1] or "")

    def test_stray_markdown_fences_are_tolerated(self) -> None:
        """Models fence code even when the markers are the delimiter."""
        reply = REPLY.replace(
            "=== SOLUTION ===\ndef steady_window",
            "=== SOLUTION ===\n```python\ndef steady_window",
        ).replace("    return best\n\n=== CASE ===", "    return best\n```\n\n=== CASE ===")
        draft = parse_question(reply)
        assert draft.reference_solution.startswith("def steady_window")
        assert "```" not in draft.reference_solution


class TestItFailsLoudly:
    def test_a_reply_with_no_markers_is_an_error(self) -> None:
        with pytest.raises(ParseError, match="no `=== SECTION ===` markers"):
            parse_question("Here is a nice question about arrays.")

    def test_a_missing_section_is_named(self) -> None:
        reply = REPLY.replace("=== SOLUTION ===", "=== NOTES ===")
        with pytest.raises(ParseError, match="SOLUTION"):
            parse_question(reply)

    def test_non_json_args_are_rejected(self) -> None:
        with pytest.raises(ParseError, match="args is not JSON"):
            parse_cases("args: levels=[1,2]\nexpected: 1")

    def test_a_case_block_needs_args(self) -> None:
        with pytest.raises(ParseError, match="no worked examples"):
            parse_cases("expected: 3\nwhy: because")


class TestSectionSplitting:
    def test_extra_equals_are_tolerated(self) -> None:
        parsed = parse_sections("==== TITLE ====\nhi\n=== STATEMENT ===\nthere")
        assert parsed.text("title") == "hi"

    def test_names_are_case_insensitive(self) -> None:
        parsed = parse_sections("=== Title ===\nhi")
        assert parsed.text("TITLE") == "hi"


class TestBoundsAsFlatLines:
    """The constraints call was the last thing still asking for JSON, and the
    last thing failing: three delimited runs got the whole question through and
    then lost the `name` on every bound."""

    def test_each_block_becomes_one_bound(self) -> None:
        bounds = parse_bounds(parse_sections(BOUNDS_REPLY).sections["bound"])
        assert [b["name"] for b in bounds] == ["levels", "drift"]

    def test_sizes_and_values_land_in_the_right_fields(self) -> None:
        bounds = parse_bounds(parse_sections(BOUNDS_REPLY).sections["bound"])
        levels, drift = bounds
        assert levels["min_length"] == 0 and levels["max_length"] == 1000
        assert levels["element_min"] == -500 and levels["element_max"] == 500
        assert drift["min"] == 0 and drift["max"] == 1000

    def test_lengths_stay_integers(self) -> None:
        bounds = parse_bounds(parse_sections(BOUNDS_REPLY).sections["bound"])
        assert isinstance(bounds[0]["min_length"], int)

    def test_a_word_where_a_number_belongs_is_skipped_not_fatal(self) -> None:
        bounds = parse_bounds("name: n\nmin: unbounded\nmax: 10")
        assert bounds == [{"name": "n", "max": 10.0}]

    def test_no_bounds_is_an_error(self) -> None:
        with pytest.raises(ParseError, match="no bounds found"):
            parse_bounds("min: 1\nmax: 2")
