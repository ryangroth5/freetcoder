"""OpenAI-compatible LLM client.

Providers vary in how well they honour structured output, so this degrades in
three steps: native json_schema -> plain json_object -> salvage the first JSON
object out of prose. A model good enough to write a coding question is usually
good enough for step one, but a local Ollama build often is not.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, TypeVar

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from .base import LLMError

log = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json(text: str) -> str:
    """Pull a JSON object out of a response that may be wrapped in prose."""
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        return fenced.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return text[start : end + 1]
    return text


class OpenAICompatibleClient:
    """Talks to any OpenAI-compatible endpoint."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float = 120.0,
        max_retries: int = 3,
    ) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key or "unset", timeout=timeout_s, max_retries=0
        )
        self._model = model
        self._max_retries = max_retries

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.7
    ) -> M:
        last: Exception | None = None
        for attempt in range(self._max_retries):
            # Only the first attempt asks for a strict schema. If the provider
            # rejected it or produced junk, loosening the ask beats hammering
            # the same request that already failed.
            mode = "json_schema" if attempt == 0 else "json_object"
            try:
                raw = await self._request(system, user, schema, temperature, mode)
                return schema.model_validate_json(_extract_json(raw))
            except ValidationError as exc:
                last = exc
                # Feed the error back: models correct well from a concrete
                # complaint, poorly from a bare retry.
                user = (
                    f"{user}\n\nYour previous reply did not validate. Fix exactly "
                    f"these problems and return the whole object again:\n{exc}"
                )
                log.warning("LLM output failed validation (attempt %d)", attempt + 1)
            except (APIError, json.JSONDecodeError, ValueError) as exc:
                last = exc
                log.warning("LLM request failed (attempt %d): %s", attempt + 1, exc)
        raise LLMError(f"no valid response after {self._max_retries} attempts: {last}") from last

    async def _request(
        self, system: str, user: str, schema: type[M], temperature: float, mode: str
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": self._model,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": False,
                    "schema": schema.model_json_schema(),
                },
            }
        else:
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except APIError:
            if mode != "json_schema":
                raise
            # Endpoint does not support json_schema at all: retry unconstrained
            # rather than burning the attempt.
            kwargs["response_format"] = {"type": "json_object"}
            resp = await self._client.chat.completions.create(**kwargs)

        content = resp.choices[0].message.content
        if not content:
            raise LLMError("empty response from provider")
        return str(content)
