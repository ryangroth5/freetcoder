"""A tutor that can see what the candidate sees, and no more.

Two things are deliberately absent from everything this module assembles: the
**reference solution**, and the **hidden test cases verbatim**.

The reference is the obvious one -- a tutor that can read the answer is a back
channel to the oracle, which is the boundary the Solution tab already enforces.

The hidden cases are subtler. Their inputs leak little on their own, since the
expected outputs are withheld. But a "compute expected" affordance would let
someone enumerate the hidden inputs through chat, compute each answer, and pass
with a lookup table. Neither is a problem alone; together they are a bypass. So
the tutor gets a *characterisation* -- how many, what sizes, which shapes --
which is what "help me think about valid input" actually requires.

Where behaviour genuinely needs settling, `probe_reference` runs the reference
on arguments the candidate is asking about and returns **only the output**. That
grants no capability the candidate lacks; it is a more convenient route to the
same information.

This has its own context builder rather than reusing the repair prompt's, which
*does* see reference solutions. Sharing one would put a leak a single edit away.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .formats import FormatConfig
from .generate.harness import decode_results, encode_cases
from .models import GatedQuestion, Language, TestCase
from .runner import Limits, get_adapter, run_source

log = logging.getLogger(__name__)

#: Probing is a question about behaviour, not a workload.
PROBE_LIMITS = Limits(wall_seconds=10.0, cpu_seconds=8, memory_mb=512)

SYSTEM_PROMPT = """\
You are a patient coding tutor sitting beside someone working on a problem.

Your job is to help them *arrive* at the answer, not to hand it over.

- Ask what they have tried before suggesting anything.
- Give the smallest useful nudge first. Escalate only if they ask again.
- When a test fails, help them read the failure rather than fixing it for them.
- Never write a complete solution before they have submitted. A corrected line
  or a small illustrative snippet is fine; a finished function is not.
- If they ask what the problem *means* -- how input is tokenised, whether case
  matters, what a tie does -- answer plainly and definitively. Ambiguity in the
  question is not a puzzle for them to solve.

You have the clarifications the question was validated against. For anything
they do not cover, call `probe_reference` to find out what the intended solution
actually does for a given input, and report that. Do not guess at behaviour you
can check.

