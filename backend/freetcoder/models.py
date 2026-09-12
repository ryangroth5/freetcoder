"""The generated-question schema.

This is the LLM contract in both directions: `model_json_schema()` is handed to
the provider as a structured-output spec, and validation on the way back is the
solvability gate's first check. Keep it strict -- a field the model may fudge is
a field the gate has to defend.
"""

from __future__ import annotations

import enum
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator, model_validator


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


class ParamConstraint(BaseModel):
    """Machine-checkable bounds for one parameter.

    The prose in `constraints_md` is for the candidate; this is for the gate.
    Without it nothing stops a generator emitting n = 10^6 while the statement
    promises n <= 10^4 -- grading someone on input the question told them could
    not occur.
    """

    name: str = Field(description="Parameter name, matching the signature")
    min: float | None = Field(default=None, description="Minimum, for a number")
    max: float | None = Field(default=None, description="Maximum, for a number")
    min_length: int | None = Field(default=None, description="For a list or string")
    max_length: int | None = Field(default=None, description="For a list or string")
    element_min: float | None = Field(default=None, description="Minimum element")
    element_max: float | None = Field(default=None, description="Maximum element")


class Clarification(BaseModel):
    """A question the prose leaves open, with a verified answer.

    Supplied prose is routinely ambiguous: "find the word that appears most
    often" does not say whether punctuation splits words, whether case matters,
    or what a tie does. The candidate reads the statement; the grader runs the
    reference. Without these, the reference *is* the specification and nobody
    can read it.

    The gate executes `probe` against the reference and rejects the question if
    the code disagrees with `expect` -- the model asserts, the harness checks.
    """

    question: str = Field(min_length=3, max_length=200)
    answer: str = Field(min_length=1, max_length=400)
    #: Arguments demonstrating the answer, keyed by parameter name.
    probe: dict[str, object] = Field(default_factory=dict)
    #: What the reference returns for `probe`.
    expect: object = None


class Signature(BaseModel):
    """The function the candidate must implement, for one language."""

    language: Language
    function_name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    scaffold: str = Field(description="Starter code shown in the editor")
    reference_solution: str = Field(description="A known-correct implementation")


def _pair(value: object) -> tuple[float | None, float | None]:
    """`[lo, hi]` as models write bounds, in either order."""
    if isinstance(value, list | tuple) and len(value) == 2:
        lo, hi = value
        if isinstance(lo, int | float) and isinstance(hi, int | float):
            return (min(lo, hi), max(lo, hi))
    return (None, None)


def _normalise_constraint(raw: dict[str, object]) -> dict[str, object]:
    """Accept `size`/`value_range` alongside our own field names."""
    out = dict(raw)
    kind = str(raw.get("type") or "").lower()
    collection = kind in {"array", "list", "string", "str"} or "length" in str(
        raw.get("name", "")
    ).lower()

    lo, hi = _pair(raw.get("size") or raw.get("length"))
    if lo is not None:
        out.setdefault("min_length", int(lo))
        out.setdefault("max_length", int(hi))  # type: ignore[arg-type]

    lo, hi = _pair(raw.get("value_range") or raw.get("range"))
    if lo is not None:
        # For a collection the range describes its elements; for a scalar it
        # describes the value itself. Getting this backwards would silently
        # mis-bound every generated case.
        if collection or "min_length" in out:
            out.setdefault("element_min", lo)
            out.setdefault("element_max", hi)
        else:
            out.setdefault("min", lo)
            out.setdefault("max", hi)
    return out


