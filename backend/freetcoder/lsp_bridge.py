"""WebSocket <-> stdio bridge for language servers.

A transparent byte pump, deliberately. pyright emits `window/logMessage`
notifications *before* it answers `initialize`, so anything that assumes a
request/response pairing desynchronises immediately. We forward frames in both
directions and let the client's language client do the correlating.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import WebSocket, WebSocketDisconnect

from .models import Language

log = logging.getLogger(__name__)

SERVER_COMMANDS: dict[Language, list[str]] = {
    Language.PYTHON: ["pyright-langserver", "--stdio"],
    # One server covers both; the client picks the languageId per document.
    Language.JAVASCRIPT: ["typescript-language-server", "--stdio"],
    Language.TYPESCRIPT: ["typescript-language-server", "--stdio"],
    Language.GO: ["gopls", "serve"],
}

#: Guard against a wedged server flooding the socket.
MAX_FRAME_BYTES = 8 * 1024 * 1024


class LanguageServerUnavailableError(RuntimeError):
    pass


async def _pump_stdout(proc: asyncio.subprocess.Process, ws: WebSocket) -> None:
    """Server -> browser. Reads LSP frames and forwards them verbatim."""
    assert proc.stdout is not None
    while True:
        headers: dict[str, str] = {}
        while True:
            line = await proc.stdout.readline()
            if not line:
                return  # server exited
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                break
            key, _, value = text.partition(":")
            headers[key.strip().lower()] = value.strip()

        try:
            length = int(headers.get("content-length", "0"))
        except ValueError:
            continue
        if length <= 0 or length > MAX_FRAME_BYTES:
            log.warning("dropping LSP frame of %d bytes", length)
            continue

        body = await proc.stdout.readexactly(length)
        await ws.send_text(body.decode("utf-8", errors="replace"))


async def _pump_stdin(proc: asyncio.subprocess.Process, ws: WebSocket) -> None:
    """Browser -> server. Re-frames each message with a Content-Length header."""
    assert proc.stdin is not None
    while True:
        message = await ws.receive_text()
        payload = message.encode("utf-8")
        proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode())
        proc.stdin.write(payload)
        await proc.stdin.drain()


async def bridge(ws: WebSocket, language: Language) -> None:
    """Own one language-server process for the life of one WebSocket."""
    command = SERVER_COMMANDS.get(language)
    if command is None:
        await ws.close(code=4004, reason=f"no language server for {language.value}")
        return

    try:
        proc = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        log.error("language server not installed: %s", command[0])
        await ws.close(code=4005, reason=f"{command[0]} is not installed")
        return

    log.info("started %s (pid %d)", command[0], proc.pid)
    tasks = [
        asyncio.create_task(_pump_stdout(proc, ws)),
        asyncio.create_task(_pump_stdin(proc, ws)),
    ]
    try:
        # Whichever direction ends first ends the session.
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        log.info("stopped %s (pid %d)", command[0], proc.pid)
