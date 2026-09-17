"""OpenAI-compatible LLM client.

Providers vary in how well they honour structured output, so this degrades in
three steps: native json_schema -> plain json_object -> salvage the first JSON
object out of prose. A model good enough to write a coding question is usually
good enough for step one, but a local Ollama build often is not.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from collections.abc import AsyncIterator, Callable
from typing import Any, TypeVar

from openai import APIError, AsyncOpenAI
from pydantic import BaseModel, ValidationError

from . import telemetry
from .base import LLMError, LLMStalled, LLMTimeout

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


def _usage_into(usage: Any, entry: telemetry.CallRecord) -> None:
    """Token counts, including reasoning when the provider breaks it out."""
    entry.prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
    entry.completion_tokens = getattr(usage, "completion_tokens", 0) or 0
    details = getattr(usage, "completion_tokens_details", None)
    if isinstance(details, dict):
        entry.reasoning_tokens = int(details.get("reasoning_tokens") or 0)
    elif details is not None:
        entry.reasoning_tokens = int(getattr(details, "reasoning_tokens", 0) or 0)


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
        first_token_s: float = 30.0,
        idle_s: float = 60.0,
        fallback_model: str = "",
        reasoning_effort: str = "default",
    ) -> None:
        self._client = AsyncOpenAI(
            base_url=base_url, api_key=api_key or "unset", timeout=timeout_s, max_retries=0
        )
        self._model = model
        self._max_retries = max_retries
        #: A real wall-clock deadline, because the client's own `timeout` is
        #: not one. httpx applies a bare float per *operation* -- connect,
        #: read, write, pool -- so a provider that dribbles bytes keeps
        #: resetting the read timer and the request never expires. Measured on
        #: OpenRouter: a call sat for eleven minutes against a 300s timeout,
        #: with the progress log frozen on the step that started it.
        self._deadline_s = timeout_s
        #: Called with the reason each time a request is about to be re-sent.
        #: The client has no business reaching into the progress registry, but
        #: a retry that reports nothing is a silent minute -- and three of them
        #: is what made one step sit at 677 seconds with nothing to read.
        self.on_retry: Callable[[str], None] | None = None
        self._first_token_s = first_token_s
        self._idle_s = idle_s
        self._fallback = fallback_model.strip()
        self._reasoning = reasoning_effort

    def _retrying(self, exc: Exception, attempt: int, of: int) -> None:
        log.warning("request failed (attempt %d/%d): %s", attempt, of, exc)
        if self.on_retry is not None:
            self.on_retry(f"{exc} — asking again ({attempt + 1} of {of})")

    async def _ask(self, **kwargs: Any) -> Any:
        """One provider call, bounded by the clock."""
        try:
            return await asyncio.wait_for(
                self._client.chat.completions.create(**kwargs),
                timeout=self._deadline_s,
            )
        except TimeoutError as exc:
            raise LLMTimeout(
                f"the provider did not answer within {self._deadline_s:.0f}s"
            ) from exc


    def _say(self, message: str) -> None:
        log.warning("%s", message)
        if self.on_retry is not None:
            self.on_retry(message)

    async def _stream(
        self, kwargs: dict[str, Any], entry: telemetry.CallRecord
    ) -> str:
        """One call, streamed, with three clocks.

        The non-streaming request this replaces produced no bytes until the
        whole reply was done, so a model that was generating and one that was
        stuck looked identical for the full deadline. Streamed, the difference
        shows within seconds:

        - no content *or reasoning* token within `first_token_s` -> stalled.
          Reasoning counts: thinking models emit it long before content.
          Gateway keep-alive comments do not -- they prove the connection, not
          generation, and the SDK drops them before we see them anyway;
        - silence longer than `idle_s` once tokens are flowing -> stalled;
        - the whole call past `timeout_s` -> timed out.
        """
        model = kwargs["model"]
        entry.reasoning_effort = self._reasoning
        body = self._reasoning_body()
        if body is not None:
            kwargs = {**kwargs, "extra_body": {"reasoning": body}}
        started = time.monotonic()
        deadline = started + self._deadline_s
        last = started

        def window() -> float:
            now = time.monotonic()
            budget = (
                (started + self._first_token_s) - now
                if entry.ttft_s is None
                else self._idle_s
            )
            return max(0.0, min(budget, deadline - now))

        def stalled() -> LLMError:
            now = time.monotonic()
            # Whichever window was the binding one. Compared with a little
            # slack: asyncio wakes a hair early or late, and a first-token
            # window that happens to end at the deadline is a timeout.
            first_token_end = started + self._first_token_s
            binding = (
                first_token_end if entry.ttft_s is None else last + self._idle_s
            )
            if now >= deadline - 0.01 or deadline <= binding:
                entry.outcome = "timeout"
                return LLMTimeout(
                    f"the provider did not answer within {self._deadline_s:.0f}s"
                )
            entry.outcome = "stalled"
            if entry.ttft_s is None:
                return LLMStalled(
                    f"no tokens from {model} in {self._first_token_s:.0f}s"
                )
            return LLMStalled(
                f"{model} went silent for {self._idle_s:.0f}s mid-reply"
            )

        try:
            stream = await asyncio.wait_for(
                self._client.chat.completions.create(
                    **kwargs, stream=True, stream_options={"include_usage": True}
                ),
                timeout=window(),
            )
        except TimeoutError as exc:
            raise stalled() from exc
        except APIError as exc:
            # Endpoints that cannot stream reject the parameter outright. Only
            # then fall back to one whole reply -- a 429 or a 5xx is not a
            # reason to send the request a second time.
            if getattr(exc, "status_code", None) not in (400, 404, 415, 422, 501):
                raise
            log.info("streaming refused (%s); asking for a whole reply", exc)
            return await self._whole(kwargs, entry)

        if not hasattr(stream, "__aiter__"):
            # Some OpenAI-compatible servers ignore `stream` and answer with one
            # whole completion. Take it as it came.
            return self._absorb(stream, entry, started)

        text: list[str] = []
        iterator = stream.__aiter__()
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(iterator.__anext__(), timeout=window())
                except StopAsyncIteration:
                    break
                except TimeoutError as exc:
                    raise stalled() from exc

                extra = getattr(chunk, "model_extra", None) or {}
                if extra.get("provider") and not entry.served_by:
                    entry.served_by = str(extra["provider"])
                if extra.get("error"):
                    entry.outcome = "error"
                    raise LLMError(f"provider error mid-stream: {extra['error']}")
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    _usage_into(usage, entry)
                if not chunk.choices:
                    continue
                delta = chunk.choices[0].delta
                dextra = getattr(delta, "model_extra", None) or {}
                content = delta.content or ""
                thinking = (
                    dextra.get("reasoning") or dextra.get("reasoning_content") or ""
                )
                if not content and not thinking:
                    continue
                now = time.monotonic()
                if entry.ttft_s is None:
                    entry.ttft_s = now - started
                else:
                    entry.longest_gap_s = max(entry.longest_gap_s, now - last)
                last = now
                entry.tokens_streamed += 1
                if thinking and not content:
                    entry.reasoning_streamed += 1
                if content:
                    text.append(content)
                    entry.reply += content
        finally:
            with contextlib.suppress(Exception):
                await stream.close()
        return "".join(text)

    def _reasoning_body(self) -> dict[str, Any] | None:
        """OpenRouter's `reasoning` field, or None to leave the model alone."""
        if self._reasoning == "default":
            return None
        if self._reasoning == "none":
            return {"enabled": False}
        return {"effort": self._reasoning}

    async def _whole(
        self, kwargs: dict[str, Any], entry: telemetry.CallRecord
    ) -> str:
        """One non-streamed reply, for endpoints that refuse to stream."""
        started = time.monotonic()
        return self._absorb(await self._ask(**kwargs), entry, started)

    @staticmethod
    def _absorb(resp: Any, entry: telemetry.CallRecord, started: float) -> str:
        entry.ttft_s = time.monotonic() - started
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _usage_into(usage, entry)
        extra = getattr(resp, "model_extra", None) or {}
        if extra.get("provider"):
            entry.served_by = str(extra["provider"])
        choices = getattr(resp, "choices", None)
        if not choices:
            detail = getattr(resp, "error", None) or "no choices in response"
            raise LLMError(f"provider returned no completion: {detail}")
        content = str(choices[0].message.content or "")
        entry.reply = content
        entry.tokens_streamed = 1 if content else 0
        return content

    async def _with_fallback(self, call: Callable[[str], Any]) -> Any:
        """Try the configured model, then the fallback once.

        Same endpoint and key, a different model: it covers a stalled model or
        a bad provider route, which is what actually happens, without a second
        credential to manage.
        """
        try:
            return await call(self._model)
        except LLMError as exc:
            if not self._fallback or self._fallback == self._model:
                raise
            self._say(f"{exc} — switching to {self._fallback}")
            return await call(self._fallback)

    async def complete_json(
        self, *, system: str, user: str, schema: type[M], temperature: float = 0.7
    ) -> M:
        async def attempt(model: str) -> M:
            return await self._complete_json(
                model=model, system=system, user=user, schema=schema,
                temperature=temperature,
            )

        result: M = await self._with_fallback(attempt)
        return result

    async def _complete_json(
        self, *, model: str, system: str, user: str, schema: type[M],
        temperature: float,
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
                raw = await self._request(system, user, schema, temperature, mode, model)
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
            except LLMTimeout:
                # Same reasoning as complete_text: an identical request that
                # just burned a whole deadline will burn the next one too.
                raise
            except (APIError, json.JSONDecodeError, ValueError) as exc:
                last = exc
                tries = max(1, self._max_retries)
                if attempt + 1 < tries:
                    self._retrying(exc, attempt + 1, tries)
                else:
                    log.warning("LLM request failed (final attempt): %s", exc)
        raise LLMError(
            f"no valid response after {max(1, self._max_retries)} attempt(s): {last}"
        ) from last

    async def complete_text(
        self, *, system: str, user: str, temperature: float = 0.7
    ) -> str:
        """A plain reply, with no response_format at all."""

        async def attempt(model: str) -> str:
            return await self._complete_text(
                model=model, system=system, user=user, temperature=temperature
            )

        result: str = await self._with_fallback(attempt)
        return result

    async def _complete_text(
        self, *, model: str, system: str, user: str, temperature: float
    ) -> str:
        last: Exception | None = None
        tries = max(1, self._max_retries)
        for n in range(1, tries + 1):
            try:
                with telemetry.record(model, "text") as entry:
                    entry.prompt = f"{system}\n\n---\n\n{user}"
                    content = await self._stream(
                        {
                            "model": model,
                            "temperature": temperature,
                            "messages": [
                                {"role": "system", "content": system},
                                {"role": "user", "content": user},
                            ],
                        },
                        entry,
                    )
                    if not content.strip():
                        raise LLMError("empty response from provider")
                return content
            except LLMTimeout:
                # Not retried here. The caller's own loop can vary the prompt;
                # this one can only re-send the identical request that already
                # went quiet.
                raise
            except (APIError, LLMError) as exc:
                last = exc
                if n < tries:
                    self._retrying(exc, n, tries)
        raise LLMError(f"no text response: {last}") from last

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
                resp = await self._ask(**kwargs)
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
            resp = await self._ask(**kwargs)
            content = resp.choices[0].message.content or ""
            if content:
                yield {"type": "token", "text": content}
        except APIError as exc:
            yield {"type": "error", "message": f"the tutor is unavailable: {exc}"}

    async def _request(
        self, system: str, user: str, schema: type[M], temperature: float,
        mode: str, model: str,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model,
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

        with telemetry.record(model, mode) as entry:
            entry.prompt = f"{system}\n\n---\n\n{user}"
            try:
                content = await self._stream(kwargs, entry)
            except APIError as exc:
                if mode != "json_schema" or getattr(exc, "status_code", None) not in (
                    400, 404, 415, 422, 501,
                ):
                    raise
                # Endpoint does not support json_schema at all: retry
                # unconstrained rather than burning the attempt.
                kwargs["response_format"] = {"type": "json_object"}
                entry.mode = "json_object"
                entry.reply = ""
                content = await self._stream(kwargs, entry)
            if not content:
                raise LLMError("empty response from provider")
        return content
