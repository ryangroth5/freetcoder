"""Ask for a question as delimited plain text rather than JSON.

Source code does not survive a JSON string. Its newlines have to be escaped as
`\\n`, and measured across four models asked for the same Python solution,
three returned the body with no line breaks at all -- the whole function on one
line, joined by double spaces. For Python that is fatal, because indentation is
syntax, and it is not repairable.

Telling the model not to did not help; it is a serialisation habit, not a
comprehension failure. So this removes the escaping problem instead of
detecting it: code is written between markers, exactly as it would be in a
file, and parsed back out here.

The small structured values -- an example's arguments, its expected result --
stay JSON, because they are single-line and have no escaping problem.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..models import TestCase

#: `=== NAME ===` on its own line. Case-insensitive, tolerant of extra `=`.
SECTION = re.compile(r"^\s*={2,}\s*(?P<name>[A-Za-z0-9_. ]+?)\s*={2,}\s*$", re.M)


class ParseError(ValueError):
    """The reply did not contain the sections we need."""


@dataclass
class Parsed:
    """One delimited reply, split into its parts."""

    sections: dict[str, str]

    def text(self, name: str, *, required: bool = True) -> str:
        value = self.sections.get(name.lower(), "").strip()
        if required and not value:
            raise ParseError(f"missing or empty section: === {name.upper()} ===")
        return value

    def code(self, name: str, *, required: bool = True) -> str:
        """A code section, with any stray Markdown fence removed.

        Models wrap code in ``` even when told the markers are the delimiter.
        That is cosmetic and worth tolerating rather than rejecting over.
        """
        body = self.sections.get(name.lower(), "")
        body = re.sub(r"^\s*```[a-zA-Z]*\s*\n", "", body)
        body = re.sub(r"\n\s*```\s*$", "", body)
        body = body.strip("\n")
        if required and not body.strip():
            raise ParseError(f"missing or empty section: === {name.upper()} ===")
        return body


def parse_sections(reply: str) -> Parsed:
    """Split `=== NAME ===` delimited text into its sections.

    Repeated names accumulate, so several `=== CASE ===` blocks are kept in
    order rather than overwriting each other.
    """
    matches = list(SECTION.finditer(reply))
    if not matches:
        raise ParseError("no `=== SECTION ===` markers found in the reply")

    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        name = match.group("name").strip().lower()
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(reply)
        body = reply[start:end]
        sections[name] = f"{sections[name]}\n{body}" if name in sections else body
    return Parsed(sections=sections)


def parse_cases(blocks: str) -> list[TestCase]:
    """Worked examples, one `args:`/`expected:` pair per block.

    The values are single-line JSON: they are short, have no newlines, and
    round-trip cleanly. It is only code that JSON cannot carry.
    """
    cases: list[TestCase] = []
    args: dict[str, object] | None = None
    expected: object = None
    why: str | None = None
    seen = False

    def flush() -> None:
        nonlocal args, expected, why, seen
        if seen and args is not None:
            cases.append(TestCase(args=args, expected=expected, explanation=why))
        args, expected, why, seen = None, None, None, False

    for line in blocks.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "args":
            flush()
            try:
                args = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ParseError(f"args is not JSON: {value[:80]}") from exc
            if not isinstance(args, dict):
                raise ParseError("args must be an object of parameter -> value")
            seen = True
        elif key == "expected" and seen:
            try:
                expected = json.loads(value)
            except json.JSONDecodeError as exc:
                raise ParseError(f"expected is not JSON: {value[:80]}") from exc
        elif key in {"why", "explanation", "note"} and seen:
            why = value or None
    flush()

    if not cases:
        raise ParseError("no worked examples found; each needs an `args:` line")
    return cases


@dataclass
class DelimitedDraft:
    """A parsed reply, in the same shape the flat strategy produces."""

    title: str
    statement_md: str
    function_name: str
    parameter_names: list[str]
    scaffold: str
    reference_solution: str
    visible_tests: list[TestCase]


SIGNATURE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\((?P<params>[^)]*)\)"
)


def parse_question(reply: str) -> DelimitedDraft:
    """Turn one delimited reply into a draft, or raise ParseError."""
    parsed = parse_sections(reply)

    signature = parsed.text("function")
    match = SIGNATURE.search(signature)
    if match is None:
        raise ParseError(f"FUNCTION is not a signature: {signature[:80]}")
    params = [
        p.strip().split("=")[0].strip().split(":")[0].strip()
        for p in match.group("params").split(",")
        if p.strip()
    ]

    return DelimitedDraft(
        title=parsed.text("title").splitlines()[0].strip(),
        statement_md=parsed.text("statement"),
        function_name=match.group("name"),
        parameter_names=params,
        scaffold=parsed.code("scaffold"),
        reference_solution=parsed.code("solution"),
        visible_tests=parse_cases(parsed.sections.get("case", "")),
    )


#: The fields a bound may carry, matching ParamConstraint.
_BOUND_KEYS = {
    "min", "max", "min_length", "max_length", "element_min", "element_max",
}


def parse_bounds(blocks: str) -> list[dict[str, object]]:
    """Bounds as flat `key: value` lines, one `=== BOUND ===` block each.

    The constraints call was the last thing still asking for JSON, and it was
    the last thing failing: a nested list of objects is the shape models drop
    fields from. Flat lines have nowhere to lose a `name`.
    """
    bounds: list[dict[str, object]] = []
    current: dict[str, object] | None = None

    for line in blocks.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if not stripped:
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            continue
        key = key.strip().lower().replace(" ", "_")
        value = value.strip().strip("`")
        if key == "name":
            if current is not None and "name" in current:
                bounds.append(current)
            current = {"name": value}
        elif key in _BOUND_KEYS and current is not None:
            try:
                number = float(value)
            except ValueError:
                continue   # a word where a number belongs; skip it, not fatal
            current[key] = int(number) if key.endswith("_length") else number

    if current is not None and "name" in current:
        bounds.append(current)
    if not bounds:
        raise ParseError("no bounds found; each needs a `name:` line")
    return bounds


def parse_tests(reply: str) -> tuple[str, str | None]:
    """The hidden-test machinery: a generator, and an optional brute force.

    Written after the bounds exist rather than alongside the statement. Asked
    for together, the generator was produced before any bounds had been chosen
    and emitted cases outside them -- `constraint_violation`, twice out of
    four, on input the question promised could not occur.
    """
    parsed = parse_sections(reply)
    generator = parsed.code("generator")
    brute = parsed.code("brute", required=False)
    return generator, (None if brute.strip().lower() in {"", "none"} else brute)
