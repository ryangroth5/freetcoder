"""Generate a question in stages instead of one large response.

The monolithic call asks for everything at once: prose, constraints, scaffolds
and reference solutions per language, a hidden generator and a brute force. In
our fixtures the code outweighs the prose 3.4:1 and 6.7:1, and half the response
is `signatures`. Two of three models answered that prompt with a statement under
160 characters -- the prose loses an attention contest against code the
candidate never reads.

Staging is the counter-hypothesis, and this exists to be *measured* against the
monolithic path rather than assumed better. Each stage is small, carries its own
schema, and is validated the moment it arrives, so a failure costs one small
call rather than invalidating seven kilobytes.

The scenario seed is deliberately local, with no model call. Asking for "hash
maps, medium, leetcode" is a search key into memorised problems -- all three
models returned the same one -- and a concrete setting costs nothing to supply.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from ..formats import FormatConfig
from ..llm import LLMClient, LLMError, telemetry
from ..models import (
    Difficulty,
    GeneratedQuestion,
    Language,
    ParamConstraint,
    Signature,
    TestCase,
)
from ..progress import NULL_REPORTER, Reporter
from ..runner.adapters import check_syntax
from ..runner.types import Verdict
from .gate import REFERENCE_LIMITS, _failure_detail, _run_cases
from .harness import values_equal
from .pipeline import _read_prompt
from .scenarios import SCENARIOS, pick

__all__ = ["SCENARIOS", "generate_staged"]

class StatementDraft(BaseModel):
    """Stage 2: the prose, and nothing else."""

    title: str = Field(min_length=3, max_length=120)
    statement_md: str = Field(min_length=200)
    function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    parameter_names: list[str] = Field(min_length=1, max_length=4)
    topics: list[str] = Field(default_factory=list, max_length=8)


class ReferenceDraft(BaseModel):
    """Stage 3: one language's solution, and the examples it produces."""

    scaffold: str = Field(min_length=10)
    reference_solution: str = Field(min_length=20)
    visible_tests: list[TestCase] = Field(min_length=1, max_length=4)


class ConstraintsDraft(BaseModel):
    """Stage 4: bounds, derived from a statement and reference that exist."""

    constraints_md: str = Field(min_length=10)
    constraints: list[ParamConstraint] = Field(min_length=1)


class HarnessDraft(BaseModel):
    """Stage 5: the machinery that makes the question gradeable."""

    hidden_generator_py: str = Field(min_length=20)
    brute_force_py: str | None = None


@dataclass
class StageOutcome:
    """What one stage cost, so the bench can localise a failure."""

    name: str
    attempts: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class StagedResult:
    question: GeneratedQuestion | None = None
    scenario: str = ""
    stages: list[StageOutcome] = field(default_factory=list)

    @property
    def failed_stage(self) -> str:
        return next((s.name for s in self.stages if not s.ok), "")


async def _ask[M: BaseModel](
    client: LLMClient,
    *,
    name: str,
    system: str,
    user: str,
    schema: type[M],
    temperature: float,
    tries: int,
    outcomes: list[StageOutcome],
    check: Callable[[M], str] | None = None,
) -> M:
    """One stage, validated here and retried alone.

    `check` returns "" for a good draft or a sentence saying what is wrong. That
    sentence is appended to the prompt on the retry, because a model corrects
    well from a concrete complaint and poorly from a bare second ask.

    Validating *here* is the whole point of staging. Without it a bad reference
    is only noticed by the gate, after the constraints and harness stages have
    already been generated against a solution that was never going to work --
    which is exactly what the first measured run did.
    """
    outcome = StageOutcome(name=name)
    outcomes.append(outcome)
    ask = user
    last = ""
    for _ in range(tries):
        outcome.attempts += 1
        try:
            with telemetry.stage(name):
                draft = await client.complete_json(
                    system=system, user=ask, schema=schema, temperature=temperature
                )
        except LLMError as exc:
            last = str(exc)[:200]
            continue

        fault = check(draft) if check else ""
        if not fault:
            return draft
        last = fault
        ask = (
            f"{user}\n\nYour previous answer was rejected: {fault}\n"
            "Fix exactly that and return the whole object again."
        )
    outcome.error = last or "no usable response"
    raise LLMError(f"stage {name!r} failed: {outcome.error}")


