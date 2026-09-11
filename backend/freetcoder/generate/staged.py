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
from .pipeline import _read_prompt

#: Settings a published problem does not already own. The point is not the
#: domain itself but that it is not "arrays"; it displaces the search key.
SCENARIOS: tuple[str, ...] = (
    "a tide gauge logging sea levels",
    "a warehouse picker walking one aisle",
    "a nurse rota of shift swaps",
    "a bakery tracking loaves through a day",
    "a bike-share dock counting departures",
    "a seismograph recording tremor amplitudes",
    "a greenhouse logging overnight temperatures",
    "a ferry timetable with sailings and delays",
    "a library tracking loans and returns",
    "a call centre logging queue lengths",
    "a beekeeper weighing hives through a season",
    "a locksmith recording key cuttings",
)


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
) -> M:
    """One stage, retried alone.

    This is the whole point of staging: a bad reference costs a reference call,
    not the statement, constraints and harness that were already fine.
    """
    outcome = StageOutcome(name=name)
    outcomes.append(outcome)
    last = ""
    for _ in range(tries):
        outcome.attempts += 1
        try:
            with telemetry.stage(name):
                return await client.complete_json(
                    system=system, user=user, schema=schema, temperature=temperature
                )
        except LLMError as exc:
            last = str(exc)[:200]
    outcome.error = last or "no usable response"
    raise LLMError(f"stage {name!r} failed: {outcome.error}")


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
    chosen = scenario or (rng or random).choice(SCENARIOS)
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
