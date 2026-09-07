"""SQLite persistence for the library. Same shape as the app's storage layer."""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from .models import PublishRequest, QuestionSummary, StoredQuestion

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    style       TEXT NOT NULL DEFAULT '',
    difficulty  TEXT NOT NULL DEFAULT '',
    topics      TEXT NOT NULL DEFAULT '[]',
    languages   TEXT NOT NULL DEFAULT '[]',
    author      TEXT NOT NULL DEFAULT 'anonymous',
    source      TEXT NOT NULL DEFAULT 'generated',
    import_text TEXT NOT NULL DEFAULT '',
    votes       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_style ON questions(style, difficulty);

-- Ratios, never milliseconds: see models.TimingRequest.
CREATE TABLE IF NOT EXISTS timings (
    id          TEXT PRIMARY KEY,
    question_id TEXT NOT NULL,
    language    TEXT NOT NULL,
    ratio       REAL NOT NULL,
    created_at  REAL NOT NULL,
    FOREIGN KEY (question_id) REFERENCES questions(id)
);
CREATE INDEX IF NOT EXISTS idx_timings_lookup ON timings(question_id, language);
"""


class LibraryStore:
    def __init__(self, path: str = "") -> None:
        self._dsn = path or "file:library?mode=memory&cache=shared"
        self._uri = not path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._dsn, uri=self._uri)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._migrate()
        await self._db.commit()

    async def _migrate(self) -> None:
        """Add columns that a database created by an older version lacks.

        CREATE TABLE IF NOT EXISTS does nothing to an existing table, and this
        service keeps a durable volume -- so a new column silently produced
        "no such column" 500s against any database that predated it.
        """
        cur = await self.db.execute("PRAGMA table_info(questions)")
        existing = {row["name"] for row in await cur.fetchall()}
        for column, ddl in (
            ("source", "TEXT NOT NULL DEFAULT 'generated'"),
            ("import_text", "TEXT NOT NULL DEFAULT ''"),
        ):
            if column not in existing:
                await self.db.execute(
                    f"ALTER TABLE questions ADD COLUMN {column} {ddl}"
                )

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("LibraryStore.connect() was never awaited")
        return self._db

    async def publish(self, payload: PublishRequest) -> str:
        qid = uuid.uuid4().hex
        await self.db.execute(
            "INSERT INTO questions (id, title, style, difficulty, topics, languages,"
            " author, source, import_text, votes, created_at, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
            (qid, payload.title, payload.style, payload.difficulty,
             json.dumps(payload.topics), json.dumps(payload.languages),
             payload.author, payload.source, payload.import_text,
             time.time(), json.dumps(payload.question)),
        )
        await self.db.commit()
        return qid

    async def get(self, qid: str) -> StoredQuestion | None:
        cur = await self.db.execute("SELECT * FROM questions WHERE id = ?", (qid,))
        row = await cur.fetchone()
        if row is None:
            return None
        return StoredQuestion(
            **await self._summary_fields(row), question=json.loads(row["payload"])
        )

    async def search(
        self,
        *,
        style: str | None = None,
        difficulty: str | None = None,
        language: str | None = None,
        topic: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[QuestionSummary], int]:
        where: list[str] = []
        params: list[Any] = []
        if style:
            where.append("style = ?")
            params.append(style)
        if difficulty:
            where.append("difficulty = ?")
            params.append(difficulty)
        # Topics and languages are JSON arrays; a LIKE is crude but adequate at
        # this scale and avoids a join table before it is needed.
        if language:
            where.append("languages LIKE ?")
            params.append(f'%"{language}"%')
        if topic:
            where.append("topics LIKE ?")
            params.append(f"%{topic}%")

        clause = f" WHERE {' AND '.join(where)}" if where else ""
        cur = await self.db.execute(
            f"SELECT COUNT(*) AS n FROM questions{clause}", params
        )
        total = int((await cur.fetchone())["n"])  # type: ignore[index]

        cur = await self.db.execute(
            f"SELECT * FROM questions{clause} ORDER BY votes DESC, created_at DESC"
            " LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = await cur.fetchall()
        return [QuestionSummary(**await self._summary_fields(r)) for r in rows], total

    async def record_timing(self, qid: str, language: str, ratio: float) -> None:
        await self.db.execute(
            "INSERT INTO timings (id, question_id, language, ratio, created_at)"
            " VALUES (?,?,?,?,?)",
            (uuid.uuid4().hex, qid, language, ratio, time.time()),
        )
        await self.db.commit()

    async def ratios(self, qid: str, language: str) -> list[float]:
        cur = await self.db.execute(
            "SELECT ratio FROM timings WHERE question_id = ? AND language = ?",
            (qid, language),
        )
        return [float(r["ratio"]) for r in await cur.fetchall()]

    async def _summary_fields(self, row: aiosqlite.Row) -> dict[str, Any]:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM timings WHERE question_id = ?", (row["id"],)
        )
        submissions = int((await cur.fetchone())["n"])  # type: ignore[index]
        return {
            "id": row["id"],
            "title": row["title"],
            "style": row["style"],
            "difficulty": row["difficulty"],
            "topics": json.loads(row["topics"]),
            "languages": json.loads(row["languages"]),
            "author": row["author"],
            "source": row["source"],
            "votes": row["votes"],
            "created_at": row["created_at"],
            "submissions": submissions,
        }


@asynccontextmanager
async def store_for(path: str = "") -> AsyncIterator[LibraryStore]:
    store = LibraryStore(path)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()
