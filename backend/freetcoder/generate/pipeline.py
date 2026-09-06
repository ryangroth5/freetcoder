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
from ..llm import LLMClient, LLMError
from ..models import (
    Difficulty,
    GatedQuestion,
    GateOutcome,
    GeneratedQuestion,
    Language,
)
from .gate import validate_question

log = logging.getLogger(__name__)

PROMPT_DIR = Path(__file__).parent / "prompts"


def _read_prompt(name: str) -> str:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"no prompt template {name!r} at {path}")
    return path.read_text(encoding="utf-8")


def build_user_prompt(
    config: FormatConfig, difficulty: Difficulty, *, exclude_titles: list[str] | None = None
) -> str:
    """Assemble the per-question instruction from the resolved format."""
    gen = config.generation
    parts: list[str] = [_read_prompt(gen.style)]

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


@dataclass(slots=True)
class GenerationResult:
    question: GatedQuestion | None
    attempts: list[GenerationAttempt] = field(default_factory=list)

    @property
    def accepted(self) -> bool:
        return self.question is not None


async def generate_question(
    client: LLMClient,
    config: FormatConfig,
    *,
    difficulty: Difficulty | None = None,
    language: Language = Language.PYTHON,
    max_attempts: int = 4,
    exclude_titles: list[str] | None = None,
) -> GenerationResult:
    """Produce one gate-approved question, or report why we could not."""
    difficulty = difficulty or config.session.difficulty_for(0)
    system = _read_prompt("system")
    user = build_user_prompt(config, difficulty, exclude_titles=exclude_titles)
    result = GenerationResult(question=None)

    for attempt in range(max_attempts):
        try:
            candidate = await client.complete_json(
                system=system, user=user, schema=GeneratedQuestion, temperature=0.8
            )
        except LLMError as exc:
            result.attempts.append(
                GenerationAttempt(GateOutcome.SCHEMA_INVALID, str(exc)[:300])
            )
            continue

        report = validate_question(
            candidate,
            language=language,
            languages=list(config.environment.languages),
        )
        result.attempts.append(
            GenerationAttempt(report.outcome, report.detail[:300], candidate.title)
        )

        if report.accepted:
            result.question = GatedQuestion(
                question=candidate,
                hidden_tests=report.hidden_cases,
                reference_ms=report.reference_ms,
                language=language,
            )
            return result

        log.info(
            "attempt %d rejected (%s): %s", attempt + 1, report.outcome.value, report.detail
        )
        user = (
            f"{user}\n\n### Your previous attempt was rejected\n\n"
            f"Reason: **{report.outcome.value}**\n{report.detail}\n\n"
            f"A harness executed your code to determine this, so the problem is "
            f"real. Fix that specific issue and return the corrected question."
        )

    return result
