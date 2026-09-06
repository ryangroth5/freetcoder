"""Value types for the execution sandbox."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class Verdict(enum.StrEnum):
    """Outcome of a single execution. Mirrors the vocabulary the UI renders."""

    OK = "ok"
    WRONG_ANSWER = "wrong_answer"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    RUNTIME_ERROR = "runtime_error"
    COMPILE_ERROR = "compile_error"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True, slots=True)
class Limits:
    """Resource ceilings applied to a submission.

    Defaults are deliberately tight; a legitimate solution to an interview-style
    question needs none of the headroom an attacker would want.
    """

    wall_seconds: float = 5.0
    cpu_seconds: int = 4
    memory_mb: int = 256
    #: NOTE: RLIMIT_NPROC counts processes per *UID across the whole kernel*,
    #: not per process tree. Under Docker Desktop uid 999 already carries a
    #: ~25-process baseline we do not control, so this is a backstop only --
    #: the process-group kill is the real containment. Below ~32 it rejects
    #: legitimate submissions outright.
    max_processes: int = 64
    file_size_mb: int = 8
    max_output_bytes: int = 64 * 1024
    block_network: bool = True
    #: RLIMIT_AS caps *virtual* address space, which JIT runtimes cannot live
    #: with: V8 reserves gigabytes for its CodeRange regardless of actual usage,
    #: so Node dies instantly with "Failed to reserve virtual memory for
    #: CodeRange". Adapters for such runtimes turn this off and bound the heap
    #: through the runtime's own flag instead (see JavaScriptAdapter).
    limit_address_space: bool = True

    def __post_init__(self) -> None:
        if self.cpu_seconds >= self.wall_seconds + 30:
            raise ValueError("cpu_seconds must not dwarf wall_seconds")
        for name in ("wall_seconds", "cpu_seconds", "memory_mb",
                     "max_processes", "file_size_mb", "max_output_bytes"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive")


@dataclass(frozen=True, slots=True)
class RunResult:
    """What happened when we ran the code."""

    verdict: Verdict
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    stdout_truncated: bool = False
    stderr_truncated: bool = False

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.OK
