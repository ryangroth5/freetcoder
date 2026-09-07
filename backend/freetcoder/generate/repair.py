"""Repair a rejected question instead of regenerating it.

Regenerating throws away everything that was right -- often a good statement and
a sound reference -- to fix one broken artifact. Repair asks for a patch to the
specific thing the gate objected to, showing the model the real execution
output. A patch is far smaller than a question, so it is cheaper, faster and
likelier to come back valid.

The gate is deliberately **not** repairable. `validate_question` is the trust
anchor: a loop that could weaken the validator could make anything pass, and the
guarantee that questions are provably solvable would evaporate.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field

from ..llm import LLMClient, LLMError
from ..models import (
    Clarification,
    GateOutcome,
    GateReport,
    GeneratedQuestion,
    Language,
    TestCase,
)
from ..progress import NULL_REPORTER, Reporter
from .gate import validate_question
from .tools import TOOL_SCHEMAS, dispatch

log = logging.getLogger(__name__)

RepairTarget = Literal[
    "reference", "scaffold", "generator", "brute_force", "visible_tests",
    "constraints", "clarifications", "whole",
]

#: Which artifact each rejection implicates. `VISIBLE_MISMATCH` is the one
#: genuine ambiguity -- either the reference is wrong or the stated answers are
#: -- so the model is shown both and chooses.
TARGET_FOR: dict[GateOutcome, RepairTarget] = {
    GateOutcome.REFERENCE_FAILED: "reference",
    GateOutcome.SCAFFOLD_INVALID: "scaffold",
    GateOutcome.GENERATOR_FAILED: "generator",
    GateOutcome.NO_HIDDEN_CASES: "generator",
    GateOutcome.CONSTRAINT_VIOLATION: "constraints",
    GateOutcome.CLARIFICATION_WRONG: "clarifications",
    GateOutcome.UNSAFE_MAGNITUDE: "generator",
    GateOutcome.BRUTE_FORCE_DISAGREES: "brute_force",
    GateOutcome.MISSING_BRUTE_FORCE: "brute_force",
    GateOutcome.PERF_NOT_DISCRIMINATING: "generator",
    GateOutcome.VISIBLE_MISMATCH: "visible_tests",
    GateOutcome.SCHEMA_INVALID: "whole",
}


class QuestionPatch(BaseModel):
    """A minimal edit to one artifact of a question."""

    target: RepairTarget = Field(description="Which artifact you are replacing")
    language: Language | None = Field(
        default=None, description="Required when target is 'reference'"
    )
    content: str | None = Field(
        default=None,
        description=(
            "Replacement source for reference / scaffold / generator / brute_force"
        ),
    )
    visible_tests: list[TestCase] | None = Field(
        default=None, description="Replacement examples when target is 'visible_tests'"
    )
    clarifications: list[Clarification] | None = Field(
        default=None, description="Replacement clarifications"
    )
    constraints_md: str | None = None
    reasoning: str = Field(default="", description="One sentence: what was wrong")


class PatchError(ValueError):
    """The patch cannot be applied to this question."""


def apply_patch(q: GeneratedQuestion, patch: QuestionPatch) -> GeneratedQuestion:
    """Return a copy of `q` with the patch applied. Never mutates the original."""
    updated = q.model_copy(deep=True)

    if patch.target == "reference":
        if patch.language is None or not patch.content:
            raise PatchError("a reference patch needs both a language and content")
        sig = updated.signature_for(patch.language)
        if sig is None:
            raise PatchError(f"this question has no {patch.language.value} signature")
        sig.reference_solution = patch.content

    elif patch.target == "generator":
        if not patch.content:
            raise PatchError("a generator patch needs content")
        updated.hidden_generator_py = patch.content

    elif patch.target == "brute_force":
        if not patch.content:
            raise PatchError("a brute-force patch needs content")
        updated.brute_force_py = patch.content

    elif patch.target == "visible_tests":
        if not patch.visible_tests:
            raise PatchError("a visible-tests patch needs replacement cases")
        updated.visible_tests = patch.visible_tests

    elif patch.target == "clarifications":
        if patch.clarifications is None:
            raise PatchError("a clarifications patch needs replacement entries")
        updated.clarifications = patch.clarifications

    elif patch.target == "constraints":
        # Either side of the disagreement may be the wrong one, so a constraints
        # patch may rewrite the bounds, the generator, or both.
        if patch.constraints_md:
            updated.constraints_md = patch.constraints_md
        if patch.content:
            updated.hidden_generator_py = patch.content
        if not patch.constraints_md and not patch.content:
            raise PatchError("a constraints patch must change the bounds or the generator")

    else:  # "whole"
        raise PatchError("a whole-question rewrite is a regeneration, not a patch")

    return updated


def _artifact_text(q: GeneratedQuestion, target: RepairTarget, language: Language) -> str:
    """The current content of whatever we are asking the model to fix."""
    if target == "reference":
        sig = q.signature_for(language)
        return sig.reference_solution if sig else "(missing)"
    if target == "scaffold":
        sig = q.signature_for(language)
        return sig.scaffold if sig else "(missing)"
    if target == "generator":
        return q.hidden_generator_py
    if target == "brute_force":
        return q.brute_force_py or "(none supplied)"
    if target == "visible_tests":
        return json.dumps(
            [t.model_dump(mode="json") for t in q.visible_tests], indent=2
        )
    if target == "clarifications":
        return json.dumps(
            [c.model_dump(mode="json") for c in q.clarifications], indent=2
        )
    if target == "constraints":
        return (
            f"constraints_md:\n{q.constraints_md}\n\n"
            f"structured constraints:\n"
            f"{json.dumps([c.model_dump(mode='json') for c in q.constraints], indent=2)}"
            f"\n\nhidden_generator_py:\n{q.hidden_generator_py}"
        )
    return "(whole question)"


REPAIR_SYSTEM = """\
You repair coding questions that failed automated validation.

