"""Library service tests. In-memory database, no network."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from library.app import create_app
from library.models import PublishRequest
from library.percentile import MIN_SAMPLES, percentile_for

QUESTION = {
    "question": {"title": "Two Sum", "signatures": [{"language": "python"}]},
    "title": "Two Sum",
    "style": "leetcode",
    "difficulty": "easy",
    "topics": ["arrays", "hash maps"],
    "languages": ["python", "javascript"],
}


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app()
    async with app.router.lifespan_context(app), AsyncClient(
        transport=ASGITransport(app=app), base_url="http://library"
    ) as ac:
        yield ac


async def publish(client: AsyncClient, **overrides: object) -> str:
    resp = await client.post("/questions", json={**QUESTION, **overrides})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


class TestPublishAndFetch:
    async def test_round_trip_preserves_the_question_verbatim(
        self, client: AsyncClient
    ) -> None:
        """The library stores questions without interpreting them, so the
        question format can change without a library migration."""
        qid = await publish(client)
        body = (await client.get(f"/questions/{qid}")).json()
        assert body["question"] == QUESTION["question"]
        assert body["title"] == "Two Sum"

    async def test_new_questions_start_with_no_votes_or_submissions(
        self, client: AsyncClient
    ) -> None:
        body = (await client.get(f"/questions/{await publish(client)}")).json()
        assert body["votes"] == 0 and body["submissions"] == 0
        assert body["author"] == "anonymous"
        assert body["created_at"] > 0

    async def test_unknown_id_is_404(self, client: AsyncClient) -> None:
        assert (await client.get("/questions/nope")).status_code == 404

    async def test_a_title_is_required(self, client: AsyncClient) -> None:
        resp = await client.post("/questions", json={**QUESTION, "title": ""})
        assert resp.status_code == 422


class TestSearch:
    async def test_lists_published_questions(self, client: AsyncClient) -> None:
        await publish(client)
        await publish(client, title="Three Sum")
        body = (await client.get("/questions")).json()
        assert body["total"] == 2
        assert {q["title"] for q in body["questions"]} == {"Two Sum", "Three Sum"}

    @pytest.mark.parametrize(
        ("query", "expected"),
        [
            ("style=leetcode", 1),
            ("style=codility", 0),
            ("difficulty=easy", 1),
            ("difficulty=hard", 0),
            ("language=javascript", 1),
            ("language=go", 0),
            ("topic=hash", 1),
            ("topic=graphs", 0),
        ],
    )
    async def test_filters(
        self, client: AsyncClient, query: str, expected: int
    ) -> None:
        await publish(client)
        assert (await client.get(f"/questions?{query}")).json()["total"] == expected

    async def test_pagination(self, client: AsyncClient) -> None:
        for i in range(5):
            await publish(client, title=f"Q{i}")
        body = (await client.get("/questions?limit=2&offset=2")).json()
        assert body["total"] == 5 and len(body["questions"]) == 2

    async def test_empty_library_is_not_an_error(self, client: AsyncClient) -> None:
        body = (await client.get("/questions")).json()
        assert body == {"questions": [], "total": 0}


class TestTimings:
    async def test_records_and_counts_submissions(self, client: AsyncClient) -> None:
        qid = await publish(client)
        for ratio in (1.0, 2.0, 3.0):
            await client.post(f"/questions/{qid}/timings",
                              json={"language": "python", "ratio": ratio})
        body = (await client.get(f"/questions/{qid}")).json()
        assert body["submissions"] == 3

    async def test_percentile_withheld_below_the_sample_threshold(
        self, client: AsyncClient
    ) -> None:
        """A percentile from two data points is worse than none."""
        qid = await publish(client)
        body = (await client.post(f"/questions/{qid}/timings",
                                  json={"language": "python", "ratio": 1.0})).json()
        assert body["percentile"] is None
        assert body["enough_samples"] is False
        assert body["samples"] == 1

    async def test_percentile_once_there_are_enough_samples(
        self, client: AsyncClient
    ) -> None:
        qid = await publish(client)
        for ratio in (5.0, 4.0, 3.0, 2.0):
            await client.post(f"/questions/{qid}/timings",
                              json={"language": "python", "ratio": ratio})
        # A fast submission should beat the four slower ones already recorded.
        body = (await client.post(f"/questions/{qid}/timings",
                                  json={"language": "python", "ratio": 0.5})).json()
        assert body["enough_samples"] is True
        assert body["percentile"] is not None and body["percentile"] >= 80

    async def test_languages_are_ranked_separately(self, client: AsyncClient) -> None:
        """Python and JavaScript have different constant factors, so mixing
        their ratios would rank a language rather than a solution."""
        qid = await publish(client)
        for _ in range(MIN_SAMPLES):
            await client.post(f"/questions/{qid}/timings",
                              json={"language": "python", "ratio": 9.0})
        body = (await client.get(
            f"/questions/{qid}/percentile?language=javascript&ratio=1.0")).json()
        assert body["samples"] == 0 and body["percentile"] is None

    async def test_timings_for_unknown_question_are_404(
        self, client: AsyncClient
    ) -> None:
        resp = await client.post("/questions/nope/timings",
                                 json={"language": "python", "ratio": 1.0})
        assert resp.status_code == 404

    async def test_ratio_must_be_positive(self, client: AsyncClient) -> None:
        qid = await publish(client)
        resp = await client.post(f"/questions/{qid}/timings",
                                 json={"language": "python", "ratio": 0})
        assert resp.status_code == 422


class TestPercentileMaths:
    def test_too_few_samples(self) -> None:
        assert percentile_for(1.0, [1.0, 2.0]) == (None, 2, False)

    def test_fastest_of_the_field(self) -> None:
        pct, _, _ = percentile_for(0.1, [1.0, 2.0, 3.0, 4.0, 5.0])
        assert pct == 100

    def test_slowest_of_the_field(self) -> None:
        pct, _, _ = percentile_for(9.0, [1.0, 2.0, 3.0, 4.0, 5.0])
        assert pct == 0

    def test_ties_count_as_half(self) -> None:
        """Identical submissions should not all land on 0 or all on 100."""
        pct, _, _ = percentile_for(3.0, [1.0, 2.0, 3.0, 4.0, 5.0])
        assert pct == 50

    def test_a_ratio_below_one_is_faster_than_the_reference(self) -> None:
        pct, samples, enough = percentile_for(0.5, [0.6, 0.7, 0.8, 0.9, 1.0])
        assert enough and samples == 5 and pct == 100


class TestSchemaMigration:
    """The service keeps a durable volume, so schema changes must migrate.

    A column added to SCHEMA does nothing to an existing table -- publishing
    against an older database failed with "no such column" as a 500.
    """

    async def test_a_database_missing_new_columns_is_upgraded(
        self, tmp_path
    ) -> None:
        import aiosqlite

        from library.storage import LibraryStore

        path = str(tmp_path / "old.db")
        # A database as an earlier version would have created it.
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TABLE questions (id TEXT PRIMARY KEY, title TEXT NOT NULL,"
                " style TEXT NOT NULL DEFAULT '', difficulty TEXT NOT NULL DEFAULT '',"
                " topics TEXT NOT NULL DEFAULT '[]', languages TEXT NOT NULL DEFAULT '[]',"
                " author TEXT NOT NULL DEFAULT 'anonymous', votes INTEGER NOT NULL"
                " DEFAULT 0, created_at REAL NOT NULL, payload TEXT NOT NULL)"
            )
            await db.commit()

        store = LibraryStore(path)
        await store.connect()
        try:
            cur = await store.db.execute("PRAGMA table_info(questions)")
            columns = {row["name"] for row in await cur.fetchall()}
            assert {"source", "import_text"} <= columns

            qid = await store.publish(PublishRequest.model_validate(QUESTION))
            fetched = await store.get(qid)
            assert fetched is not None and fetched.source == "generated"
        finally:
            await store.close()
