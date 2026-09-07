"""Procedural solvability gate.

An LLM-authored question is untrusted. Before a user ever sees one we prove,
by execution, that it is actually solvable and that its tests discriminate.

The central design choice: **expected outputs for hidden cases are never taken
from the model.** The model supplies a case *generator*; we compute the answers
by running its reference solution. A model that writes a confused oracle then
fails the visible-test check rather than shipping wrong answers to the user.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from ..models import (
    GateOutcome,
    GateReport,
    GeneratedQuestion,
    Language,
    ParamConstraint,
    Signature,
    TestCase,
)
from ..progress import NULL_REPORTER, Reporter
from ..runner import Limits, Verdict, check_syntax, get_adapter, run_python, run_source
from .harness import (
    CaseResult,
    decode_results,
    encode_cases,
    values_equal,
)

log = logging.getLogger(__name__)

#: The reference solution is trusted code we generated the prompt for, so it
#: gets more headroom than a submission and keeps its network block.
REFERENCE_LIMITS = Limits(wall_seconds=15.0, cpu_seconds=12, memory_mb=512)
GENERATOR_LIMITS = Limits(wall_seconds=15.0, cpu_seconds=12, memory_mb=512)
#: Brute force is *expected* to blow through this on large inputs.
BRUTE_FORCE_LIMITS = Limits(wall_seconds=4.0, cpu_seconds=3, memory_mb=256)

MAX_HIDDEN_CASES = 40


def _run_cases(
    solution: str,
    function_name: str,
    cases: list[TestCase],
    limits: Limits,
    language: Language = Language.PYTHON,
) -> tuple[Verdict, list[CaseResult], str]:
    """Execute `solution` against `cases`, returning per-case results."""
    adapter = get_adapter(language)
    result = run_source(
        language,
        solution,
        harness=adapter.build_harness(function_name),
        stdin=encode_cases(cases),
        limits=limits,
    )
    return result.verdict, decode_results(result.stdout), result.stderr


def _failure_detail(
    verdict: Verdict, results: list[CaseResult], stderr: str
) -> str:
    """Say *why* something failed, in a form the model can act on.

    The harness reports its own faults -- "function is not defined or not
    exported" -- as a record on **stdout**, so reporting only stderr produced an
    empty complaint. That made the retry loop blind: it repeated the same
    mistake every attempt because it was never told what the mistake was.

    Never returns an empty string.
    """
    for res in results:
        if not res.ok and res.error:
            return res.error.strip()[:400]
    if stderr.strip():
        return stderr.strip()[:400]
    if results:
        return f"{verdict.value}, and the harness produced no diagnostic"
    return (
        f"{verdict.value}, and the harness produced no output at all -- the "
        f"solution most likely failed to load"
    )


def _check_reference_on_visible(
    q: GeneratedQuestion, sig: Signature
) -> GateReport | None:
    """The reference must reproduce the examples shown to the candidate.

    This is the check that catches a wrong oracle: if the model's own solution
    disagrees with the examples in its own statement, nothing downstream can be
    trusted.
    """
    verdict, results, stderr = _run_cases(
        sig.reference_solution, sig.function_name, q.visible_tests, REFERENCE_LIMITS
    )
    if verdict is not Verdict.OK:
        return GateReport(
            outcome=GateOutcome.REFERENCE_FAILED,
            detail=(
                "reference solution did not run: "
                f"{_failure_detail(verdict, results, stderr)}"
            ),
        )
    if len(results) != len(q.visible_tests):
        return GateReport(
            outcome=GateOutcome.REFERENCE_FAILED,
            detail=f"expected {len(q.visible_tests)} results, got {len(results)}",
        )
    for i, (res, case) in enumerate(zip(results, q.visible_tests, strict=True)):
        if not res.ok:
            return GateReport(
                outcome=GateOutcome.REFERENCE_FAILED,
                detail=f"reference raised on visible case {i + 1}: {res.error[:300]}",
            )
        if not values_equal(case.expected, res.value):
            return GateReport(
                outcome=GateOutcome.VISIBLE_MISMATCH,
                detail=(
                    f"visible case {i + 1}: statement says {case.expected!r}, "
                    f"reference produced {res.value!r}"
                ),
            )
    return None


def _materialise_hidden_cases(q: GeneratedQuestion) -> tuple[list[TestCase], GateReport | None]:
    """Run the model's generator to get hidden inputs (inputs only)."""
    result = run_python(q.hidden_generator_py, limits=GENERATOR_LIMITS)
    if result.verdict is not Verdict.OK:
        return [], GateReport(
            outcome=GateOutcome.GENERATOR_FAILED,
            detail=(
                "the hidden-case generator did not run: "
                f"{_failure_detail(result.verdict, [], result.stderr)}"
            ),
        )

    cases: list[TestCase] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        args = payload.get("args")
        if isinstance(args, dict):
            cases.append(TestCase(args=args, expected=None))
        if len(cases) >= MAX_HIDDEN_CASES:
            break

    if not cases:
        return [], GateReport(
            outcome=GateOutcome.NO_HIDDEN_CASES,
            detail="generator produced no parseable {'args': ...} lines",
        )
    return cases, None


