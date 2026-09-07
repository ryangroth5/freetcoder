"""A progress channel for work that takes a while.

Generating a question routinely takes tens of seconds and, with repair rounds,
minutes. This records what is happening so the UI can say something truthful
instead of spinning.

It is an **observer**: every reporting call is optional and defaults to a no-op,
so generation neither depends on it nor changes shape because of it. Progress
must never be able to affect whether a question is produced.

In memory on purpose. Progress is worthless once the request it describes has
returned, so writing it to the mounted volume would be noise.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

StepKind = Literal["info", "ok", "warn", "fail"]

#: Runs are evicted once finished and older than this. A container meant to run
#: for days must not accumulate them.
RUN_TTL_SECONDS = 900.0

#: Hard ceiling regardless of age, oldest first.
MAX_RUNS = 64

#: A runaway repair loop should not produce an unbounded log.
MAX_STEPS = 200


class GenerationCancelled(Exception):
    """Raised when a run was cancelled between steps."""


class Step(BaseModel):
    #: Seconds since the run began. Relative, because that is what a reader
    #: actually wants to know.
    at: float
    kind: StepKind = "info"
    #: Written for a person: "checking the TypeScript scaffold parses", not
    #: "SCAFFOLD_INVALID".
    message: str


class Run(BaseModel):
    id: str
    started_at: float
    steps: list[Step] = Field(default_factory=list)
    finished: bool = False
    cancelled: bool = False
    #: "accepted" | "failed" | "cancelled", once finished.
    outcome: str = ""

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


class ProgressRegistry:
    """Holds in-flight runs. Safe to touch from any task."""

    def __init__(self, ttl: float = RUN_TTL_SECONDS, max_runs: int = MAX_RUNS) -> None:
        self._runs: dict[str, Run] = {}
        self._lock = threading.Lock()
        self._ttl = ttl
        self._max_runs = max_runs

    def start(self, run_id: str | None = None) -> Run:
        rid = run_id or uuid.uuid4().hex
        run = Run(id=rid, started_at=time.monotonic())
        with self._lock:
            existing = self._runs.get(rid)
            # A cancel can arrive *before* the request it refers to: the client
            # mints the id, then hits Start and Cancel in quick succession.
            # Clobbering the flag here would silently ignore that.
            if existing is not None and existing.cancelled:
                run.cancelled = True
            self._runs[rid] = run
            self._evict()
        return run

    def step(self, run_id: str | None, message: str, kind: StepKind = "info") -> None:
        if not run_id:
            return
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.finished:
                return
            if len(run.steps) >= MAX_STEPS:
                return
            run.steps.append(
                Step(at=round(time.monotonic() - run.started_at, 2),
                     kind=kind, message=message)
            )

    def finish(self, run_id: str | None, outcome: str) -> None:
        if not run_id:
            return
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.finished:
                return  # terminal: the first outcome is the real one
            run.finished = True
            run.outcome = outcome

    def cancel(self, run_id: str) -> bool:
        """Ask a run to stop. False if it is unknown or already finished."""
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or run.finished:
                return False
            run.cancelled = True
            return True

    def is_cancelled(self, run_id: str | None) -> bool:
        if not run_id:
            return False
        with self._lock:
            run = self._runs.get(run_id)
            return bool(run and run.cancelled)

    def get(self, run_id: str) -> Run | None:
        with self._lock:
            self._evict()
            return self._runs.get(run_id)

    def _evict(self) -> None:
        """Drop finished runs past their TTL, then the oldest if still over."""
        now = time.monotonic()
        stale = [
            rid for rid, run in self._runs.items()
            if run.finished and now - run.started_at > self._ttl
        ]
        for rid in stale:
            del self._runs[rid]

        if len(self._runs) > self._max_runs:
            oldest = sorted(self._runs.items(), key=lambda kv: kv[1].started_at)
            for rid, _ in oldest[: len(self._runs) - self._max_runs]:
                del self._runs[rid]


class Reporter:
    """What the pipeline holds. A no-op unless a run is attached.

    Threading this rather than a global keeps generation testable and keeps
    progress from becoming an implicit dependency.
    """

    def __init__(
        self, registry: ProgressRegistry | None = None, run_id: str | None = None
    ) -> None:
        self._registry = registry
        self._run_id = run_id

    def __call__(self, message: str, kind: StepKind = "info") -> None:
        if self._registry is not None:
            self._registry.step(self._run_id, message, kind)

    def checkpoint(self) -> None:
        """Stop here if the run was cancelled.

        Called between steps rather than during them: a request already in
        flight to the model cannot be interrupted, and pretending otherwise
        would make the button lie.
        """
        if self._registry is not None and self._registry.is_cancelled(self._run_id):
            raise GenerationCancelled("cancelled by the candidate")


#: A reporter that discards everything, so every call site works untouched.
NULL_REPORTER = Reporter()

registry = ProgressRegistry()
