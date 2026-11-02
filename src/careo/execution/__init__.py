"""Running one program once, with timeouts, output caps and process-group cleanup.

This is NOT a sandbox. Programs run as the current user with full filesystem
and network access. Careo only runs programs the user configured explicitly.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO

EXITED = "exited"
TIMEOUT = "timeout"
OUTPUT_LIMIT = "output_limit"
SPAWN_ERROR = "spawn_error"

_CHUNK = 65536
_POLL = 0.005
_READER_JOIN_SECONDS = 5.0


@dataclass(frozen=True)
class RunResult:
    status: str
    exit_code: int | None  # negative = killed by that signal (POSIX)
    stdout: bytes
    stderr: bytes
    duration_seconds: float
    error: str | None = None
    stdout_truncated: bool = False
    stderr_truncated: bool = False


class _Reader(threading.Thread):
    def __init__(self, stream: IO[bytes], limit: int, overflow: threading.Event) -> None:
        super().__init__(daemon=True)
        self.stream = stream
        self.limit = limit
        self.overflow = overflow
        self.buf = bytearray()
        self.truncated = False

    def run(self) -> None:
        try:
            while True:
                chunk = self.stream.read1(_CHUNK) if hasattr(self.stream, "read1") else b""
                if not chunk:
                    break
                room = self.limit - len(self.buf)
                if len(chunk) > room:
                    self.buf += chunk[: max(room, 0)]
                    self.truncated = True
                    self.overflow.set()
                    # keep draining so the child never blocks on a full pipe
                else:
                    self.buf += chunk
        except (OSError, ValueError):
            pass


def _write_stdin(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
    except (BrokenPipeError, OSError, ValueError):
        pass  # the program may legitimately exit without reading stdin
    finally:
        with contextlib.suppress(OSError, ValueError):
            stream.close()


def _exited_without_reaping(proc: subprocess.Popen[bytes]) -> bool:
    """True once the child has exited, leaving it a zombie so its PID (and thus
    its process-group id) cannot be reused before we kill the group."""
    if hasattr(os, "waitid"):
        try:
            info = os.waitid(os.P_PID, proc.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        except ChildProcessError:
            return True
        return info is not None
    return proc.poll() is not None


def _kill_group(proc: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(proc.pid, signal.SIGKILL)
    with contextlib.suppress(ProcessLookupError, OSError):
        proc.kill()


def run_program(
    argv: Sequence[str],
    cwd: Path,
    env: Mapping[str, str],
    stdin: bytes,
    timeout_seconds: float,
    max_output_bytes: int,
) -> RunResult:
    """Run ``argv`` (never through a shell) and capture its observable behavior.

    The child gets its own process group; after it exits, times out, or exceeds
    the output cap, the whole group is SIGKILLed so stray grandchildren do not
    outlive the run.
    """
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            cwd=cwd,
            env=dict(env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            close_fds=True,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return RunResult(
            SPAWN_ERROR, None, b"", b"", time.monotonic() - start, f"{type(exc).__name__}: {exc}"
        )

    assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
    overflow = threading.Event()
    out = _Reader(proc.stdout, max_output_bytes, overflow)
    err = _Reader(proc.stderr, max_output_bytes, overflow)
    writer = threading.Thread(target=_write_stdin, args=(proc.stdin, stdin), daemon=True)
    out.start()
    err.start()
    writer.start()

    deadline = start + timeout_seconds
    timed_out = False
    try:
        while not _exited_without_reaping(proc):
            if overflow.is_set():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(_POLL)
    finally:
        _kill_group(proc)
        exit_code = proc.wait()
    duration = time.monotonic() - start

    out.join(_READER_JOIN_SECONDS)
    err.join(_READER_JOIN_SECONDS)
    writer.join(_READER_JOIN_SECONDS)
    error = None
    if out.is_alive() or err.is_alive():
        # A descendant escaped the process group and still holds the pipes open.
        error = "output pipes still open after the process group was killed"
    for stream in (proc.stdout, proc.stderr):
        with contextlib.suppress(OSError):
            stream.close()

    if timed_out:
        status = TIMEOUT
        exit_code_out: int | None = None
    elif out.truncated or err.truncated:
        status = OUTPUT_LIMIT
        exit_code_out = exit_code
    else:
        status = EXITED
        exit_code_out = exit_code
    return RunResult(
        status,
        exit_code_out,
        bytes(out.buf),
        bytes(err.buf),
        duration,
        error,
        out.truncated,
        err.truncated,
    )