def _compute_oracle(
    sig: Signature, cases: list[TestCase]
) -> tuple[list[TestCase], int, GateReport | None]:
    """Fill in expected outputs by running the reference solution."""
    verdict, results, stderr = _run_cases(
        sig.reference_solution, sig.function_name, cases, REFERENCE_LIMITS
    )
    if verdict is not Verdict.OK or len(results) != len(cases):
        return [], 0, GateReport(
            outcome=GateOutcome.REFERENCE_FAILED,
            detail=(
                f"reference failed on hidden cases; {len(results)}/{len(cases)} "
                f"completed: {_failure_detail(verdict, results, stderr)}"
            ),
        )

    resolved: list[TestCase] = []
    total_ms = 0.0
    for case, res in zip(cases, results, strict=True):
        if not res.ok:
            return [], 0, GateReport(
                outcome=GateOutcome.REFERENCE_FAILED,
                detail=f"reference raised on a hidden case: {res.error[:300]}",
            )
        total_ms += res.ms
        resolved.append(TestCase(args=case.args, expected=res.value))
    return resolved, int(total_ms), None


def _check_brute_force_discriminates(
    q: GeneratedQuestion, sig: Signature, hidden: list[TestCase]
) -> GateReport | None:
    """Prove the tests separate a good solution from a naive one.

    Two independent things are being established:
      1. The reference is *right* -- an independently-written naive solution
         agrees with it on small inputs.
      2. The tests are *demanding* -- the naive solution cannot pass them all
         within the budget. Without this, a Codility-style performance score is
         decoration.
    """
    if not q.brute_force_py:
        if q.complexity_target:
            # Otherwise a claimed target goes entirely unenforced with no signal
            # -- the decorative performance score this gate exists to prevent.
            return GateReport(
                outcome=GateOutcome.MISSING_BRUTE_FORCE,
                detail=(
                    f"the question states a complexity target "
                    f"({q.complexity_target}) but supplies no brute_force_py, so "
                    f"nothing proves the tests actually reject a naive solution"
                ),
            )
        return None

    small = sorted(hidden, key=lambda c: len(json.dumps(c.args)))[:5]
    verdict, results, _ = _run_cases(
        q.brute_force_py, sig.function_name, small, BRUTE_FORCE_LIMITS
    )
    if verdict is Verdict.OK and len(results) == len(small):
        for case, res in zip(small, results, strict=True):
            if res.ok and not values_equal(case.expected, res.value):
                return GateReport(
                    outcome=GateOutcome.BRUTE_FORCE_DISAGREES,
                    detail=(
                        f"brute force says {res.value!r}, reference says "
                        f"{case.expected!r} for the same input -- one of them is wrong"
                    ),
                )

    if q.complexity_target:
        verdict, _, _ = _run_cases(
            q.brute_force_py, sig.function_name, hidden, BRUTE_FORCE_LIMITS
        )
        if verdict is Verdict.OK:
            return GateReport(
                outcome=GateOutcome.PERF_NOT_DISCRIMINATING,
                detail=(
                    "brute force passed every hidden case within the budget, so the "
                    f"stated target {q.complexity_target} is not actually enforced"
                ),
            )
    return None


