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

import json
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
from ..runner import run_python
from ..runner.adapters import check_syntax
from ..runner.types import Verdict
from .delimited import (
    DelimitedDraft,
    ParseError,
    parse_bounds,
    parse_question,
    parse_sections,
    parse_tests,
)
from .gate import (
    GENERATOR_LIMITS,
    REFERENCE_LIMITS,
    _failure_detail,
    _run_cases,
    decode_generated_args,
)
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
    #: The module source, when the strategy produced one.
    source: str = ""
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


def prose_fault(statement_md: str, parameters: list[str]) -> str:
    """Does the prose describe what the candidate is handed?

    The same rule the gate applies, run here where it costs one small call to
    fix instead of the whole question. `prose_too_thin` was the dominant
    rejection in every measured run, and by the time the gate says so the
    reference, generator and brute force have all been written for nothing.

    Deterministic and free -- no model, no sandbox.
    """
    body = statement_md.lower()
    missing = [p for p in parameters if p.lower() not in body]
    if missing:
        return (
            "the statement never mentions "
            + ", ".join(repr(p) for p in missing)
            + "; name and describe every parameter in the prose"
        )
    return ""


def constraints_fault(
    draft: ConstraintsDraft, parameters: list[str]
) -> str:
    """Is there a bound for every parameter?

    The gate's third prose rule, and the one the in-stage statement check does
    not cover: a statement can name every parameter and still arrive with no
    bound for one of them. An empty or partial constraints list also makes the
    gate's case checking pass vacuously, so this is not cosmetic.
    """
    named = {c.name for c in draft.constraints}
    missing = [p for p in parameters if p not in named]
    if missing:
        return (
            "no bound given for "
            + ", ".join(repr(p) for p in missing)
            + "; every parameter needs one entry in `constraints`, named exactly"
        )
    if not draft.constraints_md.strip():
        return "constraints_md is empty; the candidate reads that, not the data"
    return ""


def flattened_code_fault(source: str, language: Language) -> str:
    """Did the newlines survive the JSON round trip?

    Measured across four models asked for the same Python solution:
    deepseek-chat, glm-4.6 and kimi-k2.5 all returned the body with no line
    breaks, joined by double spaces --

        def max_fruits(tree):  # sliding window  left = 0  res = 0  for ...

    Only deepseek-v4.1-flash emitted real newlines. For Python this is fatal,
    because indentation is syntax, and it is not repairable: the block
    structure is gone.

    The test is the real parser, not a pattern. A first version guessed from
    keywords and waved glm-4.6 straight through -- zero newlines, not flagged.
    Parsing is exact, and it keeps `def f(x): return x`, which is legal, from
    being rejected.
    """
    if "\n" in source.strip():
        return ""
    if check_syntax(language, source).verdict is Verdict.OK:
        return ""   # a genuine one-liner
    return (
        "the code arrived on a single line with no line breaks and does not "
        "parse -- indentation is syntax. Emit real newlines inside the JSON "
        "string, one statement per line, indented."
    )


def reference_fault(
    *,
    scaffold: str,
    reference_solution: str,
    function_name: str,
    visible_tests: list[TestCase],
    language: Language,
) -> str:
    """Does this solution actually parse, and produce the examples it claims?

    One sandboxed run, against the draft's own examples. It costs a fraction of
    a second and it is the difference between a stage that catches its own
    mistakes and one that hands them downstream.
    """
    # Cheapest first, and the one whose SyntaxError explains nothing.
    for label, code in (("scaffold", scaffold), ("reference", reference_solution)):
        if fault := flattened_code_fault(code, language):
            return f"{label}: {fault}"

    syntax = check_syntax(language, scaffold)
    if syntax.verdict is not Verdict.OK:
        return f"the scaffold does not parse: {syntax.stderr.strip()[:200]}"

    verdict, results, stderr = _run_cases(
        reference_solution,
        function_name,
        list(visible_tests),
        REFERENCE_LIMITS,
        language,
    )
    if verdict is not Verdict.OK:
        return (
            "the reference did not run cleanly on your own examples: "
            + _failure_detail(verdict, results, stderr)
        )

    for case, got in zip(visible_tests, results, strict=False):
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
    if gen.source == "imported" and gen.import_text.strip():
        # Supplied prose is the same input as a freeform request, with more
        # words in it: the candidate saying what they want the problem to be.
        # `import.md` carries the extra duty that comes with it -- decide what
        # the prose leaves open rather than inheriting its ambiguity.
        parts.append(
            f"\n{_read_prompt('import')}\n\n"
            "### The candidate's text\n\n```\n"
            f"{gen.import_text.strip()}\n```"
        )
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
            check=lambda d: prose_fault(d.statement_md, list(d.parameter_names)),
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
            check=lambda draft: reference_fault(
                scaffold=draft.scaffold,
                reference_solution=draft.reference_solution,
                function_name=statement.function_name,
                visible_tests=list(draft.visible_tests),
                language=language,
            ),
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
            check=lambda c: constraints_fault(c, list(statement.parameter_names)),
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