def _reference_fault(
    draft: ReferenceDraft, statement: StatementDraft, language: Language
) -> str:
    """Does this solution actually parse, and produce the examples it claims?

    One sandboxed run, against the draft's own examples. It costs a fraction of
    a second and it is the difference between a stage that catches its own
    mistakes and one that hands them downstream.
    """
    syntax = check_syntax(language, draft.scaffold)
    if syntax.verdict is not Verdict.OK:
        return f"the scaffold does not parse: {syntax.stderr.strip()[:200]}"

    verdict, results, stderr = _run_cases(
        draft.reference_solution,
        statement.function_name,
        list(draft.visible_tests),
        REFERENCE_LIMITS,
        language,
    )
    if verdict is not Verdict.OK:
        return (
            "the reference did not run cleanly on your own examples: "
            + _failure_detail(verdict, results, stderr)
        )

    for case, got in zip(draft.visible_tests, results, strict=False):
        if not got.ok:
            return f"the reference failed on {case.args}: {got.error}"
        if not values_equal(got.value, case.expected):
            return (
                f"for {case.args} you claimed {case.expected!r} but your "
                f"reference returns {got.value!r}; one of them is wrong"
            )
    return ""


def _brief(config: FormatConfig, difficulty: Difficulty, scenario: str) -> str:
    gen = config.generation
    parts = [
        f"Difficulty: {difficulty.value}.",
        f"Setting: {scenario}.",
        "Invent the problem inside that setting. Do not reproduce a published "
        "question; a familiar technique is fine, a familiar problem is not.",
    ]
    if gen.topics:
        parts.append(
            f"The underlying technique should suit: {', '.join(gen.topics)}."
        )
    if gen.freeform.strip():
        parts.append(f"The candidate also asked for: {gen.freeform.strip()}")
    return "\n".join(parts)


async def generate_staged(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    tries_per_stage: int = 2,
    scenario: str | None = None,
    rng: random.Random | None = None,
    report_to: Reporter = NULL_REPORTER,
) -> StagedResult:
    """Build a question stage by stage. Returns it unvalidated by the gate."""
    difficulty = difficulty or config.session.difficulty_for(0)
    chosen = scenario or pick(rng)
    result = StagedResult(scenario=chosen)
    brief = _brief(config, difficulty, chosen)
    style = _read_prompt(config.generation.style)

    try:
        report_to("writing the problem statement")
        statement = await _ask(
            client,
            name="statement",
            system=_read_prompt("stage_statement"),
            user=f"{style}\n\n{brief}",
            schema=StatementDraft,
            # The creative step, so the loosest sampling of the five.
            temperature=0.9,
            tries=tries_per_stage,
            outcomes=result.stages,
        )

        shown = (
            f"# {statement.title}\n\n{statement.statement_md}\n\n"
            f"Function: `{statement.function_name}"
            f"({', '.join(statement.parameter_names)})`"
        )

        report_to(f"solving it in {language.value}")
        reference = await _ask(
            client,
            name="reference",
            system=_read_prompt("stage_reference"),
            user=f"{shown}\n\nTarget language: {language.value}.",
            schema=ReferenceDraft,
            # Precision, not invention.
            temperature=0.2,
            tries=tries_per_stage,
            outcomes=result.stages,
            check=lambda draft: _reference_fault(draft, statement, language),
        )

        report_to("deriving the constraints")
        constraints = await _ask(
            client,
            name="constraints",
            system=_read_prompt("stage_constraints"),
            user=(
                f"{shown}\n\nReference solution:\n\n```\n"
                f"{reference.reference_solution}\n```\n\n"
                f"Parameters: {', '.join(statement.parameter_names)}"
            ),
            schema=ConstraintsDraft,
            temperature=0.2,
            tries=tries_per_stage,
            outcomes=result.stages,
        )

        report_to("building the hidden tests")
        harness = await _ask(
            client,
            name="harness",
            system=_read_prompt("stage_harness"),
            user=(
                f"{shown}\n\nReference solution:\n\n```\n"
                f"{reference.reference_solution}\n```\n\n"
                f"Constraints:\n{constraints.constraints_md}\n\n"
                f"Produce at least {config.scoring.hidden_test_count} cases."
            ),
            schema=HarnessDraft,
            temperature=0.3,
            tries=tries_per_stage,
            outcomes=result.stages,
        )
    except LLMError:
        return result

    result.question = GeneratedQuestion(
        title=statement.title,
        difficulty=difficulty,
        topics=statement.topics or list(config.generation.topics),
        statement_md=statement.statement_md,
        constraints_md=constraints.constraints_md,
        constraints=constraints.constraints,
        signatures=[
            Signature(
                language=language,
                function_name=statement.function_name,
                scaffold=reference.scaffold,
                reference_solution=reference.reference_solution,
            )
        ],
        visible_tests=reference.visible_tests,
        hidden_generator_py=harness.hidden_generator_py,
        brute_force_py=harness.brute_force_py,
    )
    return result
