"""Per-language execution adapters.

The protocol these implement is specified in `docs/execution-protocol.md`, and
enforced against every entry in `ADAPTERS` by
`backend/tests/test_language_conformance.py`. Adding a language means adding an
adapter here and making that suite pass -- there is deliberately no other way in.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from ..models import Language
from .sandbox import Workspace, execute
from .types import Limits, RunResult, Verdict


class LanguageAdapter(Protocol):
    """How one language is compiled, launched and interpreted.

    Declared read-only so the frozen dataclasses below satisfy it: a mutable
    protocol attribute is invariant and a frozen field cannot match it.
    """

    @property
    def language(self) -> Language: ...

    @property
    def source_filename(self) -> str: ...

    @property
    def harness_filename(self) -> str: ...

    def build_harness(self, function_name: str) -> str:
        """The driver program wrapped around the submission."""
        ...

    def compile(self, ws: Workspace, limits: Limits) -> RunResult | None:
        """Prepare the workspace. A non-None result short-circuits the run."""
        ...

    def command(self, entry: str, limits: Limits) -> Sequence[str]:
        """argv to execute `entry`."""
        ...

    def classify(self, result: RunResult) -> RunResult:
        """Refine the sandbox's verdict using language-specific evidence."""
        ...

    def adjust_limits(self, limits: Limits) -> Limits:
        """Adapt the caller's limits to what this runtime can survive."""
        ...

    def syntax_command(self, entry: str) -> Sequence[str]:
        """argv that parses `entry` without running or type-checking it.

        Used to prove an artifact is written in the language it claims. Type
        checking would be wrong here: a scaffold with an unimplemented body is
        valid starter code but need not satisfy its own return type.
        """
        ...


def _as_compile_error(result: RunResult) -> RunResult:
    return RunResult(
        Verdict.COMPILE_ERROR,
        result.stdout,
        result.stderr,
        result.exit_code,
        result.duration_ms,
        result.stdout_truncated,
        result.stderr_truncated,
    )


@dataclass(frozen=True, slots=True)
class PythonAdapter:
    language: Language = Language.PYTHON
    source_filename: str = "solution.py"
    harness_filename: str = "_harness.py"

    def build_harness(self, function_name: str) -> str:
        from ..generate.harness import build_python_harness

        return build_python_harness(function_name)

    def compile(self, ws: Workspace, limits: Limits) -> RunResult | None:
        return None  # interpreted; a SyntaxError surfaces at run time

    def command(self, entry: str, limits: Limits) -> Sequence[str]:
        # -I isolates the interpreter: ignores PYTHON* env vars and keeps the
        # CWD off sys.path, so a submission cannot shadow a stdlib module.
        return [sys.executable, "-I", "-B", entry]

    def classify(self, result: RunResult) -> RunResult:
        if result.verdict is Verdict.RUNTIME_ERROR and "SyntaxError" in result.stderr:
            return _as_compile_error(result)
        return result

    def adjust_limits(self, limits: Limits) -> Limits:
        return limits

    def syntax_command(self, entry: str) -> Sequence[str]:
        return [sys.executable, "-c",
                f"import ast,sys;ast.parse(open({entry!r}).read())"]


def _node_command(entry: str, limits: Limits) -> list[str]:
    """Node argv with the heap bounded by flag rather than by RLIMIT_AS."""
    return [
        "node",
        "--disable-proto=throw",
        f"--max-old-space-size={max(64, limits.memory_mb)}",
        entry,
    ]


def _node_limits(limits: Limits) -> Limits:
    """V8 cannot run under an RLIMIT_AS cap; bound the heap instead."""
    return replace(limits, limit_address_space=False)


@dataclass(frozen=True, slots=True)
class JavaScriptAdapter:
    language: Language = Language.JAVASCRIPT
    source_filename: str = "solution.js"
    harness_filename: str = "_harness.js"

    def build_harness(self, function_name: str) -> str:
        from ..generate.harness import build_js_harness

        return build_js_harness(function_name)

    def compile(self, ws: Workspace, limits: Limits) -> RunResult | None:
        return None

    def command(self, entry: str, limits: Limits) -> Sequence[str]:
        return _node_command(entry, limits)

    def classify(self, result: RunResult) -> RunResult:
        if result.verdict is Verdict.RUNTIME_ERROR and (
            "SyntaxError" in result.stderr or "ReferenceError: require" in result.stderr
        ):
            return _as_compile_error(result)
        return result

    def adjust_limits(self, limits: Limits) -> Limits:
        return _node_limits(limits)

    def syntax_command(self, entry: str) -> Sequence[str]:
        return ["node", "--check", entry]


#: Ambient declarations for the globals a submission may legitimately use.
#: Preferred over `--lib dom`, which would also type-check `document` and
#: `window` that then fail at run time, and over @types/node, which is not
#: resolvable from a throwaway workspace with no node_modules.
TS_GLOBALS_DTS = """\
declare const console: {
  log(...data: unknown[]): void;
  info(...data: unknown[]): void;
  warn(...data: unknown[]): void;
  error(...data: unknown[]): void;
  debug(...data: unknown[]): void;
};
declare const process: {
  argv: string[];
  env: Record<string, string | undefined>;
  exit(code?: number): never;
};
declare function require(id: string): unknown;
declare const module: { exports: unknown };
declare const exports: unknown;
"""


