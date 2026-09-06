"""LLM client protocol shared by the real and fake implementations."""

from __future__ import annotations

from typing import Protocol, TypeVar

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
