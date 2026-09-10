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
from freetcoder.models import (
    GateOutcome,
    GeneratedQuestion,
    Language,
    ParamConstraint,
    TestCase,
)
from freetcoder.runner import Verdict


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
    def test_hostile_generator_cannot_hang_the_gate(self, quick_kill: None) -> None:
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

    def test_reference_that_loops_forever_is_caught(self, quick_kill: None) -> None:
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


class TestFailuresExplainThemselves:
    """A rejection with an empty reason made the retry loop blind.

    Production showed four identical attempts, each rejected with
    "javascript reference did not run (runtime_error):" and nothing after the
    colon -- because the harness reports its own faults on stdout while the gate
    reported only stderr. More retries cannot help without a signal.
    """

    ALL_LANGUAGES = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]

    @pytest.mark.parametrize(
        "fixture",
        [
            "two_sum_wrong_oracle",
            "two_sum_unsolvable",
            "two_sum_bad_generator",
            "two_sum_brute_disagrees",
            "two_sum_perf_not_enforced",
            "two_sum_js_missing_function",
        ],
    )
    def test_every_rejection_says_why(self, fixture: str) -> None:
        report = validate_question(load(fixture), languages=self.ALL_LANGUAGES)
        assert not report.accepted, f"{fixture} was expected to fail"
        assert report.detail.strip(), "a rejection with no reason teaches nothing"
        # The old bug: a detail that trails off after the colon.
        assert not report.detail.rstrip().endswith(":")

    def test_a_missing_function_is_named_as_such(self) -> None:
        """The exact detail the model needed and never received."""
        report = validate_question(
            load("two_sum_js_missing_function"), languages=self.ALL_LANGUAGES
        )
        assert report.outcome is GateOutcome.REFERENCE_FAILED
        assert "not defined or not exported" in report.detail
        assert "javascript" in report.detail

    def test_an_unexported_js_reference_now_runs(self) -> None:
        """Script-style JavaScript is reachable via the vm fallback.

        This is the shape that blocked a real session: a correct solution
        rejected purely for omitting `module.exports`.
        """
        report = validate_question(
            load("two_sum_js_unexported"), languages=self.ALL_LANGUAGES
        )
        assert report.accepted, report.detail

    def test_failure_detail_never_returns_empty(self) -> None:
        from freetcoder.generate.gate import _failure_detail
        from freetcoder.generate.harness import CaseResult

        assert _failure_detail(Verdict.RUNTIME_ERROR, [], "").strip()
        assert _failure_detail(Verdict.TIMEOUT, [], "   ").strip()
        assert "boom" in _failure_detail(
            Verdict.RUNTIME_ERROR, [CaseResult(ok=False, error="boom")], ""
        )
        # A harness diagnostic beats stderr, because it is the specific one.
        assert "boom" in _failure_detail(
            Verdict.RUNTIME_ERROR, [CaseResult(ok=False, error="boom")], "generic noise"
        )


