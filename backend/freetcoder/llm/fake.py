"""Offline LLM stub.

Replays recorded responses so the entire test suite runs with no API key and no
network. Keeping this in the shipped package rather than in tests/ lets the
generator CLI run offline too, which is how the gate's own fixtures were built.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMError

M = TypeVar("M", bound=BaseModel)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm"


class FakeLLM:
    """Returns queued responses in order.

    A queued item may be a dict/model (returned as-is) or an Exception
    (raised), so tests can rehearse provider failures and malformed output.
    """

    def __init__(self, responses: Sequence[object] | None = None) -> None:
        self._queue: list[object] = list(responses or [])
        self.calls: list[tuple[str, str]] = []

    @classmethod
    def from_fixtures(cls, *names: str) -> FakeLLM:
        payloads: list[object] = []
        for name in names:
            path = FIXTURE_DIR / f"{name}.json"
            if not path.exists():
                raise FileNotFoundError(f"no LLM fixture named {name!r} at {path}")
            payloads.append(json.loads(path.read_text()))
        return cls(payloads)

    def queue(self, *responses: object) -> FakeLLM:
        self._queue.extend(responses)
        return self

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.7
    ) -> M:
        self.calls.append((system, user))
        if not self._queue:
            raise LLMError("FakeLLM exhausted: more calls than queued responses")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, schema):
            return item
        try:
            return schema.model_validate(item)
        except ValidationError as exc:
            raise LLMError(f"fixture does not satisfy {schema.__name__}: {exc}") from exc

    @property
    def exhausted(self) -> bool:
        return not self._queue


def iter_fixture_names() -> Iterable[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
