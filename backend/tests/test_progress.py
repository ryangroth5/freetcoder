"""Progress registry tests.

The registry lives for the life of the container, so the properties that matter
are that it stays bounded and that it can never affect the work it observes.
"""

from __future__ import annotations

import pytest

from freetcoder.progress import (
    MAX_STEPS,
    GenerationCancelled,
    ProgressRegistry,
    Reporter,
)


class TestRecordingSteps:
    def test_steps_accumulate_in_order(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        for message in ("asking the model", "running the reference", "accepted"):
            reg.step(run.id, message)

        got = reg.get(run.id)
        assert got is not None
        assert [s.message for s in got.steps] == [
            "asking the model", "running the reference", "accepted",
        ]

    def test_timestamps_are_relative_and_non_decreasing(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reg.step(run.id, "one")
        reg.step(run.id, "two")

        got = reg.get(run.id)
        assert got is not None
        assert got.steps[0].at >= 0
        assert got.steps[1].at >= got.steps[0].at

    def test_steps_carry_a_kind(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reg.step(run.id, "that failed", kind="fail")
        got = reg.get(run.id)
        assert got is not None and got.steps[0].kind == "fail"

    def test_the_step_log_is_bounded(self) -> None:
        """A runaway repair loop must not produce an unbounded log."""
        reg = ProgressRegistry()
        run = reg.start()
        for i in range(MAX_STEPS + 50):
            reg.step(run.id, f"step {i}")
        got = reg.get(run.id)
        assert got is not None and len(got.steps) == MAX_STEPS

    def test_steps_for_an_unknown_run_are_ignored(self) -> None:
        """Reporting must never raise into the work it observes."""
        reg = ProgressRegistry()
        reg.step("nonexistent", "hello")
        reg.finish("nonexistent", "accepted")
        assert reg.get("nonexistent") is None

    def test_a_none_run_id_is_ignored(self) -> None:
        reg = ProgressRegistry()
        reg.step(None, "hello")
        assert reg.is_cancelled(None) is False


class TestFinishing:
    def test_finish_records_the_outcome(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reg.finish(run.id, "accepted")
        got = reg.get(run.id)
        assert got is not None and got.finished and got.outcome == "accepted"

    def test_finish_is_terminal(self) -> None:
        """The first outcome is the real one; later noise cannot overwrite it."""
        reg = ProgressRegistry()
        run = reg.start()
        reg.finish(run.id, "accepted")
        reg.finish(run.id, "failed")
        got = reg.get(run.id)
        assert got is not None and got.outcome == "accepted"

    def test_steps_after_finishing_are_ignored(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reg.finish(run.id, "accepted")
        reg.step(run.id, "late arrival")
        got = reg.get(run.id)
        assert got is not None and got.steps == []


class TestCancellation:
    def test_cancel_is_visible_to_a_reader(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        assert reg.cancel(run.id) is True
        assert reg.is_cancelled(run.id) is True

    def test_cancelling_an_unknown_run_is_a_no_op(self) -> None:
        assert ProgressRegistry().cancel("nope") is False

    def test_cancelling_a_finished_run_is_a_no_op(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reg.finish(run.id, "accepted")
        assert reg.cancel(run.id) is False

    def test_a_checkpoint_raises_once_cancelled(self) -> None:
        reg = ProgressRegistry()
        run = reg.start()
        reporter = Reporter(reg, run.id)

        reporter.checkpoint()  # not cancelled: no effect
        reg.cancel(run.id)
        with pytest.raises(GenerationCancelled):
            reporter.checkpoint()


class TestTheRegistryStaysBounded:
    """An in-memory store that grows forever is a slow leak in a long-lived
    container."""

    def test_finished_runs_are_evicted_past_their_ttl(self) -> None:
        reg = ProgressRegistry(ttl=-1.0)  # everything is immediately stale
        run = reg.start()
        reg.finish(run.id, "accepted")
        assert reg.get(run.id) is None

    def test_unfinished_runs_survive_their_ttl(self) -> None:
        """A slow generation must not have its own progress collected."""
        reg = ProgressRegistry(ttl=-1.0)
        run = reg.start()
        assert reg.get(run.id) is not None

    def test_the_number_of_runs_is_capped(self) -> None:
        reg = ProgressRegistry(max_runs=5)
        ids = [reg.start().id for _ in range(20)]
        alive = sum(1 for rid in ids if reg.get(rid) is not None)
        assert alive <= 5

    def test_the_oldest_runs_are_dropped_first(self) -> None:
        reg = ProgressRegistry(max_runs=3)
        first = reg.start().id
        for _ in range(5):
            reg.start()
        assert reg.get(first) is None


class TestTheNullReporter:
    def test_it_accepts_everything_and_does_nothing(self) -> None:
        """Every existing call site keeps working untouched."""
        from freetcoder.progress import NULL_REPORTER

        NULL_REPORTER("anything at all")
        NULL_REPORTER("even a failure", kind="fail")
        NULL_REPORTER.checkpoint()  # must not raise


class TestProgressOverTheApi:
    """Uses the shared client fixture from conftest."""

    async def test_a_generation_reports_real_steps(self, client) -> None:
        resp = await client.post("/api/sessions", json={
            "style": "leetcode", "progress_id": "run-alpha",
        })
        assert resp.status_code == 200

        run = (await client.get("/api/progress/run-alpha")).json()
        assert run["finished"] is True
        assert run["outcome"] == "accepted"

        messages = [s["message"] for s in run["steps"]]
        # Not a fixed script -- the point is that real work is named.
        assert any("asking the model" in m for m in messages)
        assert any("reference" in m for m in messages)
        assert any("hidden" in m for m in messages)

    async def test_steps_carry_increasing_timestamps(self, client) -> None:
        await client.post("/api/sessions", json={
            "style": "leetcode", "progress_id": "run-times",
        })
        run = (await client.get("/api/progress/run-times")).json()
        times = [s["at"] for s in run["steps"]]
        assert times == sorted(times)

    async def test_generation_works_without_a_progress_id(self, client) -> None:
        """Progress is an observer: nothing depends on it."""
        resp = await client.post("/api/sessions", json={"style": "leetcode"})
        assert resp.status_code == 200

    async def test_an_unknown_run_is_404(self, client) -> None:
        assert (await client.get("/api/progress/never-existed")).status_code == 404

    async def test_a_failure_leaves_the_reason_in_the_log(
        self, client, fake_llm
    ) -> None:
        """The case that previously collapsed to one sentence of apology."""
        import json as _json

        from freetcoder.llm.fake import FIXTURE_DIR

        broken = _json.loads(
            (FIXTURE_DIR / "two_sum_unsolvable.json").read_text()
        )
        fake_llm.queue_next(*[broken] * 12)

        resp = await client.post("/api/sessions", json={
            "style": "leetcode", "progress_id": "run-doomed",
        })
        assert resp.status_code == 502

        run = (await client.get("/api/progress/run-doomed")).json()
        assert run["outcome"] == "failed"
        assert any(s["kind"] == "warn" for s in run["steps"])
        assert any("rejected" in s["message"] for s in run["steps"])

    async def test_cancelling_stops_the_run(self, client) -> None:
        from freetcoder.progress import registry

        registry.start("run-cancel")
        assert (await client.post("/api/progress/run-cancel/cancel")).json() == {
            "cancelled": True
        }

        resp = await client.post("/api/sessions", json={
            "style": "leetcode", "progress_id": "run-cancel",
        })
        assert resp.status_code == 409
        assert "cancelled" in resp.json()["detail"].lower()

    async def test_cancelling_an_unknown_run_is_not_an_error(self, client) -> None:
        resp = await client.post("/api/progress/nope/cancel")
        assert resp.status_code == 200
        assert resp.json() == {"cancelled": False}


class TestCancelBeforeTheRequestArrives:
    def test_a_cancel_survives_the_run_being_started(self) -> None:
        """Start and Cancel in quick succession is a real sequence.

        The client mints the id before the POST, so a cancel can land first.
        Clobbering the flag on start() would silently ignore it.
        """
        reg = ProgressRegistry()
        reg.start("early")
        reg.cancel("early")
        reg.start("early")  # the request finally arrives
        assert reg.is_cancelled("early") is True

    def test_an_ordinary_restart_is_not_cancelled(self) -> None:
        reg = ProgressRegistry()
        reg.start("plain")
        reg.start("plain")
        assert reg.is_cancelled("plain") is False
