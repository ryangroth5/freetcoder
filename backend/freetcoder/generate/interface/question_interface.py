"""The contract a generated question implements.

This file is handed to the model verbatim. It is a specification it can read
rather than prose about one, which is the whole point: models are tuned to
implement an interface inside an agent harness, and every serialisation failure
we measured came from asking for code inside a data format instead.

Module-level names, not a class. They translate to exported functions in
TypeScript and package functions in Go; a class does not.

Nothing here is imported by the app. It exists to be read, and to be checked
against by `module.py`.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from typing import Any

# --------------------------------------------------------------------------
# Prose. The candidate reads these and nothing else.
# --------------------------------------------------------------------------

#: A short name for the problem. Not a slug, not a URL.
TITLE: str = ""

#: Markdown. State the task in one sentence, then name and describe every
#: parameter in backticks using the exact identifiers from `solution`, say what
#: is returned and in what order, show each example from EXAMPLES inline with
#: its numbers and a reason, and settle the degenerate cases in words -- empty
#: input, no valid answer, ties, duplicates, negatives.
STATEMENT: str = ""

#: Markdown bullet list of the bounds, one line per parameter. This is the
#: human half of `is_valid`; the two must agree.
CONSTRAINTS: str = ""

#: The techniques this question exercises, two or three of them, lower case:
#: ["arrays", "hash maps"]. They label the question in the UI and tell a
#: candidate what they are practising. Name what the *solution* needs, not the
#: story it is dressed in -- "sliding window", not "ferries".
TOPICS: list[str] = []

#: Markdown. One nudge toward the approach, not the approach itself: name the
#: idea ("a sliding window keeps this linear"), never the code. Leave it ""
#: unless the brief asks for a hint.
HINT: str = ""

#: The intended time bound of `solution`, e.g. "O(n log n)" or "O(n)". Leave it
#: "" unless the brief asks for one. When set it is measured: the reference is
#: timed across growing inputs and a claim it does not meet rejects the
#: question, so state what `solution` actually achieves rather than the best
#: bound you know of.
COMPLEXITY: str = ""

#: The questions the statement leaves open, answered. Each is
#: `{"question": "...", "answer": "...", "probe": {parameter_name: value}}`.
#:
#: `probe` is a small input that demonstrates the answer -- if the answer is
#: "ties break toward the lower index", `probe` is an input containing a tie.
#: It is run through `solution` and the result is shown alongside, so do not
#: supply an expected value: you would only be guessing at your own code.
#:
#: Prefer the ambiguities a careful candidate would actually raise -- empty
#: input, ties, duplicates, case sensitivity -- over restating CONSTRAINTS.
CLARIFICATIONS: list[dict[str, Any]] = []


# --------------------------------------------------------------------------
# Code. Ordinary Python -- no escaping, no delimiters, no JSON.
# --------------------------------------------------------------------------


def solution(*args: Any, **kwargs: Any) -> Any:
    """The correct implementation, and the oracle for every expected value.

    Give it real parameter names and type hints: the function name, the
    parameter names and the starter code shown to the candidate are all taken
    from this signature, so it is the single source of truth for the shape of
    the problem.
    """
    raise NotImplementedError


def brute_force(*args: Any, **kwargs: Any) -> Any:
    """The obvious, slow implementation of the same function.

    The nested loop or the exhaustive search -- not a second clever one. It is
    run against `solution` on small inputs, and disagreement rejects the
    question. Two independent implementations agreeing is the only evidence we
    have that either is right.

    Same signature as `solution`.
    """
    raise NotImplementedError


def is_valid(*args: Any, **kwargs: Any) -> bool:
    """Are these arguments within the stated constraints?

    Same signature as `solution`, returning True when the arguments are legal
    input for this problem. This is the machine-checkable twin of CONSTRAINTS:
    every generated case is passed through it, and a case the question says
    cannot occur must not be used to grade anyone.

    It must actually test something. A body that ignores its arguments and
    returns True is rejected.
    """
    raise NotImplementedError


def generate_cases(rng: random.Random) -> Iterator[dict[str, Any]]:
    """Yield hidden test inputs as `{parameter_name: value}` mappings.

    Use the `rng` given rather than the `random` module, so a run is
    reproducible. Yield at least as many as asked for.

    Cover what a plausible-but-wrong solution would survive: the smallest legal
    input, the largest, all-equal values, ties, duplicates, negatives where the
    bounds allow, and the shape that defeats a greedy or off-by-one attempt.

    Yield inputs only. Expected values come from running `solution`, never from
    you -- that is what makes them trustworthy.
    """
    raise NotImplementedError


#: Worked examples, in the order they appear in STATEMENT. Each is
#: `{"args": {parameter_name: value}, "why": "one line"}`.
#:
#: These are ordinary Python literals: tuples, sets and negative numbers are all
#: fine, and nothing needs escaping.
EXAMPLES: list[dict[str, Any]] = []
