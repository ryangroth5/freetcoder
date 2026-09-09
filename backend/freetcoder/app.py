"""Application entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import router
from .library import build_library
from .llm import LLMClient, build_client
from .lsp_bridge import bridge
from .models import Language
from .settings import get_settings
from .storage import Storage

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("freetcoder")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    store = Storage(settings.db_path)
    await store.connect()
    app.state.store = store
    # Saved settings must land before build_library, which reads library_url --
    # applying them afterwards would leave a stale library client on every boot.
    app.state.saved_settings = settings.apply_saved(await store.load_settings())
    app.state.llm = None
    app.state.library = build_library(settings)
    log.info("library: %s", settings.library_url or "not configured")
    log.info(
        "storage: %s", settings.db_path or "in-memory (no volume mounted)"
    )
    try:
        yield
    finally:
        await store.close()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="freetcoder", version="0.1.0", lifespan=lifespan)

    def get_llm() -> LLMClient:
        """Built lazily so a key supplied on the setup screen takes effect."""
        if app.state.llm is None:
            app.state.llm = build_client(settings)
        client: LLMClient = app.state.llm
        return client

    app.state.get_llm = get_llm
    app.include_router(router)

    @app.get("/api/health")
    async def health() -> JSONResponse:
        return JSONResponse({"ok": True, "configured": settings.configured})

    @app.websocket("/lsp/{language}")
    async def lsp(ws: WebSocket, language: str) -> None:
        try:
            lang = Language(language)
        except ValueError:
            await ws.close(code=4004, reason=f"unknown language {language!r}")
            return
        await ws.accept()
        await bridge(ws, lang)

    _mount_frontend(app, settings.static_dir)
    return app


def _mount_frontend(app: FastAPI, static_dir: Path | None) -> None:
    """Serve the built SPA, when one was baked into the image."""
    if static_dir is None or not static_dir.is_dir():
        log.info("no static build mounted; run the Vite dev server for the UI")
        return

    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/{path:path}")
    async def spa(path: str) -> FileResponse:
        # Client-side routing: unknown paths return index.html, not a 404.
        candidate = static_dir / path
        if path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(static_dir / "index.html")

    log.info("serving frontend from %s", static_dir)


app = create_app()
