"""Client for the question library service.

The library is a separate service that may be unreachable, slow, or not
configured at all. **It must never be able to break local practice.** Every call
is therefore total: failures are logged and returned as an absent result, never
raised into a request handler.

Calls are timed and logged, which is where rate limiting and latency budgets
will attach once the service is centralized.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .models import GatedQuestion

log = logging.getLogger(__name__)

#: Deliberately short. A slow library should degrade the feature, not the page.
TIMEOUT_S = 5.0


@dataclass(frozen=True, slots=True)
class Percentile:
    percentile: int | None
    samples: int
    enough_samples: bool


@dataclass(frozen=True, slots=True)
class LibraryError:
    """Why a call did not succeed, in words a candidate can act on."""

    message: str


class QuestionLibrary:
    """Talks to the library service, or reports that it could not."""

    def __init__(self, base_url: str, timeout_s: float = TIMEOUT_S) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self._base)

    async def _call(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[Any | None, LibraryError | None]:
        if not self.configured:
            return None, LibraryError("No question library is configured.")

        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.request(method, f"{self._base}{path}", **kwargs)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.info("library %s %s -> %s in %dms", method, path,
                     resp.status_code, elapsed_ms)
            if resp.status_code >= 400:
                return None, LibraryError(
                    f"The library rejected the request ({resp.status_code})."
                )
            return resp.json(), None
        except httpx.HTTPError as exc:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.warning("library %s %s failed after %dms: %s",
                        method, path, elapsed_ms, exc)
            return None, LibraryError("The question library is unreachable.")

    async def publish(
        self, gated: GatedQuestion, *, style: str, author: str = "anonymous"
    ) -> tuple[str | None, LibraryError | None]:
        payload = {
            "question": gated.model_dump(mode="json"),
            "title": gated.question.title,
            "style": style,
            "difficulty": gated.question.difficulty.value,
            "topics": gated.question.topics,
            "languages": [s.language.value for s in gated.question.signatures],
            "author": author,
        }
        body, error = await self._call("POST", "/questions", json=payload)
        return (body["id"] if body else None), error

    async def search(self, **filters: Any) -> tuple[list[dict[str, Any]], int]:
        params = {k: v for k, v in filters.items() if v not in (None, "")}
        body, _ = await self._call("GET", "/questions", params=params)
        if body is None:
            # An unreachable library looks like an empty one, so browsing
            # degrades to "nothing here" rather than an error page.
            return [], 0
        return body["questions"], body["total"]

    async def fetch(self, qid: str) -> tuple[GatedQuestion | None, LibraryError | None]:
        body, error = await self._call("GET", f"/questions/{qid}")
        if body is None:
            return None, error
        try:
            return GatedQuestion.model_validate(body["question"]), None
        except Exception as exc:  # noqa: BLE001 - a stranger published this
            log.warning("library question %s does not validate: %s", qid, exc)
            return None, LibraryError(
                "That question was saved in a format this version cannot read."
            )

    async def record_timing(
        self, qid: str, language: str, ratio: float
    ) -> Percentile | None:
        body, _ = await self._call(
            "POST", f"/questions/{qid}/timings",
            json={"language": language, "ratio": ratio},
        )
        if body is None:
            return None
        return Percentile(
            percentile=body.get("percentile"),
            samples=int(body.get("samples", 0)),
            enough_samples=bool(body.get("enough_samples")),
        )

    async def health(self) -> bool:
        body, _ = await self._call("GET", "/health")
        return bool(body and body.get("ok"))


def build_library(settings: Any | None = None) -> QuestionLibrary:
    from .settings import get_settings

    s = settings or get_settings()
    return QuestionLibrary(s.library_url)
