"""Loading styles and resolving the three picker tiers into one FormatConfig.

`resolve()` is the only place the tiers merge. The UI and the generator CLI both
call it, so the button path and the freeform path cannot drift apart.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from ..models import Difficulty
from .config import FormatConfig

PRESET_DIR = Path(__file__).parent / "presets"

#: Tier-3 concentrations. Two need plumbing beyond the default executable path.
TOPIC_VOCABULARY: tuple[str, ...] = (
    "arrays & strings",
    "hash maps",
    "two pointers",
    "sliding window",
    "trees & graphs",
    "dynamic programming",
    "sorting & searching",
    "recursion",
    "math",
    "greedy",
    "intervals",
    "sql",
    "regex / parsing",
    "concurrency",
    "system design",
)

#: Concentrations the executable pipeline cannot serve yet, surfaced in the UI
#: rather than silently producing something broken.
UNSUPPORTED_TOPICS: dict[str, str] = {
    "sql": "SQL questions need the SQLite runner adapter (not built yet).",
    "system design": (
        "System design has no executable answer, so the solvability gate cannot "
        "apply; it needs LLM-rubric grading (not built yet)."
    ),
}


class StylePreset(BaseModel):
    """A tier-2 preset: a named set of overrides within a style."""

    id: str
    label: str
    #: One sentence of guidance for the generator. Without it a preset's effects
    #: reach the config but its *intent* never reaches the prompt, so e.g.
    #: "Blind 75" and "Top interview 150" produced near-identical questions.
    intent: str = ""
    difficulty: list[Difficulty] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    question_count: int | None = None
    total_seconds: int | None = None
    perf_tests: bool | None = None
    state_complexity_target: bool | None = None
    real_world_framing: bool | None = None


class Style(BaseModel):
    """A tier-1 style: base config plus the presets available inside it."""

    config: FormatConfig
    presets: list[StylePreset]


class UnknownStyleError(KeyError):
    pass


class DifficultyLockedError(ValueError):
    """Raised when a difficulty override is applied to a fixed-format preset."""


@lru_cache
def load_styles() -> dict[str, Style]:
    """Read and validate every preset YAML. Cached: they never change at runtime."""
    styles: dict[str, Style] = {}
    for path in sorted(PRESET_DIR.glob("*.yaml")):
        raw: dict[str, Any] = yaml.safe_load(path.read_text())
        presets = [StylePreset.model_validate(p) for p in raw.pop("presets", [])]
        config = FormatConfig.model_validate(raw)
        styles[config.id] = Style(config=config, presets=presets)
    return styles


def get_style(style_id: str) -> Style:
    styles = load_styles()
    if style_id not in styles:
        raise UnknownStyleError(
            f"unknown style {style_id!r}; available: {sorted(styles)}"
        )
    return styles[style_id]


def resolve(
    style_id: str,
    preset_id: str | None = None,
    topics: list[str] | None = None,
    freeform: str = "",
    *,
    difficulty: Difficulty | None = None,
    import_text: str = "",
) -> FormatConfig:
    """Merge the three picker tiers into one config.

    Precedence, narrowest last: style defaults -> tier-2 preset -> tier-3 topics
    and freeform. The freeform text is carried as an *additional* constraint on
    the prompt; it never rewrites the style's structural rules (question count,
    timing, scoring), which is what keeps "GCA" meaning GCA.
    """
    style = get_style(style_id)
    config = style.config.model_copy(deep=True)

    if preset_id:
        preset = next((p for p in style.presets if p.id == preset_id), None)
        if preset is None:
            raise KeyError(
                f"style {style_id!r} has no preset {preset_id!r}; "
                f"available: {[p.id for p in style.presets]}"
            )
        _apply_preset(config, preset)

    if difficulty is not None:
        if config.difficulty_locked:
            raise DifficultyLockedError(
                f"{config.label} defines its own difficulty curve; it cannot be overridden"
            )
        config.session.difficulty_curve = [difficulty] * config.session.question_count

    if topics:
        config.generation.topics = _normalise_topics(topics)
    if freeform.strip():
        config.generation.freeform = freeform.strip()
    if import_text.strip():
        # Supplied prose to adapt, as opposed to a topic steer. The style's
        # structural rules still apply -- an imported question is still a
        # LeetCode or a Codility question.
        config.generation.source = "imported"
        config.generation.import_text = import_text.strip()

    # Re-validate: preset overrides can produce an incoherent combination
    # (e.g. a question_count that no longer matches the difficulty curve).
    return FormatConfig.model_validate(config.model_dump())


def _apply_preset(config: FormatConfig, preset: StylePreset) -> None:
    config.generation.preset_id = preset.id
    config.generation.preset_intent = preset.intent or preset.label
    if preset.question_count is not None:
        config.session.question_count = preset.question_count
    if preset.total_seconds is not None:
        config.session.total_seconds = preset.total_seconds
    if preset.difficulty:
        config.session.difficulty_curve = list(preset.difficulty)
    elif preset.question_count is not None and config.session.difficulty_curve:
        # Curve length must track question_count or validation fails.
        curve = config.session.difficulty_curve
        config.session.difficulty_curve = (
            curve * preset.question_count
        )[: preset.question_count]
    if preset.topics:
        config.generation.topics = _normalise_topics(preset.topics)
    if preset.perf_tests is not None:
        config.scoring.perf_tests = preset.perf_tests
    if preset.state_complexity_target is not None:
        config.generation.state_complexity_target = preset.state_complexity_target
    if preset.real_world_framing is not None:
        config.generation.real_world_framing = preset.real_world_framing


def _normalise_topics(topics: list[str]) -> list[str]:
    return [t.strip().lower() for t in topics if t.strip()]


def unsupported_topics(topics: list[str]) -> dict[str, str]:
    """Which of these concentrations cannot be served yet, and why."""
    return {
        t: UNSUPPORTED_TOPICS[t]
        for t in _normalise_topics(topics)
        if t in UNSUPPORTED_TOPICS
    }
