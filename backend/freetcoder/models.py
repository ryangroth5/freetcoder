"""The generated-question schema.

This is the LLM contract in both directions: `model_json_schema()` is handed to
the provider as a structured-output spec, and validation on the way back is the
solvability gate's first check. Keep it strict -- a field the model may fudge is
a field the gate has to defend.
"""

from __future__ import annotations

import enum
from typing import Annotated

from pydantic import BaseModel, Field, field_validator


class Difficulty(enum.StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class Language(enum.StrEnum):
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    GO = "go"


class TestCase(BaseModel):
    """One input/output pair.

    (`__test__ = False` keeps pytest from trying to collect this as a test
    class purely because of its name.)

    `args` is a mapping of parameter name to JSON value so the UI can render
    each argument in its own labelled box, the way LeetCode does, rather than
    dumping one opaque blob.
    """

    __test__ = False

    args: dict[str, object] = Field(description="Parameter name -> value")
    expected: object = Field(description="Expected return value")
    explanation: str | None = Field(default=None, max_length=600)


class Signature(BaseModel):
    """The function the candidate must implement, for one language."""

    language: Language
    function_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    scaffold: str = Field(description="Starter code shown in the editor")
    reference_solution: str = Field(description="A known-correct implementation")


class GeneratedQuestion(BaseModel):
    """A question as the LLM produces it -- untrusted until the gate passes it."""

    title: Annotated[str, Field(min_length=3, max_length=120)]
    difficulty: Difficulty
    topics: list[str] = Field(default_factory=list, max_length=8)

    statement_md: str = Field(
        min_length=80,
        description="Markdown. May contain ```mermaid fences and inline images.",
    )
    constraints_md: str = Field(description="Markdown bullet list of input bounds")
    hint_md: str | None = Field(
        default=None, description="Omitted for formats that give no candidate support"
    )
    complexity_target: str | None = Field(
        default=None, description="e.g. 'O(n log n)'; set when the format demands it"
    )

    signatures: list[Signature] = Field(min_length=1)
    visible_tests: list[TestCase] = Field(min_length=1, max_length=4)

    #: Python program printing one JSON case per line: {"args": {...}}.
    #: Expected outputs are NOT taken from the model -- the gate computes them
    #: with the reference solution, so a confused model cannot ship a wrong oracle.
    hidden_generator_py: str = Field(
        description="Python program printing one JSON object per line with an 'args' key"
    )

    #: A deliberately naive implementation. The gate uses it to prove the
    #: performance tests actually discriminate -- it must agree with the
    #: reference on small inputs and time out on large ones.
    brute_force_py: str | None = None

    @field_validator("topics")
    @classmethod
    def _clean_topics(cls, v: list[str]) -> list[str]:
        return [t.strip().lower() for t in v if t.strip()]

    def signature_for(self, language: Language) -> Signature | None:
        return next((s for s in self.signatures if s.language is language), None)


class GateOutcome(enum.StrEnum):
    """Why a generated question was accepted or rejected."""

    ACCEPTED = "accepted"
    SCHEMA_INVALID = "schema_invalid"
    REFERENCE_FAILED = "reference_failed"
    GENERATOR_FAILED = "generator_failed"
    VISIBLE_MISMATCH = "visible_mismatch"
    NO_HIDDEN_CASES = "no_hidden_cases"
    CONSTRAINT_VIOLATION = "constraint_violation"
    UNSAFE_MAGNITUDE = "unsafe_magnitude"
    MISSING_BRUTE_FORCE = "missing_brute_force"
    BRUTE_FORCE_DISAGREES = "brute_force_disagrees"
    PERF_NOT_DISCRIMINATING = "perf_not_discriminating"


class GateReport(BaseModel):
    """The verdict on one candidate question."""

    outcome: GateOutcome
    detail: str = ""
    hidden_cases: list[TestCase] = Field(default_factory=list)
    reference_ms: int = 0
    #: Reference timing per language. A JS submission judged against a
    #: Python-derived budget is being measured against the wrong yardstick.
    reference_ms_by_language: dict[str, int] = Field(default_factory=dict)

    @property
    def accepted(self) -> bool:
        return self.outcome is GateOutcome.ACCEPTED


class GatedQuestion(BaseModel):
    """A question that survived the gate, plus what the gate computed.

    This is the unit that gets cached and served: the hidden cases carry
    reference-computed expected values, so serving never re-runs the gate.
    """

    question: GeneratedQuestion
    hidden_tests: list[TestCase]
    reference_ms: int = 0
    reference_ms_by_language: dict[str, int] = Field(default_factory=dict)
    language: Language = Language.PYTHON

    @property
    def title(self) -> str:
        return self.question.title
