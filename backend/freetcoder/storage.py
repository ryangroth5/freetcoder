"""SQLite persistence.

Optional by design: with no volume mounted the database lives in memory and the
app works exactly the same, just slower and forgetful. The cache of gated
questions is what makes a volume worth attaching -- generating and validating a
question costs tokens and tens of seconds, so replaying one is a large win.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aiosqlite

from .formats import FormatConfig
from .models import GatedQuestion, Language

SCHEMA = """
CREATE TABLE IF NOT EXISTS questions (
    id            TEXT PRIMARY KEY,
    cache_key     TEXT NOT NULL,
    language      TEXT NOT NULL,
    payload       TEXT NOT NULL,
    created_at    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_questions_cache_key ON questions(cache_key);

-- Which library entry a locally-held question corresponds to, so submissions
-- can be ranked against everyone else's.
CREATE TABLE IF NOT EXISTS library_links (
    question_id TEXT PRIMARY KEY,
    library_id  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id            TEXT PRIMARY KEY,
    config        TEXT NOT NULL,
    question_ids  TEXT NOT NULL,
    started_at    REAL NOT NULL,
    finished_at   REAL,
    current_index INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    language       TEXT NOT NULL,
    source         TEXT NOT NULL,
    kind           TEXT NOT NULL,   -- 'run' | 'submit' | 'skip'
    verdict        TEXT NOT NULL,
    score          REAL,
    detail         TEXT,
    created_at     REAL NOT NULL,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);
CREATE INDEX IF NOT EXISTS idx_attempts_session ON attempts(session_id);

CREATE TABLE IF NOT EXISTS chat_messages (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    question_index INTEGER NOT NULL,
    role           TEXT NOT NULL,   -- 'user' | 'assistant'
    content        TEXT NOT NULL,
    created_at     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_session ON chat_messages(session_id, question_index);
"""


def cache_key(config: FormatConfig, difficulty: str, language: Language) -> str:
    """Identity of an interchangeable question.

    Deliberately coarse: two requests differing only in freeform wording should
    still share a cached question, or the cache never hits.

    The offered languages are part of the key. A question cached when only
    Python was offered carries no JavaScript signature, so replaying it would
    silently hand back a question the format cannot actually be solved in.
    """
    topics = ",".join(sorted(config.generation.topics))
    langs = ",".join(sorted(lang.value for lang in config.environment.languages))
    return (
        f"{config.generation.style}|{config.generation.preset_id}|{difficulty}"
        f"|{language.value}|{topics}|{langs}"
    )


class Storage:
    """Thin async wrapper over aiosqlite. No ORM; the schema is three tables."""

    def __init__(self, path: str = "") -> None:
        #: Empty path -> in-memory. `cache=shared` keeps one database across
        #: connections, which an in-memory database otherwise would not do.
        self._dsn = path or "file:freetcoder?mode=memory&cache=shared"
        self._uri = not path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self._db = await aiosqlite.connect(self._dsn, uri=self._uri)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        if self._db is None:
            raise RuntimeError("Storage.connect() was never awaited")
        return self._db

    # ---------------------------------------------------------- questions
    async def cache_question(self, key: str, question: GatedQuestion) -> str:
        qid = uuid.uuid4().hex
        await self.db.execute(
            "INSERT INTO questions (id, cache_key, language, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (qid, key, question.language.value,
             question.model_dump_json(), time.time()),
        )
        await self.db.commit()
        return qid

    async def get_question(self, qid: str) -> GatedQuestion | None:
        cur = await self.db.execute("SELECT payload FROM questions WHERE id = ?", (qid,))
        row = await cur.fetchone()
        return GatedQuestion.model_validate_json(row["payload"]) if row else None

    async def find_cached(
        self, key: str, *, exclude_ids: list[str] | None = None
    ) -> tuple[str, GatedQuestion] | None:
        """A previously validated question matching this key, if any."""
        sql = "SELECT id, payload FROM questions WHERE cache_key = ?"
        params: list[Any] = [key]
        if exclude_ids:
            sql += f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
            params.extend(exclude_ids)
        sql += " ORDER BY RANDOM() LIMIT 1"
        cur = await self.db.execute(sql, params)
        row = await cur.fetchone()
        if row is None:
            return None
        return row["id"], GatedQuestion.model_validate_json(row["payload"])

    async def set_library_id(self, question_id: str, library_id: str) -> None:
        await self.db.execute(
            "INSERT INTO library_links (question_id, library_id) VALUES (?, ?)"
            " ON CONFLICT(question_id) DO UPDATE SET library_id = excluded.library_id",
            (question_id, library_id),
        )
        await self.db.commit()

    async def library_id_for(self, question_id: str) -> str | None:
        cur = await self.db.execute(
            "SELECT library_id FROM library_links WHERE question_id = ?",
            (question_id,),
        )
        row = await cur.fetchone()
        return str(row["library_id"]) if row else None

    # ----------------------------------------------------------- sessions
    async def create_session(self, config: FormatConfig, question_ids: list[str]) -> str:
        sid = uuid.uuid4().hex
        await self.db.execute(
            "INSERT INTO sessions (id, config, question_ids, started_at) "
            "VALUES (?, ?, ?, ?)",
            (sid, config.model_dump_json(), json.dumps(question_ids), time.time()),
        )
        await self.db.commit()
        return sid

    async def get_session(self, sid: str) -> dict[str, Any] | None:
        cur = await self.db.execute("SELECT * FROM sessions WHERE id = ?", (sid,))
        row = await cur.fetchone()
        if row is None:
            return None
        data = dict(row)
        data["config"] = FormatConfig.model_validate_json(data["config"])
        data["question_ids"] = json.loads(data["question_ids"])
        return data

    async def set_current_index(self, sid: str, index: int) -> None:
        await self.db.execute(
            "UPDATE sessions SET current_index = ? WHERE id = ?", (index, sid)
        )
        await self.db.commit()

    async def finish_session(self, sid: str) -> None:
        await self.db.execute(
            "UPDATE sessions SET finished_at = ? WHERE id = ?", (time.time(), sid)
        )
        await self.db.commit()

    # --------------------------------------------------------------- chat
    async def add_chat_message(
        self, session_id: str, question_index: int, role: str, content: str
    ) -> None:
        await self.db.execute(
            "INSERT INTO chat_messages (id, session_id, question_index, role,"
            " content, created_at) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, session_id, question_index, role, content, time.time()),
        )
        await self.db.commit()

    async def chat_history(
        self, session_id: str, question_index: int, limit: int = 40
    ) -> list[dict[str, Any]]:
        """Most recent messages, oldest first."""
        cur = await self.db.execute(
            "SELECT role, content FROM chat_messages WHERE session_id = ?"
            " AND question_index = ? ORDER BY created_at DESC LIMIT ?",
            (session_id, question_index, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
        return list(reversed(rows))

    async def chat_message_count(self, session_id: str) -> int:
        cur = await self.db.execute(
            "SELECT COUNT(*) AS n FROM chat_messages WHERE session_id = ? "
            "AND role = 'user'",
            (session_id,),
        )
        return int((await cur.fetchone())["n"])  # type: ignore[index]

    # ----------------------------------------------------------- attempts
    async def record_attempt(
        self,
        *,
        session_id: str,
        question_index: int,
        language: Language,
        source: str,
        kind: str,
        verdict: str,
        score: float | None = None,
        detail: str = "",
    ) -> str:
        aid = uuid.uuid4().hex
        await self.db.execute(
            "INSERT INTO attempts (id, session_id, question_index, language, source, "
            "kind, verdict, score, detail, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (aid, session_id, question_index, language.value, source, kind,
             verdict, score, detail, time.time()),
        )
        await self.db.commit()
        return aid

    async def attempts_for(
        self, sid: str, question_index: int | None = None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM attempts WHERE session_id = ?"
        params: list[Any] = [sid]
        if question_index is not None:
            sql += " AND question_index = ?"
            params.append(question_index)
        sql += " ORDER BY created_at"
        cur = await self.db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


@asynccontextmanager
async def storage_for(path: str = "") -> AsyncIterator[Storage]:
    store = Storage(path)
    await store.connect()
    try:
        yield store
    finally:
        await store.close()
