"""Containment tests for the execution sandbox.

These assert the security boundary, so they matter more than anything else in
the suite. They MUST run inside the container: on a developer's macOS host there
is no `runner` user, no libseccomp and different rlimit semantics, so several
would vacuously pass. `make test` runs them in-container.
"""

from __future__ import annotations

import pytest

from freetcoder.runner import Limits, Verdict, run_python
from freetcoder.runner.sandbox import SECCOMP_AVAILABLE, runner_identity

FAST = Limits(wall_seconds=3.0, cpu_seconds=2, memory_mb=192)

in_container = pytest.mark.skipif(
    runner_identity() is None,
    reason="needs the container's root + `runner` user; run via `make test`",
)


class TestHappyPath:
    def test_stdout_is_captured(self) -> None:
        r = run_python("print(sum(range(100)))", limits=FAST)
        assert r.verdict is Verdict.OK
        assert r.stdout.strip() == "4950"

    def test_stdin_is_delivered(self) -> None:
        r = run_python("import sys; print(sys.stdin.read().upper())",
                       stdin="hello", limits=FAST)
        assert r.stdout.strip() == "HELLO"

    def test_stdlib_is_importable(self) -> None:
        r = run_python("import json, math, collections; print(json.dumps({'a': 1}))",
                       limits=FAST)
        assert r.verdict is Verdict.OK
        assert r.stdout.strip() == '{"a": 1}'

    def test_duration_is_recorded(self) -> None:
        assert run_python("print(1)", limits=FAST).duration_ms >= 0


class TestFailureClassification:
    def test_syntax_error_is_compile_error(self) -> None:
        r = run_python("def broken(:\n    pass", limits=FAST)
        assert r.verdict is Verdict.COMPILE_ERROR

    def test_exception_is_runtime_error(self) -> None:
        r = run_python("raise ValueError('boom')", limits=FAST)
        assert r.verdict is Verdict.RUNTIME_ERROR
        assert "ValueError" in r.stderr

    def test_nonzero_exit_is_runtime_error(self) -> None:
        assert run_python("import sys; sys.exit(3)", limits=FAST).verdict is Verdict.RUNTIME_ERROR


class TestResourceContainment:
    def test_infinite_loop_hits_wall_clock(self) -> None:
        r = run_python("while True: pass", limits=Limits(wall_seconds=1.5, cpu_seconds=1))
        assert r.verdict is Verdict.TIMEOUT
        assert r.duration_ms < 8_000, "process group was not killed promptly"

    def test_memory_bomb_is_capped(self) -> None:
        r = run_python("x = [0] * 10**9", limits=FAST)
        assert r.verdict is Verdict.MEMORY_EXCEEDED

    def test_output_flood_is_truncated(self) -> None:
        r = run_python("print('A' * 10**7)", limits=Limits(max_output_bytes=4096))
        assert len(r.stdout) <= 4096
        assert r.stdout_truncated

    @in_container
    def test_fork_bomb_is_contained(self) -> None:
        r = run_python("import os\nwhile True: os.fork()",
                       limits=Limits(wall_seconds=3.0, cpu_seconds=2))
        assert r.verdict in (Verdict.RUNTIME_ERROR, Verdict.TIMEOUT)

    @in_container
    def test_forked_children_do_not_survive(self) -> None:
        """Regression: a submission that forks and then exits cleanly used to
        orphan its children, leaking 32 live processes per run."""
        import subprocess as sp
        import time

        run_python(
            "import os, time\n"
            "for _ in range(8):\n"
            "    if os.fork() == 0:\n"
            "        time.sleep(30)\n"
            "        os._exit(0)\n"
            "print('parent done')",
            limits=Limits(wall_seconds=3.0),
        )
        time.sleep(0.5)
        # Exclude zombies (state Z): those are already dead, merely awaiting
        # reaping by PID 1 (docker compose `init: true`). A *live* survivor is
        # the actual leak.
        alive = sp.run(
            ["bash", "-c", "ps -u runner -o stat= | grep -cv '^Z' || true"],
            capture_output=True, text=True).stdout.strip()
        assert int(alive or 0) == 0, f"{alive} live runner processes leaked"


@in_container
class TestPrivilegeContainment:
    def test_runs_as_unprivileged_user(self) -> None:
        r = run_python("import os; print(os.getuid())", limits=FAST)
        assert r.stdout.strip() != "0", "submission ran as root"

    def test_cannot_read_shadow(self) -> None:
        r = run_python("print(open('/etc/shadow').read())", limits=FAST)
        assert r.verdict is Verdict.RUNTIME_ERROR
        assert "PermissionError" in r.stderr

    @pytest.mark.parametrize("path", ["/srv/app/X", "/etc/X", "/usr/local/X", "/X"])
    def test_cannot_write_outside_workspace(self, path: str) -> None:
        r = run_python(f"open({path!r}, 'w').write('x')", limits=FAST)
        assert r.verdict is Verdict.RUNTIME_ERROR
        assert "PermissionError" in r.stderr

    def test_can_write_inside_workspace(self) -> None:
        r = run_python("open('scratch.txt','w').write('x'); print('ok')", limits=FAST)
        assert r.verdict is Verdict.OK


@in_container
@pytest.mark.skipif(not SECCOMP_AVAILABLE, reason="libseccomp unavailable")
class TestNetworkContainment:
    def test_outbound_connection_is_blocked(self) -> None:
        r = run_python(
            "import socket; socket.create_connection(('1.1.1.1', 80), timeout=2)",
            limits=FAST,
        )
        assert r.verdict is Verdict.RUNTIME_ERROR
        assert "PermissionError" in r.stderr

    def test_dns_is_blocked(self) -> None:
        r = run_python("import socket; print(socket.gethostbyname('example.com'))",
                       limits=FAST)
        assert r.verdict is Verdict.RUNTIME_ERROR

    def test_network_block_can_be_disabled(self) -> None:
        """The gate runs trusted reference code; it should not be crippled."""
        r = run_python("import socket; socket.socket(); print('created')",
                       limits=Limits(wall_seconds=3.0, block_network=False))
        assert r.verdict is Verdict.OK


class TestLimitsValidation:
    @pytest.mark.parametrize("kwargs", [{"wall_seconds": 0}, {"memory_mb": -1},
                                        {"max_processes": 0}])
    def test_rejects_nonsense(self, kwargs: dict[str, int]) -> None:
        with pytest.raises(ValueError):
            Limits(**kwargs)  # type: ignore[arg-type]
