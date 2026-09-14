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


def python_only():
    """A format offering Python and nothing else.

    The presets offer more than one language -- `leetcode` offers JavaScript
    too -- so a test that does not say what it wants is really testing the
    translation stage by accident.
    """
    from freetcoder.formats import resolve
    from freetcoder.models import Language

    cfg = resolve("leetcode")
    cfg.environment.languages = [Language.PYTHON]
    return cfg


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

        from freetcoder.generate.gate import validate_question
        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty, GateOutcome

        result = asyncio.run(generate_module(
            FakeLLM([GOOD]),
            python_only(),
            difficulty=Difficulty.MEDIUM,
            tries_per_stage=1,
        ))
        assert result.question is not None, result.failed_stage
        report = validate_question(result.question)
        assert report.outcome is GateOutcome.ACCEPTED, report.detail

    def test_the_scaffold_matches_the_solution_signature(self) -> None:
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        result = asyncio.run(generate_module(
            FakeLLM([GOOD]), python_only(),
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None
        sig = result.question.signatures[0]
        assert sig.function_name == "solution"
        assert sig.scaffold == "def solution(levels, drift):\n    pass\n"

    def test_a_rejected_module_is_revised_not_abandoned(self) -> None:
        """The failing critic's own words go back to the model."""
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        broken = GOOD.replace("    return best\n\n\ndef brute_force",
                              "    return undefined_total\n\n\ndef brute_force")
        llm = FakeLLM([broken, GOOD])
        result = asyncio.run(generate_module(
            llm, python_only(), difficulty=Difficulty.MEDIUM,
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


class TestTheGatesBruteForceCheckIsNotVacuous:
    """The gate runs brute_force_py and calls the question's function name in
    it. Handed the module unchanged it called `solution` -- comparing the
    reference against itself and agreeing every time.

    A check that cannot fail is worse than no check, because it reads as
    evidence that something was verified.
    """

    def test_the_function_name_is_bound_to_the_slow_implementation(self) -> None:
        from freetcoder.generate.module import _brute_force_module

        built = _brute_force_module("def solution(x):\n    return x\n", "solution")
        assert built.rstrip().endswith("solution = brute_force")

    def test_the_gate_now_catches_a_disagreeing_brute_force(self) -> None:
        """Bypass the probe and hand the gate what it would have received."""
        import asyncio

        from freetcoder.generate.gate import validate_question
        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty, GateOutcome

        result = asyncio.run(generate_module(
            FakeLLM([GOOD]), python_only(),
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None
        q = result.question
        # Same module, but its brute force now returns something else.
        q.brute_force_py = q.signatures[0].reference_solution.replace(
            "def brute_force(levels: list[int], drift: int) -> int:",
            "def brute_force(levels: list[int], drift: int) -> int:\n    return -99",
        ) + "\nsolution = brute_force\n"
        assert validate_question(q).outcome is GateOutcome.BRUTE_FORCE_DISAGREES


#: The same module, now carrying the three names a question needs beyond the
#: core contract. They are declared unconditionally; whether we *ask* for them
#: is what varies by format.
RICH = GOOD.replace(
    'EXAMPLES = [',
    '''HINT = "Grow a window while the spread holds, then shrink from the left."

COMPLEXITY = "O(n log n)"

CLARIFICATIONS = [
    {
        "question": "What is returned for empty input?",
        "answer": "0 -- there is no run to measure.",
        "probe": {"levels": [], "drift": 3},
    },
    {
        "question": "Does a single reading count as a run?",
        "answer": "Yes, its spread is 0, so it always fits.",
        "probe": {"levels": [7], "drift": 0},
    },
]

EXAMPLES = [''',
)


class TestTheNamesBeyondTheCoreContract:
    """HINT, COMPLEXITY and CLARIFICATIONS: asked for by format, verified by
    execution, and dropped when the format did not want them."""

    def test_the_probe_reports_all_three(self) -> None:
        fault, payload = module_fault(RICH, wanted=12)
        assert fault == "", fault
        assert payload is not None
        assert payload["complexity"] == "O(n log n)"
        assert "window" in str(payload["hint"])
        assert len(payload["clarifications"]) == 2  # type: ignore[arg-type]

    def test_the_expected_value_is_computed_not_taken_from_the_model(self) -> None:
        """A model asked for `expect` would be guessing at its own code.

        The probe runs `solution` on the probe arguments instead, which is the
        same reason hidden cases carry inputs only.
        """
        source = RICH.replace(
            '"probe": {"levels": [], "drift": 3},',
            '"probe": {"levels": [], "drift": 3}, "expect": 99,',
        )
        fault, payload = module_fault(source, wanted=12)
        assert fault == "", fault
        assert payload is not None
        first = payload["clarifications"][0]  # type: ignore[index]
        assert first["expect"] == 0, "the model's 99 must not survive"

    def test_a_probe_the_solution_does_not_accept_is_rejected(self) -> None:
        source = RICH.replace(
            '"probe": {"levels": [7], "drift": 0},',
            '"probe": {"readings": [7], "drift": 0},',
        )
        fault, _ = module_fault(source, wanted=12)
        assert fault.startswith("clarifications:"), fault
        assert "readings" in fault, "the model must be told which argument is wrong"

    def test_a_clarification_without_an_answer_is_rejected(self) -> None:
        source = RICH.replace(
            '"answer": "0 -- there is no run to measure.",', '"answer": "",'
        )
        fault, _ = module_fault(source, wanted=12)
        assert fault.startswith("clarifications:"), fault
        assert "answer" in fault

    def test_a_probe_that_raises_is_rejected(self) -> None:
        source = RICH.replace(
            '"probe": {"levels": [], "drift": 3},',
            '"probe": {"levels": None, "drift": 3},',
        )
        fault, _ = module_fault(source, wanted=12)
        assert fault.startswith("clarifications:"), fault

    def test_the_format_decides_whether_a_hint_survives(self) -> None:
        """The module always declares HINT. A format that gives no support must
        not show one, so the drop happens here rather than being trusted to the
        model's restraint."""
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        def build(*, hints: bool, perf: bool):
            cfg = python_only()
            cfg.generation.give_hints = hints
            cfg.scoring.perf_tests = perf
            return asyncio.run(generate_module(
                FakeLLM([RICH]), cfg,
                difficulty=Difficulty.MEDIUM, tries_per_stage=1,
            ))

        wanted = build(hints=True, perf=True)
        assert wanted.question is not None, wanted.failed_stage
        assert wanted.question.hint_md and "window" in wanted.question.hint_md
        assert wanted.question.complexity_target == "O(n log n)"
        assert len(wanted.question.clarifications) == 2

        bare = build(hints=False, perf=False)
        assert bare.question is not None, bare.failed_stage
        assert bare.question.hint_md is None
        assert bare.question.complexity_target is None

    def test_the_prompt_says_which_names_are_wanted(self) -> None:
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        cfg = python_only()
        cfg.generation.give_hints = False
        llm = FakeLLM([RICH])
        asyncio.run(generate_module(
            llm, cfg, difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        asked = llm.calls[0][1]
        assert 'Leave HINT as ""' in asked
        assert "CLARIFICATIONS" in asked


class TestTheProbeSourceIsValidPython:
    """The probe is a string built with escapes, so a stray `\\n` becomes a real
    newline inside a string literal and the whole probe fails at import -- which
    surfaces as every module being rejected, not as a broken probe."""

    def test_it_compiles(self) -> None:
        from freetcoder.generate.module_probe import PROBE

        compile(PROBE, "<probe>", "exec")


#: A correct TypeScript translation of GOOD's `solution`, in the two sections
#: the translate stage asks for.
TS_GOOD = '''=== SCAFFOLD ===
function solution(levels: number[], drift: number): number {
  return 0;
}
=== SOLUTION ===
function solution(levels: number[], drift: number): number {
  let best = 0;
  for (let i = 0; i < levels.length; i++) {
    let lo = levels[i];
    let hi = levels[i];
    for (let j = i; j < levels.length; j++) {
      lo = Math.min(lo, levels[j]);
      hi = Math.max(hi, levels[j]);
      if (hi - lo <= drift) best = Math.max(best, j - i + 1);
    }
  }
  return best;
}
'''


class TestTranslationIntoTheOtherLanguages:
    """Only `solution` and the scaffold cross the boundary. `generate_cases`,
    `is_valid` and `brute_force` stay Python, because Python is the oracle."""

    def _config(self):
        from freetcoder.models import Language

        cfg = python_only()
        cfg.environment.languages = [Language.PYTHON, Language.TYPESCRIPT]
        return cfg

    def _run(self, replies):
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        return asyncio.run(generate_module(
            FakeLLM(list(replies)), self._config(),
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))

    def test_a_second_language_gets_its_own_verified_signature(self) -> None:
        from freetcoder.models import Language

        result = self._run([GOOD, TS_GOOD])
        assert result.question is not None, result.failed_stage
        langs = {s.language for s in result.question.signatures}
        assert langs == {Language.PYTHON, Language.TYPESCRIPT}
        ts = result.question.signature_for(Language.TYPESCRIPT)
        assert ts is not None and "Math.min" in ts.reference_solution

    def test_the_gate_accepts_a_two_language_question(self) -> None:
        """The end of the point: the gate re-runs the translation against the
        oracle's answers, so this proves the two agree by execution."""
        from freetcoder.generate.gate import validate_question
        from freetcoder.models import GateOutcome

        result = self._run([GOOD, TS_GOOD])
        assert result.question is not None, result.failed_stage
        report = validate_question(
            result.question, languages=self._config().environment.languages
        )
        assert report.outcome is GateOutcome.ACCEPTED, report.detail

    def test_a_translation_that_disagrees_is_rejected(self) -> None:
        """A wrong translation must not reach the gate as a half-built
        question -- it fails at its own stage, named, so the failure is
        attributable."""
        wrong = TS_GOOD.replace("best = Math.max(best, j - i + 1)", "best = 99")
        result = self._run([GOOD, wrong])
        assert result.question is None
        assert result.failed_stage == "translate:typescript"
        assert result.stages[-1].error

    def test_a_reply_without_the_sections_is_rejected(self) -> None:
        result = self._run([GOOD, "here you go:\nfunction solution() {}"])
        assert result.question is None
        assert result.failed_stage == "translate:typescript"

    def test_a_single_language_format_asks_for_no_translation(self) -> None:
        """One call, not two. The translation is not free."""
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        llm = FakeLLM([GOOD])
        result = asyncio.run(generate_module(
            llm, python_only(),
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None, result.failed_stage
        assert len(llm.calls) == 1


class TestFencesAreToleratedInEveryLanguage:
    """Models fence code even when asked for a bare file.

    An earlier `extract_code` matched only ```python, so a translation fenced
    ```javascript kept its backticks, went to Node as source, and came back a
    SyntaxError blamed on the model's translation. Measured: one of three
    deepseek-chat questions was thrown away for it.
    """

    def test_a_python_fence_is_stripped(self) -> None:
        assert extract_code("```python\nx = 1\n```") == "x = 1\n"

    def test_any_language_tag_is_stripped(self) -> None:
        for tag in ("javascript", "typescript", "ts", "js", ""):
            assert extract_code(f"```{tag}\nlet x = 1;\n```") == "let x = 1;\n"

    def test_an_unterminated_fence_is_stripped(self) -> None:
        """A model that opens a fence and runs out of budget should not cost a
        whole question."""
        assert extract_code("```javascript\nlet x = 1;") == "let x = 1;\n"

    def test_bare_code_is_untouched(self) -> None:
        assert extract_code("let x = 1;") == "let x = 1;\n"

    def test_a_fenced_translation_survives_the_stage(self) -> None:
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty, Language

        cfg = python_only()
        cfg.environment.languages = [Language.PYTHON, Language.TYPESCRIPT]
        head, _, body = TS_GOOD.partition("=== SOLUTION ===\n")
        fenced = f"{head}=== SOLUTION ===\n```typescript\n{body.strip()}\n```\n"
        result = asyncio.run(generate_module(
            FakeLLM([GOOD, fenced]), cfg,
            difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None, result.failed_stage
        ts = result.question.signature_for(Language.TYPESCRIPT)
        assert ts is not None and "```" not in ts.reference_solution


class TestTheHiddenCasesAreNotSilentlyTruncated:
    """Forty cases of a thousand elements is 200KB of generator output.

    At the default 64KB cap it was cut mid-line and yielded fourteen cases
    with no error: the question was served, graded on a third of the hidden
    tests it promised, and nothing reported it. Truncation that fails loudly
    is a bug; truncation that succeeds quietly is a wrong grade.
    """

    def test_a_large_case_set_survives_the_generator(self) -> None:
        from freetcoder.generate.gate import GENERATOR_LIMITS
        from freetcoder.generate.module import _replay_generator
        from freetcoder.runner import run_python

        cases = [{"nums": list(range(1000)), "k": i} for i in range(40)]
        result = run_python(_replay_generator(cases), limits=GENERATOR_LIMITS)
        assert result.verdict.value == "ok", result.stderr[:200]
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert len(lines) == 40, f"only {len(lines)} of 40 cases survived"

    def test_the_generator_never_parses_the_cases_into_objects(self) -> None:
        """The bug this guards was a shape, not a size.

        Embedding the cases as a Python *literal* made the interpreter build
        millions of boxed integers before printing them straight back out as
        JSON, and forty cases of a hundred thousand elements died with
        MemoryError inside the generator's limits -- surfacing as
        `generator_failed`, a question thrown away for being as large as the
        format asked it to be.

        Asserted structurally rather than by reproducing the megabytes: the
        data must appear exactly once, inside a single string constant. That
        is the property that makes the size irrelevant, and checking it costs
        nothing.
        """
        import ast

        from freetcoder.generate.module import _replay_generator

        tree = ast.parse(_replay_generator([{"nums": [1, 2, 3], "k": 4}]))
        constants = [
            node for node in ast.walk(tree) if isinstance(node, ast.Constant)
        ]
        assert all(isinstance(c.value, str) for c in constants), (
            "the cases were parsed into Python objects; they are already JSON"
        )
        assert not [
            n for n in ast.walk(tree) if isinstance(n, ast.List | ast.Dict)
        ], "a data literal the interpreter has to build"

    def test_trimming_never_drops_below_what_was_promised(self) -> None:
        """A perf question is supposed to generate large inputs, so "too big"
        must not mean "fewer hidden tests than the question claims"."""
        from freetcoder.generate.module import trim_cases

        over = [{"nums": list(range(50))} for _ in range(30)]
        assert len(trim_cases(over, keep_at_least=12, budget=100)) == 12

    def test_trimming_keeps_everything_that_fits(self) -> None:
        from freetcoder.generate.module import trim_cases

        small = [{"n": i} for i in range(40)]
        assert len(trim_cases(small, keep_at_least=12, budget=1_000_000)) == 40

    def test_trimming_stops_once_the_budget_is_spent(self) -> None:
        from freetcoder.generate.module import trim_cases

        cases = [{"n": "x" * 100} for _ in range(40)]
        kept = trim_cases(cases, keep_at_least=2, budget=500)
        assert 2 <= len(kept) < 40


class TestARejectedModuleIsRevisedNotRewritten:
    """Measured, a module costs between one and twenty minutes of provider
    time, almost all of it waiting. Throwing that away because one validator
    complained is the expensive way to fix a cheap problem."""

    def _run(self, replies, **kw):
        import asyncio

        from freetcoder.generate.module import generate_question_as_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        llm = FakeLLM(list(replies))
        result = asyncio.run(generate_question_as_module(
            llm, python_only(), difficulty=Difficulty.MEDIUM,
            check_sufficiency=False, **kw,
        ))
        return llm, result

    def test_the_gates_complaint_goes_back_with_the_module(self) -> None:
        """A module claiming O(n) for an O(n^2) solution is rejected as
        `perf_not_discriminating` every time, so this pins the revision path
        rather than hoping the gate happens to object."""
        import asyncio

        from freetcoder.generate.module import generate_question_as_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        lying = GOOD.replace(
            "EXAMPLES = [", 'COMPLEXITY = "O(n)"\n\nEXAMPLES = [', 1
        )
        assert lying != GOOD, "the fixture did not change"
        cfg = python_only()
        cfg.scoring.perf_tests = True

        llm = FakeLLM([lying, lying])
        result = asyncio.run(generate_question_as_module(
            llm, cfg, difficulty=Difficulty.MEDIUM, max_attempts=1,
            check_sufficiency=False, repair_rounds=1,
        ))
        assert result.question is None, "a question claiming a bound it misses was served"
        assert len(llm.calls) == 2, "the rejection must be answered by a revision"
        second = llm.calls[1][1]
        assert "def solution" in second, "the module itself must go back"
        assert "not actually enforced" in second, "the gate's own words must go back"

    def test_it_starts_over_once_the_rounds_are_spent(self) -> None:
        """Revision is not unbounded: a model that cannot fix its own module
        must not spend the whole budget failing the same way."""
        llm, result = self._run([GOOD], max_attempts=1, repair_rounds=0)
        assert result.question is not None, [a.detail for a in result.attempts]
        assert len(llm.calls) == 1

    def test_a_revision_prompt_carries_the_previous_module(self) -> None:
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        llm = FakeLLM([GOOD])
        asyncio.run(generate_module(
            llm, python_only(), difficulty=Difficulty.MEDIUM, tries_per_stage=1,
            revise_from=("# the old module\nTITLE = 'x'\n", "the gate said no"),
        ))
        asked = llm.calls[0][1]
        assert "# the old module" in asked
        assert "the gate said no" in asked
        assert "Fix exactly that" in asked


class TestSuppliedProseGoesThroughTheSamePipeline:
    """"Bring your own" is a longer brief, not a second strategy.

    `describe` mode already folded into `_brief` as one line. Pasted prose is
    the same input with more words in it, and forcing it down the monolithic
    path put the strategy that measured zero acceptances on the main road for
    anyone who pasted a problem.
    """

    def _imported(self, text: str):
        from freetcoder.formats import resolve
        from freetcoder.models import Language

        cfg = resolve("leetcode", import_text=text)
        cfg.environment.languages = [Language.PYTHON]
        return cfg

    def test_the_candidates_text_reaches_the_model(self) -> None:
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        llm = FakeLLM([GOOD])
        cfg = self._imported("find the longest run of readings within a drift")
        result = asyncio.run(generate_module(
            llm, cfg, difficulty=Difficulty.MEDIUM, tries_per_stage=1,
        ))
        assert result.question is not None, result.failed_stage
        asked = llm.calls[0][1]
        assert "longest run of readings within a drift" in asked
        assert "The candidate's text" in asked
        assert "Adapting a question the candidate supplied" in asked

    def test_it_is_not_forced_onto_the_monolithic_path(self) -> None:
        """The whole point: an imported question is a module like any other."""
        import asyncio

        from freetcoder.llm import FakeLLM
        from freetcoder.service import obtain_question
        from freetcoder.settings import get_settings
        from freetcoder.storage import Storage

        async def run():
            store = Storage(None)
            await store.connect()
            try:
                return await obtain_question(
                    store, FakeLLM([GOOD]),
                    self._imported("something about tides"), 0, max_attempts=1,
                )
            finally:
                await store.close()

        import os

        os.environ["FREETCODER_GENERATION_STRATEGY"] = "module"
        get_settings.cache_clear()
        try:
            got = asyncio.run(run())
        finally:
            os.environ["FREETCODER_GENERATION_STRATEGY"] = "monolithic"
            get_settings.cache_clear()

        assert got is not None, "a pasted problem produced nothing"
        assert got[1].question.title == "Steady Tide Windows"

    def test_a_pasted_published_problem_is_still_caught(self) -> None:
        """Pasting "two sum" is precisely how a published problem gets in, so
        the recall check has to apply to imported questions too."""
        from freetcoder.generate.quality import score_question
        from freetcoder.models import (
            Difficulty,
            GeneratedQuestion,
            Language,
            Signature,
            TestCase,
        )

        q = GeneratedQuestion(
            title="Two Sum",
            difficulty=Difficulty.EASY,
            statement_md="Given `nums` and a `target`, return the two indices. " * 3,
            constraints_md="- `2 <= len(nums)`",
            signatures=[Signature(
                language=Language.PYTHON, function_name="two_sum",
                scaffold="def two_sum(nums, target):\n    pass\n",
                reference_solution="def two_sum(nums, target):\n    return [0, 1]\n",
            )],
            visible_tests=[TestCase(args={"nums": [2, 7, 11, 15], "target": 9},
                                    expected=[0, 1])],
            hidden_generator_py="print()",
        )
        report = score_question(q)
        assert report.looks_recalled
        assert report.recalled_example == "two sum"


class TestTopicsSurviveTheModulePath:
    """The monolithic path let the model name the techniques; this one dropped
    them, so every question generated without a chosen concentration arrived
    unlabelled."""

    def _run(self, source: str, topics: list[str] | None = None):
        import asyncio

        from freetcoder.generate.module import generate_module
        from freetcoder.llm import FakeLLM
        from freetcoder.models import Difficulty

        cfg = python_only()
        cfg.generation.topics = list(topics or [])
        return asyncio.run(generate_module(
            FakeLLM([source]), cfg, difficulty=Difficulty.MEDIUM,
            tries_per_stage=1,
        ))

    def test_the_model_names_them_when_nobody_else_did(self) -> None:
        source = GOOD.replace(
            'EXAMPLES = [', 'TOPICS = ["sliding window", "arrays"]\n\nEXAMPLES = [', 1
        )
        result = self._run(source)
        assert result.question is not None, result.failed_stage
        assert result.question.topics == ["sliding window", "arrays"]

    def test_the_candidates_concentration_wins(self) -> None:
        """They asked for a subject; the model does not get to overrule it."""
        source = GOOD.replace(
            'EXAMPLES = [', 'TOPICS = ["something else"]\n\nEXAMPLES = [', 1
        )
        result = self._run(source, topics=["dynamic programming"])
        assert result.question is not None, result.failed_stage
        assert result.question.topics == ["dynamic programming"]

    def test_a_module_naming_none_still_works(self) -> None:
        result = self._run(GOOD)
        assert result.question is not None, result.failed_stage
        assert result.question.topics == []