class FlatDraft(BaseModel):
    """Everything except the constraints, with nothing nested that models fumble.

    The signature is flat -- `function_name`, `scaffold`, `reference_solution`
    at top level -- because `signatures` as a list of objects is where the shape
    failures land. Captured from a live provider: it simply does not produce
    that list, it writes the fields flat and we rejected the result.

    `visible_tests` stays nested because models produce it reliably; the
    failures there were wrong *values*, which execution catches.

    `clarifications` is dropped from this call entirely. It was the other
    reliable source of shape failures, and it is optional to a valid question.
    """

    title: str = Field(min_length=3, max_length=120)
    statement_md: str = Field(min_length=200)
    function_name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    scaffold: str = Field(min_length=10)
    reference_solution: str = Field(min_length=20)
    visible_tests: list[TestCase] = Field(min_length=1, max_length=4)
    hidden_generator_py: str = Field(min_length=20)
    brute_force_py: str | None = None
    topics: list[str] = Field(default_factory=list, max_length=8)


async def generate_flat(
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
    """One wide call with no nested signature, then constraints on their own.

    The middle option between one fourteen-field request and four narrow ones.
    Measured, every shape failure landed in a nested list, so this removes the
    lists rather than the breadth -- two calls instead of four, while the main
    call still validates its own reference before anything downstream runs.
    """
    difficulty = difficulty or config.session.difficulty_for(0)
    chosen = scenario or pick(rng)
    result = StagedResult(scenario=chosen)
    style = _read_prompt(config.generation.style)
    brief = _brief(config, difficulty, chosen)

    try:
        report_to("writing the question")
        draft = await _ask(
            client,
            name="question",
            system=_read_prompt("stage_flat"),
            user=(
                f"{style}\n\n{brief}\n\n"
                f"Target language: {language.value}.\n"
                f"Show {config.environment.visible_tests} worked example(s).\n"
                f"Produce at least {config.scoring.hidden_test_count} hidden cases."
            ),
            schema=FlatDraft,
            temperature=0.8,
            tries=tries_per_stage,
            outcomes=result.stages,
            check=lambda d: prose_fault(
                d.statement_md,
                sorted({k for t in d.visible_tests for k in t.args}),
            ) or reference_fault(
                scaffold=d.scaffold,
                reference_solution=d.reference_solution,
                function_name=d.function_name,
                visible_tests=list(d.visible_tests),
                language=language,
            ),
        )

        parameters = sorted({k for t in draft.visible_tests for k in t.args})
        report_to("deriving the constraints")
        constraints = await _ask(
            client,
            name="constraints",
            system=_read_prompt("stage_constraints"),
            user=(
                f"# {draft.title}\n\n{draft.statement_md}\n\n"
                f"Reference solution:\n\n```\n{draft.reference_solution}\n```\n\n"
                f"Parameters: {', '.join(parameters)}"
            ),
            schema=ConstraintsDraft,
            temperature=0.2,
            tries=tries_per_stage,
            outcomes=result.stages,
            check=lambda c: constraints_fault(c, parameters),
        )
    except LLMError:
        return result

    result.question = GeneratedQuestion(
        title=draft.title,
        difficulty=difficulty,
        topics=draft.topics or list(config.generation.topics),
        statement_md=draft.statement_md,
        constraints_md=constraints.constraints_md,
        constraints=constraints.constraints,
        signatures=[
            Signature(
                language=language,
                function_name=draft.function_name,
                scaffold=draft.scaffold,
                reference_solution=draft.reference_solution,
            )
        ],
        visible_tests=draft.visible_tests,
        hidden_generator_py=draft.hidden_generator_py,
        brute_force_py=draft.brute_force_py,
    )
    return result


async def _delimited_question(
    client: LLMClient,
    *,
    system: str,
    user: str,
    language: Language,
    tries: int,
    outcome: StageOutcome,
) -> DelimitedDraft | None:
    """Ask for the question as text, parse it, and check it here."""
    ask = user
    last = ""
    for _ in range(tries):
        outcome.attempts += 1
        try:
            with telemetry.stage("question"):
                reply = await client.complete_text(
                    system=system, user=ask, temperature=0.8
                )
            candidate = parse_question(reply)
        except (LLMError, ParseError) as exc:
            last = str(exc)[:200]
            ask = f"{user}\n\nYour previous answer could not be read: {last}"
            continue

        fault = prose_fault(
            candidate.statement_md, list(candidate.parameter_names)
        ) or reference_fault(
            scaffold=candidate.scaffold,
            reference_solution=candidate.reference_solution,
            function_name=candidate.function_name,
            visible_tests=list(candidate.visible_tests),
            language=language,
        )
        if not fault:
            return candidate
        last = fault
        ask = (
            f"{user}\n\nYour previous answer was rejected: {fault}\n"
            "Fix exactly that and send the whole thing again."
        )
    outcome.error = last or "no usable response"
    return None


async def _delimited_bounds(
    client: LLMClient,
    *,
    user: str,
    parameters: list[str],
    tries: int,
    outcome: StageOutcome,
) -> tuple[str, list[ParamConstraint]] | None:
    """Bounds as flat `key: value` lines rather than a nested JSON list.

    This was the last call still asking for JSON, and the last one failing:
    three delimited runs got the whole question through and then lost the
    `name` on every bound. Flat lines have nowhere to drop a field.
    """
    ask = user
    last = ""
    for _ in range(tries):
        outcome.attempts += 1
        try:
            with telemetry.stage("constraints"):
                reply = await client.complete_text(
                    system=_read_prompt("stage_bounds"), user=ask, temperature=0.2
                )
            parsed = parse_sections(reply)
            prose = parsed.text("bounds")
            bounds = [
                ParamConstraint.model_validate(b)
                for b in parse_bounds(parsed.sections.get("bound", ""))
            ]
        except (LLMError, ParseError, ValueError) as exc:
            last = str(exc)[:200]
            ask = f"{user}\n\nYour previous answer could not be read: {last}"
            continue

        missing = [p for p in parameters if p not in {b.name for b in bounds}]
        if not missing:
            return prose, bounds
        last = (
            "no bound given for " + ", ".join(repr(p) for p in missing)
            + "; every parameter needs its own `=== BOUND ===` block"
        )
        ask = f"{user}\n\nYour previous answer was rejected: {last}"
    outcome.error = last or "no bounds produced"
    return None


async def _delimited_tests(
    client: LLMClient,
    *,
    user: str,
    bounds: list[ParamConstraint],
    wanted: int,
    tries: int,
    outcome: StageOutcome,
) -> tuple[str, str | None] | None:
    """The generator, written after the bounds and checked against them.

    Asked for alongside the statement, the generator was written before any
    bounds existed and emitted cases outside them -- `constraint_violation`
    twice in four, on input the question promised could not occur. Running it
    here turns that into one retry instead of a discarded question.
    """
    ask = user
    last = ""
    for _ in range(tries):
        outcome.attempts += 1
        try:
            with telemetry.stage("tests"):
                reply = await client.complete_text(
                    system=_read_prompt("stage_tests"), user=ask, temperature=0.3
                )
            generator, brute = parse_tests(reply)
        except (LLMError, ParseError) as exc:
            last = str(exc)[:200]
            ask = f"{user}\n\nYour previous answer could not be read: {last}"
            continue

        fault = flattened_code_fault(generator, Language.PYTHON)
        if not fault:
            run = run_python(generator, limits=GENERATOR_LIMITS)
            cases = _decode_generated(run.stdout, [b.name for b in bounds])
            if run.verdict is not Verdict.OK:
                fault = f"the generator did not run: {run.stderr.strip()[:200]}"
            elif len(cases) < wanted:
                fault = (
                    f"the generator printed {len(cases)} case(s); at least "
                    f"{wanted} are needed, one JSON object per line"
                )
            else:
                fault = _cases_within(cases, bounds)

        if not fault:
            return generator, brute
        last = fault
        ask = f"{user}\n\nYour previous answer was rejected: {fault}"
    outcome.error = last or "no usable response"
    return None


def _decode_generated(
    stdout: str, parameters: list[str]
) -> list[dict[str, object]]:
    """Shares the gate's decoder, including its tolerance of positional args."""
    cases: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        args = decode_generated_args(row, parameters)
        if args is not None:
            cases.append(args)
    return cases


def _cases_within(
    cases: list[dict[str, object]], bounds: list[ParamConstraint]
) -> str:
    """The gate's own constraint check, run where a retry is cheap."""
    by_name = {b.name: b for b in bounds}
    for args in cases:
        for name, value in args.items():
            bound = by_name.get(name)
            if bound is None:
                continue
            if isinstance(value, list | str):
                size = len(value)
                if bound.min_length is not None and size < bound.min_length:
                    return f"{name!r} of size {size} is below the stated minimum"
                if bound.max_length is not None and size > bound.max_length:
                    return f"{name!r} of size {size} exceeds the stated maximum"
                if isinstance(value, list):
                    for item in value:
                        if not isinstance(item, int | float):
                            continue
                        if (bound.element_min is not None
                                and item < bound.element_min):
                            return f"{name!r} contains {item}, below its bound"
                        if (bound.element_max is not None
                                and item > bound.element_max):
                            return f"{name!r} contains {item}, above its bound"
            elif isinstance(value, int | float):
                if bound.min is not None and value < bound.min:
                    return f"{name!r} is {value}, below the stated minimum"
                if bound.max is not None and value > bound.max:
                    return f"{name!r} is {value}, above the stated maximum"
    return ""


async def generate_delimited(
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
    """Two calls, neither of them JSON.

    Source code does not survive a JSON string: three of four models measured
    returned whole functions on one line. This asks for the same content
    between `=== MARKERS ===`, so code is written exactly as it would be in a
    file and nothing needs escaping.
    """
    difficulty = difficulty or config.session.difficulty_for(0)
    chosen = scenario or pick(rng)
    result = StagedResult(scenario=chosen)

    question_outcome = StageOutcome(name="question")
    result.stages.append(question_outcome)
    report_to("writing the question")
    draft = await _delimited_question(
        client,
        system=_read_prompt("stage_delimited"),
        user=(
            f"{_read_prompt(config.generation.style)}\n\n"
            f"{_brief(config, difficulty, chosen)}\n\n"
            f"Target language: {language.value}.\n"
            f"Show {config.environment.visible_tests} worked example(s).\n"
            f"Produce at least {config.scoring.hidden_test_count} hidden cases."
        ),
        language=language,
        tries=tries_per_stage,
        outcome=question_outcome,
    )
    if draft is None:
        return result

    parameters = sorted({k for t in draft.visible_tests for k in t.args})
    bounds_outcome = StageOutcome(name="constraints")
    result.stages.append(bounds_outcome)
    report_to("deriving the constraints")
    got = await _delimited_bounds(
        client,
        user=(
            f"# {draft.title}\n\n{draft.statement_md}\n\n"
            f"Reference solution:\n\n```\n{draft.reference_solution}\n```\n\n"
            f"Parameters: {', '.join(parameters)}"
        ),
        parameters=parameters,
        tries=tries_per_stage,
        outcome=bounds_outcome,
    )
    if got is None:
        return result
    constraints_md, bounds = got

    tests_outcome = StageOutcome(name="tests")
    result.stages.append(tests_outcome)
    report_to("building the hidden tests")
    machinery = await _delimited_tests(
        client,
        user=(
            f"# {draft.title}\n\n{draft.statement_md}\n\n"
            f"Reference solution:\n\n```\n{draft.reference_solution}\n```\n\n"
            f"Bounds every case must obey:\n{constraints_md}\n\n"
            f"Produce at least {config.scoring.hidden_test_count} cases."
        ),
        bounds=bounds,
        wanted=config.scoring.hidden_test_count,
        tries=tries_per_stage,
        outcome=tests_outcome,
    )
    if machinery is None:
        return result
    hidden_generator_py, brute_force_py = machinery

    result.question = GeneratedQuestion(
        title=draft.title,
        difficulty=difficulty,
        topics=list(config.generation.topics),
        statement_md=draft.statement_md,
        constraints_md=constraints_md,
        constraints=bounds,
        signatures=[
            Signature(
                language=language,
                function_name=draft.function_name,
                scaffold=draft.scaffold,
                reference_solution=draft.reference_solution,
            )
        ],
        visible_tests=draft.visible_tests,
        hidden_generator_py=hidden_generator_py,
        brute_force_py=brute_force_py,
    )
    return result
