"""Can the question be solved from what the candidate is actually given?

Every other check validates the question against itself: the reference agrees
with the examples, the generator obeys the constraints, the clarifications match
the code. None of them read the *statement*, so a question can be internally
perfect and still impossible to derive from its own prose.

That is a real failure, not a theoretical one. "Most Frequent Word" asked for
"the word that appears most often" while its examples silently established that
punctuation splits words and case is folded. The candidate reads the statement;
the grader runs the reference. Nothing compared the two.

So a second model solves the question from the statement, constraints,
clarifications and visible examples **alone** -- no reference, no hidden cases --
and its solution is run against the oracle. Disagreement means the prose is
missing something.

Deliberately kept out of `gate.py`. The gate is synchronous, LLM-free and
deterministic, which is what makes it the trust anchor and testable offline.
This stage needs a model, so it runs after the gate rather than inside it.
"""

from __future__ import annotations

import json
import logging

from pydantic import BaseModel, Field

from ..llm import LLMClient, LLMError
from ..models import GateOutcome, GateReport, GeneratedQuestion, Language, TestCase
from ..progress import NULL_REPORTER, Reporter
from ..runner import Limits, Verdict, get_adapter, run_source
from .gate import _violation
from .harness import decode_results, encode_cases, values_equal

log = logging.getLogger(__name__)

SOLVER_LIMITS = Limits(wall_seconds=15.0, cpu_seconds=12, memory_mb=512)

#: How many hidden cases the independent solution is checked against. Enough to
#: catch a misreading, few enough to stay cheap.
SAMPLE_SIZE = 10

SOLVER_SYSTEM = """\
You are solving a coding problem exactly as a candidate would.

You are given the problem statement, its constraints, any clarifications, and
the worked examples -- and nothing else. There is no reference solution and
there are no hidden tests. Work only from what you are given.

Where the statement leaves something genuinely open, choose the reading a
careful candidate would choose, and say which choice you made in `assumptions`.
Do not invent behaviour the statement rules out.

Return a complete, correct Python module defining exactly the named function.
"""


class CandidateSolution(BaseModel):
    """A solution derived from the statement alone."""

    code: str = Field(description="A complete Python module defining the function")
    assumptions: str = Field(
        default="",
        description="Anything the statement left open, and what you assumed",
    )


def build_solver_prompt(q: GeneratedQuestion, language: Language) -> str:
    """Everything a candidate sees, minus the title.

    The title is withheld deliberately. A memorable one -- "Longest Subarray
    With Sum Divisible by K" -- plus the worked examples is often enough for a
    strong model to *recall* a published problem and solve it without reading a
    word of the statement. That made this check pass on precisely the questions
    it exists to catch: one shipped with its description missing entirely.

    Withholding it means a pass says the prose carries the task, which is the
    property being tested. A candidate does see the title, so this is stricter
    than their experience -- deliberately, because the title is the one part we
    cannot trust to be informative rather than evocative.
    """
    sig = q.signature_for(language)
    parts = [
        f"# Problem\n\n{q.statement_md}",
        f"\n## Constraints\n\n{q.constraints_md}",
    ]
    if q.clarifications:
        parts.append(
            "\n## Clarifications\n\n"
            + "\n".join(f"- **{c.question}** {c.answer}" for c in q.clarifications)
        )
    parts.append(
        "\n## Examples\n\n"
        + "\n".join(
            f"- {json.dumps(t.args)} -> {json.dumps(t.expected)}"
            + (f"  ({t.explanation})" if t.explanation else "")
            for t in q.visible_tests
        )
    )
    parts.append(
        f"\n## Your task\n\nWrite a Python module defining "
        f"`{sig.function_name if sig else 'solve'}`. Its starting shape:\n\n"
        f"```python\n{sig.scaffold if sig else ''}\n```"
    )
    return "\n".join(parts)


def _within_constraints(q: GeneratedQuestion, case: TestCase) -> bool:
    """Is this input one the statement actually promises?

    A failure on out-of-bounds input blames the statement for the generator's
    fault, so those cases are excluded from the verdict.
    """
    by_name = {c.name: c for c in q.constraints}
    for name, value in case.args.items():
        constraint = by_name.get(name)
        if constraint is not None and _violation(value, constraint) is not None:
            return False
    return True


async def check_statement_sufficiency(
    client: LLMClient,
    q: GeneratedQuestion,
    hidden: list[TestCase],
    *,
    language: Language = Language.PYTHON,
    report_to: Reporter = NULL_REPORTER,
) -> GateReport | None:
    """None if the statement is enough to derive the intended behaviour.

    A second model failing does not *prove* ambiguity -- it may simply be
    weaker -- so the verdict is worded as "may be under-specified" and carries
    the disagreeing case, giving a repair something concrete to work from.
    """
    sig = q.signature_for(language)
    if sig is None or not hidden:
        return None

    report_to("solving the question from the statement alone")
    try:
        attempt = await client.complete_json(
            system=SOLVER_SYSTEM,
            user=build_solver_prompt(q, language),
            schema=CandidateSolution,
            temperature=0.2,
        )
    except LLMError as exc:
        # Inconclusive, not failing: a provider problem is not the question's
        # fault, and rejecting a good question over it would be worse.
        log.info("sufficiency check could not run: %s", exc)
        report_to("could not check the statement independently", kind="warn")
        return None

    sample = [c for c in hidden if _within_constraints(q, c)][:SAMPLE_SIZE]
    if not sample:
        return None

    # The reply has landed; what follows is execution, and it was silent. A
    # long gap with no step in the log reads as a hang, and it is exactly where
    # 193 unaccounted seconds hid in a real run.
    report_to(f"running that solution against {len(sample)} cases")
    adapter = get_adapter(language)
    result = run_source(
        language,
        attempt.code,
        harness=adapter.build_harness(sig.function_name),
        stdin=encode_cases(sample),
        limits=SOLVER_LIMITS,
    )
    if result.verdict is not Verdict.OK:
        # The independent solution not even running says more about that
        # attempt than about the statement.
        report_to("the independent solution did not run; skipping", kind="warn")
        return None

    results = decode_results(result.stdout)
    if len(results) != len(sample):
        return None

    for case, produced in zip(sample, results, strict=True):
        if produced.ok and values_equal(case.expected, produced.value):
            continue
        got = produced.value if produced.ok else f"an error: {produced.error[:120]}"
        return GateReport(
            outcome=GateOutcome.STATEMENT_INSUFFICIENT,
            detail=(
                f"the statement may be under-specified: solving from it alone "
                f"produced {got!r} for {json.dumps(case.args)[:200]} where the "
                f"intended solution gives {case.expected!r}. "
                + (f"The attempt assumed: {attempt.assumptions[:200]} " 
                   if attempt.assumptions else "")
                + "Say explicitly in the statement or the clarifications what "
                  "that input should produce."
            ),
        )

    report_to("the statement is enough to solve it", kind="ok")
    return None
