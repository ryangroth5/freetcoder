"""Machine-checkable quality metrics for a generated question.

The gate answers "is this solvable?". These answer "is this any good?" -- the
questions the gate structurally cannot ask, because it reads only executable
artifacts. Written after a generation shipped with its description missing and
its examples lifted verbatim from a published problem.

Pure functions over a question, so the bench and the tests share one definition
of quality rather than each inventing their own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..models import GeneratedQuestion

#: Canonical examples from widely-published problems. Matching one is strong
#: evidence the model recalled rather than authored: inventing [2,7,11,15] with
#: target 9 by chance does not happen.
#:
#: Deliberately small and exact. A fuzzy "does this look like Two Sum" test
#: would flag legitimate array questions; an exact input match will not.
KNOWN_EXAMPLES: dict[str, list[dict[str, object]]] = {
    "two sum": [{"nums": [2, 7, 11, 15], "target": 9}],
    "subarray sums divisible by k": [{"nums": [4, 5, 0, -2, -3, 1], "k": 5}],
    "maximum subarray": [{"nums": [-2, 1, -3, 4, -1, 2, 1, -5, 4]}],
    "longest substring without repeating characters": [{"s": "abcabcbb"}],
    "container with most water": [{"height": [1, 8, 6, 2, 5, 4, 8, 3, 7]}],
    "trapping rain water": [{"height": [0, 1, 0, 2, 1, 0, 1, 3, 2, 1, 2, 1]}],
    "product of array except self": [{"nums": [1, 2, 3, 4]}],
    "best time to buy and sell stock": [{"prices": [7, 1, 5, 3, 6, 4]}],
    "valid parentheses": [{"s": "()[]{}"}],
    "group anagrams": [{"strs": ["eat", "tea", "tan", "ate", "nat", "bat"]}],
    "merge intervals": [{"intervals": [[1, 3], [2, 6], [8, 10], [15, 18]]}],
    "climbing stairs": [{"n": 2}, {"n": 3}],
    "coin change": [{"coins": [1, 2, 5], "amount": 11}],
    "longest common subsequence": [{"text1": "abcde", "text2": "ace"}],
    "number of islands": [{"grid": [["1", "1", "0"], ["0", "1", "0"]]}],
}

#: Titles of well-known problems, for the weaker title-only signal.
KNOWN_TITLES: frozenset[str] = frozenset(KNOWN_EXAMPLES) | frozenset({
    "3sum", "two sum ii", "reverse linked list", "merge two sorted lists",
    "binary search", "valid anagram", "top k frequent elements",
    "longest palindromic substring", "word break", "house robber",
    "edit distance", "course schedule", "lru cache", "spiral matrix",
    "rotate image", "set matrix zeroes", "search in rotated sorted array",
    "kth largest element in an array", "min stack", "meeting rooms",
})


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", text.lower()).strip()


def _words(text: str) -> set[str]:
    return {w for w in _normalise(text).split() if len(w) > 2}


def parameter_names(q: GeneratedQuestion) -> list[str]:
    names: list[str] = []
    for case in q.visible_tests:
        for name in case.args:
            if name not in names:
                names.append(name)
    return names


@dataclass
class QualityReport:
    """One question, scored. Every field is machine-derived."""

    title: str = ""
    #: The prose names every parameter the candidate is handed.
    names_all_parameters: bool = False
    #: Worked examples appear in the statement, not only in the data.
    shows_examples: bool = False
    #: Says something about empty, zero or degenerate input.
    addresses_edge_cases: bool = False
    statement_chars: int = 0
    constraints_chars: int = 0
    #: A machine-checkable bound exists for every parameter.
    bounds_every_parameter: bool = False
    clarification_count: int = 0
    has_hint: bool = False
    #: A complexity target was stated. The gate measures it separately; this
    #: only records whether the question makes the claim at all.
    states_complexity: bool = False
    #: How many languages the candidate can actually attempt this in. A
    #: question offering fewer than its format promises is unservable.
    language_count: int = 0
    #: Matched a published problem's canonical example inputs, or its title.
    recalled_example: str = ""
    recalled_title: str = ""
    #: Highest word overlap with any exemplar shown in the prompt, 0.0-1.0.
    exemplar_overlap: float = 0.0

    #: Filled in by the bench, not derivable from the question alone.
    outcome: str = ""
    repairs_used: int = 0
    accepted_first_pass: bool = False
    #: Wall clock for the whole question, and the part spent waiting on the
    #: provider. The gap between them is our own sandbox work.
    seconds: float = 0.0
    provider_seconds: float = 0.0
    completion_tokens: int = 0
    #: Translation into the format's other languages: how many were attempted
    #: and how many produced a reference that agreed with the Python oracle.
    #: A plain counter rather than a bench dimension -- translating one function
    #: is a conformance check, not a strategy, so it has a success rate and not
    #: a quality score.
    translations_attempted: int = 0
    translations_ok: int = 0

    @property
    def looks_recalled(self) -> bool:
        return bool(self.recalled_example or self.recalled_title)

    @property
    def prose_complete(self) -> bool:
        """The properties a candidate needs before the question is fair."""
        return (
            self.names_all_parameters
            and self.bounds_every_parameter
            and self.shows_examples
            and self.constraints_chars > 0
        )


EDGE_WORDS = (
    "empty", "no such", "none exist", "zero", "single", "duplicate",
    "negative", "tie", "if there is no", "when no", "at most one",
)


def score_question(
    q: GeneratedQuestion, *, exemplars: list[str] | None = None
) -> QualityReport:
    """Everything measurable about a question's prose and provenance."""
    body = q.statement_md.lower()
    params = parameter_names(q)

    rendered = 0
    for case in q.visible_tests:
        # An example counts as shown when one of its argument values appears in
        # the prose -- the data alone is not the statement.
        for value in case.args.values():
            token = json.dumps(value).strip('"')
            if len(token) >= 3 and token.lower() in body:
                rendered += 1
                break

    report = QualityReport(
        title=q.title,
        names_all_parameters=bool(params) and all(p.lower() in body for p in params),
        shows_examples=rendered >= max(1, len(q.visible_tests) - 1),
        addresses_edge_cases=any(w in body for w in EDGE_WORDS),
        statement_chars=len(q.statement_md),
        constraints_chars=len(q.constraints_md.strip()),
        bounds_every_parameter=(
            bool(params) and {c.name for c in q.constraints} >= set(params)
        ),
        clarification_count=len(q.clarifications),
        has_hint=bool(q.hint_md and q.hint_md.strip()),
        states_complexity=bool(q.complexity_target and q.complexity_target.strip()),
        language_count=len(q.signatures),
    )

    title_key = _normalise(q.title)
    if title_key in KNOWN_TITLES:
        report.recalled_title = title_key

    for known, examples in KNOWN_EXAMPLES.items():
        for example in examples:
            for case in q.visible_tests:
                if case.args == example:
                    report.recalled_example = known
                    break

    for exemplar in exemplars or []:
        shown, generated = _words(exemplar), _words(q.statement_md)
        if shown and generated:
            overlap = len(shown & generated) / len(shown)
            report.exemplar_overlap = max(report.exemplar_overlap, overlap)

    return report