You cannot see the reference solution and you cannot see the hidden test cases.
Say so plainly if asked, rather than inventing either.
"""


def is_available(config: FormatConfig, *, attempted: bool) -> tuple[bool, str]:
    """Whether the tutor may answer, and why not when it may not.

    Formats encode how much support a candidate gets: LeetCode and Coderbyte
    offer hints, a CodeSignal GCA and Codility offer none. A timed assessment
    simulation that ships an AI assistant is not simulating anything, so the
    tutor follows the same policy -- and unlocks once the attempt is over.
    """
    if config.generation.give_hints or attempted:
        return True, ""
    return False, (
        f"{config.label} gives candidates no assistance, so the tutor is locked "
        f"until you submit or skip. That is the point of simulating it."
    )


def describe_hidden_cases(gated: GatedQuestion) -> str:
    """What the hidden cases are *like*, never what they are.

    Enough to reason about edge cases; not enough to build a lookup table.
    """
    cases = gated.hidden_tests
    if not cases:
        return "There are no hidden cases."

    shapes: set[str] = set()
    sizes: list[int] = []
    for case in cases:
        for value in case.args.values():
            if isinstance(value, (list, str)):
                sizes.append(len(value))
                if len(value) == 0:
                    shapes.add("empty input")
                elif len(value) == 1:
                    shapes.add("a single element")
                if isinstance(value, list):
                    numbers = [v for v in value
                               if isinstance(v, (int, float))
                               and not isinstance(v, bool)]
                    if len(set(map(str, value))) < len(value):
                        shapes.add("repeated values")
                    if any(n < 0 for n in numbers):
                        shapes.add("negative numbers")
                    if any(n == 0 for n in numbers):
                        shapes.add("zero")
            elif isinstance(value, (int, float)) and not isinstance(value, bool):
                if value < 0:
                    shapes.add("negative numbers")

    parts = [f"There are {len(cases)} hidden cases."]
    if sizes:
        parts.append(f"Input sizes range from {min(sizes)} to {max(sizes)}.")
    if shapes:
        parts.append("Among them: " + ", ".join(sorted(shapes)) + ".")
    parts.append(
        "You cannot see the cases themselves, and neither can the tutor -- only "
        "this description."
    )
    return " ".join(parts)


def build_context(
    gated: GatedQuestion,
    config: FormatConfig,
    *,
    source: str,
    language: Language,
    last_report: dict[str, Any] | None,
    attempts: list[dict[str, Any]],
) -> str:
    """Everything the tutor is allowed to know, assembled server-side.

    Never built from client-supplied content: a request could otherwise name a
    different question or inject its own instructions.
    """
    q = gated.question
    parts: list[str] = [
        f"## The question\n\n**{q.title}** ({q.difficulty.value})\n\n{q.statement_md}",
        f"\n### Constraints\n\n{q.constraints_md}",
    ]

    if q.clarifications:
        lines = "\n".join(
            f"- **{c.question}** {c.answer}" for c in q.clarifications
        )
        parts.append(
            "\n### Clarifications (verified against the intended solution)\n\n"
            f"{lines}"
        )

    parts.append(
        "\n### Worked examples\n\n"
        + "\n".join(
            f"- {json.dumps(t.args)} -> {json.dumps(t.expected)}"
            for t in q.visible_tests
        )
    )

    parts.append(f"\n### Hidden cases\n\n{describe_hidden_cases(gated)}")

    parts.append(
        f"\n### Their current code ({language.value})\n\n```\n{source or '(empty)'}\n```"
    )

    if last_report:
        parts.append(f"\n### Their last run\n\n{_describe_report(last_report)}")

    if attempts:
        history = "\n".join(
            f"- {a.get('kind', '?')}: {a.get('verdict', '?')}" for a in attempts[-8:]
        )
        parts.append(
            "\n### Earlier attempts, oldest first\n\n" + history
            + "\n\nUse this to notice progress or regressions -- a case that "
              "started passing, or one that broke while fixing another."
        )

    return "\n".join(parts)


def _describe_report(report: dict[str, Any]) -> str:
    verdict = report.get("verdict", "unknown")
    lines = [f"Verdict: **{verdict}**"]

    stderr = (report.get("stderr") or "").strip()
    if stderr:
        # Compiler and syntax errors verbatim: "why won't this parse" is one of
        # the most common things to be stuck on.
        lines.append(f"Compiler or runtime output:\n```\n{stderr[:1500]}\n```")

    cases = report.get("cases") or []
    if cases:
        passed = sum(1 for c in cases if c.get("passed"))
        lines.append(f"{passed} of {len(cases)} cases passed.")

    failure = report.get("first_failure")
    if failure:
        lines.append(
            "First failing case:\n"
            f"- input: {json.dumps(failure.get('args'))}\n"
            f"- expected: {json.dumps(failure.get('expected'))}\n"
            f"- produced: {json.dumps(failure.get('actual'))}"
            + (f"\n- error: {failure.get('error')}" if failure.get("error") else "")
        )
    return "\n".join(lines)


def run_reference(
    gated: GatedQuestion, args: dict[str, Any], language: Language | None = None
) -> tuple[bool, Any, str]:
    """Run the reference on `args`. Returns (ok, value, error).

    The one place the intended solution is executed on demand. Both the tutor's
    probe and the candidate's "compute expected" button go through here, so the
    boundary -- output only, never source -- is enforced once.
    """
    language = language or gated.language
    sig = gated.question.signature_for(language)
    if sig is None:
        return False, None, f"no {language.value} reference for this question"

    adapter = get_adapter(language)
    result = run_source(
        language,
        sig.reference_solution,
        harness=adapter.build_harness(sig.function_name),
        stdin=encode_cases([TestCase(args=args, expected=None)]),
        limits=PROBE_LIMITS,
    )
    decoded = decode_results(result.stdout)
    if not decoded:
        return False, None, (
            result.stderr.strip().splitlines()[-1][:200]
            if result.stderr.strip() else "the reference produced no output"
        )
    outcome = decoded[0]
    if not outcome.ok:
        lines = outcome.error.strip().splitlines()
        return False, None, (lines[-1][:200] if lines else "the reference raised")
    return True, outcome.value, ""


def probe_reference(gated: GatedQuestion, args: dict[str, Any]) -> str:
    """Run the reference on `args` and report **only** what it returned.

    Behavioural access to the oracle, never source access. This is the same
    information a candidate can obtain themselves, reached more conveniently.
    """
    ok, value, error = run_reference(gated, args)
    if not ok:
        return f"the intended solution could not run on {json.dumps(args)}: {error}"
    return (
        f"for {json.dumps(args)} the intended solution returns {json.dumps(value)}"
    )


def probe_tool_schema() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "probe_reference",
            "description": (
                "Find out what the intended solution returns for a specific "
                "input. Use this to settle questions about behaviour the "
                "statement leaves open. Returns only the output, never the code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "object",
                        "description": 'Arguments by parameter name, e.g. {"message": ""}',
                    },
                },
                "required": ["args"],
            },
        },
    }
