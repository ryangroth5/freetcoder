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
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from . import telemetry
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

    #: Set to False the first time a tool-call request is refused, so we stop
    #: paying for a round trip the provider cannot serve. Local endpoints vary
    #: widely here, and the feedback loop works without tools.
    supports_tools: bool | None = None

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
        # max(1, ...) because these are *retries*: zero of them still means one
        # attempt. Looping over range(0) made a configured 0 do nothing at all
        # and report "no valid response after 0 attempts: None".
        for attempt in range(max(1, self._max_retries)):
            # Stay schema-constrained on every attempt.
            #
            # This used to drop to json_object after the first failure, on the
            # theory that loosening the ask beat repeating a failed request.
            # Measured, it does the opposite: a validation failure is usually
            # transient, and the unconstrained retry then has to hit a
            # fourteen-field shape unaided. The failures scattered across
            # unrelated fields -- `difficulty` (a three-value enum), `title` (a
            # string), `signatures.0.*` -- which is what an unguided model
            # produces, not one that cannot write prose.
            #
            # _request still falls back to json_object on its own if the
            # provider *rejects* json_schema outright, which is the case the
            # old rule was really aiming at.
            mode = "json_schema"
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
                # Name the fields. "failed validation" alone leaves you
                # guessing which of fourteen fields the model got wrong, which
                # is the difference between a fixable prompt and a mystery.
                fields = ", ".join(
                    ".".join(str(p) for p in err["loc"]) for err in exc.errors()[:5]
                )
                log.warning(
                    "LLM output failed validation (attempt %d): %s",
                    attempt + 1, fields or "unknown field",
                )
            except (APIError, json.JSONDecodeError, ValueError) as exc:
                last = exc
                log.warning("LLM request failed (attempt %d): %s", attempt + 1, exc)
        raise LLMError(
            f"no valid response after {max(1, self._max_retries)} attempt(s): {last}"
        ) from last

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
        """Let the model run code before answering, then validate its answer.

        Falls back to `complete_json` when the provider cannot do tool calls, so
        a local model without tool support still repairs questions -- just
        without being able to test its work first.
        """
        if self.supports_tools is False:
            return await self.complete_json(
                system=system, user=user, schema=schema, temperature=temperature
            )

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

        for _ in range(tool_budget):
            # Built as a dict for the same reason as _request: the SDK's typed
            # overloads do not accept the plain message/tool dicts we assemble.
            kwargs: dict[str, Any] = {
                "model": self._model,
                "temperature": temperature,
                "messages": messages,
                "tools": tools,
                "response_format": {"type": "json_object"},
            }
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except APIError as exc:
                log.info("provider refused tool calls (%s); continuing without", exc)
                self.supports_tools = False
                return await self.complete_json(
                    system=system, user=user, schema=schema, temperature=temperature
                )

            self.supports_tools = True
            message = resp.choices[0].message
            calls = getattr(message, "tool_calls", None)

            if not calls:
                content = message.content or ""
                try:
                    return schema.model_validate_json(_extract_json(content))
                except ValidationError as exc:
                    # Ask for a correction in place rather than restarting: the
                    # transcript holds everything the model has learned so far.
                    messages.append({"role": "assistant", "content": content})
                    messages.append({
                        "role": "user",
                        "content": f"That did not validate. Fix exactly this:\n{exc}",
                    })
                    continue

            messages.append({
                "role": "assistant",
                "content": message.content or "",
                "tool_calls": [
                    {
                        "id": c.id,
                        "type": "function",
                        "function": {
                            "name": c.function.name,
                            "arguments": c.function.arguments,
                        },
                    }
                    for c in calls
                ],
            })
            for call in calls:
                try:
                    arguments = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = dispatch(call.function.name, arguments)
                log.info("tool %s -> %d chars", call.function.name, len(result))
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result,
                })

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
        """Yield {"type": "token"|"tool"|"error", ...} as the reply is produced.

        Falls back to a single non-streamed reply when the provider cannot
        stream, and reports an error event rather than raising if a stream dies
        part-way -- a chat that arrives late beats one that breaks.
        """
        convo: list[dict[str, Any]] = [{"role": "system", "content": system}, *messages]

        for _ in range(tool_budget + 1):
            kwargs: dict[str, Any] = {
                "model": self._model,
                "temperature": temperature,
                "messages": convo,
                "stream": True,
            }
            if tools and self.supports_tools is not False:
                kwargs["tools"] = tools

            text = ""
            calls: dict[int, dict[str, Any]] = {}
            try:
                stream = await self._client.chat.completions.create(**kwargs)
                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta
                    if delta.content:
                        text += delta.content
                        yield {"type": "token", "text": delta.content}
                    for call in getattr(delta, "tool_calls", None) or []:
                        entry = calls.setdefault(
                            call.index, {"id": "", "name": "", "arguments": ""}
                        )
                        if call.id:
                            entry["id"] = call.id
                        if call.function and call.function.name:
                            entry["name"] = call.function.name
                        if call.function and call.function.arguments:
                            entry["arguments"] += call.function.arguments
            except APIError as exc:
                if text:
                    # Partial reply already delivered: say what went wrong
                    # rather than leaving it looking finished.
                    yield {"type": "error", "message": f"the reply was cut short: {exc}"}
                    return
                log.info("streaming unavailable (%s); falling back", exc)
                async for event in self._unstreamed(convo, tools, temperature):
                    yield event
                return

            if not calls:
                return

            convo.append({
                "role": "assistant",
                "content": text,
                "tool_calls": [
                    {"id": c["id"], "type": "function",
                     "function": {"name": c["name"], "arguments": c["arguments"]}}
                    for c in calls.values()
                ],
            })
            for call in calls.values():
                try:
                    arguments = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    arguments = {}
                result = dispatch(call["name"], arguments) if dispatch else "unavailable"
                yield {"type": "tool", "name": call["name"], "result": result}
                convo.append({
                    "role": "tool", "tool_call_id": call["id"], "content": result,
                })

        yield {"type": "error", "message": "the tutor ran out of tool calls"}

    async def _unstreamed(
        self,
        convo: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
    ) -> AsyncIterator[dict[str, Any]]:
        """One whole reply, for providers that cannot stream."""
        # Built as a dict for the same reason as _request: the SDK's typed
        # overloads do not accept the plain message dicts we assemble.
        kwargs: dict[str, Any] = {
            "model": self._model, "temperature": temperature, "messages": convo,
        }
        try:
            resp = await self._client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            if content:
                yield {"type": "token", "text": content}
        except APIError as exc:
            yield {"type": "error", "message": f"the tutor is unavailable: {exc}"}

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

        with telemetry.record(self._model, mode) as entry:
            try:
                resp = await self._client.chat.completions.create(**kwargs)
            except APIError:
                if mode != "json_schema":
                    raise
                # Endpoint does not support json_schema at all: retry
                # unconstrained rather than burning the attempt.
                kwargs["response_format"] = {"type": "json_object"}
                entry.mode = "json_object"
                resp = await self._client.chat.completions.create(**kwargs)

            usage = getattr(resp, "usage", None)
            if usage is not None:
                entry.prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                entry.completion_tokens = getattr(usage, "completion_tokens", 0) or 0

        # An OpenAI-compatible gateway can answer 200 with an error payload and
        # no `choices` at all -- rate limits and upstream provider failures both
        # look like this. Indexing it blind turned that into a TypeError deep in
        # the stack instead of something a caller could report.
        choices = getattr(resp, "choices", None)
        if not choices:
            detail = getattr(resp, "error", None) or "no choices in response"
            raise LLMError(f"provider returned no completion: {detail}")

        content = choices[0].message.content
        if not content:
            raise LLMError("empty response from provider")
        return str(content)
