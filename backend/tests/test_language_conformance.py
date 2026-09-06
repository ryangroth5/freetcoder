"""One suite, run against every registered language adapter.

This is the guard that answers "does the print fix extend to other languages?".
It is parameterized over `ADAPTERS`, so a new language cannot be added without
satisfying the contract in `docs/execution-protocol.md` -- in particular the
rule that a submission's own output must never reach the result channel.

The dangerous case is not the crash that first exposed the bug. It is a printed
JSON *object* being counted as a result, which shifts every later case and
silently misgrades a correct answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from freetcoder.generate.harness import (
    MAX_CASE_STDOUT,
    RECORD_MARKER,
    CaseResult,
    build_js_harness,
    build_python_harness,
    decode_results,
    encode_cases,
)
from freetcoder.models import Language, TestCase
from freetcoder.runner import ADAPTERS, Limits, Verdict, run_source

# Compiling TypeScript costs a couple of seconds, so give every language room.
LIMITS = Limits(wall_seconds=20.0, cpu_seconds=15, memory_mb=512)

CASES = [
    TestCase(args={"x": 1}, expected=2),
    TestCase(args={"x": 2}, expected=4),
    TestCase(args={"x": 3}, expected=6),
]


@dataclass(frozen=True)
class Sample:
    """The same program expressed in one language, for each contract rule."""

    language: Language
    doubles: str                 # returns x * 2
    prints_text: str             # ...and prints a plain string
    prints_json_array: str       # ...and prints a JSON array
    prints_json_object: str      # ...and prints a JSON object
    forges_record: str           # ...writes a marked record to the RAW stdout
    prints_at_load: str          # prints while the module loads
    prints_a_lot: str            # floods stdout
    raises_on_two: str           # throws for x == 2 only
    returns_unserialisable: str
    syntax_error: str
    missing_function: str
    harness: str                 # driver for this language


PY = Sample(
    language=Language.PYTHON,
    doubles="def f(x):\n    return x * 2\n",
    prints_text="def f(x):\n    print('debugging', x)\n    return x * 2\n",
    prints_json_array="def f(x):\n    print([x, x])\n    return x * 2\n",
    prints_json_object=(
        "import json\n"
        "def f(x):\n"
        "    print(json.dumps({'ok': True, 'value': 999, 'ms': 0}))\n"
        "    return x * 2\n"
    ),
    forges_record=(
        "import json, sys\n"
        "def f(x):\n"
        f"    print(json.dumps({{{RECORD_MARKER!r}: 1, 'ok': True, 'value': 999}}),"
        "          file=sys.__stdout__, flush=True)\n"
        "    return x * 2\n"
    ),
    prints_at_load="print('module loaded')\ndef f(x):\n    return x * 2\n",
    prints_a_lot="def f(x):\n    print('A' * 200000)\n    return x * 2\n",
    raises_on_two=(
        "def f(x):\n"
        "    if x == 2:\n"
        "        raise ValueError('nope')\n"
        "    return x * 2\n"
    ),
    returns_unserialisable="def f(x):\n    return object()\n",
    syntax_error="def f(x):\n    return x *\n",
    missing_function="def other(x):\n    return x\n",
    harness=build_python_harness("f"),
)

JS = Sample(
    language=Language.JAVASCRIPT,
    doubles="function f(x) { return x * 2; }\nmodule.exports = { f };\n",
    prints_text=(
        "function f(x) { console.log('debugging', x); return x * 2; }\n"
        "module.exports = { f };\n"
    ),
    prints_json_array=(
        "function f(x) { console.log(JSON.stringify([x, x])); return x * 2; }\n"
        "module.exports = { f };\n"
    ),
    prints_json_object=(
        "function f(x) {\n"
        "  console.log(JSON.stringify({ ok: true, value: 999, ms: 0 }));\n"
        "  return x * 2;\n"
        "}\nmodule.exports = { f };\n"
    ),
    forges_record=(
        "const fs = require('fs');\n"
        "function f(x) {\n"
        f"  fs.writeSync(1, JSON.stringify({{ {RECORD_MARKER!r}: 1,"
        " ok: true, value: 999 }) + '\\n');\n"
        "  return x * 2;\n"
        "}\nmodule.exports = { f };\n"
    ).replace("'" + RECORD_MARKER + "'", '"' + RECORD_MARKER + '"'),
    prints_at_load=(
        "console.log('module loaded');\n"
        "function f(x) { return x * 2; }\nmodule.exports = { f };\n"
    ),
    prints_a_lot=(
        "function f(x) { console.log('A'.repeat(200000)); return x * 2; }\n"
        "module.exports = { f };\n"
    ),
    raises_on_two=(
        "function f(x) { if (x === 2) throw new Error('nope'); return x * 2; }\n"
        "module.exports = { f };\n"
    ),
    returns_unserialisable=(
        "function f(x) { const a = {}; a.self = a; return a; }\n"
        "module.exports = { f };\n"
    ),
    syntax_error="function f(x) { return x * ; }\n",
    missing_function="function other(x) { return x; }\nmodule.exports = { other };\n",
    harness=build_js_harness("f"),
)

TS = Sample(
    language=Language.TYPESCRIPT,
    doubles="export function f(x: number): number { return x * 2; }\n",
    prints_text=(
        "export function f(x: number): number {\n"
        "  console.log('debugging', x);\n  return x * 2;\n}\n"
    ),
    prints_json_array=(
        "export function f(x: number): number {\n"
        "  console.log(JSON.stringify([x, x]));\n  return x * 2;\n}\n"
    ),
    prints_json_object=(
        "export function f(x: number): number {\n"
        "  console.log(JSON.stringify({ ok: true, value: 999, ms: 0 }));\n"
        "  return x * 2;\n}\n"
    ),
    forges_record=(
        "export function f(x: number): number {\n"
        "  const fs = require('fs') as { writeSync(fd: number, s: string): void };\n"
        '  fs.writeSync(1, JSON.stringify({ "' + RECORD_MARKER + '": 1, '
        "ok: true, value: 999 }) + '\\n');\n"
        "  return x * 2;\n}\n"
    ),
    prints_at_load=(
        "console.log('module loaded');\n"
        "export function f(x: number): number { return x * 2; }\n"
    ),
    prints_a_lot=(
        "export function f(x: number): number {\n"
        "  console.log('A'.repeat(200000));\n  return x * 2;\n}\n"
    ),
    raises_on_two=(
        "export function f(x: number): number {\n"
        "  if (x === 2) throw new Error('nope');\n  return x * 2;\n}\n"
    ),
    returns_unserialisable=(
        "export function f(x: number): unknown {\n"
        "  const a: Record<string, unknown> = {};\n  a.self = a;\n  return a;\n}\n"
    ),
    # A *type* error is this language's compile error; that is the point of it.
    syntax_error='export function f(x: number): number { return "not a number"; }\n',
    missing_function="export function other(x: number): number { return x; }\n",
    harness=build_js_harness("f"),
)

SAMPLES = [PY, JS, TS]


def test_every_registered_adapter_has_conformance_samples() -> None:
    """Adding an adapter without samples must fail loudly, not skip silently."""
    assert {s.language for s in SAMPLES} == set(ADAPTERS), (
        "every language in ADAPTERS needs a Sample here; see "
        "docs/execution-protocol.md"
    )


def run(sample: Sample, source: str, cases: list[TestCase] | None = None) -> list[CaseResult]:
    result = run_source(
        sample.language,
        source,
        harness=sample.harness,
        stdin=encode_cases(cases if cases is not None else CASES),
        limits=LIMITS,
    )
    assert result.verdict is not Verdict.INTERNAL_ERROR, result.stderr
    return decode_results(result.stdout)


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.language.value)
class TestOutputNeverCorruptsResults:
    """Rule 1 and 2 of docs/execution-protocol.md, per language."""

    def test_baseline(self, sample: Sample) -> None:
        assert [r.value for r in run(sample, sample.doubles)] == [2, 4, 6]

    def test_plain_output_is_captured_not_swallowed(self, sample: Sample) -> None:
        results = run(sample, sample.prints_text)
        assert [r.value for r in results] == [2, 4, 6]
        assert "debugging 1" in results[0].stdout
        assert "debugging 2" in results[1].stdout

    def test_printing_a_json_array_does_not_crash(self, sample: Sample) -> None:
        results = run(sample, sample.prints_json_array)
        assert [r.value for r in results] == [2, 4, 6]

    def test_printing_a_json_object_cannot_forge_a_result(self, sample: Sample) -> None:
        """The silent-misgrading case: results must stay aligned."""
        results = run(sample, sample.prints_json_object)
        assert len(results) == 3
        assert [r.value for r in results] == [2, 4, 6]

    def test_writing_to_the_raw_handle_cannot_forge_a_result(
        self, sample: Sample
    ) -> None:
        """Every runtime offers a way around the redirect; the marker is not enough
        on its own, so the graded values must still be ours."""
        results = run(sample, sample.forges_record)
        genuine = [r for r in results if r.value in (2, 4, 6)]
        assert len(genuine) == 3

    def test_load_time_output_belongs_to_the_first_case(self, sample: Sample) -> None:
        results = run(sample, sample.prints_at_load)
        assert "module loaded" in results[0].stdout
        assert "module loaded" not in results[1].stdout


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.language.value)
class TestOutputIsBounded:
    """Rule 3."""

    def test_a_flood_is_truncated(self, sample: Sample) -> None:
        results = run(sample, sample.prints_a_lot, [CASES[0]])
        assert len(results) == 1
        assert len(results[0].stdout) <= MAX_CASE_STDOUT + 64

    def test_results_survive_a_flood(self, sample: Sample) -> None:
        assert [r.value for r in run(sample, sample.prints_a_lot)] == [2, 4, 6]


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.language.value)
class TestFailureHandling:
    """Rules 4, 5 and 6."""

    def test_one_case_failing_does_not_lose_the_others(self, sample: Sample) -> None:
        results = run(sample, sample.raises_on_two)
        assert [r.ok for r in results] == [True, False, True]
        assert results[1].error

    def test_unserialisable_return_is_a_wrong_answer(self, sample: Sample) -> None:
        results = run(sample, sample.returns_unserialisable, [CASES[0]])
        assert results and not results[0].ok

    def test_unparseable_source_is_a_compile_error(self, sample: Sample) -> None:
        result = run_source(
            sample.language, sample.syntax_error,
            harness=sample.harness, stdin=encode_cases(CASES), limits=LIMITS,
        )
        assert result.verdict is Verdict.COMPILE_ERROR, result.stderr[:300]
        assert result.stderr.strip()

    def test_missing_function_is_reported(self, sample: Sample) -> None:
        results = run(sample, sample.missing_function)
        assert results and not results[0].ok
        assert "not defined" in results[0].error


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: s.language.value)
class TestRecordShape:
    def test_records_carry_the_marker(self, sample: Sample) -> None:
        result = run_source(
            sample.language, sample.doubles, harness=sample.harness,
            stdin=encode_cases([CASES[0]]), limits=LIMITS,
        )
        lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
        assert lines
        for line in lines:
            assert RECORD_MARKER in json.loads(line)

    def test_timings_are_reported(self, sample: Sample) -> None:
        assert all(r.ms >= 0 for r in run(sample, sample.doubles))
