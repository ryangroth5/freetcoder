"""Shared fixtures. Everything runs offline against FakeLLM."""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from freetcoder.app import create_app
from freetcoder.llm import FakeLLM
from freetcoder.llm.fake import FIXTURE_DIR


def fixture_payload(name: str) -> dict:
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text())


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Enough good questions queued to satisfy a multi-question session."""
    return FakeLLM([fixture_payload("two_sum_good") for _ in range(12)])


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    """Never inherit the dev container's environment.

    The dev container sets FREETCODER_DB_PATH=/data/freetcoder.db, so without
    this every test run pollutes (and reads from) the real question cache --
    which quietly made tests pass on cached data rather than what they set up.
    It also points FREETCODER_LIBRARY_URL at the library service; tests opt in
    to a library rather than inheriting one.
    """
    from freetcoder.settings import get_settings

    monkeypatch.setenv("FREETCODER_DB_PATH", "")
    monkeypatch.setenv("FREETCODER_LIBRARY_URL", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
async def client(fake_llm: FakeLLM) -> AsyncIterator[AsyncClient]:
    app = create_app()
    app.state.llm = fake_llm
    app.state.get_llm = lambda: fake_llm
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        # The lifespan sets state.llm back to None; restore the fake.
        app.state.llm = fake_llm
        app.state.get_llm = lambda: fake_llm
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


SOLUTION = (
    "def two_sum(nums, target):\n"
    "    seen = {}\n"
    "    for i, n in enumerate(nums):\n"
    "        if target - n in seen:\n"
    "            return [seen[target - n], i]\n"
    "        seen[n] = i\n"
    "    return []\n"
)

WRONG_SOLUTION = "def two_sum(nums, target):\n    return [0, 0]\n"


@pytest.fixture
def quick_kill(monkeypatch: pytest.MonkeyPatch) -> None:
    """Shorten the wall clock for tests that wait out a real timeout.

    These tests assert that runaway code *is* killed and its process tree goes
    with it. The production limit governs how long a candidate may wait, which
    is not what is under test, so waiting the full 15 seconds only buys slower
    feedback. Two seconds still exercises the same kill path.

    Applied per test rather than globally: anything that measures runtime --
    the reference-scaling check especially -- needs the real headroom.
    """
    from freetcoder import service
    from freetcoder.generate import gate, tools
    from freetcoder.runner.types import Limits

    def short(limits: Limits) -> Limits:
        return dataclasses.replace(limits, wall_seconds=2.0, cpu_seconds=2)

    for module, name in (
        (gate, "REFERENCE_LIMITS"),
        (gate, "GENERATOR_LIMITS"),
        (gate, "BRUTE_FORCE_LIMITS"),
        (tools, "TOOL_LIMITS"),
        (service, "SUBMISSION_LIMITS"),
    ):
        monkeypatch.setattr(module, name, short(getattr(module, name)))
