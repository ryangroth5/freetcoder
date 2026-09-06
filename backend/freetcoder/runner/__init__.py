"""Sandboxed execution of untrusted code."""

from .adapters import (
    ADAPTERS,
    LanguageAdapter,
    UnsupportedLanguageError,
    get_adapter,
    run_source,
)
from .python_adapter import run_python
from .sandbox import SandboxUnavailableError, Workspace, execute, runner_identity
from .types import Limits, RunResult, Verdict

__all__ = [
    "ADAPTERS",
    "Limits",
    "LanguageAdapter",
    "RunResult",
    "SandboxUnavailableError",
    "UnsupportedLanguageError",
    "Verdict",
    "Workspace",
    "execute",
    "get_adapter",
    "run_python",
    "run_source",
    "runner_identity",
]