@dataclass
class Scorecard:
    """A run of one variant, aggregated.

    `attempted` is tracked separately from `reports` on purpose. A rejected
    question has no prose to score, so prose rates are over accepted questions
    while acceptance is over everything asked for. Averaging prose over
    accepted-only *and* calling that the variant's score would flatter a prompt
    that produces one immaculate question and two rejects.
    """

    variant: str = ""
    attempted: int = 0
    reports: list[QualityReport] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    def _rate(self, attr: str) -> float:
        if not self.reports:
            return 0.0
        return sum(bool(getattr(r, attr)) for r in self.reports) / len(self.reports)

    def summary(self) -> dict[str, float | int | str]:
        n = len(self.reports)
        return {
            "variant": self.variant,
            "asked": self.attempted,
            "accepted": n,
            "accept_rate": round(n / self.attempted, 2) if self.attempted else 0.0,
            "first_pass": round(self._rate("accepted_first_pass"), 2),
            "prose_complete": round(self._rate("prose_complete"), 2),
            "names_params": round(self._rate("names_all_parameters"), 2),
            "bounds_params": round(self._rate("bounds_every_parameter"), 2),
            "shows_examples": round(self._rate("shows_examples"), 2),
            "edge_cases": round(self._rate("addresses_edge_cases"), 2),
            "looks_recalled": round(self._rate("looks_recalled"), 2),
            "median_statement": (
                sorted(r.statement_chars for r in self.reports)[n // 2] if n else 0
            ),
            "max_exemplar_overlap": round(
                max((r.exemplar_overlap for r in self.reports), default=0.0), 2
            ),
            "mean_repairs": round(
                sum(r.repairs_used for r in self.reports) / n if n else 0.0, 2
            ),
            "median_seconds": (
                sorted(r.seconds for r in self.reports)[n // 2] if n else 0.0
            ),
            "median_llm_s": (
                sorted(r.provider_seconds for r in self.reports)[n // 2] if n else 0.0
            ),
            "median_tokens": (
                sorted(r.completion_tokens for r in self.reports)[n // 2] if n else 0
            ),
        }
