"""The settings API: what persists, what is refused, and what never leaks.

These are the first tests to cover this surface at all. The route that can
reconfigure a running server had none, which is how a browser run silently
reconfigured a developer's backend for a day without anyone noticing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from freetcoder.app import create_app
from freetcoder.settings import get_settings
from freetcoder.storage import Storage


@pytest.fixture
async def configured_app(
    tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[AsyncClient, str]]:
    """A real app over a real database file, so persistence can be proven."""
    db = str(tmp_path / "settings.db")  # type: ignore[operator]
    monkeypatch.setenv("FREETCODER_DB_PATH", db)
    monkeypatch.setenv("FREETCODER_LLM_API_KEY", "sk-secret-wxyz")
    get_settings.cache_clear()
    app = create_app()
    transport = ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=transport, base_url="http://t") as ac,
    ):
        yield ac, db
    get_settings.cache_clear()


class TestTheKeyStaysOutOfIt:
    """The key is environment-only, and the API must never hand it back."""

    async def test_the_key_is_never_in_a_response(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        get_resp = await client.get("/api/settings")
        put_resp = await client.put("/api/settings", json={"llm_model": "m"})
        assert "sk-secret-wxyz" not in get_resp.text
        assert "sk-secret-wxyz" not in put_resp.text

    async def test_only_a_hint_is_offered(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        body = (await client.get("/api/settings")).json()
        # Enough to recognise the key you meant; useless to anyone else.
        assert body["has_key"] is True
        assert body["key_hint"] == "wxyz"

    async def test_the_key_cannot_be_set_through_this_route(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        resp = await client.put("/api/settings", json={"llm_api_key": "sk-new"})
        assert resp.status_code == 422
        assert get_settings().llm_api_key == "sk-secret-wxyz"


class TestPersistence:
    async def test_a_saved_value_survives_a_new_storage(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        """The only assertion that actually proves 'survives a restart'."""
        client, db = configured_app
        await client.put("/api/settings", json={"generation_attempts": 7})

        fresh = Storage(db)
        await fresh.connect()
        saved = await fresh.load_settings()
        await fresh.close()
        assert json.loads(saved["generation_attempts"]) == 7

    async def test_a_fresh_process_picks_saved_values_up(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, db = configured_app
        await client.put("/api/settings", json={"repair_rounds": 9})

        get_settings.cache_clear()
        second = create_app()
        async with second.router.lifespan_context(second):
            assert get_settings().repair_rounds == 9

    async def test_source_tracks_where_a_value_came_from(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        before = (await client.get("/api/settings")).json()
        assert before["sources"]["tutor_tool_budget"] == "default"

        after = (await client.put(
            "/api/settings", json={"tutor_tool_budget": 2}
        )).json()
        assert after["sources"]["tutor_tool_budget"] == "saved"

    async def test_reset_forgets_a_saved_value(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, db = configured_app
        await client.put("/api/settings", json={"generation_attempts": 8})
        body = (await client.delete("/api/settings/generation_attempts")).json()

        assert body["values"]["generation_attempts"] == 4  # the default
        assert body["sources"]["generation_attempts"] != "saved"
        fresh = Storage(db)
        await fresh.connect()
        assert "generation_attempts" not in await fresh.load_settings()
        await fresh.close()

    async def test_resetting_something_unsettable_is_a_404(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        assert (await client.delete("/api/settings/db_path")).status_code == 404


class TestTheBoundsAreLoadBearing:
    """This API is unauthenticated; without bounds a PUT can spend money."""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("generation_attempts", 100000),
            ("repair_rounds", -1),
            ("tool_call_budget", 9999),
            ("llm_timeout_s", 0),
            ("tutor_message_cap", 0),
        ],
    )
    async def test_out_of_range_is_refused(
        self, configured_app: tuple[AsyncClient, str], field: str, value: object
    ) -> None:
        client, _ = configured_app
        resp = await client.put("/api/settings", json={field: value})
        assert resp.status_code == 422

    @pytest.mark.parametrize("field", ["db_path", "static_dir", "fake_llm"])
    async def test_infrastructure_fields_are_not_settable(
        self, configured_app: tuple[AsyncClient, str], field: str
    ) -> None:
        """A file path or a fixture switch over HTTP is not a preference."""
        client, _ = configured_app
        resp = await client.put("/api/settings", json={field: "x"})
        assert resp.status_code == 422


class TestClientsAreRebuilt:
    async def test_a_write_forces_the_llm_client_to_rebuild(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        client, _ = configured_app
        await client.put("/api/settings", json={"llm_model": "another"})
        assert client._transport.app.state.llm is None  # type: ignore[attr-defined,union-attr]

    async def test_changing_the_library_url_replaces_the_library_client(
        self, configured_app: tuple[AsyncClient, str]
    ) -> None:
        """Nothing rebuilt this before; a runtime change was inert."""
        client, _ = configured_app
        app = client._transport.app  # type: ignore[attr-defined,union-attr]
        before = app.state.library
        await client.put(
            "/api/settings", json={"library_url": "http://elsewhere:8090"}
        )
        assert app.state.library is not before
        assert app.state.library.configured


class TestHonestyAboutStorage:
    async def test_an_in_memory_database_reports_itself(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Saving still works; the page must just not claim it will last."""
        monkeypatch.setenv("FREETCODER_DB_PATH", "")
        get_settings.cache_clear()
        app = create_app()
        transport = ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=transport, base_url="http://t") as c,
        ):
            body = (await c.get("/api/settings")).json()
            assert body["persistent"] is False
            saved = (await c.put(
                "/api/settings", json={"repair_rounds": 5}
            )).json()
            # Applied for this process even though it will not survive.
            assert saved["values"]["repair_rounds"] == 5
        get_settings.cache_clear()