def _check_other_languages(
    q: GeneratedQuestion,
    hidden: list[TestCase],
    languages: Sequence[Language],
    oracle: Language,
    timings: dict[str, int] | None = None,
) -> GateReport | None:
    """Every language the format offers must reproduce the oracle's answers.

    Expected values come from one oracle (Python). If another language's
    reference disagrees, the question is rejected rather than served with a
    language the candidate cannot actually pass in.
    """
    sample = hidden[:8] or list(q.visible_tests)
    for lang in languages:
        if lang is oracle:
            continue
        sig = q.signature_for(lang)
        if sig is None:
            return GateReport(
                outcome=GateOutcome.SCHEMA_INVALID,
                detail=f"no {lang.value} signature, but the format offers it",
            )
        verdict, results, stderr = _run_cases(
            sig.reference_solution, sig.function_name, sample, REFERENCE_LIMITS, lang
        )
        if verdict is not Verdict.OK or len(results) != len(sample):
            return GateReport(
                outcome=GateOutcome.REFERENCE_FAILED,
                detail=(
                    f"the {lang.value} reference did not run: "
                    f"{_failure_detail(verdict, results, stderr)}"
                ),
            )
        if timings is not None and len(results) > 1:
            # Drop the first case: on a JIT runtime it carries warmup that has
            # nothing to do with the algorithm.
            timings[lang.value] = int(sum(r.ms for r in results[1:]))
        for case, res in zip(sample, results, strict=True):
            if not res.ok or not values_equal(case.expected, res.value):
                return GateReport(
                    outcome=GateOutcome.REFERENCE_FAILED,
                    detail=(
                        f"{lang.value} reference disagrees with the "
                        f"{oracle.value} oracle: expected {case.expected!r}, "
                        f"got {res.value!r}{' / ' + res.error[:120] if res.error else ''}"
                    ),
                )
    return None


#: Beyond this, IEEE-754 doubles lose integer precision, so a Python oracle and
#: a JavaScript reference can disagree with nothing anywhere reporting an error.
JS_SAFE_INTEGER = 2**53


def _exceeds_safe_integer(value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) > JS_SAFE_INTEGER
    if isinstance(value, list):
        return any(_exceeds_safe_integer(v) for v in value)
    if isinstance(value, dict):
        return any(_exceeds_safe_integer(v) for v in value.values())
    return False


def _violation(value: object, c: ParamConstraint) -> str | None:
    """How `value` breaks constraint `c`, or None if it does not."""
    if isinstance(value, (list, str)):
        if c.min_length is not None and len(value) < c.min_length:
            return f"length {len(value)} is below the stated minimum {c.min_length}"
        if c.max_length is not None and len(value) > c.max_length:
            return f"length {len(value)} exceeds the stated maximum {c.max_length}"
        if isinstance(value, list):
            for element in value:
                if isinstance(element, bool) or not isinstance(element, (int, float)):
                    continue
                if c.element_min is not None and element < c.element_min:
                    return f"element {element} is below the stated minimum {c.element_min}"
                if c.element_max is not None and element > c.element_max:
                    return f"element {element} exceeds the stated maximum {c.element_max}"
        return None

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if c.min is not None and value < c.min:
        return f"{value} is below the stated minimum {c.min}"
    if c.max is not None and value > c.max:
        return f"{value} exceeds the stated maximum {c.max}"
    return None


def _check_constraints(
    q: GeneratedQuestion, cases: list[TestCase], label: str
) -> GateReport | None:
    """Every case must satisfy the bounds the question itself advertises.

    A candidate who reads "n <= 10^4" and optimises accordingly should never be
    handed n = 10^6. Prose alone cannot be checked, so this reads the structured
    constraints beside it.
    """
    if not q.constraints:
        return None
    by_name = {c.name: c for c in q.constraints}
    for i, case in enumerate(cases):
        for name, value in case.args.items():
            constraint = by_name.get(name)
            if constraint is None:
                continue
            if (why := _violation(value, constraint)) is not None:
                return GateReport(
                    outcome=GateOutcome.CONSTRAINT_VIOLATION,
                    detail=(
                        f"{label} case {i + 1}: parameter {name!r} {why}. The "
                        f"statement's constraints and the generated cases must "
                        f"agree -- fix whichever is wrong."
                    ),
                )
    return None


def _check_scaffolds(
    q: GeneratedQuestion, languages: Sequence[Language]
) -> GateReport | None:
    """Each scaffold must be written in the language it claims.

    The gate has always executed reference solutions but never looked at the
    starter code, and the prompt only asked for "the same function with an empty
    body" -- not that it be written in that language. So a Python-shaped
    JavaScript scaffold passed validation and reached the editor, which is what
    a candidate switching language actually sees.

    Checked for syntax only: a scaffold with an unimplemented body is valid
    starter code even though it need not satisfy its own declared return type.
    """
    for lang in languages:
        sig = q.signature_for(lang)
        if sig is None or not sig.scaffold.strip():
            continue
        result = check_syntax(lang, sig.scaffold)
        if result.verdict is not Verdict.OK:
            lines = result.stderr.strip().splitlines()
            why = lines[-1][:200] if lines else "does not parse"
            return GateReport(
                outcome=GateOutcome.SCAFFOLD_INVALID,
                detail=f"the {lang.value} scaffold is not valid {lang.value}: {why}",
            )
    return None


