"""Generate a question, then prove it is usable.

The loop is deliberately simple: ask, gate, and on rejection ask again with the
gate's complaint attached. Feeding the failure back matters -- a model that
produced a wrong oracle will usually fix it when told exactly which example
disagreed, but will reproduce the same mistake if simply asked again.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..formats import FormatConfig
from ..llm import LLMClient, LLMError, telemetry
from ..models import (
    Difficulty,
    GatedQuestion,
    GateOutcome,
    GateReport,
    GeneratedQuestion,
    Language,
)
from ..progress import NULL_REPORTER, Reporter
from .gate import validate_question
from .repair import repair_question
from .scenarios import brief as scenario_brief
from .sufficiency import check_statement_sufficiency

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"


def _read_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt template {name!r} at {path}")
    return path.read_text(encoding="utf-8")


def build_user_prompt(
    config: FormatConfig,
    difficulty: Difficulty,
    *,
    exclude_titles: list[str] | None = None,
    scenario: str = "",
) -> str:
    """Assemble the per-question instruction from the resolved format."""
    gen = config.generation
    parts: list[str] = [_read_prompt(gen.style)]
    if scenario:
        parts.append(scenario_brief(scenario))

    if gen.source == "imported" and gen.import_text:
        # Layered *after* the style so an imported question is still a LeetCode
        # or a Codility question -- the source text supplies the problem, not
        # the format.
        parts.append(f"\n{_read_prompt('import')}")
        parts.append(
            "\n### The candidate's text\n\n"
            "```\n" + gen.import_text + "\n```"
        )

    parts.append(f"\n## This question\n\n- Difficulty: **{difficulty.value}**")
    langs = ", ".join(lang.value for lang in config.environment.languages)
    parts.append(f"- Target language: {langs}")
    parts.append(f"- Show exactly {config.environment.visible_tests} worked example(s).")
    parts.append(f"- Produce at least {config.scoring.hidden_test_count} hidden cases.")

    if gen.preset_intent:
        parts.append(f"- The candidate chose this drill: {gen.preset_intent}")
    if gen.topics:
        parts.append(f"- Concentrate on: {', '.join(gen.topics)}.")
    if gen.state_complexity_target or config.scoring.perf_tests:
        parts.append(
            "- State an explicit `complexity_target`, and make the hidden cases "
            "large enough that a naive solution cannot finish them."
        )
    if gen.real_world_framing:
        parts.append("- Use a concrete real-world framing.")
    if not gen.give_hints:
        parts.append("- Do NOT provide `hint_md`; this format gives no support.")
    else:
        parts.append("- Provide a short `hint_md` that nudges without giving the answer.")
    if not gen.allow_diagrams:
        parts.append("- Do not include diagrams.")

    if gen.freeform:
        # Deliberately framed as an *additional* constraint: the style's
        # structural rules above still win.
        parts.append(
            f"\n### Additional focus requested by the candidate\n\n"
            f"{gen.freeform}\n\n"
            f"Honour this within the format rules above; it refines the topic, "
            f"it does not change the format."
        )

    if exclude_titles:
        parts.append(
            "\n### Already used in this session -- pick something different\n\n"
            + "\n".join(f"- {t}" for t in exclude_titles)
        )
    return "\n".join(parts)


@dataclass(slots=True)
class GenerationAttempt:
    """One trip through generate-and-gate, for the acceptance-rate report."""

    outcome: GateOutcome
    detail: str = ""
    title: str = ""
    #: True when this round patched an existing question rather than asking for
    #: a new one, so the acceptance report can tell the two apart.
    repaired: bool = False


@dataclass(slots=True)
class GenerationResult:
    question: GatedQuestion | None
    attempts: list[GenerationAttempt] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.question is not None


def _accept(
    result: GenerationResult,
    question: GeneratedQuestion,
    report: GateReport,
    language: Language,
    config: FormatConfig,
) -> GenerationResult:
    result.question = _gated(question, report, language, config)
    return result


def _gated(
    question: GeneratedQuestion,
    report: GateReport,
    language: Language,
    config: FormatConfig | None = None,
) -> GatedQuestion:
    """Package an accepted question with everything the gate computed."""
    gen = config.generation if config else None
    return GatedQuestion(
        question=question,
        hidden_tests=report.hidden_cases,
        reference_ms=report.reference_ms,
        reference_ms_by_language=report.reference_ms_by_language,
        measured_growth=report.measured_growth,
        language=language,
        source=gen.source if gen else "generated",
        import_text=gen.import_text if gen else "",
    )


async def generate_question(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    max_attempts: int = 4,
    repair_rounds: int = 3,
    tool_budget: int = 6,
    check_sufficiency: bool = True,
    exclude_titles: list[str] | None = None,
    system_extra: str = "",
    scenario: str = "",
    report_to: Reporter = NULL_REPORTER,
) -> GenerationResult:
    """Produce one gate-approved question, or report why we could not.

    Each attempt generates, then *repairs* -- patching the artifact the gate
    objected to rather than discarding a question that may be mostly right.
    Only when repair is exhausted does it ask for a fresh question.
    """
    difficulty = difficulty or config.session.difficulty_for(0)
    # `system_extra` is how the bench swaps prompt variants without forking the
    # pipeline. Empty in normal use, so the shipped path is the measured one.
    system = _read_prompt("system") + system_extra
    user = build_user_prompt(
        config, difficulty, exclude_titles=exclude_titles, scenario=scenario
    )
    result = GenerationResult(question=None)

    for attempt in range(max_attempts):
        report_to.checkpoint()
        report_to(
            "asking the model for a question"
            if attempt == 0
            else f"asking for a fresh question (attempt {attempt + 1})"
        )
        try:
            with telemetry.stage("generate"):
                candidate = await client.complete_json(
                    system=system, user=user, schema=GeneratedQuestion,
                    temperature=0.8,
                )
        except LLMError as exc:
            report_to(f"the model did not answer usefully: {exc}"[:200], kind="warn")
            result.attempts.append(
                GenerationAttempt(GateOutcome.SCHEMA_INVALID, str(exc)[:300])
            )
            continue

        report_to(f"validating \u201c{candidate.title}\u201d", kind="ok")

        report = validate_question(
            candidate,
            language=language,
            languages=list(config.environment.languages),
            report_to=report_to,
        )
        result.attempts.append(
            GenerationAttempt(report.outcome, report.detail[:300], candidate.title)
        )

        if report.accepted:
            # The gate proves the question is internally sound. It never reads
            # the statement, so this is where we find out whether a candidate
            # could actually derive the answer from what they are given.
            if check_sufficiency:
                gap = await check_statement_sufficiency(
                    client, candidate, report.hidden_cases,
                    language=language, report_to=report_to,
                )
                if gap is not None:
                    report = gap
                    result.attempts.append(
                        GenerationAttempt(gap.outcome, gap.detail[:300], candidate.title)
                    )
                    report_to(f"rejected: {gap.detail}"[:300], kind="warn")
                else:
                    report_to("the question passed every check", kind="ok")
                    return _accept(result, candidate, report, language, config)
            else:
                report_to("the question passed every check", kind="ok")
                return _accept(result, candidate, report, language, config)

        log.info(
            "attempt %d rejected (%s): %s", attempt + 1, report.outcome.value, report.detail
        )
        report_to(f"rejected: {report.detail}"[:300], kind="warn")

        # Try to fix what is broken before throwing the whole thing away.
        if repair_rounds > 0:
            repaired, final_report, history = await repair_question(
                client, candidate, report,
                language=language,
                languages=list(config.environment.languages),
                rounds=repair_rounds,
                tool_budget=tool_budget,
                report_to=report_to,
            )
            for outcome in history:
                result.attempts.append(
                    GenerationAttempt(outcome, title=candidate.title, repaired=True)
                )
            if repaired is not None:
                # A repaired question is re-checked for sufficiency too: a
                # rewritten statement is exactly the thing that might still not
                # say enough.
                gap = (
                    await check_statement_sufficiency(
                        client, repaired, final_report.hidden_cases,
                        language=language, report_to=report_to,
                    )
                    if check_sufficiency else None
                )
                if gap is None:
                    report_to("repaired, and it now passes", kind="ok")
                    return _accept(result, repaired, final_report, language, config)
                report = gap
                result.attempts.append(
                    GenerationAttempt(gap.outcome, gap.detail[:300],
                                      repaired.title, repaired=True)
                )
            report = final_report
        user = (
            f"{user}\n\n### Your previous attempt was rejected\n\n"
            f"Reason: **{report.outcome.value}**\n{report.detail}\n\n"
            f"A harness executed your code to determine this, so the problem is "
            f"real. Fix that specific issue and return the corrected question."
        )

    return result
