"""The question library: a small, separate service.

Deliberately its own app, its own database and its own port. Accounts, voting
and authentication belong here later, and keeping the boundary real now means
adding them will not touch the practice app.

It stores questions verbatim and never interprets them, so the question format
can evolve without a library migration.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query

from .models import (
    PercentileResponse,
    PublishRequest,
    SearchResponse,
    StoredQuestion,
    TimingRequest,
)
from .percentile import percentile_for
from .storage import LibraryStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s library: %(message)s")
log = logging.getLogger("library")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    store = LibraryStore(os.environ.get("LIBRARY_DB_PATH", ""))
    await store.connect()
    app.state.store = store
    log.info("storage: %s", os.environ.get("LIBRARY_DB_PATH") or "in-memory")
    try:
        yield
    finally:
        await store.close()


def create_app() -> FastAPI:
    app = FastAPI(title="freetcoder-library", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/questions")
    async def publish(payload: PublishRequest) -> dict[str, str]:
        qid = await app.state.store.publish(payload)
        log.info("published %s (%s)", qid, payload.title)
        return {"id": qid}

    @app.get("/questions", response_model=SearchResponse)
    async def search(
        style: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        limit: int = Query(50, ge=1, le=200),
        offset: int = Query(0, ge=0),
    ) -> SearchResponse:
        questions, total = await app.state.store.search(
            style=style, difficulty=difficulty, language=language,
            topic=topic, limit=limit, offset=offset,
        )
        return SearchResponse(questions=questions, total=total)

    @app.get("/questions/{qid}", response_model=StoredQuestion)
    async def fetch(qid: str) -> StoredQuestion:
        found = await app.state.store.get(qid)
        if found is None:
            raise HTTPException(404, "no such question")
        return found

    @app.post("/questions/{qid}/timings", response_model=PercentileResponse)
    async def record_timing(qid: str, payload: TimingRequest) -> PercentileResponse:
        """Record one submission and return where it stands.

        Recording and ranking in one call: the caller always wants both, and a
        separate round trip would only widen the window for inconsistency.
        """
        if await app.state.store.get(qid) is None:
            raise HTTPException(404, "no such question")
        await app.state.store.record_timing(qid, payload.language, payload.ratio)
        ratios = await app.state.store.ratios(qid, payload.language)
        pct, samples, enough = percentile_for(payload.ratio, ratios)
        return PercentileResponse(percentile=pct, samples=samples,
                                  enough_samples=enough)

    @app.get("/questions/{qid}/percentile", response_model=PercentileResponse)
    async def percentile(qid: str, language: str, ratio: float) -> PercentileResponse:
        ratios = await app.state.store.ratios(qid, language)
        pct, samples, enough = percentile_for(ratio, ratios)
        return PercentileResponse(percentile=pct, samples=samples,
                                  enough_samples=enough)

    return app


app = create_app()
