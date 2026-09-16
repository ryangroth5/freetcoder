"""LLM client protocol shared by the real and fake implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    """The provider failed, or returned something we could not use."""


class LLMTimeout(LLMError):
    """The provider did not answer inside the deadline.

    Distinct from every other failure because it is the one worth *not*
    retrying: a 429 or a 5xx says the provider was momentarily unable, while a
    timeout says this request is too slow for this budget -- and re-sending it
    unchanged is the least promising use of another full deadline. Measured: a
    300s deadline fired three times inside one progress step, 677 seconds of
    silence for a call that takes 103s when it works.
    """



class LLMStalled(LLMTimeout):
    """The stream went quiet: no first token in time, or silence mid-reply.

    Detectable long before the overall deadline, because a streamed call that
    is generating shows it within seconds. The non-streaming calls this
    replaced gave no signal at all until they finished, so a stuck model and a
    busy one looked identical for the full 300s.
    """

class LLMClient(Protocol):
    """Anything that can turn a prompt into a validated pydantic model."""

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = ...
    ) -> M:
        """Return an instance of `schema`, or raise LLMError."""
        ...

    async def complete_text(
        self, *, system: str, user: str, temperature: float = ...
    ) -> str:
        """Return the raw reply.

        For content that does not survive JSON: source code has to have its
        newlines escaped inside a JSON string, and most models measured here
        simply do not, returning a whole function on one line.
        """
        ...

    async def complete_json_with_tools(
        self,
        *,
        system: str,
        user: str,
        schema: type[M],
        tools: list[dict[str, Any]],
        dispatch: Callable[[str, dict[str, Any]], str],
        temperature: float = ...,
        tool_budget: int = ...,
    ) -> M:
        """As `complete_json`, but the model may execute code first.

        Implementations fall back to `complete_json` when the provider cannot do
        tool calls, so this is always safe to call.
        """
        ...
