"""Per-call records for provider traffic.

There was no timing and no token accounting anywhere, so every claim about
generation being slow rested on a stopwatch and a guess. This makes the cost of
a strategy measurable: which stage was slow, how many tokens it spent, and
whether the time went to the provider or to our own sandbox.

Off unless something asks for it. A collector is a context manager rather than
a global switch, so a bench run measures itself without the serving path paying
for anything.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field


@dataclass
class CallRecord:
    """One provider round trip."""

    model: str
    #: Which part of generation asked for this. "" when nothing labelled it.
    stage: str
    mode: str
    seconds: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    #: Empty when the call succeeded.
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class Collector:
    """Records for one run, in call order."""

    calls: list[CallRecord] = field(default_factory=list)

    def add(self, record: CallRecord) -> None:
        self.calls.append(record)

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.calls)

    @property
    def completion_tokens(self) -> int:
        return sum(c.completion_tokens for c in self.calls)

    @property
    def prompt_tokens(self) -> int:
        return sum(c.prompt_tokens for c in self.calls)

    def by_stage(self) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for call in self.calls:
            row = out.setdefault(
                call.stage or "unlabelled",
                {"calls": 0, "seconds": 0.0, "completion_tokens": 0, "failures": 0},
            )
            row["calls"] += 1
            row["seconds"] += call.seconds
            row["completion_tokens"] += call.completion_tokens
            row["failures"] += 0 if call.ok else 1
        return out


#: None means nobody is collecting, which is the normal serving case.
_collector: ContextVar[Collector | None] = ContextVar("llm_collector", default=None)
#: What the current call is for, so a record can say which stage was slow.
_stage: ContextVar[str] = ContextVar("llm_stage", default="")


@contextmanager
def collecting() -> Iterator[Collector]:
    """Collect every provider call made inside this block."""
    collector = Collector()
    token = _collector.set(collector)
    try:
        yield collector
    finally:
        _collector.reset(token)


@contextmanager
def stage(name: str) -> Iterator[None]:
    """Label the calls made inside this block."""
    token = _stage.set(name)
    try:
        yield
    finally:
        _stage.reset(token)


@contextmanager
def record(model: str, mode: str) -> Iterator[CallRecord]:
    """Time one call and file it, whether it succeeds or raises.

    Cheap when nobody is collecting: a ContextVar read and a clock call. The
    record is yielded so the caller can fill in token counts from the response.
    """
    entry = CallRecord(model=model, stage=_stage.get(), mode=mode, seconds=0.0)
    started = time.monotonic()
    try:
        yield entry
    except Exception as exc:
        entry.error = f"{type(exc).__name__}: {exc}"[:200]
        raise
    finally:
        entry.seconds = time.monotonic() - started
        collector = _collector.get()
        if collector is not None:
            collector.add(entry)
