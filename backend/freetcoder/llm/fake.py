"""Offline LLM stub.

Replays recorded responses so the entire test suite runs with no API key and no
network. Keeping this in the shipped package rather than in tests/ lets the
generator CLI run offline too, which is how the gate's own fixtures were built.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .base import LLMError

M = TypeVar("M", bound=BaseModel)

FIXTURE_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "llm"


@dataclass
class ToolCall:
    """A tool invocation to replay. The tool really runs."""

    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


class FakeLLM:
    """Returns queued responses in order.

    A queued item may be a dict/model (returned as-is) or an Exception
    (raised), so tests can rehearse provider failures and malformed output.
    """

    #: Mirrors the real client's capability flag. Tests set it False to
    #: rehearse a provider that cannot do tool calls.
    supports_tools: bool | None = None

    def __init__(
        self,
        responses: Sequence[object] | None = None,
        *,
        cycle: bool = False,
        chat_reply: str | None = None,
    ) -> None:
        #: A canned tutor reply for offline mode. Tests leave it unset so a
        #: mis-queued payload still fails loudly rather than being papered over.
        self._chat_reply = chat_reply
        #: Replay the queue forever instead of running dry. Offline mode uses
        #: this: since the question cache became a fallback rather than the
        #: default source, every session generates, so a fixed number of canned
        #: responses runs out mid-session. Tests that assert on exhaustion leave
        #: it off.
        self._cycle = cycle
        self._original: list[object] = list(responses or [])
        self._queue: list[object] = list(responses or [])
        self.calls: list[tuple[str, str]] = []
        #: Every tool invocation the loop made, for assertions.
        self.tool_calls: list[tuple[str, dict[str, Any]]] = []
        #: What each replayed tool actually returned.
        self.tool_results: list[str] = []

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

    def queue_next(self, *responses: object) -> FakeLLM:
        """Put these at the *front*, ahead of anything already queued.

        Tests usually care about what comes back from the next call, not about
        appending behind a pre-seeded backlog.
        """
        self._queue[:0] = responses
        return self

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.7
    ) -> M:
        self.calls.append((system, user))
        if not self._queue and self._cycle and self._original:
            self._queue = list(self._original)
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

    async def complete_text(
        self, *, system: str, user: str, temperature: float = 0.7
    ) -> str:
        self.calls.append((system, user))
        if not self._queue and self._cycle and self._original:
            self._queue = list(self._original)
        if not self._queue:
            raise LLMError("FakeLLM queue is empty")
        item = self._queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return str(item)

    async def complete_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema: type[M],
        tools: list[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], str],
        temperature: float = 0.3,
        tool_budget: int = 6,
    ) -> M:
        """Replay queued items, running any ToolCall against the real dispatcher.

        Queue a `ToolCall` to rehearse the model probing its own code; the tool
        genuinely executes, so these tests exercise the sandbox too.
        """
        if self.supports_tools is False:
            return await self.complete_json(
                system=system, user=user, schema=schema, temperature=temperature
            )
        for _ in range(tool_budget):
            if self._queue and isinstance(self._queue[0], ToolCall):
                call = self._queue.pop(0)
                assert isinstance(call, ToolCall)
                self.tool_calls.append((call.name, call.arguments))
                self.tool_results.append(dispatch(call.name, call.arguments))
                continue
            return await self.complete_json(
                system=system, user=user, schema=schema, temperature=temperature
            )
        raise LLMError(f"no valid response within a budget of {tool_budget} tool calls")

    async def stream_chat(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        dispatch: Callable[[str, dict[str, Any]], str] | None = None,
        temperature: float = 0.4,
        tool_budget: int = 4,
    ) -> AsyncIterator[dict[str, Any]]:
        """Replay queued chat items as stream events.

        Queue a string to have it delivered as tokens, or a ToolCall to have the
        tool genuinely run -- so these tests exercise the sandbox too.

        A queued item that is not a chat reply (a question payload, say) is an
        error unless `chat_reply` was set: a test that queued the wrong thing
        should say so rather than stream nonsense.
        """
        self.calls.append(
            (system, messages[-1].get("content", "") if messages else "")
        )

        while self._queue and isinstance(self._queue[0], ToolCall):
            call = self._queue.pop(0)
            assert isinstance(call, ToolCall)
            self.tool_calls.append((call.name, call.arguments))
            result = dispatch(call.name, call.arguments) if dispatch else "unavailable"
            self.tool_results.append(result)
            yield {"type": "tool", "name": call.name, "result": result}

        item: object
        if self._queue and isinstance(self._queue[0], (str, Exception)):
            item = self._queue.pop(0)
        elif self._chat_reply is not None:
            # Offline mode: the queue holds questions, not chat replies, and
            # they must stay there for the next generation.
            item = self._chat_reply
        elif self._queue:
            wrong = self._queue[0]
            yield {
                "type": "error",
                "message": (
                    f"FakeLLM.stream_chat expected a string reply, found "
                    f"{type(wrong).__name__}; queue_next() a reply first"
                ),
            }
            return
        else:
            yield {"type": "error", "message": "FakeLLM has nothing queued"}
            return

        if isinstance(item, Exception):
            yield {"type": "error", "message": str(item)}
            return

        # Chunked, so a test can prove tokens arrive incrementally.
        text = str(item)
        for i in range(0, len(text), 8):
            yield {"type": "token", "text": text[i : i + 8]}

    @property
    def exhausted(self) -> bool:
        return not self._queue and not self._cycle


def iter_fixture_names() -> Iterable[str]:
    return sorted(p.stem for p in FIXTURE_DIR.glob("*.json"))