A harness executed the question's code and rejected it. The failure is real and
reproducible -- it is not a matter of opinion, and arguing with it will not make
it pass.

Return a **patch** to the single artifact named in `target`, not a new question.
Keep everything else exactly as it is: the statement, the title and the other
artifacts are not yours to change here.

You may call `run_code` and `run_against_cases` to test your fix before
returning it. They execute in the same sandbox the harness uses, so what you see
is exactly what validation will see. Use them -- a patch you have run is worth
far more than one you have reasoned about.

Rules that the harness enforces, and that your patch must satisfy:
- Python: define the function at module level.
- JavaScript: `module.exports = { fn };` is required.
- TypeScript: `export function fn(...)` with typed parameters.
- Generators print one JSON object per line: {"args": {...}}, inputs only,
  never expected outputs, with any randomness seeded.
- All values must be JSON types.
"""


def build_repair_prompt(
    q: GeneratedQuestion, report: GateReport, target: RepairTarget, language: Language
) -> str:
    parts = [
        f"## The question\n\n**{q.title}**\n\n{q.statement_md}",
        f"\n## Why it was rejected\n\n**{report.outcome.value}**\n\n{report.detail}",
        f"\n## The artifact to fix (`{target}`)\n\n```\n"
        f"{_artifact_text(q, target, language)}\n```",
    ]
    if target == "visible_tests":
        sig = q.signature_for(language)
        parts.append(
            "\n## Also relevant\n\nThe reference solution the harness ran:\n\n"
            f"```\n{sig.reference_solution if sig else '(missing)'}\n```\n\n"
            "Either the examples or the reference is wrong. Decide which, and "
            "patch that one: use target `visible_tests` to correct the stated "
            "answers, or target `reference` to correct the solution."
        )
    parts.append(
        f"\nReturn a patch with target `{target}`"
        + (f" and language `{language.value}`."
           if target in ("reference", "scaffold") else ".")
    )
    return "\n".join(parts)


#: How each repair target reads in a progress log.
_TARGET_LABEL: dict[str, str] = {
    "reference": "{lang} solution",
    "scaffold": "{lang} starter code",
    "generator": "hidden-case generator",
    "brute_force": "naive solution used to check the tests bite",
    "visible_tests": "worked examples",
    "constraints": "stated constraints",
    "clarifications": "clarifications",
}


def _describe(target: RepairTarget, language: Language) -> str:
    return _TARGET_LABEL.get(target, target).format(lang=language.value)


def _language_at_fault(report: GateReport, default: Language) -> Language:
    """Which language's artifact the gate complained about."""
    for lang in Language:
        if f"the {lang.value} reference" in report.detail:
            return lang
        if f"the {lang.value} scaffold" in report.detail:
            return lang
    return default


async def repair_question(
    client: LLMClient,
    q: GeneratedQuestion,
    report: GateReport,
    *,
    language: Language = Language.PYTHON,
    languages: list[Language] | None = None,
    rounds: int = 3,
    tool_budget: int = 6,
    report_to: Reporter = NULL_REPORTER,
) -> tuple[GeneratedQuestion | None, GateReport, list[GateOutcome]]:
    """Patch and re-gate until the question passes or the budget runs out.

    Returns the repaired question (or None), the final report, and the outcome
    of every round for the acceptance-rate report.
    """
    history: list[GateOutcome] = []
    current = q
    current_report = report

    for round_no in range(rounds):
        target = TARGET_FOR.get(current_report.outcome, "whole")
        if target == "whole":
            log.info("outcome %s is not patchable", current_report.outcome.value)
            break

        report_to.checkpoint()
        at_fault = _language_at_fault(current_report, language)
        report_to(f"asking the model to fix the {_describe(target, at_fault)}")
        try:
            patch = await client.complete_json_with_tools(
                system=REPAIR_SYSTEM,
                user=build_repair_prompt(current, current_report, target, at_fault),
                schema=QuestionPatch,
                tools=TOOL_SCHEMAS,
                dispatch=dispatch,
                # Repair is a precision task, not a creative one.
                temperature=0.3,
                tool_budget=tool_budget,
            )
        except LLMError as exc:
            log.warning("repair round %d: model call failed: %s", round_no + 1, exc)
            break

        try:
            candidate = apply_patch(current, patch)
        except PatchError as exc:
            # A malformed patch must not corrupt a question we might still fix.
            log.warning("repair round %d: unusable patch: %s", round_no + 1, exc)
            history.append(GateOutcome.SCHEMA_INVALID)
            continue

        current = candidate
        current_report = validate_question(
            current, language=language, languages=languages, report_to=report_to
        )
        history.append(current_report.outcome)
        log.info(
            "repair round %d (%s -> %s): %s",
            round_no + 1, target, current_report.outcome.value,
            current_report.detail[:120],
        )
        if current_report.accepted:
            return current, current_report, history

    return None, current_report, history
