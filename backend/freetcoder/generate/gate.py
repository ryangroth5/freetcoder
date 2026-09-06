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
    Signature,
    TestCase,
)
from ..runner import Limits, Verdict, get_adapter, run_python, run_source
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
            detail=f"reference solution did not run ({verdict.value}): {stderr[:400]}",
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
            detail=f"hidden generator failed ({result.verdict.value}): {result.stderr[:400]}",
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
                f"reference failed on hidden cases ({verdict.value}); "
                f"{len(results)}/{len(cases)} completed: {stderr[:300]}"
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
                    f"{lang.value} reference did not run ({verdict.value}): "
                    f"{stderr[:300]}"
                ),
            )
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


def validate_question(
    q: GeneratedQuestion,
    *,
    language: Language = Language.PYTHON,
    languages: Sequence[Language] | None = None,
) -> GateReport:
    """Run the full gate. Never raises; every failure is an outcome."""
    sig = q.signature_for(language)
    if sig is None:
        return GateReport(
            outcome=GateOutcome.SCHEMA_INVALID,
            detail=f"no signature for {language.value}",
        )

    if (report := _check_reference_on_visible(q, sig)) is not None:
        return report

    cases, report = _materialise_hidden_cases(q)
    if report is not None:
        return report

    hidden, reference_ms, report = _compute_oracle(sig, cases)
    if report is not None:
        return report

    if (report := _check_brute_force_discriminates(q, sig, hidden)) is not None:
        return report

    if languages:
        report = _check_other_languages(q, hidden, languages, language)
        if report is not None:
            return report

    return GateReport(
        outcome=GateOutcome.ACCEPTED, hidden_cases=hidden, reference_ms=reference_ms
    )