class TestCorrectnessGuards:
    """Checks for ways the gate could previously pass something unsound."""

    ALL = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]

    def test_a_stated_complexity_target_requires_a_brute_force(self) -> None:
        """Otherwise the target is decoration: nothing proves the tests bite."""
        q = load("two_sum_good")
        q.complexity_target = "O(n)"
        q.brute_force_py = None
        report = validate_question(q)
        assert report.outcome is GateOutcome.MISSING_BRUTE_FORCE
        assert "O(n)" in report.detail

    def test_no_target_means_no_brute_force_is_required(self) -> None:
        q = load("two_sum_good")
        q.complexity_target = None
        q.brute_force_py = None
        assert validate_question(q).accepted

    def test_answers_beyond_2_53_are_rejected_when_js_is_offered(self) -> None:
        """IEEE-754 doubles lose integer precision there, silently."""
        q = load("two_sum_multilang")
        # The point is the 2^53 boundary, not the bounds; widen them so the
        # magnitude check is what fires rather than the constraint check.
        q.constraints = [
            ParamConstraint(name="nums", min_length=2, max_length=10000,
                            element_min=-(2**60), element_max=2**60),
            ParamConstraint(name="target", min=-(2**60), max=2**60),
        ]
        q.hidden_generator_py = (
            "import json\n"
            "for _ in range(12):\n"
            "    print(json.dumps({'args': {'nums': [2**53 + 1, 1], 'target': 2**53 + 2}}))\n"
        )
        report = validate_question(q, languages=self.ALL)
        assert report.outcome is GateOutcome.UNSAFE_MAGNITUDE
        assert "2^53" in report.detail

    def test_large_integers_are_fine_when_only_python_is_offered(self) -> None:
        q = load("two_sum_good")
        q.hidden_generator_py = (
            "import json\n"
            "for _ in range(12):\n"
            "    print(json.dumps({'args': {'nums': [2**60, 1], 'target': 2**60 + 1}}))\n"
        )
        report = validate_question(q, languages=[Language.PYTHON])
        assert report.outcome is not GateOutcome.UNSAFE_MAGNITUDE

    def test_per_language_reference_timings_are_recorded(self) -> None:
        """A JS submission must be budgeted against a JS reference."""
        report = validate_question(load("two_sum_multilang"), languages=self.ALL)
        assert report.accepted, report.detail
        assert set(report.reference_ms_by_language) >= {"python", "javascript"}


class TestConstraintsAreChecked:
    """The statement's bounds are a promise; the gate now holds it.

    A candidate who reads "n <= 10^4" and picks an algorithm accordingly must
    never be graded on n = 10^6. Prose cannot be checked, so questions carry the
    same bounds as data.
    """

    def _with_bounds(self) -> GeneratedQuestion:
        q = load("two_sum_good")
        q.constraints = [
            ParamConstraint(name="nums", min_length=2, max_length=50,
                            element_min=-500, element_max=500),
            ParamConstraint(name="target", min=-1000, max=1000),
        ]
        return q

    def test_a_question_within_its_own_bounds_is_accepted(self) -> None:
        assert validate_question(self._with_bounds()).accepted

    def test_a_generator_exceeding_the_stated_length_is_rejected(self) -> None:
        q = self._with_bounds()
        q.hidden_generator_py = (
            "import json, random\nrandom.seed(1)\n"
            "for _ in range(12):\n"
            "    nums = [random.randint(-500, 500) for _ in range(200)]\n"
            "    print(json.dumps({'args': {'nums': nums, "
            "'target': nums[0] + nums[1]}}))\n"
        )
        report = validate_question(q)
        assert report.outcome is GateOutcome.CONSTRAINT_VIOLATION
        assert "exceeds the stated maximum 50" in report.detail

    def test_a_generator_exceeding_element_bounds_is_rejected(self) -> None:
        q = self._with_bounds()
        q.hidden_generator_py = (
            "import json\n"
            "for _ in range(12):\n"
            "    print(json.dumps({'args': {'nums': [99999, 1], 'target': 100000}}))\n"
        )
        report = validate_question(q)
        assert report.outcome is GateOutcome.CONSTRAINT_VIOLATION
        assert "element 99999" in report.detail

    def test_a_visible_example_outside_the_bounds_is_rejected(self) -> None:
        """Drift between the prose and the examples is caught too."""
        q = self._with_bounds()
        q.visible_tests[0].args["target"] = 99_999
        report = validate_question(q)
        assert report.outcome is GateOutcome.CONSTRAINT_VIOLATION
        assert "visible case 1" in report.detail

    def test_the_rejection_says_which_side_to_fix(self) -> None:
        q = self._with_bounds()
        q.visible_tests[0].args["target"] = 99_999
        assert "fix whichever is wrong" in validate_question(q).detail

    def test_questions_without_structured_constraints_are_now_rejected(self) -> None:
        """Reversal, deliberate. Constraints used to be optional -- "additive,
        so older questions are not invalidated". That is exactly how a question
        shipped with blank constraints: an empty list makes _check_constraints
        pass vacuously, so nothing anywhere noticed. A candidate cannot reason
        about an input with no stated bounds, and the generator is not checked
        against one either."""
        q = load("two_sum_good")
        q.constraints = []
        report = validate_question(q)
        assert report.outcome is GateOutcome.PROSE_TOO_THIN