@dataclass(frozen=True, slots=True)
class TypeScriptAdapter:
    """TypeScript, type-checked by tsc and executed as the JavaScript it emits.

    Deliberately not Node's --experimental-strip-types: stripping skips type
    checking entirely, which is the main reason to pick TypeScript, and it
    rejects features needing transformation such as `enum`.
    """

    language: Language = Language.TYPESCRIPT
    source_filename: str = "solution.ts"
    harness_filename: str = "_harness.js"

    def build_harness(self, function_name: str) -> str:
        from ..generate.harness import build_js_harness

        # tsc emits solution.js, so the JavaScript harness serves both.
        return build_js_harness(function_name)

    def compile(self, ws: Workspace, limits: Limits) -> RunResult | None:
        """Type-check and emit solution.js. Type errors are compile errors."""
        ws.write("globals.d.ts", TS_GLOBALS_DTS)
        result = execute(
            [
                "tsc", "solution.ts", "globals.d.ts",
                "--target", "es2022",
                "--module", "commonjs",
                "--lib", "es2022",
                "--skipLibCheck",
                "--strict", "false",
                "--outDir", ".",
            ],
            ws.path,
            limits=limits,
        )
        if result.verdict is not Verdict.OK:
            # tsc writes diagnostics to stdout, not stderr; surface them where
            # the UI already looks for compiler output.
            return RunResult(
                Verdict.COMPILE_ERROR,
                "",
                (result.stdout + result.stderr).strip() or "type check failed",
                result.exit_code,
                result.duration_ms,
            )
        return None

    def command(self, entry: str, limits: Limits) -> Sequence[str]:
        return _node_command(entry, limits)

    def classify(self, result: RunResult) -> RunResult:
        if result.verdict is Verdict.RUNTIME_ERROR and "SyntaxError" in result.stderr:
            return _as_compile_error(result)
        return result

    def adjust_limits(self, limits: Limits) -> Limits:
        return _node_limits(limits)

    def syntax_command(self, entry: str) -> Sequence[str]:
        # --noCheck parses and reports syntax errors while skipping type
        # checking, which is what "is this TypeScript at all?" needs.
        return ["tsc", "--noEmit", "--noCheck", entry]


ADAPTERS: dict[Language, LanguageAdapter] = {
    Language.PYTHON: PythonAdapter(),
    Language.JAVASCRIPT: JavaScriptAdapter(),
    Language.TYPESCRIPT: TypeScriptAdapter(),
}


class UnsupportedLanguageError(ValueError):
    """No adapter is registered for the requested language."""


def get_adapter(language: Language) -> LanguageAdapter:
    try:
        return ADAPTERS[language]
    except KeyError as exc:
        raise UnsupportedLanguageError(
            f"no execution adapter for {language.value}; "
            f"available: {sorted(a.value for a in ADAPTERS)}"
        ) from exc


#: Compilation gets its own, more generous budget: tsc is slow, and its cost
#: must not eat into the candidate's execution time.
COMPILE_LIMITS = Limits(
    wall_seconds=30.0, cpu_seconds=25, memory_mb=768, limit_address_space=False
)


def check_syntax(language: Language, source: str) -> RunResult:
    """Does `source` parse as `language`?

    Answers the question the gate could not previously ask: an artifact can be
    perfectly good code in the *wrong* language and nothing would notice.
    """
    adapter = get_adapter(language)
    limits = adapter.adjust_limits(
        Limits(wall_seconds=30.0, cpu_seconds=25, memory_mb=768)
    )
    with Workspace() as ws:
        ws.write(adapter.source_filename, source)
        result = execute(
            adapter.syntax_command(adapter.source_filename), ws.path, limits=limits
        )
    if result.verdict is not Verdict.OK:
        # tsc reports on stdout; python and node on stderr.
        return RunResult(
            Verdict.COMPILE_ERROR,
            "",
            (result.stderr + result.stdout).strip() or "does not parse",
            result.exit_code,
            result.duration_ms,
        )
    return result


def run_source(
    language: Language,
    source: str,
    *,
    stdin: str = "",
    limits: Limits | None = None,
    harness: str | None = None,
) -> RunResult:
    """Execute `source` in a throwaway workspace under the language's adapter.

    `harness` overrides the adapter's driver; when omitted entirely the source
    runs directly, which is what the solvability gate does for reference
    solutions and hidden-case generators.
    """
    adapter = get_adapter(language)
    limits = adapter.adjust_limits(limits or Limits())

    with Workspace() as ws:
        ws.write(adapter.source_filename, source)

        compiled = adapter.compile(ws, COMPILE_LIMITS)
        if compiled is not None:
            return compiled

        entry = adapter.source_filename
        if harness is not None:
            entry = adapter.harness_filename
            ws.write(entry, harness)
        elif language is Language.TYPESCRIPT:
            # tsc has emitted the runnable artefact next to the source.
            entry = "solution.js"

        result = execute(
            adapter.command(entry, limits), ws.path, limits=limits, stdin=stdin
        )

    return adapter.classify(result)
