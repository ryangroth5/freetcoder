"""Tools the model may call while repairing a question.

Every tool executes through `runner.run_source` -- the same sandboxed path a
candidate's submission takes. There is deliberately no second way to run code:
model-written code is exactly as untrusted as candidate-written code, and it
runs under the same unprivileged uid, rlimits and seccomp filter.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ..models import Language, TestCase
from ..runner import Limits, run_source
from .harness import decode_results, encode_cases, get_harness_for

log = logging.getLogger(__name__)

#: Tighter than the gate's budget. A model probing its own code should get a
#: fast answer, and a runaway probe should not stall question generation.
TOOL_LIMITS = Limits(wall_seconds=10.0, cpu_seconds=8, memory_mb=512)

#: Truncation for tool results. Enough to diagnose, small enough not to swamp
#: the context window with output from a runaway loop.
MAX_TOOL_OUTPUT = 4000


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_code",
            "description": (
                "Execute a complete program and see its output. Use this to check "
                "that a hidden-case generator prints what you expect, or that code "
                "parses at all."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript"],
                    },
                    "source": {"type": "string", "description": "The full program"},
                    "stdin": {"type": "string", "description": "Optional input"},
                },
                "required": ["language", "source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_against_cases",
            "description": (
                "Run a solution against specific inputs and see what it returns for "
                "each. Use this to check a reference solution before submitting it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "javascript", "typescript"],
                    },
                    "source": {"type": "string"},
                    "function_name": {"type": "string"},
                    "cases": {
                        "type": "array",
                        "description": 'Objects like {"args": {"nums": [1,2]}}',
                        "items": {"type": "object"},
                    },
                },
                "required": ["language", "source", "function_name", "cases"],
            },
        },
    },
]


def _error_summary(error: str) -> str:
    """The useful end of a traceback.

    Truncating from the front keeps the frame list and discards the exception
    message -- exactly backwards for anyone trying to understand the failure.
    """
    lines = [line for line in error.strip().splitlines() if line.strip()]
    if not lines:
        return "(no message)"
    tail = lines[-2:] if len(lines) > 1 else lines[-1:]
    return " / ".join(line.strip() for line in tail)[:300]


def _truncate(text: str) -> str:
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + "\n... output truncated"


def run_code(language: str, source: str, stdin: str = "") -> str:
    """Execute a program in the sandbox and describe what happened."""
    try:
        lang = Language(language)
    except ValueError:
        return f"unknown language {language!r}"

    result = run_source(lang, source, stdin=stdin, limits=TOOL_LIMITS)
    return _truncate(
        f"verdict: {result.verdict.value}\n"
        f"stdout:\n{result.stdout or '(empty)'}\n"
        f"stderr:\n{result.stderr or '(empty)'}"
    )


def run_against_cases(
    language: str, source: str, function_name: str, cases: list[dict[str, Any]]
) -> str:
    """Run a solution over specific inputs, reporting each result."""
    try:
        lang = Language(language)
    except ValueError:
        return f"unknown language {language!r}"

    parsed = [
        TestCase(args=c.get("args", {}) if isinstance(c, dict) else {}, expected=None)
        for c in cases[:20]
    ]
    if not parsed:
        return "no cases supplied"

    result = run_source(
        lang, source,
        harness=get_harness_for(lang, function_name),
        stdin=encode_cases(parsed),
        limits=TOOL_LIMITS,
    )
    decoded = decode_results(result.stdout)
    if not decoded:
        return _truncate(
            f"verdict: {result.verdict.value}\nno results were produced.\n"
            f"stderr:\n{result.stderr or '(empty)'}"
        )

    lines = [f"verdict: {result.verdict.value}"]
    for case, res in zip(parsed, decoded, strict=False):
        args = json.dumps(case.args)
        if res.ok:
            lines.append(f"{args} -> {json.dumps(res.value)}  ({res.ms:.2f} ms)")
        else:
            lines.append(f"{args} -> RAISED: {_error_summary(res.error)}")
        if res.stdout:
            lines.append(f"    printed: {res.stdout.strip()[:200]}")
    return _truncate("\n".join(lines))


DISPATCH: dict[str, Callable[..., str]] = {
    "run_code": run_code,
    "run_against_cases": run_against_cases,
}


def dispatch(name: str, arguments: dict[str, Any]) -> str:
    """Run one tool call, converting any failure into a message for the model."""
    fn = DISPATCH.get(name)
    if fn is None:
        return f"unknown tool {name!r}"
    try:
        return str(fn(**arguments))
    except TypeError as exc:
        return f"bad arguments for {name}: {exc}"
    except Exception as exc:  # noqa: BLE001 - a tool fault must not kill the loop
        log.warning("tool %s failed: %s", name, exc)
        return f"{name} failed: {type(exc).__name__}: {exc}"
