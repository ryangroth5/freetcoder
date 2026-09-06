"""Process-level sandbox for untrusted submissions.

The *container* is the security boundary (single-user, no mounts, one exposed
port). This module is the second layer inside it: an unprivileged uid, resource
ceilings, and a seccomp filter that removes the network.

Two behaviours here are non-obvious and were found the hard way during container
discovery; both are load-bearing. See docs/container-discovery.md.
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import os
import pwd
import resource
import shutil
import signal
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from .types import Limits, RunResult, Verdict

log = logging.getLogger(__name__)

_PR_SET_NO_NEW_PRIVS = 38

#: Syscalls denied to submissions. Blocking socket() alone is not enough --
#: name resolution reaches the network by other paths.
_NETWORK_SYSCALLS = (
    "socket", "socketcall", "connect", "bind", "listen",
    "accept", "accept4", "sendto", "recvfrom", "sendmsg", "recvmsg",
)

# --- Load in the PARENT, before any fork. -----------------------------------
# pyseccomp resolves libseccomp via ctypes.util.find_library(), which *shells
# out to ldconfig*. That fork fails once we are setuid'd under RLIMIT_NPROC,
# and subprocess reports only "Exception occurred in preexec_fn", hiding the
# cause entirely. Importing here makes the failure impossible.
_seccomp: Any = None
_libc: Any = None
try:
    import pyseccomp as _seccomp_mod

    _seccomp = _seccomp_mod
    _libc = ctypes.CDLL("libc.so.6", use_errno=True)
    SECCOMP_AVAILABLE = True
except (ImportError, OSError, RuntimeError) as exc:  # pragma: no cover
    SECCOMP_AVAILABLE = False
    log.warning("seccomp unavailable (%s); submissions will have network access", exc)


class SandboxUnavailableError(RuntimeError):
    """Raised when the sandbox cannot be established as configured."""


def runner_identity() -> tuple[int, int] | None:
    """uid/gid of the unprivileged execution user, or None if we are not root.

    Outside the container (developer laptop, CI) there is no `runner` user and
    we are not root, so privilege dropping is skipped. Tests that assert on
    containment therefore MUST run in-container.
    """
    if os.geteuid() != 0:
        return None
    try:
        ent = pwd.getpwnam("runner")
    except KeyError:
        return None
    return ent.pw_uid, ent.pw_gid


def _build_preexec(
    limits: Limits, identity: tuple[int, int] | None
) -> Callable[[], None]:
    """Return the child-side setup callable.

    Ordering matters: rlimits and privilege drop first, then no_new_privs, then
    the seccomp filter. A filter loaded without no_new_privs requires
    CAP_SYS_ADMIN, which Docker does not grant by default.
    """

    def _child() -> None:
        os.setsid()  # own process group, so a timeout can kill the whole tree

        rl = resource.setrlimit
        rl(resource.RLIMIT_CPU, (limits.cpu_seconds, limits.cpu_seconds))
        if limits.limit_address_space:
            mem = limits.memory_mb * 1024 * 1024
            rl(resource.RLIMIT_AS, (mem, mem))
        rl(resource.RLIMIT_NPROC, (limits.max_processes, limits.max_processes))
        fsize = limits.file_size_mb * 1024 * 1024
        rl(resource.RLIMIT_FSIZE, (fsize, fsize))
        rl(resource.RLIMIT_CORE, (0, 0))

        if identity is not None:
            uid, gid = identity
            os.setgid(gid)
            os.setgroups([])
            os.setuid(uid)

        if limits.block_network and SECCOMP_AVAILABLE:
            _libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
            filt = _seccomp.SyscallFilter(defaction=_seccomp.ALLOW)
            for name in _NETWORK_SYSCALLS:
                try:
                    filt.add_rule(_seccomp.ERRNO(1), name)  # EPERM
                except (ValueError, RuntimeError):
                    continue  # syscall absent on this architecture
            filt.load()

    return _child


def _truncate(raw: bytes, cap: int) -> tuple[str, bool]:
    truncated = len(raw) > cap
    return raw[:cap].decode("utf-8", errors="replace"), truncated


def _classify(exit_code: int | None, stderr: str, timed_out: bool) -> Verdict:
    if timed_out:
        return Verdict.TIMEOUT
    if exit_code == 0:
        return Verdict.OK
    # RLIMIT_CPU fires SIGXCPU (-24); SIGKILL (-9) is our own process-group kill.
    if exit_code in (-signal.SIGXCPU, -signal.SIGKILL):
        return Verdict.TIMEOUT
    if "MemoryError" in stderr or exit_code == -signal.SIGSEGV:
        return Verdict.MEMORY_EXCEEDED
    return Verdict.RUNTIME_ERROR


def execute(
    argv: Sequence[str],
    workdir: Path,
    *,
    limits: Limits | None = None,
    stdin: str = "",
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run `argv` under the sandbox and never raise on hostile input."""
    limits = limits or Limits()
    identity = runner_identity()

    if limits.block_network and not SECCOMP_AVAILABLE:
        log.warning("network blocking requested but seccomp is unavailable")

    child_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        # -I would also be nice, but adapters set it explicitly where relevant.
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    if env:
        child_env.update(env)

    started = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv is constructed, never shell
            list(argv),
            cwd=str(workdir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=child_env,
            preexec_fn=_build_preexec(limits, identity),  # noqa: PLW1509
            close_fds=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RunResult(Verdict.INTERNAL_ERROR, "", f"failed to start: {exc}", None, 0)

    try:
        out, err = proc.communicate(stdin.encode(), timeout=limits.wall_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_tree(proc.pid)
        out, err = proc.communicate()
    finally:
        # ALWAYS tear down the group, even on a clean exit. A submission that
        # forks children and then returns normally leaves them orphaned and
        # running -- observed leaking 32 processes from a fork bomb whose
        # parent exited on its own.
        _kill_tree(proc.pid)

    duration_ms = int((time.monotonic() - started) * 1000)
    stdout, out_trunc = _truncate(out or b"", limits.max_output_bytes)
    stderr, err_trunc = _truncate(err or b"", limits.max_output_bytes)

    return RunResult(
        verdict=_classify(proc.returncode, stderr, timed_out),
        stdout=stdout,
        stderr=stderr,
        exit_code=proc.returncode,
        duration_ms=duration_ms,
        stdout_truncated=out_trunc,
        stderr_truncated=err_trunc,
    )


def _kill_tree(pgid: int) -> None:
    """SIGKILL an entire process group.

    Takes the pgid captured at spawn time rather than calling getpgid() here:
    once communicate() has reaped the direct child its pid is gone, getpgid
    raises, and any surviving grandchildren are silently left running. The
    child calls setsid(), so its pgid equals its pid.
    """
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pgid, signal.SIGKILL)


class Workspace:
    """A per-run scratch directory owned by the runner uid.

    Everything outside this directory is read-only to the submission -- verified
    against /srv/app, /etc, /usr/local and /.
    """

    def __init__(self) -> None:
        self.path = Path(tempfile.mkdtemp(prefix="ftc-"))
        identity = runner_identity()
        if identity is not None:
            os.chown(self.path, identity[0], identity[1])
        os.chmod(self.path, 0o700 if identity else 0o777)

    def write(self, name: str, content: str, mode: int = 0o644) -> Path:
        target = self.path / name
        target.write_text(content, encoding="utf-8")
        os.chmod(target, mode)
        return target

    def __enter__(self) -> Workspace:
        return self

    def __exit__(self, *exc: object) -> None:
        shutil.rmtree(self.path, ignore_errors=True)