class TestScaffoldsMatchTheirLanguage:
    """The gate executed references but never looked at the starter code.

    A Python-shaped JavaScript scaffold passed validation and reached the
    editor, so switching to JavaScript showed Python -- the artifact the
    candidate actually sees was the one nothing checked.
    """

    ALL = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]

    def test_a_python_scaffold_in_a_javascript_slot_is_rejected(self) -> None:
        report = validate_question(
            load("two_sum_python_scaffold_in_js"), languages=self.ALL
        )
        assert report.outcome is GateOutcome.SCAFFOLD_INVALID
        assert "javascript scaffold is not valid javascript" in report.detail

    def test_correct_scaffolds_pass(self) -> None:
        report = validate_question(load("two_sum_multilang"), languages=self.ALL)
        assert report.accepted, report.detail

    def test_only_offered_languages_are_checked(self) -> None:
        """A bad scaffold for a language nobody is offered must not block."""
        report = validate_question(
            load("two_sum_python_scaffold_in_js"), languages=[Language.PYTHON]
        )
        assert report.outcome is not GateOutcome.SCAFFOLD_INVALID

    def test_an_unimplemented_body_is_still_valid(self) -> None:
        """Starter code need not satisfy its own declared return type."""
        q = load("two_sum_multilang")
        sig = q.signature_for(Language.TYPESCRIPT)
        assert sig is not None
        sig.scaffold = (
            "export function two_sum(nums: number[], target: number): number[] {\n"
            "  // your code here\n}\n"
        )
        report = validate_question(q, languages=self.ALL)
        assert report.outcome is not GateOutcome.SCAFFOLD_INVALID, report.detail


class TestClarificationsAreVerified:
    """A clarification is a promise about behaviour.

    An unverified one is worse than none: it reads as authoritative while
    quietly contradicting the grader. This is the "Most Frequent Word" problem
    -- prose that says "the word that appears most often" while the examples
    silently establish tokenisation and case folding.
    """

    ALL = [Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT]

    def test_a_question_whose_clarifications_hold_is_accepted(self) -> None:
        report = validate_question(load("most_frequent_word"), languages=self.ALL)
        assert report.accepted, report.detail

    def test_a_clarification_the_code_contradicts_is_rejected(self) -> None:
        report = validate_question(
            load("most_frequent_word_wrong_clarification"), languages=self.ALL
        )
        assert report.outcome is GateOutcome.CLARIFICATION_WRONG
        assert "capitalisation" in report.detail
        # The rejection names both sides, so a repair has something to act on.
        assert "reference returns" in report.detail

    def test_a_probe_the_reference_cannot_run_is_rejected(self) -> None:
        q = load("most_frequent_word")
        q.clarifications[0].probe = {"nonexistent_parameter": 1}
        report = validate_question(q)
        assert report.outcome is GateOutcome.CLARIFICATION_WRONG

    def test_clarifications_are_optional(self) -> None:
        """Additive: questions without them still validate."""
        q = load("most_frequent_word")
        q.clarifications = []
        assert validate_question(q, languages=self.ALL).accepted

    def test_the_ambiguities_that_matter_are_covered(self) -> None:
        """The fixture documents what a good question settles."""
        asked = " ".join(c.question.lower() for c in load("most_frequent_word").clarifications)
        for topic in ("capitalisation", "punctuation", "empty", "tie"):
            assert topic in asked, f"nothing settles {topic}"


