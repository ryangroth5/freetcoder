"""LLM client protocol shared by the real and fake implementations."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

M = TypeVar("M", bound=BaseModel)


class LLMError(RuntimeError):
    """The provider failed, or returned something we could not use."""


class LLMClient(Protocol):
    """Anything that can turn a prompt into a validated pydantic model."""

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = ...
    ) -> M:
        """Return an instance of `schema`, or raise LLMError."""
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