def _check_clarifications(
    q: GeneratedQuestion, sig: Signature
) -> GateReport | None:
    """Every clarification must match what the reference actually does.

    A clarification is a promise about behaviour. An unverified one is worse
    than none: it reads as authoritative while quietly contradicting the grader.
    """
    if not q.clarifications:
        return None

    probes = [TestCase(args=c.probe, expected=c.expect) for c in q.clarifications]
    verdict, results, stderr = _run_cases(
        sig.reference_solution, sig.function_name, probes, REFERENCE_LIMITS
    )
    if verdict is not Verdict.OK or len(results) != len(probes):
        return GateReport(
            outcome=GateOutcome.CLARIFICATION_WRONG,
            detail=(
                "the reference could not run the clarification probes: "
                f"{_failure_detail(verdict, results, stderr)}"
            ),
        )

    for clarification, res in zip(q.clarifications, results, strict=True):
        if not res.ok:
            return GateReport(
                outcome=GateOutcome.CLARIFICATION_WRONG,
                detail=(
                    f"the reference raised on the probe for "
                    f"{clarification.question!r}: {res.error[:200]}"
                ),
            )
        if not values_equal(clarification.expect, res.value):
            return GateReport(
                outcome=GateOutcome.CLARIFICATION_WRONG,
                detail=(
                    f"{clarification.question!r} claims {clarification.expect!r} "
                    f"for {clarification.probe!r}, but the reference returns "
                    f"{res.value!r}. The clarification and the solution must agree."
                ),
            )
    return None


def _check_magnitudes(
    cases: list[TestCase], languages: Sequence[Language]
) -> GateReport | None:
    """Reject answers JavaScript cannot represent exactly.

    Cheaper and far kinder than letting a candidate lose to invisible rounding.
    """
    if not any(
        lang in (Language.JAVASCRIPT, Language.TYPESCRIPT) for lang in languages
    ):
        return None
    for i, case in enumerate(cases):
        if _exceeds_safe_integer(case.expected) or _exceeds_safe_integer(case.args):
            return GateReport(
                outcome=GateOutcome.UNSAFE_MAGNITUDE,
                detail=(
                    f"case {i + 1} involves an integer beyond 2^53, which "
                    f"JavaScript cannot represent exactly; keep values within "
                    f"±9007199254740992 while JS or TS are offered"
                ),
            )
    return None


def validate_question(
    q: GeneratedQuestion,
    *,
    language: Language = Language.PYTHON,
    languages: Sequence[Language] | None = None,
    report_to: Reporter = NULL_REPORTER,
) -> GateReport:
    """Run the full gate. Never raises; every failure is an outcome.

    `report_to` is an observer: it defaults to a no-op, and nothing about the
    verdict depends on it.
    """
    sig = q.signature_for(language)
    if sig is None:
        return GateReport(
            outcome=GateOutcome.SCHEMA_INVALID,
            detail=f"no signature for {language.value}",
        )

    offered_languages = list(languages or [language])
    report_to(
        "checking the starter code parses in "
        + ", ".join(lang.value for lang in offered_languages)
    )
    if (report := _check_scaffolds(q, offered_languages)) is not None:
        return report

    if (report := _check_constraints(q, list(q.visible_tests), "visible")) is not None:
        return report

    report_to("running the reference against the examples in the statement")
    if (report := _check_reference_on_visible(q, sig)) is not None:
        return report

    if q.clarifications:
        report_to(f"checking {len(q.clarifications)} clarifications against the solution")
        if (report := _check_clarifications(q, sig)) is not None:
            return report

    report_to("generating hidden test cases")
    cases, report = _materialise_hidden_cases(q)
    if report is not None:
        return report

    report_to(f"computing expected answers for {len(cases)} hidden cases")
    hidden, reference_ms, report = _compute_oracle(sig, cases)
    if report is not None:
        return report

    if (report := _check_constraints(q, hidden, "hidden")) is not None:
        return report

    offered = offered_languages
    if (report := _check_magnitudes(hidden + list(q.visible_tests), offered)) is not None:
        return report

    if q.brute_force_py or q.complexity_target:
        report_to("checking a naive solution cannot pass")
    if (report := _check_brute_force_discriminates(q, sig, hidden)) is not None:
        return report

    timings: dict[str, int] = {language.value: reference_ms}
    if languages:
        others = [lang.value for lang in languages if lang is not language]
        if others:
            report_to(f"checking the {' and '.join(others)} solutions agree")
        report = _check_other_languages(q, hidden, languages, language, timings)
        if report is not None:
            return report

    return GateReport(
        outcome=GateOutcome.ACCEPTED,
        hidden_cases=hidden,
        reference_ms=reference_ms,
        reference_ms_by_language=timings,
    )