class TestTheReferenceIsAsFastAsItClaims:
    """"Your code vs the reference" means nothing if the reference is bad.

    A secretly quadratic baseline claiming O(n) would flatter every submission.
    Timings use sleep rather than real work so the test measures the *check*,
    not the machine, and finishes quickly.
    """

    # Sizes chosen so both the smallest and largest timings sit well above
    # scheduler granularity: a sleep of a few microseconds measures the OS, not
    # the algorithm, and flattens the observed exponent toward linear.
    GENERATOR = (
        "import json\n"
        "for n in (800, 1600, 3200, 6400, 12800):\n"
        "    print(json.dumps({'args': {'n': n}}))\n"
    )

    def _question(self, reference: str, target: str = "O(n) time") -> GeneratedQuestion:
        q = load("two_sum_good")
        q.complexity_target = target
        # One synthetic parameter, so it carries one bound. The prose check
        # requires every parameter to be described and bounded.
        q.constraints = [ParamConstraint(name="n", min=1, max=100000)]
        q.statement_md = (
            "Given an integer `n`, do `n` units of work and return `n`. "
            "This exists to measure how the reference scales with `n`."
        )
        # A stated target requires a brute force, and it must actually fail the
        # large cases -- otherwise the target is unenforced.
        q.brute_force_py = (
            "import time\n\n"
            "def work(n):\n"
            "    time.sleep((n ** 2) * 0.00000005)\n"
            "    return n\n"
        )
        q.hidden_generator_py = self.GENERATOR
        q.visible_tests = [TestCase(args={"n": 800}, expected=800)]
        for sig in q.signatures:
            sig.function_name = "work"
        q.signatures = [q.signatures[0]]
        q.signatures[0].reference_solution = reference
        q.signatures[0].scaffold = "def work(n):\n    pass\n"
        return q

    #: Proportional to n: 800 -> 8ms, 12800 -> 128ms.
    LINEAR = (
        "import time\n\n"
        "def work(n):\n"
        "    time.sleep(n * 0.00001)\n"
        "    return n\n"
    )
    #: Proportional to n^2: 800 -> 8ms, 12800 -> ~2s.
    QUADRATIC = (
        "import time\n\n"
        "def work(n):\n"
        "    time.sleep((n ** 2) * 0.0000000125)\n"
        "    return n\n"
    )

    def test_a_reference_matching_its_claim_passes(self) -> None:
        report = validate_question(self._question(self.LINEAR))
        assert report.outcome is not GateOutcome.REFERENCE_TOO_SLOW, report.detail

    def test_a_quadratic_reference_claiming_linear_is_rejected(self) -> None:
        report = validate_question(self._question(self.QUADRATIC))
        assert report.outcome is GateOutcome.REFERENCE_TOO_SLOW, report.detail
        assert "O(n) time" in report.detail
        assert "meaningless" in report.detail

    def test_the_same_reference_is_fine_when_it_claims_quadratic(self) -> None:
        """The check tests honesty, not speed."""
        report = validate_question(
            self._question(self.QUADRATIC, target="O(n^2) time")
        )
        assert report.outcome is not GateOutcome.REFERENCE_TOO_SLOW, report.detail

    def test_a_question_too_fast_to_measure_is_not_rejected(self) -> None:
        """Silence beats a verdict invented from scheduling noise."""
        instant = "def work(n):\n    return n\n"
        report = validate_question(self._question(instant))
        assert report.outcome is not GateOutcome.REFERENCE_TOO_SLOW

    def test_no_claim_means_no_measurement(self) -> None:
        q = load("two_sum_good")
        q.complexity_target = None
        q.brute_force_py = None
        assert validate_question(q).accepted

    def test_the_measured_growth_is_reported(self) -> None:
        """So "1.3x the reference" can say what the reference actually is."""
        report = validate_question(self._question(self.LINEAR))
        assert report.accepted, report.detail
        assert report.measured_growth == "~linear"

    def test_the_check_does_not_reject_a_good_question_under_repetition(self) -> None:
        """A check that randomly rejects good questions is worse than none."""
        for _ in range(3):
            report = validate_question(self._question(self.LINEAR))
            assert report.outcome is not GateOutcome.REFERENCE_TOO_SLOW, report.detail
