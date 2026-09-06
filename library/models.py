"""Wire types for the question library.

Deliberately carries `author`, `votes` and `created_at` from the start. They are
unused today, but having them present means adding accounts and voting later is
a change of behaviour rather than a schema migration on shared data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class PublishRequest(BaseModel):
    """A gated question offered to the library."""

    #: The full GatedQuestion payload from the app, stored verbatim so the
    #: library never needs to understand the question format to serve it back.
    question: dict[str, Any]

    #: Denormalised for search. Derived by the app from the question itself.
    title: str = Field(min_length=1, max_length=200)
    style: str = ""
    difficulty: str = ""
    topics: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)

    #: Placeholder until accounts exist. Never trusted for anything.
    author: str = "anonymous"


class QuestionSummary(BaseModel):
    """A search hit: enough to choose, not the whole question."""

    id: str
    title: str
    style: str
    difficulty: str
    topics: list[str]
    languages: list[str]
    author: str
    votes: int
    created_at: float
    submissions: int


class StoredQuestion(QuestionSummary):
    question: dict[str, Any]


class SearchResponse(BaseModel):
    questions: list[QuestionSummary]
    total: int


class TimingRequest(BaseModel):
    """One submission's speed, as a ratio -- never a wall-clock time.

    Milliseconds are meaningless across machines. The ratio of a submission to
    that language's reference solution, measured back to back on the same
    hardware, cancels CPU speed and ambient load.
    """

    language: str
    ratio: float = Field(gt=0, le=10_000)


class PercentileResponse(BaseModel):
    #: Share of recorded submissions this one is faster than, 0-100.
    percentile: int | None
    samples: int
    #: Below the threshold a percentile is noise, so the API declines to give one.
    enough_samples: bool
