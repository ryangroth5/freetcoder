"""Session orchestration: the logic behind the API, kept out of the routes."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from .formats import FormatConfig, Timing
from .generate.harness import decode_results, encode_cases, values_equal
from .llm import LLMClient
from .models import GatedQuestion, Language, TestCase
from .progress import NULL_REPORTER, Reporter
from .runner import Limits, Verdict, get_adapter, run_source
from .scoring import CaseOutcome, QuestionScore, score_question
from .storage import Storage, cache_key

log = logging.getLogger(__name__)

#: A submission gets far less headroom than the reference solution did.
SUBMISSION_LIMITS = Limits(wall_seconds=8.0, cpu_seconds=6, memory_mb=256)

#: How much slower than the reference a solution may be before a case counts as
#: over budget. Generous: we are separating quadratic from linear, not shaving
#: constant factors.
PERF_BUDGET_MULTIPLIER = 8.0
PERF_BUDGET_FLOOR_MS = 250.0


@dataclass(slots=True)
class ExecutionReport:
    """What Run or Submit produced, ready for the Test Result pane."""

    verdict: Verdict
    outcomes: list[CaseOutcome]
    stderr: str
    first_failure: dict[str, Any] | None = None
    score: QuestionScore | None = None
    #: submission time / reference time, both measured on this machine moments
    #: apart. Absolute milliseconds are meaningless across hardware; this ratio
    #: cancels CPU speed and ambient load. Below 1.0 beats the reference.
    ratio: float | None = None
    total_ms: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)


def _budget_ms(reference_ms: int, case_count: int) -> float:
    per_case = (reference_ms / case_count) if case_count else 0.0
    return max(PERF_BUDGET_FLOOR_MS, per_case * PERF_BUDGET_MULTIPLIER)


def execute_against(
    source: str,
    gated: GatedQuestion,
    cases: list[TestCase],
    *,
    hidden: bool,
    config: FormatConfig,
    language: Language | None = None,
    judged: list[bool] | None = None,
) -> ExecutionReport:
    """Run a submission over `cases` and describe what happened.

    `language` is the language the candidate is actually writing in, which need
    not be the question's oracle language.

    `judged[i]` false means the candidate supplied that case without an expected
    value: execute it and report the output, but do not call it right or wrong.
    """
    language = language or gated.language
    sig = gated.question.signature_for(language)
    if sig is None:
        return ExecutionReport(
            Verdict.INTERNAL_ERROR, [],
            f"this question has no {language.value} signature",
        )

    adapter = get_adapter(language)
    result = run_source(
        language,
        source,
        harness=adapter.build_harness(sig.function_name),
        stdin=encode_cases(cases),
        limits=SUBMISSION_LIMITS,
    )

    # A compile error or a hard kill has no per-case detail to show.
    if result.verdict in (Verdict.COMPILE_ERROR, Verdict.TIMEOUT, Verdict.MEMORY_EXCEEDED):
        return ExecutionReport(result.verdict, [], result.stderr)

    results = decode_results(result.stdout)
    # Budget against *this language's* reference. Judging a JavaScript
    # submission by a Python-derived number measures the runtime, not the code.
    reference_for_language = gated.reference_ms_by_language.get(
        language.value, gated.reference_ms
    )
    budget = _budget_ms(reference_for_language, len(gated.hidden_tests))

    outcomes: list[CaseOutcome] = []
    first_failure: dict[str, Any] | None = None
    for i, case in enumerate(cases):
        res = results[i] if i < len(results) else None
        is_judged = judged[i] if judged is not None and i < len(judged) else True
        ran = res is not None and res.ok
        if not ran or res is None:
            ok = False
        elif is_judged:
            ok = values_equal(case.expected, res.value)
        else:
            # No expected value: running without raising is all we can ask.
            ok = True
        over = bool(config.scoring.perf_tests and res is not None and res.ms > budget)
        outcomes.append(
            CaseOutcome(index=i, passed=ok, hidden=hidden,
                        ms=res.ms if res else 0.0, over_budget=over,
                        stdout=res.stdout if res else "",
                        judged=is_judged,
                        # Hidden outputs stay hidden; visible ones are the whole
                        # point of an exploratory case.
                        actual=(res.value if res and res.ok else None)
                        if not hidden else None)
        )
        if not ok and is_judged and first_failure is None:
            first_failure = {
                "index": i,
                "args": case.args,
                "expected": case.expected,
                "actual": res.value if res and res.ok else None,
                "error": (res.error if res and not res.ok else "")
                or ("no output for this case" if res is None else ""),
                "stdout": res.stdout if res else "",
            }

    # An unjudged case can still fail by *crashing*, which is worth reporting;
    # it just cannot be a wrong answer.
    verdict = Verdict.OK if all(o.passed for o in outcomes) else Verdict.WRONG_ANSWER
    return ExecutionReport(verdict, outcomes, result.stderr, first_failure)


async def obtain_question(
    store: Storage,
    client: LLMClient,
    config: FormatConfig,
    index: int,
    *,
    language: Language = Language.PYTHON,
    exclude_ids: list[str] | None = None,
    max_attempts: int = 4,
    repair_rounds: int | None = None,
    report_to: Reporter = NULL_REPORTER,
) -> tuple[str, GatedQuestion] | None:
    """Generate a fresh question, falling back to the cache if that fails.

    Variety is the product. An earlier version preferred the cache whenever one
    existed, which meant a given (style, preset, difficulty, language) set
    generated exactly one question and then replayed it forever -- the tool's
    defining feature quietly traded away to save tokens.

    The cache is now a *fallback*: it keeps practice working with no key, a dead
    provider, or a model that cannot get a question past the gate.
    """
    from .generate import generate_question  # local import: avoids a cycle
    from .generate.module import generate_question_as_module
    from .settings import get_settings

    settings = get_settings()
    difficulty = config.session.difficulty_for(min(index, config.session.question_count - 1))
    # Imported prose can only be adapted by the monolithic path, whatever the
    # setting says: the module interface has no equivalent of `import_text`.
    strategy = (
        "monolithic" if config.generation.import_text else settings.generation_strategy
    )
    key = cache_key(config, difficulty.value, language, strategy)

    if _can_generate(client):
        if strategy == "module":
            result = await generate_question_as_module(
                client, config, difficulty=difficulty, language=language,
                max_attempts=max_attempts,
                question_number=index + 1,
                report_to=report_to,
            )
        else:
            result = await generate_question(
                client, config, difficulty=difficulty, language=language,
                max_attempts=max_attempts,
                repair_rounds=(
                    settings.repair_rounds if repair_rounds is None else repair_rounds
                ),
                tool_budget=settings.tool_call_budget,
                check_sufficiency=settings.check_statement_sufficiency,
                report_to=report_to,
            )
        if result.accepted and result.question is not None:
            qid = await store.cache_question(key, result.question)
            return qid, result.question
        log.warning(
            "generation failed for %s (%s); falling back to the cache",
            key,
            result.attempts[-1].outcome.value if result.attempts else "no attempts",
        )

    cached = await store.find_cached(key, exclude_ids=exclude_ids)
    if cached is not None:
        log.info("serving a cached question for %s", key)
        return cached
    return None


def _can_generate(client: LLMClient) -> bool:
    """Whether asking this client for a new question is worth the round trip."""
    from .llm import FakeLLM

    if isinstance(client, FakeLLM):
        # A seeded fake (offline mode, tests) can still produce questions; an
        # empty one is what "no LLM configured" looks like.
        return not client.exhausted
    return True


def remaining_seconds(session: dict[str, Any], config: FormatConfig) -> float | None:
    """Seconds left on the clock, or None when the format is untimed."""
    if config.session.timing is Timing.UNTIMED:
        return None
    allowed = (
        config.session.total_seconds
        if config.session.timing is Timing.TOTAL
        else config.session.per_question_seconds
    )
    if not allowed:
        return None
    started: float = session["started_at"]
    return max(0.0, allowed - (time.time() - started))


def measure_reference(
    gated: GatedQuestion, cases: list[TestCase], language: Language
) -> float | None:
    """Time this language's reference solution over the same cases, now.

    Deliberately re-measured rather than reusing the stored `reference_ms`: a
    question pulled from the library carries a number recorded on a stranger's
    machine, and comparing against it would rank hardware rather than code.
    """
    sig = gated.question.signature_for(language)
    if sig is None:
        return None
    adapter = get_adapter(language)
    result = run_source(
        language,
        sig.reference_solution,
        harness=adapter.build_harness(sig.function_name),
        stdin=encode_cases(cases),
        limits=SUBMISSION_LIMITS,
    )
    if result.verdict is not Verdict.OK:
        return None
    results = decode_results(result.stdout)
    return sum(r.ms for r in results) or None


def grade_submission(
    source: str,
    gated: GatedQuestion,
    config: FormatConfig,
    *,
    elapsed_s: float,
    language: Language | None = None,
) -> ExecutionReport:
    """Run a submission against visible + hidden cases and score it."""
    cases = list(gated.question.visible_tests) + list(gated.hidden_tests)
    report = execute_against(
        source, gated, cases, hidden=True, config=config, language=language
    )

    # Mark which outcomes were the hidden ones; scoring grades on those.
    visible_count = len(gated.question.visible_tests)
    for outcome in report.outcomes:
        outcome.hidden = outcome.index >= visible_count

    allowed = (
        config.session.total_seconds
        if config.session.timing is Timing.TOTAL
        else config.session.per_question_seconds
    )
    report.score = score_question(
        report.outcomes, config, elapsed_s=elapsed_s, allowed_s=allowed
    )

    # Only rank a solution that actually works: timing a wrong answer would
    # reward failing fast.
    report.total_ms = sum(o.ms for o in report.outcomes)
    if report.score.solved:
        reference_ms = measure_reference(gated, cases, language or gated.language)
        if reference_ms:
            report.ratio = round(report.total_ms / reference_ms, 3)
    return report
