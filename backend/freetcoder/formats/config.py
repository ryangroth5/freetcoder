"""The format configuration object.

One choice on the setup screen fans out into three consumers: how the coding
environment is set up, how the question is prompted for, and how the attempt is
scored. Keeping that in a single validated object is what stops the three from
drifting apart.
"""

from __future__ import annotations

import enum

from pydantic import BaseModel, Field, model_validator

from ..models import Difficulty, Language


class Timing(enum.StrEnum):
    TOTAL = "total"            # one clock for the whole assessment (CodeSignal)
    PER_QUESTION = "per_question"
    UNTIMED = "untimed"        # self-paced practice (LeetCode)


class Scaffold(enum.StrEnum):
    FUNCTION_SIGNATURE = "function_signature"
    FULL_PROGRAM = "full_program"
    CLASS_STUB = "class_stub"


class ScoringMode(enum.StrEnum):
    BINARY = "binary"              # accepted / not
    PROPORTIONAL = "proportional"  # fraction of tests passed (Codility)
    COMPOSITE = "composite"        # correctness + speed + performance (CodeSignal)


class SessionConfig(BaseModel):
    question_count: int = Field(default=1, ge=1, le=10)
    timing: Timing = Timing.UNTIMED
    total_seconds: int | None = Field(default=None, ge=60)
    per_question_seconds: int | None = Field(default=None, ge=60)
    allow_skip: bool = True
    allow_revisit: bool = True
    difficulty_curve: list[Difficulty] = Field(default_factory=list)

    @model_validator(mode="after")
    def _timing_is_coherent(self) -> SessionConfig:
        if self.timing is Timing.TOTAL and not self.total_seconds:
            raise ValueError("timing=total requires total_seconds")
        if self.timing is Timing.PER_QUESTION and not self.per_question_seconds:
            raise ValueError("timing=per_question requires per_question_seconds")
        if self.difficulty_curve and len(self.difficulty_curve) != self.question_count:
            raise ValueError(
                f"difficulty_curve has {len(self.difficulty_curve)} entries "
                f"but question_count is {self.question_count}"
            )
        return self

    def difficulty_for(self, index: int) -> Difficulty:
        if self.difficulty_curve:
            return self.difficulty_curve[index]
        return Difficulty.MEDIUM


class GenerationConfig(BaseModel):
    style: str = "leetcode"
    prose_length: str = Field(default="medium", pattern="^(short|medium|long)$")
    worked_example: bool = True
    state_complexity_target: bool = False
    allow_diagrams: bool = True
    real_world_framing: bool = False
    give_hints: bool = True
    topics: list[str] = Field(default_factory=list)
    #: Which tier-2 preset was chosen, and what it means. The id keeps cached
    #: questions from two presets of one style from colliding; the intent is
    #: what actually differentiates the generated question.
    preset_id: str = ""
    preset_intent: str = ""
    #: Free-text steer from the picker's "Other..." field. Narrows the style;
    #: never overrides its structural rules.
    freeform: str = ""


class EnvironmentConfig(BaseModel):
    languages: list[Language] = Field(default=[Language.PYTHON], min_length=1)
    scaffold: Scaffold = Scaffold.FUNCTION_SIGNATURE
    visible_tests: int = Field(default=2, ge=1, le=4)


class ScoringConfig(BaseModel):
    mode: ScoringMode = ScoringMode.BINARY
    weight_correctness: float = Field(default=1.0, ge=0, le=1)
    weight_speed: float = Field(default=0.0, ge=0, le=1)
    weight_performance: float = Field(default=0.0, ge=0, le=1)
    hidden_test_count: int = Field(default=12, ge=1, le=40)
    perf_tests: bool = False
    #: Native score band, e.g. CodeSignal's 200-600. None means percentage.
    score_range: tuple[int, int] | None = None

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> ScoringConfig:
        if self.mode is ScoringMode.COMPOSITE:
            total = self.weight_correctness + self.weight_speed + self.weight_performance
            if abs(total - 1.0) > 1e-6:
                raise ValueError(f"composite weights must sum to 1.0, got {total}")
        return self


class FormatConfig(BaseModel):
    """A fully resolved assessment format."""

    id: str
    label: str
    description: str = ""
    session: SessionConfig = Field(default_factory=SessionConfig)
    generation: GenerationConfig = Field(default_factory=GenerationConfig)
    environment: EnvironmentConfig = Field(default_factory=EnvironmentConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    #: Presets that model a real fixed assessment lock difficulty: a GCA
    #: simulation is four ascending questions by definition, so letting the user
    #: pick "all hard" would stop it being a simulation of anything.
    difficulty_locked: bool = False

    def summary(self) -> str:
        """The one-line confirmation shown before generation starts."""
        bits = [f"{self.session.question_count} question"
                + ("s" if self.session.question_count != 1 else "")]
        if self.session.timing is Timing.TOTAL and self.session.total_seconds:
            bits.append(f"{self.session.total_seconds // 60} min total")
        elif self.session.timing is Timing.PER_QUESTION and self.session.per_question_seconds:
            bits.append(f"{self.session.per_question_seconds // 60} min each")
        else:
            bits.append("untimed")
        bits.append(", ".join(lang.value for lang in self.environment.languages))
        bits.append(f"hidden tests: {self.scoring.hidden_test_count}")
        if self.scoring.perf_tests:
            bits.append("performance graded")
        return " · ".join(bits)
