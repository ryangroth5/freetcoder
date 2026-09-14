"""Question-as-a-module: lint it, type-check it, import it, call it.

Every serialisation failure measured came from putting code inside a data
format. This asks for a module instead, so the checks are the ones a developer
would run, and each one's own output is what the model is told.
"""

from __future__ import annotations

import pytest

from freetcoder.generate.module import (
    alarming_source,
    extract_code,
    lint_fault,
    module_fault,
    probe_module,
    scaffold_from,
    typecheck_fault,
)

GOOD = '''
TITLE = "Steady Tide Windows"
STATEMENT = """
Given the readings `levels` and a tolerance `drift`, return the length of the
longest run whose highest and lowest readings differ by no more than `drift`.

Example: `levels = [4, 6, 5, 9]`, `drift = 2` returns `3`. Empty input gives 0.
"""
CONSTRAINTS = """
- `0 <= len(levels) <= 1000`
- `0 <= drift <= 1000`
"""


def solution(levels: list[int], drift: int) -> int:
    best = 0
    for i in range(len(levels)):
        lo = hi = levels[i] if i < len(levels) else 0
        for j in range(i, len(levels)):
            lo = min(lo, levels[j])
            hi = max(hi, levels[j])
            if hi - lo <= drift:
                best = max(best, j - i + 1)
    return best


def brute_force(levels: list[int], drift: int) -> int:
    best = 0
    for i in range(len(levels)):
        for j in range(i, len(levels)):
            window = levels[i:j + 1]
            if max(window) - min(window) <= drift:
                best = max(best, len(window))
    return best


def is_valid(levels: list[int], drift: int) -> bool:
    return 0 <= len(levels) <= 1000 and 0 <= drift <= 1000


def generate_cases(rng):
    for _ in range(14):
        n = rng.randint(1, 20)
        yield {"levels": [rng.randint(-50, 50) for _ in range(n)],
               "drift": rng.randint(0, 40)}


EXAMPLES = [
    {"args": {"levels": [4, 6, 5, 9], "drift": 2}, "why": "the first three span 2"},
]
'''


def swap(original: str, new: str) -> str:
    assert original in GOOD, original[:40]
    return GOOD.replace(original, new)


class TestTheCriticsInOrder:
    """Each step reports its own failure, because that is what the model is
    handed on the retry."""

    def test_a_good_module_passes_every_critic(self) -> None:
        fault, payload = module_fault(GOOD, wanted=12)
        assert fault == "", fault
        assert payload is not None
        assert payload["function_name"] == "solution"
        assert payload["parameters"] == ["levels", "drift"]

    def test_an_undefined_name_is_caught_by_ruff(self) -> None:
        broken = swap("    return best\n\n\ndef brute_force",
                      "    return nonexistent_total\n\n\ndef brute_force")
        fault = lint_fault(broken)
        assert "ruff" in fault and "nonexistent_total" in fault

    def test_a_type_error_is_caught_by_pyright(self) -> None:
        broken = swap("def is_valid(levels: list[int], drift: int) -> bool:\n"
                      "    return 0 <= len(levels) <= 1000 and 0 <= drift <= 1000",
                      "def is_valid(levels: list[int], drift: int) -> bool:\n"
                      "    return levels.nonexistent_method()")
        assert "pyright" in typecheck_fault(broken)

    def test_reaching_outside_is_refused(self) -> None:
        assert "reaches outside" in alarming_source(
            "import shutil\nshutil.rmtree('/')\n"
        )
        assert alarming_source(GOOD) == ""


class TestTheProbe:
    def test_it_reports_what_the_module_contains(self) -> None:
        probed = probe_module(GOOD, wanted=12)
        assert probed.ok, f"{probed.step}: {probed.detail}"
        payload = probed.payload or {}
        assert payload["title"] == "Steady Tide Windows"
        assert len(payload["examples"]) == 1  # type: ignore[arg-type]
        assert payload["examples"][0]["expected"] == 3  # type: ignore[index]

    def test_expected_values_come_from_running_the_solution(self) -> None:
        """The model never states them, so it cannot state them wrongly."""
        probed = probe_module(GOOD, wanted=12)
        assert probed.payload is not None
        example = probed.payload["examples"][0]  # type: ignore[index]
        assert "expected" in example
        assert example["expected"] == 3

    def test_a_missing_interface_name_is_named(self) -> None:
        without = GOOD.replace("EXAMPLES = [", "NOT_EXAMPLES = [")
        probed = probe_module(without, wanted=12)
        assert not probed.ok
        assert probed.step == "interface"
        assert "EXAMPLES" in probed.detail

    def test_a_validator_that_ignores_its_arguments_is_refused(self) -> None:
        """Otherwise `return True` passes every case vacuously."""
        lazy = swap(
            "def is_valid(levels: list[int], drift: int) -> bool:\n"
            "    return 0 <= len(levels) <= 1000 and 0 <= drift <= 1000",
            "def is_valid(levels: list[int], drift: int) -> bool:\n"
            "    return True",
        )
        probed = probe_module(lazy, wanted=12)
        assert not probed.ok
        assert probed.step == "is_valid"
        assert "ignores its arguments" in probed.detail

    def test_a_brute_force_that_disagrees_is_caught(self) -> None:
        """Two implementations agreeing is the only evidence either is right."""
        wrong = swap("            if max(window) - min(window) <= drift:\n"
                     "                best = max(best, len(window))",
                     "            if max(window) - min(window) <= drift:\n"
                     "                best = max(best, len(window) + 1)")
        probed = probe_module(wrong, wanted=12)
        assert not probed.ok
        assert probed.step == "brute_force"
        assert "disagree" in probed.detail

    def test_too_few_generated_cases_is_named(self) -> None:
        thin = swap("    for _ in range(14):", "    for _ in range(3):")
        probed = probe_module(thin, wanted=12)
        assert not probed.ok
        assert probed.step == "generate_cases"
        assert "3 case(s)" in probed.detail

    def test_a_case_its_own_validator_rejects_is_caught(self) -> None:
        strict = swap(
            "    return 0 <= len(levels) <= 1000 and 0 <= drift <= 1000",
            "    return 0 <= len(levels) <= 2 and 0 <= drift <= 1000",
        )
        probed = probe_module(strict, wanted=12)
        assert not probed.ok
        assert probed.step in {"generate_cases", "is_valid"}

    def test_a_module_that_never_finishes_is_not_fatal(self) -> None:
        spin = swap(
            "def generate_cases(rng):",
            "def generate_cases(rng):\n    while True:\n        pass",
        )
        probed = probe_module(spin, wanted=12)
        assert not probed.ok
        assert probed.step == "timeout"