class GeneratedQuestion(BaseModel):
    """A question as the LLM produces it -- untrusted until the gate passes it."""

    @model_validator(mode="before")
    @classmethod
    def _accept_the_natural_shape(cls, data: Any) -> Any:
        """Take the shape models actually emit, not only the one we asked for.

        Measured against a real provider: with `strict: False` the schema is a
        hint, and the model returns a perfectly good question in its own
        shape -- the signature flattened to top level, `difficulty: "Medium"`,
        bounds as `size: [lo, hi]` and `value_range: [lo, hi]`, an example's
        note under `description`. Every one of those was a rejection, and the
        question inside was usually fine.

        Rejecting a good question over its nesting is a bug in us, not the
        model. This is also the cheapest route to model-agnosticism: the same
        drift shows up across providers because the flat shape is simply the
        obvious one.
        """
        if not isinstance(data, dict):
            return data
        d = dict(data)

        # "Medium" -> "medium". A three-value enum is not worth a retry.
        if isinstance(d.get("difficulty"), str):
            d["difficulty"] = d["difficulty"].strip().lower()

        # A signature flattened to top level, which is what models default to
        # when only one language was asked for.
        if not d.get("signatures") and d.get("reference_solution"):
            d["signatures"] = [{
                "language": str(d.get("language") or "python").strip().lower(),
                "function_name": d.get("function_name") or "solve",
                "scaffold": d.get("scaffold") or "",
                "reference_solution": d["reference_solution"],
            }]

        # Only dicts need translating. Constraints already built as models --
        # the staged pipeline constructs them directly -- must pass through
        # untouched, or they are silently dropped.
        d["constraints"] = [
            _normalise_constraint(c) if isinstance(c, dict) else c
            for c in (d.get("constraints") or [])
        ]

        for case in d.get("visible_tests") or []:
            if isinstance(case, dict) and "explanation" not in case:
                note = case.get("description") or case.get("note")
                if note:
                    case["explanation"] = note
        return d


    title: Annotated[str, Field(min_length=3, max_length=120)]
    difficulty: Difficulty
    topics: list[str] = Field(default_factory=list, max_length=8)

    statement_md: str = Field(
        min_length=80,
        description="Markdown. May contain ```mermaid fences and inline images.",
    )
    constraints_md: str = Field(
        min_length=10,
        description="Markdown bullet list of input bounds",
    )
    #: The same bounds as data, so the gate can check them. Must agree with the
    #: prose: they are two renderings of one promise to the candidate.
    constraints: list[ParamConstraint] = Field(
        default_factory=list,
        description="Machine-checkable version of constraints_md, one per parameter",
    )
    #: Answers to the questions the prose leaves open. Verified by execution.
    clarifications: list[Clarification] = Field(
        default_factory=list, max_length=8,
        description="Ambiguities the statement does not settle, with a probe each",
    )
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

    #: What the model changed or assumed while adapting supplied text. Empty for
    #: a question generated from scratch. Shown to the candidate: being told
    #: "I assumed ties break toward the earliest word" is the difference between
    #: a surprising failure and an informed one.
    import_notes: str = ""

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
    CLARIFICATION_WRONG = "clarification_wrong"
    UNSAFE_MAGNITUDE = "unsafe_magnitude"
    MISSING_BRUTE_FORCE = "missing_brute_force"
    SCAFFOLD_INVALID = "scaffold_invalid"
    REFERENCE_TOO_SLOW = "reference_too_slow"
    STATEMENT_INSUFFICIENT = "statement_insufficient"
    PROSE_TOO_THIN = "prose_too_thin"
    BRUTE_FORCE_DISAGREES = "brute_force_disagrees"
    PERF_NOT_DISCRIMINATING = "perf_not_discriminating"


class GateReport(BaseModel):
    """The verdict on one candidate question."""

    outcome: GateOutcome
    detail: str = ""
    hidden_cases: list[TestCase] = Field(default_factory=list)
    reference_ms: int = 0
    measured_growth: str = ""
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
    #: Observed growth of the reference across input sizes, e.g. "~linear".
    #: Shown beside the ratio so "1.3x the reference" says what the reference is.
    measured_growth: str = ""
    #: Provenance, carried through storage and across the library boundary.
    #: Recording it costs nothing now and cannot be reconstructed later.
    source: str = "generated"
    import_text: str = ""
    language: Language = Language.PYTHON

    @property
    def title(self) -> str:
        return self.question.title
