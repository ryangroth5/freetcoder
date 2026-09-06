"""Python convenience wrapper over the adapter layer.

Kept because the solvability gate and a lot of tests read better with an
explicit `run_python`, but it delegates: there is one execution path, defined in
`adapters.py` and specified in `docs/execution-protocol.md`.
"""

from __future__ import annotations

from ..models import Language
from .adapters import run_source
from .types import Limits, RunResult

SOURCE_FILENAME = "solution.py"


def run_python(
    source: str,
    *,
    stdin: str = "",
    limits: Limits | None = None,
    harness: str | None = None,
) -> RunResult:
    """Execute Python `source` in a throwaway workspace."""
    return run_source(
        Language.PYTHON, source, stdin=stdin, limits=limits, harness=harness
    )