class TestDerivation:
    def test_the_scaffold_comes_from_the_signature(self) -> None:
        assert scaffold_from("steady", ["levels", "drift"]) == (
            "def steady(levels, drift):\n    pass\n"
        )

    @pytest.mark.parametrize("wrapped", [
        "```python\nTITLE = 'x'\n```",
        "```\nTITLE = 'x'\n```",
        "TITLE = 'x'",
    ])
    def test_markdown_fences_are_stripped(self, wrapped: str) -> None:
        assert extract_code(wrapped).strip() == "TITLE = 'x'"


class TestTheGateAcceptsWhatWeBuild:
    """The gate stays the arbiter, so this strategy is comparable with the
    others rather than grading itself."""

    def test_a_probed_module_becomes_an_accepted_question(self) -> None:
        import asyncio

        from freetcoder.formats import resolve
        from freetcoder.generate.gate import validate_question
        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty, GateOutcome

        result = asyncio.run(generate_module(
            FakeLLM([GOOD]),
            resolve("leetcode", topics=["sliding window"]),
            difficulty=Difficulty.MEDIUM,
            tries_per_stage=1,
        ))
        assert result.question is not None, result.failed_stage
        report = validate_question(result.question)
        assert report.outcome is GateOutcome.ACCEPTED, report.detail

    def test_the_scaffold_matches_the_solution_signature(self) -> None:
        import asyncio

        from freetcoder.formats import resolve
        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        result = asyncio.run(generate_module(
            FakeLLM([GOOD]), resolve("leetcode"),
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None
        sig = result.question.signatures[0]
        assert sig.function_name == "solution"
        assert sig.scaffold == "def solution(levels, drift):\n    pass\n"

    def test_a_rejected_module_is_revised_not_abandoned(self) -> None:
        """The failing critic's own words go back to the model."""
        import asyncio

        from freetcoder.formats import resolve
        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        broken = GOOD.replace("    return best\n\n\ndef brute_force",
                              "    return undefined_total\n\n\ndef brute_force")
        llm = FakeLLM([broken, GOOD])
        result = asyncio.run(generate_module(
            llm, resolve("leetcode"), difficulty=Difficulty.MEDIUM,
            tries_per_stage=2,
        ))
        assert result.question is not None, result.failed_stage
        assert result.stages[0].attempts == 2
        retry = llm.calls[1][1]
        assert "rejected" in retry
        assert "undefined_total" in retry, "the model must be told what ruff said"


class TestALargePayloadSurvives:
    """The probe's record carries the statement and every generated case.

    At the sandbox's default 64KB output cap it was truncated mid-JSON and read
    as "the module produced no result" -- a true statement about a payload that
    was in fact complete and valid.
    """

    def test_forty_large_cases_still_come_back(self) -> None:
        bulky = GOOD.replace(
            "    for _ in range(14):\n"
            "        n = rng.randint(1, 20)",
            "    for _ in range(40):\n"
            "        n = rng.randint(200, 400)",
        )
        probed = probe_module(bulky, wanted=12)
        assert probed.ok, f"{probed.step}: {probed.detail}"
        assert probed.payload is not None
        assert len(probed.payload["cases"]) == 40  # type: ignore[arg-type]


class TestTheResultCannotBeCorrupted:
    """The probe reports through a file, not stdout.

    stdout is shared with whatever the module prints, and a module that writes
    to the real handle can interleave with the record and make valid JSON
    unparseable -- which is what happened to a complete and correct payload.
    """

    def test_a_module_that_writes_to_the_real_stdout_is_still_read(self) -> None:
        noisy = GOOD.replace(
            "def solution(levels: list[int], drift: int) -> int:",
            "import sys\n\n\ndef solution(levels: list[int], drift: int) -> int:\n"
            "    sys.__stdout__.write('noise that is not json\\n')",
        )
        probed = probe_module(noisy, wanted=12)
        assert probed.ok, f"{probed.step}: {probed.detail}"
        assert probed.payload is not None
        assert probed.payload["title"] == "Steady Tide Windows"

    def test_a_module_printing_a_forged_record_cannot_impersonate_one(self) -> None:
        """The result is a file we name; nothing printed can become it."""
        forger = GOOD.replace(
            "def solution(levels: list[int], drift: int) -> int:",
            "import sys\n\n\ndef solution(levels: list[int], drift: int) -> int:\n"
            "    sys.__stdout__.write('{\"ok\": true, \"title\": \"Forged\"}\\n')",
        )
        probed = probe_module(forger, wanted=12)
        assert probed.ok
        assert probed.payload is not None
        assert probed.payload["title"] == "Steady Tide Windows"
