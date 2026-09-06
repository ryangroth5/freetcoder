"""Turning execution results into a score the way each platform does.

The three modes are not cosmetic. Binary (LeetCode) says solved or not.
Proportional (Codility) rewards partial credit and reports correctness and
performance separately. Composite (CodeSignal, Coderbyte) folds in how fast the
candidate was, which is why speed is a first-class input here rather than a
statistic shown afterwards.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from .formats import FormatConfig, ScoringMode


class CaseOutcome(BaseModel):
    """Per-case result, as shown in the Test Result tab."""

    index: int
    passed: bool
    hidden: bool = False
    ms: float = 0.0
    over_budget: bool = False
    #: What the submission printed during this case. Debugging with print() is
    #: routine, so it is surfaced rather than swallowed.
    stdout: str = ""
    #: False for a candidate-authored case with no expected value: run it and
    #: show the output, but do not call it right or wrong.
    judged: bool = True
    #: What the submission returned. Populated only for visible cases -- hidden
    #: inputs and outputs must stay hidden.
    actual: object | None = None


class QuestionScore(BaseModel):
    """Score for a single question."""

    correctness: float = Field(ge=0, le=1)
    performance: float = Field(default=1.0, ge=0, le=1)
    speed: float = Field(default=1.0, ge=0, le=1)
    total: float = Field(ge=0, le=1)
    passed_cases: int = 0
    total_cases: int = 0
    solved: bool = False

    @property
    def percent(self) -> int:
        return round(self.total * 100)


def speed_factor(elapsed_s: float, allowed_s: float | None) -> float:
    """Credit for finishing early, on the platforms that reward it.

    Full credit for the first quarter of the budget, then a linear decay to a
    floor of 0.4 -- a correct-but-slow answer is still worth most of a correct
    one, which is how Coderbyte's time-period points behave.
    """
    if not allowed_s or allowed_s <= 0:
        return 1.0
    used = max(0.0, elapsed_s) / allowed_s
    if used <= 0.25:
        return 1.0
    if used >= 1.0:
        return 0.4
    return 1.0 - 0.6 * ((used - 0.25) / 0.75)


def score_question(
    outcomes: list[CaseOutcome],
    config: FormatConfig,
    *,
    elapsed_s: float = 0.0,
    allowed_s: float | None = None,
) -> QuestionScore:
    """Score one question's submission under the resolved format."""
    # An unjudged case has no expected value, so it can be neither passed nor
    # failed. Run is never scored anyway, but excluding them here means a
    # candidate's own exploratory cases can never influence a grade.
    judged = [o for o in outcomes if o.judged]
    graded = [o for o in judged if o.hidden] or judged
    total_cases = len(graded)
    passed = sum(1 for o in graded if o.passed)
    correctness = passed / total_cases if total_cases else 0.0
    solved = total_cases > 0 and passed == total_cases

    # Performance is measured only over cases that were actually correct:
    # penalising a wrong answer twice would double-count it.
    correct = [o for o in graded if o.passed]
    if not correct:
        # Nothing correct means nothing to be fast about. Defaulting to 1.0 here
        # handed an empty submission full marks on the performance component --
        # worth 20% of a composite score, which floated a blank GCA to 312/600.
        performance = 0.0
    elif config.scoring.perf_tests:
        within = sum(1 for o in correct if not o.over_budget)
        performance = within / len(correct)
    else:
        performance = 1.0

    speed = speed_factor(elapsed_s, allowed_s)

    mode = config.scoring.mode
    if mode is ScoringMode.BINARY:
        total = 1.0 if solved else 0.0
    elif mode is ScoringMode.PROPORTIONAL:
        # Codility reports correctness and performance separately, but the task
        # score reflects both.
        total = correctness * performance if config.scoring.perf_tests else correctness
    else:
        s = config.scoring
        total = (
            s.weight_correctness * correctness
            + s.weight_speed * speed
            + s.weight_performance * performance
        )

    return QuestionScore(
        correctness=correctness,
        performance=performance,
        speed=speed,
        total=min(1.0, max(0.0, total)),
        passed_cases=passed,
        total_cases=total_cases,
        solved=solved,
    )


class SessionScore(BaseModel):
    """Aggregate across a session, expressed in the format's native scale."""

    per_question: list[QuestionScore]
    fraction: float = Field(ge=0, le=1)
    native: int
    scale_label: str

    @property
    def solved_count(self) -> int:
        return sum(1 for q in self.per_question if q.solved)


def score_session(scores: list[QuestionScore], config: FormatConfig) -> SessionScore:
    """Combine question scores into the number the platform would report."""
    fraction = sum(q.total for q in scores) / len(scores) if scores else 0.0

    band = config.scoring.score_range
    if band:
        low, high = band
        native = round(low + fraction * (high - low))
        label = f"{low}-{high}"
    else:
        native = round(fraction * 100)
        label = "percent"

    return SessionScore(
        per_question=scores, fraction=fraction, native=native, scale_label=label
    )
